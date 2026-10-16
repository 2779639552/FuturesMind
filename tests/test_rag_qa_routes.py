"""研报问答·多轮智能知识库(/api/research/qa/*)测试。

检索层与 LLM 全部打桩(仿 test_rag_ask_route);DB 用 tmp_path 真建库,
monkeypatch database._local.db 隔离 ~/.tradingagents 生产库。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import database  # noqa: E402
import tradingagents.rag as rag_pkg  # noqa: E402
import tradingagents.rag.service as rag_service  # noqa: E402
import web_app  # noqa: E402


def _hit(report_id=42, variety="RB", chunk_index=0, text="螺纹钢库存去化。", score=0.83):
    return {
        "id": f"{report_id}:{chunk_index}",
        "text": text,
        "metadata": {
            "report_id": report_id,
            "chunk_index": chunk_index,
            "chunk_total": 3,
            "variety": variety,
            "varieties": variety,
            "publish_date": "2026-09-07",
            "report_type": "日报",
            "title": f"报告{report_id}",
            "source": "华泰期货",
            "direction": "看多",
            "text_truncated": False,
        },
        "score": score,
    }


class _FakeLLMClient:
    """按调用顺序返回预设答案;记录每次 prompt 供断言改写/钳制行为。"""

    def __init__(self, answers):
        self.answers = list(answers)  # 按调用顺序弹出
        self.prompts = []

    def get_llm(self):
        client = self

        class _L:
            def invoke(self, prompt):
                client.prompts.append(prompt)

                # 【2026-09-24】LLM 选检索深度路由的 prompt 含特征串 → 直接回 "6",
                # 不弹预设答案队列(否则会吃掉 rewrite/主回答的答案, 测试错位)。
                if "只输出一个数字(top_k)" in prompt:
                    class _R6:
                        content = "6"
                    return _R6()

                class _R:
                    content = client.answers.pop(0) if client.answers else "默认回答 [1]。"

                return _R()

        return _L()


@pytest.fixture
def api(app_client):
    return app_client


@pytest.fixture
def app_client(monkeypatch, tmp_path):
    db = database.AgentSenseDB(str(tmp_path / "qa.db"))
    monkeypatch.setattr(database._local, "db", db, raising=False)
    # teardown 交给 monkeypatch 撤销(delattr 恢复"无此属性"状态),
    # 这里不能再手动 del,否则 monkeypatch.undo 会 AttributeError。
    yield web_app.app.test_client()


def _patch_rag_ok(monkeypatch, hits=None):
    monkeypatch.setattr(rag_pkg, "is_available", lambda: True)
    captured = {}

    def _fake_retrieve(question, top_k=6, variety=None, report_type=None,
                       exclude_report_id=None, date_from=None, date_to=None, **kw):
        captured.update(question=question, top_k=top_k, variety=variety,
                        report_type=report_type, date_from=date_from, date_to=date_to)
        return hits if hits is not None else [_hit()]

    monkeypatch.setattr(rag_service, "retrieve_hits", _fake_retrieve)
    return captured


# ---------------------------------------------------------------------------
# POST /api/research/qa/ask — 基本链路
# ---------------------------------------------------------------------------
def test_qa_ask_auto_creates_session(api, monkeypatch):
    _patch_rag_ok(monkeypatch)
    monkeypatch.setattr(web_app, "create_llm_client",
                        lambda *a, **k: _FakeLLMClient(["回答 [1]。"]))
    body = api.post("/api/research/qa/ask", json={"question": "螺纹钢怎么看"}).get_json()
    assert body["ok"] is True and body["session_id"] > 0 and body["message_id"] > 0
    assert body["answer"] == "回答 [1]。" and len(body["citations"]) == 1
    assert body["rewritten_query"] == ""  # 首轮不改写
    # 消息落库:2 行(1 user + 1 assistant)
    msgs = database.get_db().get_qa_messages(body["session_id"])
    assert len(msgs) == 2 and msgs[0]["role"] == "user" and msgs[1]["role"] == "assistant"
    # 会话标题=首问截 30 字
    assert database.get_db().get_qa_session(body["session_id"])["title"] == "螺纹钢怎么看"


def test_qa_ask_followup_rewrites_query(api, monkeypatch):
    captured = _patch_rag_ok(monkeypatch)
    monkeypatch.setattr(web_app, "create_llm_client",
                        lambda *a, **k: _FakeLLMClient(["螺纹钢 成本端", "回答 [1]。"]))
    # 第一轮
    b1 = api.post("/api/research/qa/ask", json={"question": "近期螺纹钢供需怎么看"}).get_json()
    assert b1["ok"] is True
    # 第二轮追问:改写 LLM 返回"螺纹钢 成本端",应透传检索
    b2 = api.post("/api/research/qa/ask",
                  json={"question": "那成本端呢", "session_id": b1["session_id"]}).get_json()
    assert b2["ok"] is True
    assert captured["question"] == "螺纹钢 成本端"  # 检索收到改写 query 而非原话
    assert b2["rewritten_query"] == "螺纹钢 成本端"
    msgs = database.get_db().get_qa_messages(b1["session_id"])
    assert len(msgs) == 4 and msgs[-1]["rewritten_query"] == "螺纹钢 成本端"


def test_qa_ask_first_turn_skips_rewrite_llm(api, monkeypatch):
    _patch_rag_ok(monkeypatch)
    fake = _FakeLLMClient(["回答 [1]。"])
    seen = []
    monkeypatch.setattr(web_app, "create_llm_client", lambda *a, **k: (seen.append(1), fake)[1])
    body = api.post("/api/research/qa/ask", json={"question": "螺纹钢"}).get_json()
    assert body["ok"] is True and body["rewritten_query"] == ""
    # 【2026-09-24】首轮调用 2 次: ①top_k 路由(选检索深度) ②作答; 无改写调用
    assert len(fake.prompts) == 2, f"prompts={len(fake.prompts)}"
    assert "只输出一个数字(top_k)" in fake.prompts[0]  # 第一次是检索深度路由
    assert "研究助理" in fake.prompts[1] and "用户问题:螺纹钢" in fake.prompts[1]


def test_qa_ask_rewrite_failure_falls_back(api, monkeypatch):
    captured = _patch_rag_ok(monkeypatch)

    class _BoomFirst:
        def get_llm(self):
            class _L:
                def invoke(self, prompt):
                    if "改写器" in prompt:  # 只让改写调用失败(按 prompt 特征区分)
                        raise RuntimeError("rewrite down")
                    class _R:
                        content = "回答 [1]。"
                    return _R()
            return _L()

    monkeypatch.setattr(web_app, "create_llm_client", lambda *a, **k: _BoomFirst())
    b1 = api.post("/api/research/qa/ask", json={"question": "螺纹钢"}).get_json()
    b2 = api.post("/api/research/qa/ask",
                  json={"question": "那库存呢", "session_id": b1["session_id"]}).get_json()
    assert b2["ok"] is True
    assert captured["question"] == "那库存呢"  # 改写失败 → 原问题直接检索
    assert b2["rewritten_query"] == ""  # 空串 = 改写未成功(审计可辨)


def test_qa_ask_history_clamped_to_3_turns(api, monkeypatch):
    _patch_rag_ok(monkeypatch)
    prompts = []
    fake = _FakeLLMClient([])
    monkeypatch.setattr(web_app, "create_llm_client",
                        lambda *a, **k: (prompts.append(fake), fake)[1])
    sid = None
    for i in range(5):  # 造 5 轮历史(改写只应看到最近 3 轮)
        fake.answers.append(f"改写{i}")
        fake.answers.append(f"回答{i}")
        body = api.post("/api/research/qa/ask",
                        json={"question": f"问题{i}", "session_id": sid}).get_json()
        sid = body["session_id"]
    last_rewrite_prompt = fake.prompts[-2]  # 倒数第 1 是作答,-2 是第 5 轮的改写
    assert "问题4" in last_rewrite_prompt  # 最新问题在
    assert "问题0" not in last_rewrite_prompt and "问题1" not in last_rewrite_prompt  # 旧轮被钳掉
    assert "问题2" in last_rewrite_prompt  # 最近 3 轮(2/3/4)保留


def test_qa_ask_filters_passthrough(api, monkeypatch):
    captured = _patch_rag_ok(monkeypatch)
    monkeypatch.setattr(web_app, "create_llm_client",
                        lambda *a, **k: _FakeLLMClient(["回答 [1]。"]))
    body = api.post("/api/research/qa/ask", json={
        "question": "螺纹钢", "variety": "rb", "report_type": "日报",
        "date_from": "2026-08-01", "date_to": "2026-09-11",
    }).get_json()
    assert body["ok"] is True
    assert captured["variety"] == "RB"
    assert captured["report_type"] == "日报"
    assert captured["date_from"] == "2026-08-01" and captured["date_to"] == "2026-09-11"
    # 筛选快照落消息行(复盘可对账)
    msg = database.get_db().get_qa_message(body["message_id"])
    assert msg["filters"] == {"variety": "RB", "report_type": "日报",
                              "date_from": "2026-08-01", "date_to": "2026-09-11"}


def test_qa_ask_session_not_found(api, monkeypatch):
    _patch_rag_ok(monkeypatch)
    body = api.post("/api/research/qa/ask",
                    json={"question": "x", "session_id": 99999}).get_json()
    assert resp_ok(body) is False and "会话不存在" in body["error"]


def resp_ok(body):
    return body.get("ok")


# ---------------------------------------------------------------------------
# 失败矩阵
# ---------------------------------------------------------------------------
def test_qa_ask_rag_disabled_logs_user_message(api, monkeypatch):
    monkeypatch.setattr(rag_pkg, "is_available", lambda: False)
    body = api.post("/api/research/qa/ask", json={"question": "螺纹钢"}).get_json()
    assert body["ok"] is False and body["rag_enabled"] is False
    msgs = database.get_db().get_qa_messages(body["session_id"])
    assert len(msgs) == 2  # user + assistant(error 行也落库)
    assert msgs[0]["content"] == "螺纹钢" and msgs[1]["error"]


def test_qa_ask_no_hits(api, monkeypatch):
    _patch_rag_ok(monkeypatch, hits=[])
    monkeypatch.setattr(web_app, "create_llm_client",
                        lambda *a, **k: _FakeLLMClient(["螺纹钢", "回答。"]))
    body = api.post("/api/research/qa/ask", json={"question": "冷门品种"}).get_json()
    assert body["ok"] is True and body["retrieved"] == 0 and body["citations"] == []
    assert "未检索到" in body["error"]


def test_qa_ask_llm_failure_keeps_citations_and_logs(api, monkeypatch):
    _patch_rag_ok(monkeypatch, hits=[_hit()])
    calls = {"n": 0}

    class _BoomSecond:
        def get_llm(self):
            class _L:
                def invoke(self, prompt):
                    calls["n"] += 1
                    if calls["n"] >= 2:  # 第 1 次(首轮作答)成功,之后改写/作答全失败
                        raise RuntimeError("429")
                    class _R:
                        content = "改写后的查询"
                    return _R()
            return _L()

    monkeypatch.setattr(web_app, "create_llm_client", lambda *a, **k: _BoomSecond())
    b1 = api.post("/api/research/qa/ask", json={"question": "螺纹钢"}).get_json()
    b2 = api.post("/api/research/qa/ask",
                  json={"question": "追问", "session_id": b1["session_id"]}).get_json()
    assert b2["ok"] is False and len(b2["citations"]) == 1
    assert "生成回答失败" in b2["error"]
    last = database.get_db().get_qa_message(b2["message_id"])
    assert last["role"] == "assistant" and last["error"] and last["citations"]


# ---------------------------------------------------------------------------
# 会话 CRUD / 回放
# ---------------------------------------------------------------------------
def test_qa_session_list_delete_messages(api, monkeypatch):
    _patch_rag_ok(monkeypatch)
    monkeypatch.setattr(web_app, "create_llm_client",
                        lambda *a, **k: _FakeLLMClient(["回答 [1]。"]))
    b = api.post("/api/research/qa/ask", json={"question": "会话甲"}).get_json()
    sid = b["session_id"]
    # 列表出现该会话
    lst = api.get("/api/research/qa/sessions").get_json()["sessions"]
    assert any(s["id"] == sid for s in lst)
    # 消息回放:citations JSON 反序列化成 list
    r = api.get(f"/api/research/qa/sessions/{sid}/messages").get_json()
    assert r["ok"] is True and len(r["messages"]) == 2
    assert r["messages"][1]["citations"][0]["report_id"] == 42
    assert r["session"]["id"] == sid
    # 删除:消息一并消失
    assert api.delete(f"/api/research/qa/sessions/{sid}").get_json()["ok"] is True
    assert api.get(f"/api/research/qa/sessions/{sid}/messages").status_code == 404
    assert database.get_db().get_qa_messages(sid) == []
    # 再删 → 404
    assert api.delete(f"/api/research/qa/sessions/{sid}").status_code == 404


def test_qa_sessions_list_order_by_updated(api, monkeypatch):
    monkeypatch.setattr(web_app, "create_llm_client", lambda *a, **k: _FakeLLMClient(["答"]))
    db = database.get_db()
    s1 = db.create_qa_session(title="旧会话")
    s2 = db.create_qa_session(title="新会话")
    sessions = api.get("/api/research/qa/sessions").get_json()["sessions"]
    ids = [s["id"] for s in sessions]
    assert ids.index(s2["id"]) < ids.index(s1["id"])  # 后建的在前


# ---------------------------------------------------------------------------
# DB 层单测
# ---------------------------------------------------------------------------
def test_db_qa_message_count_and_update(app_client):
    db = database.get_db()
    s = db.create_qa_session(title="t", variety="RB")
    db.insert_qa_message(s["id"], "user", "q1")
    db.insert_qa_message(s["id"], "assistant", "a1")
    before = db.get_qa_session(s["id"])
    assert before["message_count"] == 0  # insert 不自动刷计数,update 时重算
    import time as _t
    _t.sleep(1.1)  # 保证 updated_at 可见变化(SQLite datetime 精度秒级)
    db.update_qa_session(s["id"], variety="CU")
    after = db.get_qa_session(s["id"])
    assert after["message_count"] == 2 and after["variety"] == "CU"
    assert after["updated_at"] > before["updated_at"]


def test_db_qa_history_rewrite_truncates_oldest(app_client):
    db = database.get_db()
    s = db.create_qa_session()
    for i in range(4):
        db.insert_qa_message(s["id"], "user", f"长问题{'x' * 50}{i}")
        db.insert_qa_message(s["id"], "assistant", "答")
    hist = db.get_qa_history_for_rewrite(s["id"], max_turns=2, max_chars=200)
    contents = [m["content"] for m in hist]
    assert len(hist) <= 4
    assert any("长问题" in c and c.endswith("3") for c in contents)  # 最新一轮完整保留
    assert not any(c.endswith("0") for c in contents)  # 最旧的先被丢


# ---------------------------------------------------------------------------
# 聪明截断 + 自指检测(2026-09-13 改进)
# ---------------------------------------------------------------------------
def test_shrink_answer_keeps_tail_headings():
    """超预算截断应保住尾部标题行——它们正是"点"的名字,指代对象不能被截掉。"""
    content = "开头段。" + "x" * 150 + "\n## 1. 供给端维持偏弱\n" + "y" * 300 \
        + "\n## 2. 需求端成交偏弱\n" + "z" * 300 + "\n结尾段。"
    out = web_app._shrink_answer_for_history(content, budget=400)
    assert out.startswith(content[:120])          # 开头保留
    assert "## 1. 供给端维持偏弱" in out          # 尾部标题幸存(旧实现从头截会丢)
    assert "## 2. 需求端成交偏弱" in out
    assert len(out) <= 400


def test_shrink_answer_short_passthrough():
    assert web_app._shrink_answer_for_history("短回答") == "短回答"


def test_qa_ask_selfref_includes_prior_answer(api, monkeypatch):
    """自指追问("展开讲讲第二点")应把上轮回答全文注入作答 prompt(带"以片段为准"标注);
    非自指追问不应注入。"""
    _patch_rag_ok(monkeypatch)
    fake = _FakeLLMClient([])
    monkeypatch.setattr(web_app, "create_llm_client", lambda *a, **k: fake)
    # 第一轮:长回答(带可识别标记)
    fake.answers.append("首轮长回答,要点A是MARKER unique_token_42,后续展开。")
    b1 = api.post("/api/research/qa/ask", json={"question": "螺纹钢供需怎么看"}).get_json()
    assert b1["ok"] is True
    # 第二轮:自指追问
    fake.answers.append("改写后的查询")
    fake.answers.append("衔接式回答 [1]。")
    b2 = api.post("/api/research/qa/ask",
                  json={"question": "展开讲讲第二点", "session_id": b1["session_id"]}).get_json()
    assert b2["ok"] is True
    answer_prompt = fake.prompts[-1]
    assert "你上一轮的回答" in answer_prompt        # 附加材料块的标注在
    assert "unique_token_42" in answer_prompt       # 上轮回答全文(未被截断)在
    assert "一律以片段为准" in answer_prompt        # 幻觉压制标注在
    # 第三轮:非自指追问 → 不注入
    fake.answers.append("改写后的查询")
    fake.answers.append("普通回答 [1]。")
    b3 = api.post("/api/research/qa/ask",
                  json={"question": "铁矿石现在怎么样", "session_id": b1["session_id"]}).get_json()
    assert b3["ok"] is True
    assert "你上一轮的回答" not in fake.prompts[-1]


def test_qa_ask_selfref_off_on_first_turn(api, monkeypatch):
    """首轮无历史可衔接,即使措辞像自指也不注入空块。"""
    _patch_rag_ok(monkeypatch)
    fake = _FakeLLMClient(["展开说说的查询", "回答 [1]。"])
    monkeypatch.setattr(web_app, "create_llm_client", lambda *a, **k: fake)
    body = api.post("/api/research/qa/ask", json={"question": "展开讲讲第二点"}).get_json()
    assert body["ok"] is True
    assert "你上一轮的回答" not in fake.prompts[-1]


def test_qa_ask_rewrite_history_keeps_tail_headings(api, monkeypatch):
    """改写历史经聪明截断后,上轮长回答的尾部标题应进入改写 prompt(旧实现会截丢)。"""
    _patch_rag_ok(monkeypatch)
    fake = _FakeLLMClient([])
    monkeypatch.setattr(web_app, "create_llm_client", lambda *a, **k: fake)
    long_answer = "开头。" + "x" * 150 + "\n## 1. 供给端\n" + "y" * 300 \
        + "\n## 2. 需求端成交偏弱\n" + "z" * 300
    fake.answers.append(long_answer)  # 第一轮回答
    b1 = api.post("/api/research/qa/ask", json={"question": "螺纹钢供需怎么看"}).get_json()
    assert b1["ok"] is True
    fake.answers.append("螺纹钢 需求端")  # 第二轮改写产物
    fake.answers.append("回答 [1]。")
    api.post("/api/research/qa/ask",
             json={"question": "那第二点呢", "session_id": b1["session_id"]})
    rewrite_prompt = fake.prompts[-2]  # -1 是作答, -2 是第二轮改写
    assert "助理:" in rewrite_prompt
    assert "## 2. 需求端成交偏弱" in rewrite_prompt  # 尾部标题幸存,改写器看得见"点"的名字
    assert len(rewrite_prompt) < 3000  # 截断仍生效,没有全量塞入


# ---------------------------------------------------------------------------
# 自进化 P0:反馈信号(2026-09-14)
# ---------------------------------------------------------------------------
def test_qa_feedback_roundtrip_and_replay(api, monkeypatch):
    _patch_rag_ok(monkeypatch)
    monkeypatch.setattr(web_app, "create_llm_client",
                        lambda *a, **k: _FakeLLMClient(["回答 [1]。"]))
    b = api.post("/api/research/qa/ask", json={"question": "螺纹钢"}).get_json()
    mid = b["message_id"]
    r = api.post("/api/research/qa/feedback",
                 json={"message_id": mid, "feedback": "down"}).get_json()
    assert r["ok"] is True and r["message"]["feedback"] == "down"
    # 回放(会话切换)恢复反馈状态
    msgs = api.get(f"/api/research/qa/sessions/{b['session_id']}/messages").get_json()["messages"]
    assert msgs[-1]["feedback"] == "down"


def test_qa_feedback_invalid_value_and_missing_message(api, monkeypatch):
    _patch_rag_ok(monkeypatch)
    monkeypatch.setattr(web_app, "create_llm_client",
                        lambda *a, **k: _FakeLLMClient(["答"]))
    b = api.post("/api/research/qa/ask", json={"question": "螺纹钢"}).get_json()
    # 非法值 → 200 + ok:false(业务失败约定)
    r = api.post("/api/research/qa/feedback",
                 json={"message_id": b["message_id"], "feedback": "meh"}).get_json()
    assert r["ok"] is False and "up/down" in r["error"]
    # 消息不存在 → 200 + ok:false
    r2 = api.post("/api/research/qa/feedback",
                  json={"message_id": 99999, "feedback": "up"}).get_json()
    assert r2["ok"] is False and "不存在" in r2["error"]
    # message_id 缺失 → 400
    assert api.post("/api/research/qa/feedback", json={"feedback": "up"}).status_code == 400


def test_db_migrate_adds_feedback_column_to_old_schema(tmp_path):
    """旧库(无 feedback 列)经 _migrate 后补列——CREATE TABLE IF NOT EXISTS 不改老表。"""
    import sqlite3
    p = tmp_path / "old.db"
    conn = sqlite3.connect(p)
    conn.executescript("""
        CREATE TABLE qa_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT DEFAULT '',
            citations TEXT DEFAULT '[]',
            retrieved INTEGER DEFAULT 0,
            filters TEXT DEFAULT '{}',
            rewritten_query TEXT DEFAULT '',
            error TEXT DEFAULT '',
            latency_ms INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    conn.close()
    db = database.AgentSenseDB(str(p))
    with db._conn() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(qa_messages)").fetchall()}
    assert "feedback" in cols


