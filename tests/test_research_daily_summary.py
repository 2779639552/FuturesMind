"""研报每日总结(_generate_daily_summary 链路)单元测试。

覆盖:逐品种结论条目收集(多品种拆段/方向逐品种口径)、md 元信息读写、
日期列表并集、文件复用(force 重跑)、LLM 生成与失败降级、路由参数校验。
不真调 LLM(client 打桩);文件路径隔离到 tmp_path。
"""

import pytest

import web_app


# ---------------------------------------------------------------------------
# 桩件
# ---------------------------------------------------------------------------
class _FakeDB:
    """最小 DB 桩:list_research_reports 返回预置行。"""

    def __init__(self, rows):
        self._rows = rows

    def list_research_reports(self, variety=None, limit=50, ingest_source=None):
        return [dict(r) for r in self._rows]


def _row(rid=1, date="2026-09-02", codes="SC", status="done", direction="看多",
         confidence=0.7, conclusion=None, publish_date=None, report_type=""):
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
    }


_TRED = (
    "## 供需格局\n供应收紧。\n\n"
    "## 交易要素与风险\n方向:看多;形态与区间:单边看多;头寸:轻仓;头寸范围:—;风险:增产\n\n"
    "## 建议权重\n多头 20%~30%。"
)


@pytest.fixture
def daily_dir(tmp_path, monkeypatch):
    """把每日/周报总结目录隔离到临时目录。"""
    monkeypatch.setattr(web_app, "RESEARCH_DAILY_DIR", tmp_path / "research_daily")
    monkeypatch.setattr(web_app, "RESEARCH_WEEKLY_DIR", tmp_path / "research_weekly")
    return tmp_path / "research_daily"


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------
def test_daily_variety_segment_extracts_named_block():
    md = f"## SC 结论\n{_TRED}\n\n## LU 结论\n LU 自己的结论。"
    assert "方向:看多" in web_app._daily_variety_segment(md, "SC")
    assert "LU 自己的结论" in web_app._daily_variety_segment(md, "LU")


def test_daily_variety_segment_fallback_whole_text():
    assert web_app._daily_variety_segment("## 核心观点\n旧格式。", "RB") == "## 核心观点\n旧格式。"
    assert web_app._daily_variety_segment("", "RB") == ""


def test_collect_items_splits_multivariety_and_per_variety_direction():
    md = (
        "## SC 结论\n## 交易要素与风险\n方向:看空;形态与区间:—;头寸:—;头寸范围:—;风险:—\n\n"
        "## LU 结论\n## 交易要素与风险\n方向:看多;形态与区间:—;头寸:—;头寸范围:—;风险:—\n"
    )
    rows = [_row(rid=9, codes="SC,LU", direction="看多", conclusion=md)]
    items = web_app._collect_daily_report_items(rows, "2026-09-02")
    assert [(i["variety"].split("(")[0], i["direction"]) for i in items] == [("SC", "看空"), ("LU", "看多")]


def test_collect_items_confidence_per_variety(monkeypatch):
    """置信度逐品种口径(2026-09-07 修复):双品种研报的次品种不再冒用主品种 DB 行值
    (曾导致每日总结总表 LU=45% 与观点总览 LU=50% 不一致)。
    聚合 JSON 有逐品种值 → 各用各的;缺该品种 → 主品种回退 DB 值,次品种不标(—)。"""
    import tradingagents.dataflows.research_data as rd

    md = (
        "## SC 结论\n## 交易要素与风险\n方向:看空;形态与区间:—;头寸:—;头寸范围:—;风险:—\n\n"
        "## LU 结论\n## 交易要素与风险\n方向:看多;形态与区间:—;头寸:—;头寸范围:—;风险:—\n"
    )
    rows = [_row(rid=9, codes="SC,LU", confidence=0.45, conclusion=md)]
    agg = {
        "SC": {"reports": [{"id": 9, "confidence": 0.45}]},
        "LU": {"reports": [{"id": 9, "confidence": 0.5}]},
    }
    monkeypatch.setattr(rd, "load_research_data", lambda code: agg.get(code))
    confs = {
        i["variety"].split("(")[0]: i["confidence"]
        for i in web_app._collect_daily_report_items(rows, "2026-09-02")
    }
    assert confs == {"SC": "45%", "LU": "50%"}

    monkeypatch.setattr(rd, "load_research_data", lambda code: None)  # 聚合缺失 → 回退
    confs2 = {
        i["variety"].split("(")[0]: i["confidence"]
        for i in web_app._collect_daily_report_items(rows, "2026-09-02")
    }
    assert confs2 == {"SC": "45%", "LU": "—"}


def test_collect_items_skips_not_done_and_other_dates():
    rows = [
        _row(rid=1, status="processing"),                      # 未完成 → 跳过
        _row(rid=2, date="2026-09-01"),                        # 非目标日期 → 跳过
        _row(rid=3, conclusion="## 交易要素与风险\n方向:看多;"),  # 命中
    ]
    assert [i["title"] for i in web_app._collect_daily_report_items(rows, "2026-09-02")] == ["研报3"]


