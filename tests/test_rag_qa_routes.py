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
    assert len(fake.prompts) == 1  # 只调用一次(作答),无改写调用
    assert "研究助理" in fake.prompts[0] and "用户问题:螺纹钢" in fake.prompts[0]


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
