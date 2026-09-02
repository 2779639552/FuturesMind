"""研报上传模块(Part B)测试:数据库 CRUD、研报聚合层、web_app 提取/JSON 解析、
LLM 后台处理线程(全 mock)、B6 高优先级消费(get_research_report 工具路由 +
基差/库存/供需三处并入)。全隔离——不联网、不落真实库、不碰真实 ~/.tradingagents。

优先级链校验核心:RESEARCH(人工上传研报) > EXTERNAL(外部 JSON) > FREE_API。
"""

import json
import re
import sys
import types
from pathlib import Path

import pytest

import database
import tradingagents.dataflows.external_data as ed
import tradingagents.dataflows.research_data as rd
import web_app


# ---------------------------------------------------------------------------
# fixtures 与合成数据
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_dirs(tmp_path, monkeypatch):
    """把研报聚合目录 + 外部数据目录隔离到临时目录,并清空内存缓存。"""
    monkeypatch.setattr(rd, "RESEARCH_DIR", tmp_path)
    monkeypatch.setenv("TRADINGAGENTS_EXTERNAL_DATA_DIR", str(tmp_path))
    rd._research_cache.clear()
    yield tmp_path
    rd._research_cache.clear()


def _seed_research(tmp_path, variety="RB"):
    """往隔离目录写一份研报聚合数据(与 _process_research_report 写入格式同构)。"""
    data = {
        "variety": variety,
        "updated": "2026-09-01T10:00:00",
        "reports": [
            {
                "id": 1,
                "title": "华泰月报",
                "source": "华泰",
                "uploaded_at": "2026-09-01T09:00:00",
                "direction": "看多",
                "confidence": 0.82,
                "conclusion": "## 核心观点\n需求回暖钢价偏强。",
                "data_points": {
                    "spot_price": {"value": 3200, "unit": "元/吨", "date": "2026-09-01"},
                    "social_inventory": {"value": 500, "unit": "万吨", "date": "2026-09-01"},
                    "mill_inventory": {"value": 120, "unit": "万吨", "date": "2026-09-01"},
                    "supply": {"note": "限产", "date": "2026-09-01"},
                    "demand": {"note": "回暖", "date": "2026-09-01"},
                },
            }
        ],
    }
    rd._save_research(variety, data)
    return data


def _empty_df():
    import pandas as pd

    return pd.DataFrame()


# ---------------------------------------------------------------------------
# 1) 数据库 research_reports 表 CRUD
# ---------------------------------------------------------------------------


class TestResearchDatabaseCRUD:
    def test_insert_list_get_update_delete(self, tmp_path):
        db = database.AgentSenseDB(tmp_path / "test.db")
        rid = db.insert_research_report(
            variety="RB", title="标题", source="来源", filename="a.pdf", file_path="/x/a.pdf"
        )
        assert rid > 0
        rows = db.list_research_reports()
        assert len(rows) == 1 and rows[0]["variety"] == "RB" and rows[0]["status"] == "processing"
        got = db.get_research_report(rid)
        assert got["filename"] == "a.pdf"

        db.update_research_report(
            rid, status="done", direction="看多", confidence=0.8,
            variety="CU", varieties="CU,RB",  # 主品种/多品种回写(处理线程用)
            structured_data='{"a":1}', conclusion_md="结论", error="",
        )
        got = db.get_research_report(rid)
        assert got["status"] == "done" and got["direction"] == "看多"
        assert got["confidence"] == 0.8
        assert got["variety"] == "CU" and got["varieties"] == "CU,RB"

        assert db.delete_research_report(rid) is True
        assert db.get_research_report(rid) is None
        assert db.list_research_reports() == []

    def test_list_filters_by_variety(self, tmp_path):
        db = database.AgentSenseDB(tmp_path / "test.db")
        db.insert_research_report(variety="RB", title="A", source="", filename="", file_path="")
        db.insert_research_report(variety="CU", title="B", source="", filename="", file_path="")
        rows = db.list_research_reports("CU")
        assert len(rows) == 1 and rows[0]["title"] == "B"

    def test_varieties_column_and_filter(self, tmp_path):
        db = database.AgentSenseDB(tmp_path / "test.db")
        rid = db.insert_research_report(variety="RB", title="A", source="", filename="", file_path="")
        db.update_research_report(rid, varieties="RB,CU")
        got = db.get_research_report(rid)
        assert got["varieties"] == "RB,CU"
        # 多品种: RB 与 CU 都能命中
        assert [r["id"] for r in db.list_research_reports("RB")] == [rid]
        assert [r["id"] for r in db.list_research_reports("CU")] == [rid]
        # I 不命中(逗号精确匹配,不会把 "RB,CU" 误匹配成 I)
        assert db.list_research_reports("I") == []
        # 旧行(varieties 为空)回退主品种列
        rid2 = db.insert_research_report(variety="HC", title="B", source="", filename="", file_path="")
        assert [r["id"] for r in db.list_research_reports("HC")] == [rid2]


