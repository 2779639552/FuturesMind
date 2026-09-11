"""研报单品种总结(_generate_variety_summary 链路)单元测试。

覆盖:逐篇条目收集(按品种过滤/整段结论/四指标逐品种段不串味)、文件复用
(force 重跑)、LLM 生成与失败降级、周报口径独立落盘、列表聚合路由、参数校验。
不真调 LLM(client 打桩);文件路径隔离到 tmp_path;RAG 注入打桩为空。
"""

import json

import pytest

import web_app


# ---------------------------------------------------------------------------
# 桩件(与 test_research_daily_summary 同款)
# ---------------------------------------------------------------------------
class _FakeDB:
    """最小 DB 桩:list_research_reports 返回预置行。"""

    def __init__(self, rows):
        self._rows = rows

    def list_research_reports(self, variety=None, limit=50, ingest_source=None):
        return [dict(r) for r in self._rows]


def _row(rid=1, date="2026-09-02", codes="SC", status="done", direction="看多",
         confidence=0.7, conclusion=None, publish_date=None, report_type="",
         structured=None):
    return {
        "id": rid,
        "title": f"研报{rid}",
        "source": "华泰期货",
        "variety": codes.split(",")[0],
        "varieties": codes,
        "uploaded_at": f"{date} 08:00:00",
        "publish_date": publish_date or "",
        "report_type": report_type,
        "status": status,
        "direction": direction,
        "confidence": confidence,
        "conclusion_md": conclusion,
        "structured_data": json.dumps(structured, ensure_ascii=False) if structured else None,
    }


@pytest.fixture
def vsum_dir(tmp_path, monkeypatch):
    """把单品种总结目录隔离到临时目录,并打桩 RAG 注入(不真查向量库)。"""
    monkeypatch.setattr(web_app, "RESEARCH_VARIETY_DIR", tmp_path / "research_variety")
    monkeypatch.setattr(web_app, "_rag_context_for_variety", lambda *a, **k: "")
    return tmp_path / "research_variety"


# ---------------------------------------------------------------------------
# 收集链路
# ---------------------------------------------------------------------------
def test_collect_variety_items_filters_by_code_and_per_variety_direction():
    """多品种研报只收目标品种的「## {code} 结论」段,方向用逐品种口径。"""
    md = (
        "## SC 结论\n## 交易要素与风险\n方向:看空;形态与区间:—;头寸:—;头寸范围:—;风险:—\n\n"
        "## LU 结论\n## 交易要素与风险\n方向:看多;形态与区间:—;头寸:—;头寸范围:—;风险:—\n"
    )
    rows = [_row(rid=9, codes="SC,LU", direction="看多", conclusion=md)]
    sc = web_app._collect_variety_report_items(rows, "2026-09-02", "SC")
    lu = web_app._collect_variety_report_items(rows, "2026-09-02", "LU")
    assert len(sc) == 1 and "看空" in sc[0]["segment"] and sc[0]["direction"] == "看空"
    assert len(lu) == 1 and lu[0]["direction"] == "看多"


def test_collect_variety_items_metrics_match_variety_segment():
    """四指标取"该品种段":{**r, variety: code} 保证多品种研报不把主品种基差串给次品种。"""
    structured = {"varieties": [
        {"variety": "SC", "basis": {"value": "5", "unit": "元/吨"}},
        {"variety": "LU", "basis": {"value": "3", "unit": "元/吨"}},
    ]}
    rows = [_row(rid=9, codes="SC,LU", structured=structured, conclusion="LU 结论。")]
    lu = web_app._collect_variety_report_items(rows, "2026-09-02", "LU")
    basis = next(m for m in lu[0]["metrics"] if m["k"] == "basis")
    assert basis["value"] == "3"


def test_collect_variety_items_skips_other_dates_and_status():
    rows = [
        _row(rid=1, status="processing", conclusion="x"),
        _row(rid=2, date="2026-09-01", conclusion="x"),
        _row(rid=3, codes="LU", conclusion="LU 自己的结论。"),
    ]
    assert [i["title"] for i in web_app._collect_variety_report_items(rows, "2026-09-02", "LU")] == ["研报3"]