def test_report_date_prefers_publish_date():
    """有效日期:发布日期优先,缺则回退入库日期(uploaded_at 前 10 位)。"""
    assert web_app._report_date({"publish_date": "2026-09-03", "uploaded_at": "2026-09-04 08:00:00"}) == "2026-09-03"
    assert web_app._report_date({"publish_date": "", "uploaded_at": "2026-09-04 08:00:00"}) == "2026-09-04"
    assert web_app._report_date({}) == ""


def test_collect_items_groups_by_publish_date_not_uploaded_at():
    """9.3 发布、9.4 入库(隔夜回看窗口)的研报应归 9.3 的每日总结,不混进 9.4。"""
    row = _row(rid=5, date="2026-09-04", conclusion=_TRED, publish_date="2026-09-03")
    assert web_app._collect_daily_report_items([row], "2026-09-03")
    assert web_app._collect_daily_report_items([row], "2026-09-04") == []


def test_collect_items_skips_weekly():
    """每日总结只收日报(2026-09-05 定):周报行即便当天 done 也不进总结条目。"""
    rows = [
        _row(rid=1, conclusion=_TRED, report_type="周报"),   # 周报 → 跳过
        _row(rid=2, conclusion=_TRED, report_type="日报"),   # 日报 → 收
        _row(rid=3, conclusion=_TRED, report_type=""),       # 未知类型不排斥(启发式兜底前)
    ]
    assert [i["title"] for i in web_app._collect_daily_report_items(rows, "2026-09-02")] == ["研报2", "研报3"]


def test_collect_items_weekly_mode_only_weekly():
    """周报口径只收周报行(2026-09-07 拆分):日报/未知行不混入,两类总结互不混收。"""
    rows = [
        _row(rid=1, conclusion=_TRED, report_type="周报"),   # 周报 → 收
        _row(rid=2, conclusion=_TRED, report_type="日报"),   # 日报 → 跳过
        _row(rid=3, conclusion=_TRED, report_type=""),       # 未知类型 → 跳过
    ]
    assert [i["title"] for i in web_app._collect_daily_report_items(rows, "2026-09-02", "周报")] == ["研报1"]


def test_research_daily_dates_excludes_weekly(tmp_path, monkeypatch):
    """纯周报日期(周末桶)不出现在日报口径可用日期并集;日报日期照常。"""
    monkeypatch.setattr(web_app, "RESEARCH_DAILY_DIR", tmp_path)
    monkeypatch.setattr(web_app, "RESEARCH_WEEKLY_DIR", tmp_path / "w")
    db = _FakeDB([
        _row(rid=1, date="2026-08-30", report_type="周报"),  # 周六纯周报 → 日报口径排除
        _row(rid=2, date="2026-09-02", report_type="日报"),  # 日报 → 保留
        _row(rid=3, date="2026-09-01"),                      # 未知类型 → 保留
    ])
    assert web_app._research_daily_dates(db) == ["2026-09-02", "2026-09-01"]


def test_research_daily_dates_weekly_mode(tmp_path, monkeypatch):
    """周报口径日期并集只含周报行(2026-09-07 拆分):与日报口径互斥,前端下拉分开。"""
    monkeypatch.setattr(web_app, "RESEARCH_DAILY_DIR", tmp_path / "d")
    monkeypatch.setattr(web_app, "RESEARCH_WEEKLY_DIR", tmp_path / "w")
    db = _FakeDB([
        _row(rid=1, date="2026-08-30", report_type="周报"),  # 周六纯周报 → 周报口径出现
        _row(rid=2, date="2026-09-02", report_type="日报"),  # 日报 → 周报口径排除
    ])
    assert web_app._research_daily_dates(db, "周报") == ["2026-08-30"]
    assert web_app._research_daily_dates(db) == ["2026-09-02"]


def test_research_daily_dates_uses_publish_date(tmp_path, monkeypatch):
    """日期并集按发布日期归键:9.3 发布/9.4 入库 → 并集出现 09-03 而非 09-04。"""
    monkeypatch.setattr(web_app, "RESEARCH_DAILY_DIR", tmp_path)
    db = _FakeDB([
        _row(rid=1, date="2026-09-04", publish_date="2026-09-03"),
        _row(rid=2, date="2026-09-02"),  # 无发布日期 → 回退 uploaded_at
    ])
    assert web_app._research_daily_dates(db) == ["2026-09-03", "2026-09-02"]


def test_daily_summary_meta_roundtrip():
    md = web_app._build_daily_summary_md([{}, {}, {}], "## 一、总表", "2026-09-04 01:00:00")
    meta = web_app._daily_summary_meta(md)
    assert meta == {"generated_at": "2026-09-04 01:00:00", "reports": 3}
    assert web_app._daily_summary_meta("无头的旧文件") == {"generated_at": "", "reports": 0}