# ---------------------------------------------------------------------------
# 2) 研报聚合层 research_data
# ---------------------------------------------------------------------------


class TestResearchAggregation:
    def test_upsert_get_text_trim(self, isolated_dirs, tmp_path):
        rd.upsert_research_report("RB", {"id": 2, "title": "新", "source": "S", "direction": "看空", "confidence": 0.6, "conclusion": "## 观点\n供给过剩。", "data_points": {}})
        rd.upsert_research_report("RB", {"id": 3, "title": "更新", "source": "S", "direction": "看多", "confidence": 0.9, "conclusion": "## 观点\n偏多。", "data_points": {"spot_price": {"value": 3000, "unit": "元/吨"}}})
        data = rd.load_research_data("RB")
        assert len(data["reports"]) == 2
        assert data["reports"][0]["id"] == 3, "最新在前"

        txt = rd.get_research_report_text("RB")
        assert "# RESEARCH 研报" in txt and "偏多" in txt and "3000" in txt
        # 无数据哨兵(确定性结论)
        assert rd.get_research_report_text("CU") == "RESEARCH_NO_DATA: 该品种暂无上传研报"

    def test_remove_and_purge(self, isolated_dirs, tmp_path):
        rd.upsert_research_report("RB", {"id": 1, "title": "A", "source": "", "direction": "中性", "confidence": 0.5, "conclusion": "", "data_points": {}})
        rd.remove_research_report("RB", 1)
        assert rd.get_research_report_text("RB") == "RESEARCH_NO_DATA: 该品种暂无上传研报"
        assert not (tmp_path / "RB_research.json").exists(), "删空后聚合文件应移除"

    def test_annotate_research_header(self):
        out = rd.annotate_research("内容")
        assert out.startswith("# DATA_SOURCE: RESEARCH") and "内容" in out

    def test_multi_variety_remove_clears_all_files(self, isolated_dirs, tmp_path):
        # 一份覆盖 RB+CU 的研报被写入两个聚合文件后,删除需两个文件都清干净
        for code in ("RB", "CU"):
            rd.upsert_research_report(code, {
                "id": 7, "title": "多品种", "source": "S", "varieties": ["RB", "CU"],
                "direction": "看多", "confidence": 0.8, "conclusion": "## x",
                "data_points": {"spot_price": {"value": 1, "unit": "元/吨"}},
            })
        assert rd.load_research_data("RB") and rd.load_research_data("CU")
        for code in ("RB", "CU"):
            rd.remove_research_report(code, 7)
        assert rd.get_research_report_text("RB").startswith("RESEARCH_NO_DATA")
        assert rd.get_research_report_text("CU").startswith("RESEARCH_NO_DATA")
        assert not (tmp_path / "RB_research.json").exists()
        assert not (tmp_path / "CU_research.json").exists()


# ---------------------------------------------------------------------------
# 3) web_app 辅助函数:JSON 解析 / 文本提取 / OCR 管线加载
# ---------------------------------------------------------------------------


