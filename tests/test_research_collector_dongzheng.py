"""Tests for `research_collector_dongzheng`(2026-09-08 东证繁微采集器)。

database / web_app 懒导入用 stub 顶掉 sys.modules 同名模块(与
test_research_collector_gtja 同套路):不落真库、不调 LLM、不下载真文件
(RESEARCH_UPLOAD_DIR/STATE_FILE 都指 tmp_path)。
"""

import json
import sys
import types

import pytest

import research_collector_dongzheng as dzc


def _install_stubs(monkeypatch, tmp_path, *, existing=None):
    """装 database/web_app 假模块;返回 (FakeDB, 已插入列表, 已处理 id 列表)。"""
    inserted: list[dict] = []
    processed: list[int] = []

    class FakeDB:
        def get_research_report_by_filename(self, fname):
            if existing is None:
                return None
            return {"id": 9, "status": existing}

        def insert_research_report(self, **kw):
            inserted.append(kw)
            return 301

    web_app_mod = types.ModuleType("web_app")
    web_app_mod.RESEARCH_UPLOAD_DIR = tmp_path

    @staticmethod
    def _extract_report_text(path):
        return "正文" * 300, False  # PDF 预检 ≥ MIN_BODY_CHARS

    @staticmethod
    def _process_research_report(rid):
        processed.append(rid)

    web_app_mod._extract_report_text = _extract_report_text
    web_app_mod._process_research_report = _process_research_report
    database_mod = types.ModuleType("database")
    db = FakeDB()
    database_mod.get_db = lambda: db
    monkeypatch.setitem(sys.modules, "database", database_mod)
    monkeypatch.setitem(sys.modules, "web_app", web_app_mod)
    monkeypatch.setattr(dzc, "STATE_FILE", tmp_path / "dz_state.json")
    return db, inserted, processed


@pytest.fixture()
def _rpt_row():
    return {
        "report_id": 212914,
        "title": "2026Q2铁矿季度运营报告：增量符合预期",
        "author": "许惠敏",
        "write_date": "2026-09-07",
        "type_name": "热点报告",
        "industry_name": "黑色金属",
        "summary": "★ 2026Q2运营：总量波澜不惊。" * 20,  # ≥ MIN_BODY_CHARS(摘要降级路径)
        "product_names": ["铁矿石"],
    }


# ── 纯函数 ──────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_variety_name_map_covers_metadata_and_aliases():
    m = dzc._variety_name_map()
    assert m["螺纹钢"] == "RB" and m["铁矿石"] == "I"
    assert m["LLDPE"] == "L" and m["PVC"] == "V"  # 别名补映射


@pytest.mark.unit
def test_report_variety_codes_maps_product_names():
    row = {"product_names": ["铁矿石", "螺纹钢", "美元指数"]}
    assert dzc.report_variety_codes(row) == ["I", "RB"]  # 未知品种忽略,去重保序
    assert dzc.report_variety_codes({"product_names": []}) == []


@pytest.mark.unit
def test_view_variety_code_maps_prefix_and_rejects():
    assert dzc.view_variety_code({"product_code": "M.DCE"}) == "M"
    assert dzc.view_variety_code({"product_code": "USDX.FX"}) == ""  # 宏观/外盘跳过
    assert dzc.view_variety_code({}) == ""


@pytest.mark.unit
def test_authors_text_handles_string_and_dict_list():
    assert dzc._authors_text({"author": "许惠敏"}) == "许惠敏"  # 研报行纯字符串
    row = {"authors": [{"user_name": "张三", "title": "化工"}, {"user_name": "李四"}]}
    assert dzc._authors_text(row) == "张三(化工)、李四"
    assert dzc._authors_text({}) == ""


# ── 研报入库(主源) ──────────────────────────────────────────────────────