def test_research_daily_dates_union_and_sorted(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "RESEARCH_DAILY_DIR", tmp_path)
    (tmp_path / "2026-08-30.md").write_text("旧总结", encoding="utf-8")
    db = _FakeDB([_row(rid=1, date="2026-09-02"), _row(rid=2, date="2026-09-02"),
                  _row(rid=3, date="")])
    assert web_app._research_daily_dates(db) == ["2026-09-02", "2026-08-30"]


# ---------------------------------------------------------------------------
# 生成链路(文件复用 / LLM 打桩)
# ---------------------------------------------------------------------------
def test_generate_reuses_existing_file(daily_dir):
    daily_dir.mkdir(parents=True)
    (daily_dir / "2026-09-02.md").write_text(
        "<!-- AgentSense 每日总结 | generated_at:x | reports:1 -->\n\n# 已有", encoding="utf-8")
    out = web_app._generate_daily_summary("2026-09-02")
    assert out["ok"] and out["cached"] and "已有" in out["content"]


def test_generate_llm_success_writes_file(daily_dir, monkeypatch):
    db = _FakeDB([_row(rid=1, conclusion=_TRED)])

    class _FakeClient:
        def get_llm(self):
            class _L:
                def invoke(self, prompt):
                    class _R:
                        content = "## 一、当日观点总表\n(汇总正文)"
                    return _R()
            return _L()

    monkeypatch.setattr(web_app, "get_db", lambda: db)
    monkeypatch.setattr(web_app, "create_llm_client", lambda *a, **k: _FakeClient())
    out = web_app._generate_daily_summary("2026-09-02")
    assert out["ok"] and not out["cached"] and out["reports"] == 1
    saved = web_app._research_daily_path("2026-09-02")
    assert saved.is_file() and "汇总正文" in saved.read_text(encoding="utf-8")
    # 再跑一次 → 走文件复用,LLM 不再被调(桩被撤后仍应成功)
    monkeypatch.setattr(web_app, "create_llm_client", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not call")))
    out2 = web_app._generate_daily_summary("2026-09-02")
    assert out2["ok"] and out2["cached"]


def test_generate_no_reports_error(daily_dir, monkeypatch):
    monkeypatch.setattr(web_app, "get_db", lambda: _FakeDB([]))  # 必须打桩:真库有当日研报会真调 LLM
    out = web_app._generate_daily_summary("2026-09-02")
    assert not out["ok"] and "没有已完成" in out["error"]


def test_generate_llm_failure_degrades(daily_dir, monkeypatch):
    monkeypatch.setattr(web_app, "get_db", lambda: _FakeDB([_row(rid=1, conclusion=_TRED)]))

    def _boom(*a, **k):
        raise RuntimeError("429")
    monkeypatch.setattr(web_app, "create_llm_client", _boom)
    out = web_app._generate_daily_summary("2026-09-02")
    assert not out["ok"] and "LLM 生成失败" in out["error"]
    assert not web_app._research_daily_path("2026-09-02").exists()  # 失败不留半截文件


def test_generate_weekly_writes_weekly_dir(daily_dir, monkeypatch):
    """周报口径生成:落盘 research_weekly,标题用「研报周报总结」;同日日报口径收不到该行。"""
    monkeypatch.setattr(web_app, "get_db", lambda: _FakeDB([_row(rid=1, date="2026-09-06", conclusion=_TRED, report_type="周报")]))

    class _FakeClient:
        def get_llm(self):
            class _L:
                def invoke(self, prompt):
                    assert "研报周报总结" in prompt  # 周报口径标题进 prompt
                    class _R:
                        content = "## 一、当日观点总表\n(周报复盘)"
                    return _R()
            return _L()

    monkeypatch.setattr(web_app, "create_llm_client", lambda *a, **k: _FakeClient())
    out = web_app._generate_daily_summary("2026-09-06", rtype="周报")
    assert out["ok"] and out["reports"] == 1
    saved = web_app._research_daily_path("2026-09-06", "周报")
    assert saved.is_file() and "周报复盘" in saved.read_text(encoding="utf-8")
    assert "research_weekly" in str(saved)
    # 日报口径同日无日报行 → 报错,不串味
    out2 = web_app._generate_daily_summary("2026-09-06", force=True)
    assert not out2["ok"] and "没有已完成分析的日报" in out2["error"]


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
def test_route_generate_rejects_bad_date():
    client = web_app.app.test_client()
    resp = client.post("/api/research/daily/generate", json={"date": "bad"})
    assert resp.status_code == 400


def test_route_get_missing_returns_exists_false(daily_dir):
    client = web_app.app.test_client()
    body = client.get("/api/research/daily/2026-09-02").get_json()
    assert body == {"date": "2026-09-02", "exists": False}
