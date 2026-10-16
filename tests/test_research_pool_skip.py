"""品种池「已跳过」口径测试(2026-09-22)。

【要防的回归】识别出的品种**全部**在当前品种池(ACTIVE_VARIETIES)外时,原先复用
`ValueError` 走 error 分支 ⇒ 研报库标红并计入"失败"数。实测东证一批 7 条 error
全是池外品种(JD 鸡蛋 / SA 纯碱 / JM,J,CU / TL,TF,T 国债),属白名单的**预期过滤**,
真失败反被噪声淹没(用户读成"链路坏了")。

现在改抛 `_VarietyPoolSkip` ⇒ 落 `status="skipped"`。本测试锁死三件事:
  1) 全池外 → skipped(**绝不能**是 error);
  2) 至少 1 个在池 → 仍走 done(闸门口径是"任一在池即放行");
  3) skipped 的行不写聚合 JSON ⇒ 不进逐品种观点总览 / 单品种总结 / RAG。
另外锁住 except 顺序:若有人把 `except Exception` 提到前面,用例 1 会立刻变红。

全隔离:不联网、不落真实库、不碰真实 ~/.tradingagents、不烧 LLM。
"""

import types

import database
import tradingagents.dataflows.research_data as rd
import web_app

# 池外品种(ACTIVE_VARIETIES 不含 JD/SA/JM/TL)与池内品种(SC)各取几个做样本。
OUT_OF_POOL_JSON = (
    '{"report_title":"鸡蛋纯碱周度跟踪","publisher":"东证期货","publish_date":"2026-09-21",'
    '"varieties":['
    '{"variety":"JD","direction":"看空","confidence":0.6},'
    '{"variety":"SA","direction":"中性","confidence":0.5}]}'
)
MIXED_JSON = (
    '{"report_title":"能化与黑色系","publisher":"测试源","publish_date":"2026-09-21",'
    '"varieties":['
    '{"variety":"JD","direction":"看空","confidence":0.6},'
    '{"variety":"SC","direction":"看多","confidence":0.8}]}'
)


class _Resp:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    """第一步(结构化抽取)返回给定 varieties;第二步(逐品种结论)返回固定文本。"""

    def __init__(self, varieties_json):
        self._json = varieties_json

    def invoke(self, prompt):
        if "只输出一个 JSON 对象" in prompt:
            return _Resp(self._json)
        return _Resp("## 核心观点\n测试结论。")


def _run(monkeypatch, tmp_path, varieties_json):
    """插一行研报并跑完整后台处理,返回 (db, rid)。"""
    monkeypatch.setattr(rd, "RESEARCH_DIR", tmp_path)
    rd._research_cache.clear()
    db = database.AgentSenseDB(tmp_path / "test.db")
    md_path = tmp_path / "r.md"
    md_path.write_text("## 研报\n测试正文。", encoding="utf-8")
    rid = db.insert_research_report(
        variety="SC", title="测试研报", source="测试源", filename="r.md", file_path=str(md_path)
    )
    monkeypatch.setattr(web_app, "get_db", lambda: db)
    monkeypatch.setattr(
        web_app,
        "create_llm_client",
        lambda *a, **k: types.SimpleNamespace(get_llm=lambda: _FakeLLM(varieties_json)),
    )
    web_app._process_research_report(rid)
    return db, rid


def test_all_out_of_pool_sets_skipped(monkeypatch, tmp_path):
    """品种全在池外 ⇒ skipped + 原因落 error 列;绝不能被记成 error。"""
    db, rid = _run(monkeypatch, tmp_path, OUT_OF_POOL_JSON)
    got = db.get_research_report(rid)
    assert got["status"] == "skipped", f"池外品种应记 skipped,实际 {got['status']}"
    assert got["status"] != "error", "池外过滤是预期行为,不该标红为失败"
    assert "不在当前品种池内" in (got["error"] or ""), "应保留可读的跳过原因"


def test_partial_in_pool_still_done(monkeypatch, tmp_path):
    """至少 1 个品种在池内 ⇒ 照常 done(闸门是"任一在池即放行",不是"全都得在池")。"""
    db, rid = _run(monkeypatch, tmp_path, MIXED_JSON)
    got = db.get_research_report(rid)
    assert got["status"] == "done", f"有品种在池就该处理,实际 {got['status']}"
    # 既有口径:varieties 列会带上池外代码(池外品种只是不单独成篇,不阻断整篇)
    assert "SC" in (got["varieties"] or "")
    assert "JD" in (got["varieties"] or "")


def test_skipped_does_not_write_aggregate(monkeypatch, tmp_path):
    """skipped 的行不进聚合 JSON ⇒ 不进观点总览/总结/RAG。"""
    db, rid = _run(monkeypatch, tmp_path, OUT_OF_POOL_JSON)
    for code in ("JD", "SA", "SC"):
        blob = rd.load_research_data(code)
        ids = [r.get("id") for r in (blob or {}).get("reports", [])] if blob else []
        assert rid not in ids, f"skipped 的 {rid} 不该出现在 {code} 的聚合里"


def test_pool_skip_is_its_own_exception_type():
    """必须是独立异常类型,且**不继承 ValueError**。

    理由:闸门若继续复用 ValueError,任何一层 `except ValueError` 都可能先把它吞成
    普通失败;独立成类后,处理链路上的 except 顺序才可控(见 `_process_research_report_inner`)。
    """
    assert issubclass(web_app._VarietyPoolSkip, Exception)
    assert not issubclass(web_app._VarietyPoolSkip, ValueError), (
        "_VarietyPoolSkip 不该是 ValueError 子类 —— 会被既有的 except ValueError 抢走"
    )