def test_collect_variety_items_weekly_mode(tmp_path, monkeypatch):
    """周报口径只收周报行,与日报口径互斥(与每日总结同规则)。"""
    monkeypatch.setattr(web_app, "RESEARCH_VARIETY_DIR", tmp_path)
    monkeypatch.setattr(web_app, "_rag_context_for_variety", lambda *a, **k: "")
    rows = [
        _row(rid=1, conclusion="周报结论。", report_type="周报"),
        _row(rid=2, conclusion="日报结论。", report_type="日报"),
    ]
    assert [i["title"] for i in web_app._collect_variety_report_items(rows, "2026-09-02", "SC", "周报")] == ["研报1"]
    assert [i["title"] for i in web_app._collect_variety_report_items(rows, "2026-09-02", "SC")] == ["研报2"]


# ---------------------------------------------------------------------------
# 生成链路(文件复用 / LLM 打桩)
# ---------------------------------------------------------------------------
def test_generate_variety_reuses_existing_file(vsum_dir):
    path = vsum_dir / "daily" / "2026-09-02_LU.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "<!-- AgentSense 单品种总结 | code:LU | generated_at:x | reports:1 -->\n\n# 已有", encoding="utf-8")
    out = web_app._generate_variety_summary("2026-09-02", "LU")
    assert out["ok"] and out["cached"] and "已有" in out["content"]