class TestWebExtractHelpers:
    def test_extract_json_object(self):
        assert web_app._extract_json_object('```json\n{"a":1,"b":"x"}\n```') == {"a": 1, "b": "x"}
        assert web_app._extract_json_object('前缀 {"ok":true} 后缀') == {"ok": True}
        assert web_app._extract_json_object("没有json") is None
        assert web_app._extract_json_object("") is None

    def test_extract_report_text_md(self, tmp_path):
        p = tmp_path / "r.md"
        p.write_text("## 研报\n基本面偏强。", encoding="utf-8")
        t, ocr = web_app._extract_report_text(str(p))
        assert t == "## 研报\n基本面偏强。" and ocr is False

    def test_extract_report_text_unknown_ext(self, tmp_path):
        t, ocr = web_app._extract_report_text(str(tmp_path / "a.xlsx"))
        assert t == "" and ocr is False

    def test_extract_report_text_missing_pdf_graceful(self, tmp_path):
        # 文本层为空 + OCR 不可用 → 返回 ("", False),不抛异常
        t, ocr = web_app._extract_report_text(str(tmp_path / "none.pdf"))
        assert t == "" and ocr is False

    def test_ocr_pipeline_loaded(self):
        pipe = web_app._load_ocr_pipeline()
        assert pipe is not None and hasattr(pipe, "stage1_classify_and_ocr")

    def test_extract_structured_multi_normalization(self):
        """品种名归一化 + 去重 + 每品种方向/置信度归一化。"""
        class _FakeLLM:
            def invoke(self, prompt):
                return types.SimpleNamespace(content=(
                    '{"report_title":"T","publisher":"P","varieties":['
                    '{"variety":"螺纹钢","direction":"多头","confidence":"0.9"},'
                    '{"variety":"rb","direction":"bearish","confidence":0.3},'
                    '{"variety":"RB","direction":"看多","confidence":0.8}]}'
                ))

        data = web_app._llm_extract_structured(_FakeLLM(), "RB", "文本")
        vs = data["varieties"]
        assert len(vs) == 1, "同名品种应去重"
        assert vs[0]["variety"] == "RB"
        assert vs[0]["direction"] == "看多"
        assert vs[0]["confidence"] == 0.9

    def test_normalize_variety_code(self):
        assert web_app._normalize_variety_code("螺纹钢") == "RB"
        assert web_app._normalize_variety_code("rb") == "RB"
        assert web_app._normalize_variety_code("RB ") == "RB"
        assert web_app._normalize_variety_code("") is None
        assert web_app._normalize_variety_code(None) is None


# ---------------------------------------------------------------------------
# 4) 后台处理线程 _process_research_report(LLM 全 mock)
# ---------------------------------------------------------------------------