# ---------------------------------------------------------------------------
# 自进化 P1:错题本(2026-09-14)
# ---------------------------------------------------------------------------
def test_qa_insights_stats_and_failure_pairing(api):
    db = database.get_db()
    s = db.create_qa_session(title="错题会话")
    db.insert_qa_message(s["id"], "user", "被点踩的问题")
    m1 = db.insert_qa_message(s["id"], "assistant", "回答甲", retrieved=3)
    db.set_qa_feedback(m1["id"], "down")
    db.insert_qa_message(s["id"], "user", "检索空转的问题")
    m2 = db.insert_qa_message(s["id"], "assistant", "", retrieved=0)
    db.insert_qa_message(s["id"], "user", "报错的问题")
    m3 = db.insert_qa_message(s["id"], "assistant", "", retrieved=2, error="生成回答失败")
    db.insert_qa_message(s["id"], "user", "正常的问题")
    db.insert_qa_message(s["id"], "assistant", "回答乙", retrieved=4)  # 不应浮出
    body = api.get("/api/research/qa/insights").get_json()
    st = body["stats"]
    assert st == {"up": 0, "down": 1, "zero_hit": 1, "failed": 1}
    fails = body["failures"]
    assert {f["message_id"] for f in fails} == {m1["id"], m2["id"], m3["id"]}
    by_id = {f["message_id"]: f for f in fails}
    assert by_id[m1["id"]]["question"] == "被点踩的问题"   # 配对前一条 user 提问
    assert by_id[m2["id"]]["question"] == "检索空转的问题"
    assert by_id[m3["id"]]["error"] == "生成回答失败"
    assert len(by_id[m1["id"]]["answer"]) <= 200  # 回答摘要截断