@pytest.mark.unit
def test_ingest_report_downloads_pdf_and_inserts(_rpt_row, monkeypatch, tmp_path):
    _, inserted, processed = _install_stubs(monkeypatch, tmp_path)
    import tradingagents.dataflows.dongzheng_api as dz_api

    monkeypatch.setattr(dz_api, "fetch_report_url",
                        lambda rid: {"report_id": rid, "pdf_url": "https://x/a.pdf"})
    monkeypatch.setattr(dzc, "_download_pdf",
                        lambda url, dest: (dest.write_bytes(b"%PDF-"), True)[1])
    assert dzc._ingest_report(_rpt_row) is True
    assert len(inserted) == 1 and processed == [301]
    kw = inserted[0]
    assert kw["variety"] == "I"  # product_names 首个映射代码作主品种提示
    assert kw["publish_date"] == "2026-09-07"  # write_date 直接入库
    assert kw["report_type"] == ""  # 热点报告不在 TYPE_MAP → 留空 LLM 自愈
    assert kw["filename"].startswith("dzrpt212914_")
    assert kw["filename"].endswith(".pdf")
    assert (tmp_path / dzc.SOURCE_ORG / kw["filename"]).read_bytes()[:5] == b"%PDF-"


@pytest.mark.unit
def test_ingest_report_type_map_daily_weekly(_rpt_row, monkeypatch, tmp_path):
    _, inserted, _ = _install_stubs(monkeypatch, tmp_path)
    import tradingagents.dataflows.dongzheng_api as dz_api

    monkeypatch.setattr(dz_api, "fetch_report_url", lambda rid: {"pdf_url": "https://x/a.pdf"})
    monkeypatch.setattr(dzc, "_download_pdf", lambda url, dest: True)
    row = dict(_rpt_row, type_name="周度报告")
    assert dzc._ingest_report(row) is True
    assert inserted[0]["report_type"] == "周报"


@pytest.mark.unit
def test_ingest_report_pdf_fallback_to_summary_md(_rpt_row, monkeypatch, tmp_path):
    """PDF 下载失败 → summary 摘要落 .md(与国君同法),幂等键同步切换。"""
    _, inserted, _ = _install_stubs(monkeypatch, tmp_path)
    import tradingagents.dataflows.dongzheng_api as dz_api

    monkeypatch.setattr(dz_api, "fetch_report_url", lambda rid: {"pdf_url": "https://x/a.pdf"})
    monkeypatch.setattr(dzc, "_download_pdf", lambda url, dest: False)
    assert dzc._ingest_report(_rpt_row) is True
    kw = inserted[0]
    assert kw["filename"].endswith(".md")
    body = (tmp_path / dzc.SOURCE_ORG / kw["filename"]).read_text(encoding="utf-8")
    assert "东证期货-繁微MCP" in body and "2026Q2运营" in body


# ── 动态快评入库(--dynamics) ────────────────────────────────────────────

@pytest.mark.unit
def test_ingest_dynamic_writes_md_and_inserts(monkeypatch, tmp_path):
    _, inserted, processed = _install_stubs(monkeypatch, tmp_path)
    row = {
        "source_id": 35366,
        "title": "苯乙烯重点数据日度跟踪20260907",
        "text": "苯乙烯开工率回升,港口库存去化。" * 20,  # ≥ MIN_BODY_CHARS(200)
        "catalogue": "化工",
        "publish_time": "2026-09-07 09:00:00",
        "authors": [{"user_name": "张三", "title": "化工"}],
    }
    assert dzc._ingest_dynamic(row) is True
    assert len(inserted) == 1 and processed == [301]
    kw = inserted[0]
    assert kw["variety"] == ""  # 品种留空由 LLM 识别(与发现报告同法)
    assert kw["publish_date"] == "2026-09-07"
    assert kw["filename"].startswith("dzdyn35366_")
    body = (tmp_path / dzc.SOURCE_ORG / kw["filename"]).read_text(encoding="utf-8")
    assert "# 苯乙烯重点数据日度跟踪20260907" in body
    assert "东证期货-繁微MCP" in body and "张三(化工)" in body


@pytest.mark.unit
def test_ingest_dynamic_skips_short_text(monkeypatch, tmp_path):
    _, inserted, _ = _install_stubs(monkeypatch, tmp_path)
    row = {"source_id": 1, "title": "t", "text": "太短", "publish_time": "2026-09-07"}
    assert dzc._ingest_dynamic(row) is False
    assert inserted == []


# ── 观点入库(--views) ──────────────────────────────────────────────────