def test_generate_variety_llm_success_writes_file(vsum_dir, monkeypatch):
    """生成:prompt 含品种与「当日关键数据」节;落盘 research_variety/daily;二跑走缓存。"""
    md = "## 交易要素与风险\n方向:看多;形态与区间:—;头寸:—;头寸范围:—;风险:—"
    monkeypatch.setattr(web_app, "get_db", lambda: _FakeDB([_row(rid=1, codes="LU", conclusion=md)]))

    class _FakeClient:
        def get_llm(self):
            class _L:
                def invoke(self, prompt):
                    assert "LU" in prompt and "## 当日关键数据" in prompt
                    assert "严禁使用 markdown 表格" in prompt  # 逻辑为主口径(2026-09-09)
                    class _R:
                        content = "## 多空驱动与逻辑链\n(逻辑正文)"
                    return _R()
            return _L()

    monkeypatch.setattr(web_app, "create_llm_client", lambda *a, **k: _FakeClient())
    out = web_app._generate_variety_summary("2026-09-02", "LU")
    assert out["ok"] and not out["cached"] and out["reports"] == 1
    saved = web_app._variety_summary_path("2026-09-02", "LU")
    assert saved.is_file() and "逻辑正文" in saved.read_text(encoding="utf-8")
    assert "research_variety" in str(saved)
    monkeypatch.setattr(web_app, "create_llm_client",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not call")))
    out2 = web_app._generate_variety_summary("2026-09-02", "LU")
    assert out2["ok"] and out2["cached"]


def test_generate_variety_metrics_block_in_payload(vsum_dir, monkeypatch):
    """有四指标时 prompt 带当日关键数据备查块;完全没有则不带(让 LLM 写「未提供」)。"""
    structured = {"basis": {"value": "3", "unit": "元/吨"}}
    md = "## 交易要素与风险\n方向:看多;形态与区间:—;头寸:—;头寸范围:—;风险:—"
    rows_with = [_row(rid=1, codes="LU", conclusion=md, structured=structured)]
    rows_without = [_row(rid=1, codes="LU", conclusion=md)]
    prompts = []

    class _FakeClient:
        def get_llm(self):
            class _L:
                def invoke(self, prompt):
                    prompts.append(prompt)
                    class _R:
                        content = "正文"
                    return _R()
            return _L()

    monkeypatch.setattr(web_app, "get_db", lambda: _FakeDB(rows_with))
    monkeypatch.setattr(web_app, "create_llm_client", lambda *a, **k: _FakeClient())
    web_app._generate_variety_summary("2026-09-02", "LU", force=True)
    assert "当日关键数据备查" in prompts[-1] and "基差=3" in prompts[-1]
    monkeypatch.setattr(web_app, "get_db", lambda: _FakeDB(rows_without))
    web_app._generate_variety_summary("2026-09-02", "LU", force=True)
    assert "当日关键数据备查" not in prompts[-1]


def test_generate_variety_no_reports_error(vsum_dir, monkeypatch):
    monkeypatch.setattr(web_app, "get_db", lambda: _FakeDB([]))  # 必须打桩:真库有研报会真调 LLM
    out = web_app._generate_variety_summary("2026-09-02", "LU")
    assert not out["ok"] and "没有涉及 LU" in out["error"]


def test_generate_variety_llm_failure_degrades(vsum_dir, monkeypatch):
    md = "LU 结论。"
    monkeypatch.setattr(web_app, "get_db", lambda: _FakeDB([_row(rid=1, codes="LU", conclusion=md)]))

    def _boom(*a, **k):
        raise RuntimeError("429")
    monkeypatch.setattr(web_app, "create_llm_client", _boom)
    out = web_app._generate_variety_summary("2026-09-02", "LU")
    assert not out["ok"] and "LLM 生成失败" in out["error"]
    assert not web_app._variety_summary_path("2026-09-02", "LU").exists()  # 失败不留半截文件


def test_generate_variety_bad_code(vsum_dir):
    assert not web_app._generate_variety_summary("2026-09-02", "rb;drop")["ok"]


def test_generate_variety_weekly_writes_weekly_dir(vsum_dir, monkeypatch):
    """周报口径落盘 research_variety/weekly,与日报互不串。"""
    monkeypatch.setattr(web_app, "get_db",
                        lambda: _FakeDB([_row(rid=1, date="2026-09-06", codes="LU", conclusion="LU 周报结论。", report_type="周报")]))

    class _FakeClient:
        def get_llm(self):
            class _L:
                def invoke(self, prompt):
                    class _R:
                        content = "(周报正文)"
                    return _R()
            return _L()

    monkeypatch.setattr(web_app, "create_llm_client", lambda *a, **k: _FakeClient())
    out = web_app._generate_variety_summary("2026-09-06", "LU", rtype="周报")
    assert out["ok"]
    saved = web_app._variety_summary_path("2026-09-06", "LU", "周报")
    assert saved.is_file() and "weekly" in str(saved)


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
def test_route_variety_summary_list_and_cache(vsum_dir, monkeypatch):
    """列表:未生成 → summary_md=None;写文件后再查 → cached=True 且带元信息;坏日期 400。"""
    md = "## 交易要素与风险\n方向:看多;形态与区间:—;头寸:—;头寸范围:—;风险:—"
    monkeypatch.setattr(web_app, "get_db", lambda: _FakeDB([_row(rid=1, codes="LU", conclusion=md)]))
    client = web_app.app.test_client()

    body = client.get("/api/research/variety-summary/2026-09-02").get_json()
    assert body["date"] == "2026-09-02" and len(body["varieties"]) == 1
    v = body["varieties"][0]
    assert v["code"] == "LU" and v["report_count"] == 1 and v["directions"] == ["看多"]
    assert v["summary_md"] is None and not v["cached"]
    # 来源观点对比(2026-09-09):逐份研报的 来源/方向/置信度/原件跳转 id
    assert v["report_rows"] == [{"report_id": 1, "source": "华泰期货", "title": "研报1",
                             "direction": "看多", "confidence": "70%"}]

    path = web_app._variety_summary_path("2026-09-02", "LU")
    path.parent.mkdir(parents=True)
    path.write_text("<!-- AgentSense 单品种总结 | code:LU | generated_at:t | reports:1 -->\n\n正文", encoding="utf-8")
    body2 = client.get("/api/research/variety-summary/2026-09-02").get_json()
    v2 = body2["varieties"][0]
    assert v2["cached"] and "正文" in v2["summary_md"] and v2["generated_at"] == "t"

    assert client.get("/api/research/variety-summary/bad").status_code == 400


def test_route_variety_generate_bad_date():
    client = web_app.app.test_client()
    assert client.post("/api/research/variety-summary/generate",
                       json={"date": "bad", "code": "LU"}).status_code == 400