# ---------------------------------------------------------------------------
# 自进化 P2:黑话词典(2026-09-14)
# ---------------------------------------------------------------------------
def test_qa_terms_crud_and_upsert(api):
    db = database.get_db()
    # 非法输入
    r0 = api.post("/api/research/qa/terms", json={"term": "螺纹"}).get_json()
    assert r0["ok"] is False
    r_same = api.post("/api/research/qa/terms",
                      json={"term": "螺纹", "mapping": "螺纹"}).get_json()
    assert r_same["ok"] is False
    # 正常添加 + 列表
    t1 = api.post("/api/research/qa/terms",
                  json={"term": "螺纹", "mapping": "RB 螺纹钢"}).get_json()
    assert t1["ok"] is True
    lst = api.get("/api/research/qa/terms").get_json()["terms"]
    assert any(x["term"] == "螺纹" and x["mapping"] == "RB 螺纹钢" for x in lst)
    # 同名再添加 → 更新 mapping,不报错不重复
    api.post("/api/research/qa/terms", json={"term": "螺纹", "mapping": "螺纹钢 RB"})
    lst2 = api.get("/api/research/qa/terms").get_json()["terms"]
    rows = [x for x in lst2 if x["term"] == "螺纹"]
    assert len(rows) == 1 and rows[0]["mapping"] == "螺纹钢 RB"
    # 删除 + 再删 False
    assert api.delete(f"/api/research/qa/terms/{rows[0]['id']}").get_json()["ok"] is True
    assert api.delete(f"/api/research/qa/terms/{rows[0]['id']}").status_code == 404
    assert db.delete_qa_term(rows[0]["id"]) is False