@pytest.mark.unit
def test_ingest_view_maps_variety_and_sections(monkeypatch, tmp_path):
    _, inserted, _ = _install_stubs(monkeypatch, tmp_path)
    row = {
        "view_id": 1141,
        "product_name": "豆粕",
        "product_code": "M.DCE",
        "view_grade": "震荡",
        "view_current_info": "港口库存高位。",
        "view_forecast_info": "预计进口大豆供应充足,期价逢高沽空。",
        "view_risk_info": "产地天气风险",
        "view_participants": [{"user_name": "黄玉萍", "title": "农产品"}],
        "prediction_start": "2026-01-01",
        "prediction_end": "2026-12-31",
    }
    assert dzc._ingest_view(row, "yearly") is True
    kw = inserted[0]
    assert kw["variety"] == "M"  # product_code 前缀直接定品种
    assert kw["title"].startswith("豆粕：东证年度观点(2026-01-01~2026-12-31)")
    body = (tmp_path / dzc.SOURCE_ORG / kw["filename"]).read_text(encoding="utf-8")
    assert "## 观点方向\n震荡" in body and "## 未来展望" in body and "黄玉萍(农产品)" in body


@pytest.mark.unit
def test_ingest_view_rejects_non_commodity(monkeypatch, tmp_path):
    _, inserted, _ = _install_stubs(monkeypatch, tmp_path)
    row = {"view_id": 1, "product_name": "美元指数", "product_code": "USDX.FX",
           "view_forecast_info": "x" * 300}
    assert dzc._ingest_view(row, "weekly") is False
    assert inserted == []


# ── 幂等/自愈 ───────────────────────────────────────────────────────────

@pytest.mark.unit
def test_idempotent_done_row_skips(_rpt_row, monkeypatch, tmp_path):
    """同名文件已存在且 done → 不下载不插行不调 LLM(文件名=幂等键)。"""
    _, inserted, processed = _install_stubs(monkeypatch, tmp_path, existing="done")
    assert dzc._ingest_report(_rpt_row) is False
    assert inserted == [] and processed == []


@pytest.mark.unit
def test_processing_residual_reuses_row(_rpt_row, monkeypatch, tmp_path):
    """processing 残留:复用该行重跑处理,不新增行(与 gtja 同一套自愈)。"""
    _, inserted, processed = _install_stubs(monkeypatch, tmp_path, existing="processing")
    import tradingagents.dataflows.dongzheng_api as dz_api

    monkeypatch.setattr(dz_api, "fetch_report_url", lambda rid: {"pdf_url": "https://x/a.pdf"})
    monkeypatch.setattr(dzc, "_download_pdf", lambda url, dest: True)
    assert dzc._ingest_report(_rpt_row) is True
    assert inserted == [] and processed == [9]


# ── ingest_recent 编排 ──────────────────────────────────────────────────

@pytest.mark.unit
def test_ingest_recent_dedupes_seen_and_persists_state(monkeypatch, tmp_path):
    """seen 命中的条目跳过;新条目处理后键落盘(崩溃续跑);三类键口径不串。"""
    _install_stubs(monkeypatch, tmp_path)
    import tradingagents.dataflows.dongzheng_api as dz_api

    monkeypatch.setattr(dz_api, "fetch_reports", lambda *a, **k: [
        {"report_id": 100, "title": "旧研报", "write_date": "2026-09-07", "summary": "x" * 300},
        {"report_id": 101, "title": "新研报", "write_date": "2026-09-08", "summary": "y" * 300},
    ])
    monkeypatch.setattr(dz_api, "fetch_dynamics",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("未开 --dynamics 不应拉")))
    monkeypatch.setattr(dz_api, "fetch_report_url", lambda rid: {"pdf_url": "https://x/a.pdf"})
    monkeypatch.setattr(dzc, "_download_pdf", lambda url, dest: True)
    state_file = tmp_path / "dz_state.json"
    state_file.write_text(json.dumps({"seen": ["r100"]}), encoding="utf-8")
    result = dzc.ingest_recent(days=1, max_reports=30, dry_run=False)
    assert result["collected"] == 1 and result["processed"] == 1
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert "r100" in state["seen"] and "r101" in state["seen"]


@pytest.mark.unit
def test_ingest_recent_api_failure_returns_stats(monkeypatch, tmp_path):
    """接口失败不崩溃:统计返回空,主流程正常退出。"""
    _install_stubs(monkeypatch, tmp_path)
    import tradingagents.dataflows.dongzheng_api as dz_api

    monkeypatch.setattr(dz_api, "fetch_reports",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("接口挂了")))
    result = dzc.ingest_recent(days=1, dry_run=True)
    assert result["collected"] == 0 and result["processed"] == 0