class TestProcessResearchReport:
    class _FakeResp:
        def __init__(self, content):
            self.content = content

    class _FakeLLM:
        """多品种研报模拟:第一步返回元数据 + RB/CU 两品种,第二步按品种返回结论。"""
        def invoke(self, prompt):
            if "只输出一个 JSON 对象" in prompt:
                return TestProcessResearchReport._FakeResp(
                    '{"report_title":"黑色系月度展望","publisher":"华泰期货","publish_date":"2026-09-01",'
                    '"varieties":['
                    '{"variety":"RB","spot_price":{"value":3200,"unit":"元/吨","date":"2026-09-01"},'
                    '"social_inventory":{"value":500,"unit":"万吨","date":"2026-09-01"},'
                    '"direction":"看多","confidence":0.85,"target_price":3400},'
                    '{"variety":"CU","spot_price":{"value":76000,"unit":"元/吨","date":"2026-09-01"},'
                    '"direction":"看空","confidence":0.7,"target_price":75000}]}'
                )
            m = re.search(r"只针对品种 (\w+)", prompt)
            code = m.group(1) if m else "?"
            return TestProcessResearchReport._FakeResp(f"## 核心观点\n{code} 需求回暖钢价偏强。")

    class _FakeLLMSingle:
        """旧版单品种输出(无 varieties 键),验证向后兼容包装。"""
        def invoke(self, prompt):
            if "只输出一个 JSON 对象" in prompt:
                return TestProcessResearchReport._FakeResp(
                    '{"spot_price":{"value":3200,"unit":"元/吨","date":"2026-09-01"},'
                    '"direction":"看多","confidence":0.85,"target_price":3400}'
                )
            return TestProcessResearchReport._FakeResp("## 核心观点\n需求回暖钢价偏强。")

    def _run(self, monkeypatch, tmp_path, fake_llm):
        """插入一条研报记录并跑完整后台处理(LLM 用给定 fake),返回 (db, rid)。"""
        monkeypatch.setattr(rd, "RESEARCH_DIR", tmp_path)
        rd._research_cache.clear()
        db = database.AgentSenseDB(tmp_path / "test.db")
        md_path = tmp_path / "r.md"
        md_path.write_text("## 研报\n黑色系与铜价展望。", encoding="utf-8")
        rid = db.insert_research_report(
            variety="RB", title="", source="", filename="r.md", file_path=str(md_path)
        )
        monkeypatch.setattr(web_app, "get_db", lambda: db)
        monkeypatch.setattr(
            web_app, "create_llm_client",
            lambda *a, **k: types.SimpleNamespace(get_llm=lambda: fake_llm),
        )
        web_app._process_research_report(rid)
        return db, rid

    def test_multi_variety_split(self, monkeypatch, tmp_path):
        """一份研报含 RB+CU → 两个品种聚合各自落数据;标题/发行方自动识别;结论按品种。"""
        db, rid = self._run(monkeypatch, tmp_path, self._FakeLLM())
        got = db.get_research_report(rid)
        assert got["status"] == "done"
        assert got["varieties"] == "RB,CU"
        assert got["variety"] == "RB"            # 主品种:用户选 RB
        assert got["direction"] == "看多"         # 主品种方向 = RB
        assert got["title"] == "黑色系月度展望"    # 标题自动识别(未手填)
        assert got["source"] == "华泰期货"         # 发行方自动识别(未手填)
        assert "## RB 结论" in got["conclusion_md"]
        assert "## CU 结论" in got["conclusion_md"]
        structured = json.loads(got["structured_data"])
        assert [v["variety"] for v in structured["varieties"]] == ["RB", "CU"]

        # RB 聚合:只含 RB 的数据点/方向/结论
        rb = rd.load_research_data("RB")
        assert rb and rb["reports"][0]["id"] == rid
        r0 = rb["reports"][0]
        assert r0["direction"] == "看多" and r0["confidence"] == 0.85
        assert r0["data_points"]["spot_price"]["value"] == 3200
        assert r0["conclusion"].startswith("## 核心观点") and "RB" in r0["conclusion"]
        # CU 聚合:只含 CU 的数据点/方向/结论
        cu = rd.load_research_data("CU")
        assert cu and cu["reports"][0]["direction"] == "看空"
        assert cu["reports"][0]["confidence"] == 0.7
        assert cu["reports"][0]["data_points"]["spot_price"]["value"] == 76000
        assert "CU" in cu["reports"][0]["conclusion"]
        rd._research_cache.clear()

    def test_single_variety_backward_compat(self, monkeypatch, tmp_path):
        """旧版单品种 LLM 输出(无 varieties)→ 包装成单品种,聚合照常写 RB。"""
        db, rid = self._run(monkeypatch, tmp_path, self._FakeLLMSingle())
        got = db.get_research_report(rid)
        assert got["status"] == "done"
        assert got["varieties"] == "RB"
        assert got["direction"] == "看多" and got["confidence"] == 0.85
        structured = json.loads(got["structured_data"])
        assert structured["varieties"][0]["spot_price"]["value"] == 3200
        agg = rd.load_research_data("RB")
        assert agg and agg["reports"][0]["data_points"]["spot_price"]["value"] == 3200
        rd._research_cache.clear()

    def test_failure_sets_error(self, monkeypatch, tmp_path):
        db = database.AgentSenseDB(tmp_path / "test.db")
        rid = db.insert_research_report(variety="RB", title="T", source="", filename="", file_path="")
        monkeypatch.setattr(web_app, "get_db", lambda: db)

        def _boom(fp):
            raise RuntimeError("boom-提取失败")

        monkeypatch.setattr(web_app, "_extract_report_text", _boom)
        web_app._process_research_report(rid)
        got = db.get_research_report(rid)
        assert got["status"] == "error" and "boom-提取失败" in got["error"]

    def test_empty_text_sets_error(self, monkeypatch, tmp_path):
        # 空文件提取不到文本 → 不喂 LLM,直接标 error(避免空/编造结构化数据)
        db = database.AgentSenseDB(tmp_path / "test.db")
        md_path = tmp_path / "r.md"
        md_path.write_text("", encoding="utf-8")
        rid = db.insert_research_report(
            variety="RB", title="T", source="", filename="r.md", file_path=str(md_path)
        )
        monkeypatch.setattr(web_app, "get_db", lambda: db)
        web_app._process_research_report(rid)
        got = db.get_research_report(rid)
        assert got["status"] == "error" and "未能从文件中提取到文本" in got["error"]

    def test_missing_report_noop(self, monkeypatch, tmp_path):
        monkeypatch.setattr(web_app, "get_db", lambda: database.AgentSenseDB(tmp_path / "test.db"))
        # 不存在的 id → 直接返回,不抛异常
        web_app._process_research_report(999999)