def test_qa_ask_first_turn_term_substitution(api, monkeypatch):
    """首轮不走改写 LLM,黑话词典直替换;rewritten_query 落替换后 query 供审计。"""
    captured = _patch_rag_ok(monkeypatch)
    monkeypatch.setattr(web_app, "create_llm_client",
                        lambda *a, **k: _FakeLLMClient(["回答 [1]。"]))
    database.get_db().add_qa_term("螺纹", "RB 螺纹钢")
    body = api.post("/api/research/qa/ask", json={"question": "螺纹现在咋样"}).get_json()
    assert body["ok"] is True
    assert captured["question"] == "RB 螺纹钢现在咋样"  # 检索收到替换后 query
    assert body["rewritten_query"] == "RB 螺纹钢现在咋样"  # 落库可审计
    # 命中计数 +1
    terms = database.get_db().list_qa_terms()
    assert terms[0]["hits"] == 1
    # 无词典命中的首轮:原问题透传,rewritten_query 为空
    body2 = api.post("/api/research/qa/ask",
                     json={"question": "铁矿石呢", "session_id": body["session_id"]}).get_json()
    assert body2["ok"] is True


def test_qa_ask_rewrite_prompt_injects_terms(api, monkeypatch):
    """有历史时词典映射表注入改写 prompt(而非直替换问题文本)。"""
    _patch_rag_ok(monkeypatch)
    fake = _FakeLLMClient([])
    monkeypatch.setattr(web_app, "create_llm_client", lambda *a, **k: fake)
    database.get_db().add_qa_term("螺纹", "RB 螺纹钢")
    fake.answers.append("回答 [1]。")  # 第一轮作答(首轮问题不含黑话,直替换不命中)
    b1 = api.post("/api/research/qa/ask", json={"question": "黑色系怎么看"}).get_json()
    assert b1["ok"] is True
    fake.answers.append("螺纹钢 供需")  # 第二轮改写产物
    fake.answers.append("回答 [1]。")
    b2 = api.post("/api/research/qa/ask",
                  json={"question": "那螺纹的库存呢", "session_id": b1["session_id"]}).get_json()
    assert b2["ok"] is True
    rewrite_prompt = fake.prompts[-2]
    assert "术语映射参考" in rewrite_prompt and "螺纹→RB 螺纹钢" in rewrite_prompt
    # 改写 LLM 的产物不被直替换干扰(问题原文送改写,映射表供参考)
    assert "那螺纹的库存呢" in rewrite_prompt
