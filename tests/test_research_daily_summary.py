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
         confidence=0.7, conclusion=None):
    return {
        "id": rid,
        "title": f"研报{rid}",
        "source": "华泰期货",
        "variety": codes.split(",")[0],
        "varieties": codes,
        "uploaded_at": f"{date} 08:00:00",
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
    """把每日总结目录隔离到临时目录。"""
    monkeypatch.setattr(web_app, "RESEARCH_DAILY_DIR", tmp_path / "research_daily")
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


def test_collect_items_skips_not_done_and_other_dates():
    rows = [
        _row(rid=1, status="processing"),                      # 未完成 → 跳过
        _row(rid=2, date="2026-09-01"),                        # 非目标日期 → 跳过
        _row(rid=3, conclusion="## 交易要素与风险\n方向:看多;"),  # 命中
    ]
    assert [i["title"] for i in web_app._collect_daily_report_items(rows, "2026-09-02")] == ["研报3"]


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