# ---------------------------------------------------------------------------
# 5) B6 高优先级消费:工具路由 + 基差/库存/供需三处并入
# ---------------------------------------------------------------------------


class TestResearchHighPriorityConsumption:
    def test_route_vendor_research(self, isolated_dirs, tmp_path):
        _seed_research(tmp_path, "RB")
        from tradingagents.dataflows.interface import route_to_vendor

        txt = route_to_vendor("get_research_report", "RB", "", "")
        assert "# RESEARCH 研报" in txt and "看多" in txt and "3200" in txt
        # 无研报品种 → RESEARCH_NO_DATA 哨兵
        assert route_to_vendor("get_research_report", "CU", "", "") == "RESEARCH_NO_DATA: 该品种暂无上传研报"

    def test_route_vendor_category(self):
        from tradingagents.dataflows.interface import get_category_for_method

        assert get_category_for_method("get_research_report") == "futures_research"

    def test_basis_research_wins_over_external(self, isolated_dirs, tmp_path):
        # 研报 + 外部都有现货价 → 研报(3200)优先,外部(3089)不出现
        _seed_research(tmp_path, "RB")
        (tmp_path / "RB.json").write_text(
            '{"variety":"RB","updated":"2026-09-01T16:00:00","source":"Mysteel","data":{"spot_price":{"value":3089,"unit":"元/吨","date":"2026-07-14"}}}',
            encoding="utf-8",
        )
        m, used = ed.merge_basis_data("RB", "API\n1,2")
        assert used is True
        assert "# RESEARCH SPOT PRICE: 3200" in m
        assert "# EXTERNAL SPOT PRICE" not in m
        assert "as of 2026-09-01" in m

    def test_basis_no_research_falls_to_free_api(self, isolated_dirs):
        m, used = ed.merge_basis_data("CU", "API\n1,2")
        assert used is False and "FREE_API" in m

    def test_inventory_research_section(self, isolated_dirs, tmp_path):
        _seed_research(tmp_path, "RB")
        m, used = ed.merge_inventory_data("RB", "API_INV\n9,8")
        assert used is True
        assert "Part 0: Research Report Inventory" in m
        assert "Research Social Inventory" in m and "500" in m
        assert "Part 1: Warehouse Receipts" in m

    def test_inventory_no_research_falls_to_free_api(self, isolated_dirs):
        m, used = ed.merge_inventory_data("CU", "API_INV\n9,8")
        assert used is False and "FREE_API" in m

    def test_inventory_external_without_research_no_empty_part0(self, isolated_dirs, tmp_path):
        # 无研报、有外部库存 → 不能输出空 Part 0 头(误导 LLM 以为有研报数据)
        (tmp_path / "RB.json").write_text(
            '{"variety":"RB","updated":"2026-09-01T16:00:00","source":"Mysteel","data":{"social_inventory":{"value":700,"unit":"万吨"}}}',
            encoding="utf-8",
        )
        m, used = ed.merge_inventory_data("RB", "API_INV\n9,8")
        assert used is True
        assert "## Part 0" not in m
        assert "## Part 2: Social & Mill Inventory" in m

    def test_supply_demand_research_section(self, isolated_dirs, tmp_path, monkeypatch):
        fake_ak = types.ModuleType("akshare")
        fake_ak.macro_china_construction_index = lambda: _empty_df()
        fake_ak.macro_china_real_estate = lambda: _empty_df()
        monkeypatch.setitem(sys.modules, "akshare", fake_ak)

        _seed_research(tmp_path, "RB")
        import tradingagents.dataflows.commodity_futures as cf

        out = cf.get_futures_supply_demand("RB")
        assert "## Research Reports (人工上传研报" in out
        assert "需求回暖钢价偏强" in out
        # 研报块在 External Data 之前
        assert out.index("## Research Reports") < out.index("## External Data")
