"""研报上传模块(Part B)测试:数据库 CRUD、研报聚合层、web_app 提取/JSON 解析、
LLM 后台处理线程(全 mock)、B6 高优先级消费(get_research_report 工具路由 +
基差/库存/供需三处并入)。全隔离——不联网、不落真实库、不碰真实 ~/.tradingagents。

优先级链校验核心:RESEARCH(人工上传研报) > EXTERNAL(外部 JSON) > FREE_API。
"""

import json
import re
import sys
import types
from datetime import datetime, timedelta

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

    def test_ingest_source_default_manual_and_roundtrip(self, tmp_path):
        db = database.AgentSenseDB(tmp_path / "test.db")
        # 默认(不传)落 manual —— 网页人工上传的既定口径
        rid = db.insert_research_report(variety="RB", title="A", source="", filename="", file_path="")
        assert db.get_research_report(rid)["ingest_source"] == "manual"
        # 显式 auto —— 采集/本地批量接入
        rid2 = db.insert_research_report(
            variety="CU", title="B", source="", filename="", file_path="", ingest_source="auto"
        )
        assert db.get_research_report(rid2)["ingest_source"] == "auto"
        assert db.get_research_report(rid)["ingest_source"] == "manual"  # 互不污染

    def test_ingest_source_filter_and_variety_combo(self, tmp_path):
        db = database.AgentSenseDB(tmp_path / "test.db")
        db.insert_research_report(variety="RB", title="A", source="", filename="", file_path="")  # manual
        db.insert_research_report(variety="RB", title="B", source="", filename="", file_path="", ingest_source="auto")
        db.insert_research_report(variety="CU", title="C", source="", filename="", file_path="", ingest_source="auto")
        # 单来源过滤
        assert [r["title"] for r in db.list_research_reports(ingest_source="manual")] == ["A"]
        assert sorted(r["title"] for r in db.list_research_reports(ingest_source="auto")) == ["B", "C"]
        # 不传来源:行为不变,返回全部
        assert len(db.list_research_reports()) == 3
        # 组合过滤 (variety, ingest_source)
        assert [r["title"] for r in db.list_research_reports("RB", ingest_source="auto")] == ["B"]
        assert db.list_research_reports("RB", ingest_source="auto")[0]["ingest_source"] == "auto"
        assert db.list_research_reports("CU", ingest_source="manual") == []
        # 未知来源过滤 → 空(参数化,不报错)
        assert db.list_research_reports(ingest_source="bogus") == []

    def test_publish_date_insert_update_and_backfill(self, tmp_path):
        """publish_date 列:入库透传 / update 白名单 / 旧库迁移从 structured_data 回填。"""
        import json as _json

        # 新库直接带列:入库透传 + update 白名单
        db = database.AgentSenseDB(tmp_path / "test.db")
        rid = db.insert_research_report(
            variety="SC", title="原油日报", source="国君", filename="", file_path="",
            publish_date="2026-09-03",
        )
        assert db.get_research_report(rid)["publish_date"] == "2026-09-03"
        db.update_research_report(rid, publish_date="2026-09-04")  # 白名单收 publish_date
        assert db.get_research_report(rid)["publish_date"] == "2026-09-04"
        # 缺省 = 空(手动上传/本地导入,消费端回退 uploaded_at)
        rid2 = db.insert_research_report(variety="RB", title="B", source="", filename="", file_path="")
        assert db.get_research_report(rid2)["publish_date"] == ""

        # 旧库迁移:手工造一个无 publish_date 列的库 → 重开触发 _migrate 补列并回填
        import sqlite3

        old = tmp_path / "old.db"
        conn = sqlite3.connect(old)
        conn.execute(
            "CREATE TABLE research_reports (id INTEGER PRIMARY KEY AUTOINCREMENT, variety TEXT NOT NULL,"
            " title TEXT DEFAULT '', source TEXT DEFAULT '', filename TEXT DEFAULT '', file_path TEXT DEFAULT '',"
            " status TEXT DEFAULT 'processing', extracted_text TEXT DEFAULT '', structured_data TEXT DEFAULT '',"
            " conclusion_md TEXT DEFAULT '', direction TEXT DEFAULT '', confidence REAL,"
            " error TEXT DEFAULT '', uploaded_at TEXT DEFAULT (datetime('now')), created_at TEXT DEFAULT (datetime('now')))"
        )
        conn.execute(
            "INSERT INTO research_reports (variety, structured_data) VALUES (?, ?)",
            ("SC", _json.dumps({"publish_date": "2026-09-03", "varieties": []})),
        )
        conn.execute("INSERT INTO research_reports (variety, structured_data) VALUES (?, ?)",
                     ("RB", "{not json"))  # 损坏 JSON → 跳过,留空
        conn.commit()
        conn.close()
        db2 = database.AgentSenseDB(old)
        rows = {r["variety"]: r for r in db2.list_research_reports()}
        assert rows["SC"]["publish_date"] == "2026-09-03"  # 回填自 LLM 抽取值
        assert rows["RB"]["publish_date"] == ""

    def test_report_type_insert_filter_and_backfill(self, tmp_path):
        """report_type 列:入库透传 / update 白名单 / list 过滤 / 旧库迁移标题启发式回填。"""
        # 新库:入库透传 + update 白名单 + 缺省空
        db = database.AgentSenseDB(tmp_path / "test.db")
        rid = db.insert_research_report(
            variety="SC", title="原油日报", source="国君", filename="", file_path="",
            report_type="日报",
        )
        assert db.get_research_report(rid)["report_type"] == "日报"
        db.update_research_report(rid, report_type="周报")
        assert db.get_research_report(rid)["report_type"] == "周报"
        rid2 = db.insert_research_report(variety="RB", title="B", source="", filename="", file_path="")
        assert db.get_research_report(rid2)["report_type"] == ""
        # list 过滤:等值匹配,未知值 → 空而不报错
        assert [r["id"] for r in db.list_research_reports(report_type="周报")] == [rid]
        assert [r["id"] for r in db.list_research_reports(report_type="日报")] == []
        assert len(db.list_research_reports()) == 2  # 不过滤时全部返回

        # 旧库迁移:无 report_type 列的库重开 → 补列 + 标题启发式回填(只扫空行)
        import sqlite3

        old = tmp_path / "old.db"
        conn = sqlite3.connect(old)
        conn.execute(
            "CREATE TABLE research_reports (id INTEGER PRIMARY KEY AUTOINCREMENT, variety TEXT NOT NULL,"
            " title TEXT DEFAULT '', source TEXT DEFAULT '', filename TEXT DEFAULT '', file_path TEXT DEFAULT '',"
            " status TEXT DEFAULT 'processing', extracted_text TEXT DEFAULT '', structured_data TEXT DEFAULT '',"
            " conclusion_md TEXT DEFAULT '', direction TEXT DEFAULT '', confidence REAL,"
            " error TEXT DEFAULT '', uploaded_at TEXT DEFAULT (datetime('now')), created_at TEXT DEFAULT (datetime('now')))"
        )
        conn.execute("INSERT INTO research_reports (variety, title) VALUES (?, ?)", ("MA", "华泰期货甲醇日报20260904"))
        conn.execute("INSERT INTO research_reports (variety, title) VALUES (?, ?)", ("SA", "纯碱周报"))
        conn.execute("INSERT INTO research_reports (variety, title) VALUES (?, ?)", ("FG", "玻璃周度观察"))
        conn.execute("INSERT INTO research_reports (variety, title) VALUES (?, ?)", ("UR", "尿素：区间运行"))
        conn.commit()
        conn.close()
        db2 = database.AgentSenseDB(old)
        rows = {r["variety"]: r for r in db2.list_research_reports()}
        assert rows["MA"]["report_type"] == "日报"
        assert rows["SA"]["report_type"] == "周报"
        assert rows["FG"]["report_type"] == "周报"  # 周度也算周报
        assert rows["UR"]["report_type"] == ""  # 无周期词 → 留空待 LLM 自愈


def test_guess_report_type_daily_before_weekly():
    """标题启发式顺序敏感:「碳酸锂**日报**…周度去库」须判日报,不能被周度误夺。"""
    assert database.guess_report_type("碳酸锂日报20260904：周度去库放缓") == "日报"
    assert database.guess_report_type("纯碱周报") == "周报"
    assert database.guess_report_type("玻璃周度观察") == "周报"
    assert database.guess_report_type("甲醇周刊") == "周报"
    assert database.guess_report_type("尿素：区间运行") == ""
    assert database.guess_report_type("") == ""


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


class TestOpinionConclusionPromptShape:
    """核心观点提示词(2026-09-02 两段式)形态:六固定小节(200 字左右)+ 补回数据支撑/分歧/建议权重。"""

    class _CaptureLLM:
        """逐品种各捕获一次:prompts 存全部调用,prompt 为最后一次(单品种时等价)。"""

        def __init__(self):
            self.prompts: list[str] = []
            self.prompt = ""

        def invoke(self, prompt):
            self.prompts.append(prompt)
            self.prompt = prompt
            return types.SimpleNamespace(content="## 供需格局\n样本观点输出")

    def test_prompt_has_fixed_sections_and_writing_rules(self):
        llm = self._CaptureLLM()
        out = web_app._llm_opinion_conclusion(
            llm,
            "研报正文:现货低库存,开工回升。",
            [{"variety": "RB", "direction": "看多", "confidence": 0.8}],
        )
        p = llm.prompt
        # 第一部分:七固定小节齐备(2026-09-03 交易要素化,新增 交易要素与风险)
        for sec in ("## 供需格局", "## 库存与结构", "## 成本与利润",
                    "## 现货与目标价", "## 事件与驱动", "## 观点与依据",
                    "## 交易要素与风险"):
            assert sec in p
        # 篇幅要求(第一部分 340 字左右)+ 未披露明说(严禁编造)
        assert "340 字左右" in p
        assert "研报未披露该指标" in p
        # 五要素引导词(交易行硬约束:单行分号分隔,缺失写 —)
        for hint in ("方向", "形态与区间", "单边", "区间震荡", "头寸", "头寸范围", "风险"):
            assert hint in p
        # 缺失写「—」,不写"未披露"字样(避开前端占位判据)
        assert "一律写「—」" in p
        # 第二部分:补回 数据支撑 / 潜在分歧 / 建议权重(原四段式中被删的三节)
        for sec in ("## 数据支撑", "## 与系统自动分析的潜在分歧", "## 建议权重"):
            assert sec in p
        # 按品种输出且首个小节就是新格式
        assert set(out) == {"RB"}
        assert out["RB"].startswith("## 供需格局")

    def test_prompt_never_targets_other_variety(self):
        # 只谈该品种:多品种逐次调用,每次 prompt 只出现本品种代码
        llm = self._CaptureLLM()
        web_app._llm_opinion_conclusion(
            llm,
            "正文",
            [{"variety": "RB", "direction": "看多", "confidence": 0.8},
             {"variety": "CU", "direction": "看空", "confidence": 0.6}],
        )
        assert len(llm.prompts) == 2
        assert "只针对品种 RB" in llm.prompts[0]
        assert re.search(r"\bCU\b", llm.prompts[0]) is None
        assert "只针对品种 CU" in llm.prompts[1]
        assert re.search(r"\bRB\b", llm.prompts[1]) is None


class TestReconcludeResearchReport:
    """只重跑结论(reconclude):不动 structured_data,更新 conclusion_md 与各品种聚合。"""

    class _FakeResp:
        def __init__(self, content):
            self.content = content

    class _FakeLLM:
        """reconclude 只走结论步:第二步按品种返回新格式观点。"""
        def invoke(self, prompt):
            m = re.search(r"只针对品种 (\w+)", prompt)
            code = m.group(1) if m else "?"
            return TestReconcludeResearchReport._FakeResp(
                f"## 供需格局\n{code} 新格式多小节观点。"
            )

    def _seed(self, monkeypatch, tmp_path) -> tuple:
        """造一条已 done、旧口径结论的研报行(带 structured_data.varieties + 正文)。"""
        monkeypatch.setattr(rd, "RESEARCH_DIR", tmp_path)
        rd._research_cache.clear()
        db = database.AgentSenseDB(tmp_path / "test.db")
        rid = db.insert_research_report(
            variety="RB", title="月度展望(旧)", source="华泰期货",
            filename="r.md", file_path=str(tmp_path / "r.md"), status="done",
        )
        structured = {
            "report_title": "月度展望",
            "publisher": "华泰期货",
            "publish_date": "2026-09-02",
            "varieties": [
                {"variety": "RB", "direction": "看多", "confidence": 0.85,
                 "spot_price": {"value": 3200, "unit": "元/吨", "date": "2026-09-02"}},
                {"variety": "CU", "direction": "看空", "confidence": 0.6,
                 "target_price": {"value": 75000, "unit": "元/吨", "date": "2026-09-02"}},
            ],
        }
        db.update_research_report(
            rid,
            status="done",
            extracted_text="## 研报\n黑色系与铜价展望,现货低库存。",
            structured_data=json.dumps(structured, ensure_ascii=False),
            conclusion_md="## RB 结论\n旧口径:需求回暖。\n\n## CU 结论\n旧口径:宏观偏弱。",
        )
        monkeypatch.setattr(web_app, "get_db", lambda: db)
        monkeypatch.setattr(
            web_app, "create_llm_client",
            lambda *a, **k: types.SimpleNamespace(get_llm=lambda: self._FakeLLM()),
        )
        return db, rid, structured

    def test_reconclude_updates_conclusion_and_aggregate_only(self, monkeypatch, tmp_path):
        db, rid, structured = self._seed(monkeypatch, tmp_path)
        res = web_app.reconclude_research_report(rid)
        assert res["ok"] is True and sorted(res["codes"]) == ["CU", "RB"]

        got = db.get_research_report(rid)
        # structured_data / 标题 / 方向未被改动;status 保持 done
        assert got["status"] == "done"
        assert json.loads(got["structured_data"]) == structured
        assert got["title"] == "月度展望(旧)"
        assert got["error"] is None or got["error"] == ""
        # 结论全文已被新格式覆盖(旧口径消失)
        assert "## RB 结论" in got["conclusion_md"]
        assert "## CU 结论" in got["conclusion_md"]
        assert "旧口径" not in got["conclusion_md"]
        assert "## 供需格局" in got["conclusion_md"]

        # 各品种聚合 conclusion 已更新(upsert 按 id 覆盖)
        agg = rd.load_research_data("RB")
        r0 = next(r for r in agg["reports"] if r["id"] == rid)
        assert r0["direction"] == "看多"          # 方向仍取 structured,未被重跑改动
        assert "RB 新格式多小节观点" in r0["conclusion"]
        assert r0["data_points"]["spot_price"]["value"] == 3200
        rd._research_cache.clear()

    def test_reconclude_twice_no_duplicate_aggregate(self, monkeypatch, tmp_path):
        db, rid, structured = self._seed(monkeypatch, tmp_path)
        assert web_app.reconclude_research_report(rid)["ok"] is True
        assert web_app.reconclude_research_report(rid)["ok"] is True  # 幂等重跑
        agg = rd.load_research_data("CU")
        hits = [r for r in agg["reports"] if r["id"] == rid]
        assert len(hits) == 1                     # 不产生重复聚合条目
        rd._research_cache.clear()

    def test_reconclude_missing_row_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rd, "RESEARCH_DIR", tmp_path)
        rd._research_cache.clear()
        db = database.AgentSenseDB(tmp_path / "test.db")
        monkeypatch.setattr(web_app, "get_db", lambda: db)
        res = web_app.reconclude_research_report(999999)
        assert res["ok"] is False and "不存在" in res["error"]
        rd._research_cache.clear()


class TestReExtractResearchReport:
    """存量"结构化重提取"(re_extract):重跑第一步,修置信度 + 补四类基本面。

    只重跑第一步(_llm_extract_structured):存量行置信度默认 0.5/0.0、四键(basis/
    warehouse_receipts/operating_rate/processing_margin)根本没提取过;重提取后按
    已入库品种代码集合并,方向/评级/结论文本不变,结论从现有聚合按 id 原样写回。
    """

    class _FakeResp:
        def __init__(self, content):
            self.content = content

    class _FakeLLM:
        """第一步返回新口径结构化:RB/CU 真实置信度 + RB 基差 + CU 开工率;CU 基差缺(靠原文补)。"""
        def invoke(self, prompt):
            assert "只输出一个 JSON 对象" in prompt  # 只允许走第一步(重提取不该触第二步)
            return TestReExtractResearchReport._FakeResp(
                '{"report_title":"月度展望(新)","publisher":"华泰期货","publish_date":"2026-09-02",'
                '"varieties":['
                '{"variety":"RB","direction":"看多","confidence":0.8,'
                '"basis":{"value":120,"unit":"元/吨","date":"2026-09-02","note":"RB 升水"},'
                '"spot_price":{"value":3200,"unit":"元/吨","date":"2026-09-02"}},'
                '{"variety":"CU","direction":"看空","confidence":0.65,'
                '"operating_rate":{"value":85,"unit":"%","date":"2026-09-02"}}]}'
            )

    def _seed(self, monkeypatch, tmp_path, structured: dict, conclusion_md: str = "") -> tuple:
        """造一条"旧口径"done 研报行(置信度默认 + 无四键),并预写各品种聚合(结论供保留)。"""
        monkeypatch.setattr(rd, "RESEARCH_DIR", tmp_path)
        rd._research_cache.clear()
        db = database.AgentSenseDB(tmp_path / "test.db")
        rid = db.insert_research_report(
            variety="RB", title="月度展望(旧)", source="华泰期货",
            filename="r.md", file_path=str(tmp_path / "r.md"), status="done",
        )
        db.update_research_report(
            rid,
            status="done",
            extracted_text=structured.get("_text") or "## 研报\n黑色系与铜价展望。",
            structured_data=json.dumps(
                {k: v for k, v in structured.items() if not k.startswith("_")},
                ensure_ascii=False,
            ),
            conclusion_md=conclusion_md,
        )
        # 预写聚合:re_extract 的结论保留依赖"现有聚合按 id 取回" → 必须先有聚合
        r = db.get_research_report(rid)
        stored = structured.get("varieties")
        codes = [v["variety"] for v in stored]
        web_app._write_research_aggregates(
            rid, r.get("uploaded_at") or "", r.get("title") or "", r.get("source") or "",
            codes, stored,
            {c: f"旧结论:{c} 供应紧平衡。" for c in codes},
        )
        monkeypatch.setattr(web_app, "get_db", lambda: db)
        monkeypatch.setattr(
            web_app, "create_llm_client",
            lambda *a, **k: types.SimpleNamespace(get_llm=lambda: self._FakeLLM()),
        )
        return db, rid

    def _legacy_structured(self) -> dict:
        """旧口径 structured_data:置信度默认 0.5、无四键,仅现货价等早期字段。"""
        return {
            "report_title": "月度展望", "publisher": "华泰期货", "publish_date": "2026-09-01",
            "varieties": [
                {"variety": "RB", "direction": "看多", "confidence": 0.5,
                 "spot_price": {"value": 3200, "unit": "元/吨", "date": "2026-09-01"}},
                {"variety": "CU", "direction": "看空", "confidence": 0.5},
            ],
        }

    def test_reextract_fixes_confidence_adds_four_keys_keeps_conclusion(
        self, monkeypatch, tmp_path
    ):
        db, rid = self._seed(monkeypatch, tmp_path, self._legacy_structured(),
                             conclusion_md="## RB 结论\n旧RB。\n\n## CU 结论\n旧CU。")

        res = web_app.re_extract_research_report(rid)
        assert res["ok"] is True and sorted(res["codes"]) == ["CU", "RB"]

        got = db.get_research_report(rid)
        assert got["status"] == "done"
        assert got["confidence"] == 0.8          # 行级主品种(RB)置信度 → 真实值,不再是默认 0.5
        assert got["direction"] == "看多"        # 方向保留(重提取不改方向,防与结论文本冲突)
        assert got["title"] == "月度展望(旧)"     # 标题保留(顶层元数据不动)
        assert "旧RB" in got["conclusion_md"] and "旧CU" in got["conclusion_md"]  # 观点全文不变

        sd = json.loads(got["structured_data"])
        assert sd["report_title"] == "月度展望"    # 顶层元数据保留(未被新提取覆盖)
        rb = next(v for v in sd["varieties"] if v["variety"] == "RB")
        cu = next(v for v in sd["varieties"] if v["variety"] == "CU")
        assert rb["confidence"] == 0.8            # 置信度真实化
        assert rb["spot_price"]["value"] == 3200  # 旧字段保留
        assert rb["basis"]["value"] == 120        # 四键:LLM 直接提取入结构化
        assert cu["confidence"] == 0.65
        assert cu["operating_rate"]["value"] == 85  # 四键:LLM 直接提取入结构化

        # 聚合:结论原样保留 + confidence/data_points 更新(RB 缺基差→原文补→仍带值)
        agg = rd.load_research_data("RB")
        r0 = next(r for r in agg["reports"] if r["id"] == rid)
        assert r0["direction"] == "看多" and r0["confidence"] == 0.8
        assert r0["data_points"]["basis"]["value"] == 120
        assert r0["data_points"]["spot_price"]["value"] == 3200
        assert r0["conclusion"] == "旧结论:RB 供应紧平衡。"   # 结论未被重生成
        rd._research_cache.clear()

    def test_reextract_missing_four_key_backfilled_from_source(self, monkeypatch, tmp_path):
        # CU 新提取无基差,但研报正文有 → _ingest_backfill_fund_metrics 从原文确定性补
        # (note 记"研报原文『…』",同新研报入库路径;不重跑第二步,观点文本不变)
        structured = self._legacy_structured()
        structured["_text"] = (
            "## 研报\n黑色系与铜价展望。\n"
            "RB 现货库存低位,供需偏紧。\n"
            "CU 现货贴水,基差 -45 元/吨,宏观偏空。"
        )
        db, rid = self._seed(monkeypatch, tmp_path, structured,
                             conclusion_md="## RB 结论\n旧RB。\n\n## CU 结论\n旧CU。")

        res = web_app.re_extract_research_report(rid)
        assert res["ok"] is True
        agg = rd.load_research_data("CU")
        r0 = next(r for r in agg["reports"] if r["id"] == rid)
        assert r0["confidence"] == 0.65
        cu_basis = r0["data_points"]["basis"]
        assert cu_basis["value"] == -45            # LLM 没给 → 原文补漏成功
        assert "研报原文" in (cu_basis.get("note") or "")
        assert r0["conclusion"] == "旧结论:CU 供应紧平衡。"   # 结论不被重生成
        # RB 基差由 LLM 直接给 → 不落入原文补漏(值仍是 120)
        agg_rb = rd.load_research_data("RB")
        r_rb = next(r for r in agg_rb["reports"] if r["id"] == rid)
        assert r_rb["data_points"]["basis"]["value"] == 120
        assert "研报原文" not in (r_rb["data_points"]["basis"].get("note") or "")
        rd._research_cache.clear()

    def test_reextract_legacy_no_structured_varieties_anchors_to_primary(
        self, monkeypatch, tmp_path
    ):
        # 最老的存量行:structured 无 varieties,只有主品种代码 → 锚到主品种单品种重提取
        structured = {"report_title": "极老研报", "publisher": "", "publish_date": "",
                      "varieties": []}
        structured["_text"] = "## 研报\nRB 现货贴水,基差 120 元/吨。"
        db, rid = self._seed(monkeypatch, tmp_path, structured,
                             conclusion_md="## RB 结论\n旧RB。")
        res = web_app.re_extract_research_report(rid)
        assert res["ok"] is True and res["codes"] == ["RB"]
        got = db.get_research_report(rid)
        sd = json.loads(got["structured_data"])
        assert [v["variety"] for v in sd["varieties"]] == ["RB"]
        assert sd["varieties"][0]["confidence"] == 0.8
        rd._research_cache.clear()

    def test_reextract_missing_row_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rd, "RESEARCH_DIR", tmp_path)
        rd._research_cache.clear()
        db = database.AgentSenseDB(tmp_path / "test.db")
        monkeypatch.setattr(web_app, "get_db", lambda: db)
        res = web_app.re_extract_research_report(999999)
        assert res["ok"] is False and "不存在" in res["error"]
        rd._research_cache.clear()


def test_reconclude_route(monkeypatch):
    """批量重跑路由:白名单必填/单次限 10/逐条透传 reconclude 结果(不真调 LLM)。"""
    client = web_app.app.test_client()
    assert client.post("/api/research/reconclude", json={}).status_code == 400
    assert client.post("/api/research/reconclude", json={"ids": []}).status_code == 400
    assert client.post("/api/research/reconclude", json={"ids": list(range(11))}).status_code == 400

    calls = []

    def fake_reconclude(rid):
        calls.append(rid)
        return {"ok": True, "report_id": rid, "codes": ["SC"]}

    monkeypatch.setattr(web_app, "reconclude_research_report", fake_reconclude)
    resp = client.post("/api/research/reconclude", json={"ids": [7, 9]})
    assert resp.status_code == 200
    assert [r["report_id"] for r in resp.get_json()["results"]] == [7, 9]
    assert calls == [7, 9]


class TestResearchViewsHelpers:
    """逐品种观点表格后端 helper:结论 markdown → 表格行(观点要点/方向/置信度)。

    只测纯函数(_extract_key_opinion / _research_view_row / _compact_md),
    不拉起 Flask 路由。
    """

    _TWO_PART = (
        "## 供需格局\n"
        "9 月 1 日美伊互袭后供应风险溢价抬升,SC 强势。\n"
        "\n"
        "## 观点与依据\n"
        "SC 看多:地缘供应扰动是本轮核心驱动,跟踪 霍尔木兹海峡通航与 OPEC 增产节奏。\n"
        "\n"
        "## 交易要素与风险\n"
        "方向:看多;形态与区间:单边看多, 运行区间 540~560;头寸:轻仓;头寸范围:回落 545 加仓;风险:OPEC 增产超预期。\n"
        "\n"
        "## 数据支撑\n"
        "- 9/1 布伦特 78.2 美元/桶,环比 +4%。\n"
        "\n"
        "## 建议权重\n"
        "多头 20%~30%。"
    )

    def test_extract_two_part_opinion_seven_sections(self):
        # 两段式口径(2026-09-03):交易要素行前置为首行,其后推理链逐节(观点+依据),止于数据支撑前
        out = web_app._extract_key_opinion(self._TWO_PART)
        assert out.startswith("交易要素与风险：")        # 交易要素提到单元格首行(先结论后论据)
        assert "头寸:轻仓" in out                        # 交易要素五段内容保留
        assert "供需格局：9 月 1 日美伊互袭" in out   # 依据小节保留(为什么这么看)
        assert "SC 看多" in out                       # 末节观点与依据收口
        assert "霍尔木兹" in out
        assert "跟踪" in out
        assert "\n" in out                            # 按节换行成多行要点
        assert "数据支撑" not in out                  # 节边界:不混入数据支撑/建议权重
        assert "##" not in out                        # 装饰符已剥

    def test_extract_trade_section_blank_uses_dash(self):
        # 交易要素节为占位/空 → 该行给 —,但仍前置为首行(结构完整、不编造)
        two = ("## 供需格局\n供应偏紧。\n\n"
               "## 观点与依据\nSC 中性。\n\n"
               "## 交易要素与风险\n研报未披露该指标。\n")
        out = web_app._extract_key_opinion(two)
        assert out.startswith("交易要素与风险：—")
        assert "供需格局：供应偏紧" in out

    def test_extract_two_part_with_trailing_sections_stops_at_next_h2(self):
        two = self._TWO_PART + "\n## 数据支撑\n仅供研究。\n## 风险提示\n仅供研究。"
        out = web_app._extract_key_opinion(two)
        assert "风险提示" not in out       # 停在「观点与依据」节内,数据支撑/风险提示不进单元格
        assert "数据支撑" not in out

    def test_extract_blank_section_uses_dash(self):
        # 某小节只写"研报未披露该指标"(或空) → 该点给 —(结构完整、不编造)
        two = ("## 供需格局\n研报未披露该指标。\n\n"
               "## 库存与结构\n9/2 港口库存 320 万吨,环比 -2%。\n\n"
               "## 观点与依据\nSC 中性:等数据验证。\n")
        out = web_app._extract_key_opinion(two)
        assert "供需格局：—" in out
        assert "库存与结构：9/2 港口库存" in out
        assert "SC 中性" in out

    def test_extract_old_format_falls_back_first_paragraph(self):
        # 旧四段式(## 核心观点 开头)无「## 供需格局」节 → 取首个非标题段
        old = "## 核心观点\nLC 震荡筑底,现货偏紧。\n\n## 数据支撑\n..."
        out = web_app._extract_key_opinion(old)
        assert "LC 震荡筑底" in out
        assert "数据支撑" not in out       # 标题行不算正文

    def test_extract_empty_returns_empty(self):
        assert web_app._extract_key_opinion("") == ""
        assert web_app._extract_key_opinion(None) == ""
        assert web_app._extract_key_opinion("   \n  ") == ""

    def test_fit_over_budget_keeps_last_section(self):
        # 旧截断从尾部砍,会把末节「观点与依据」截掉;新裁剪首末节必保、保序、限长
        lines = [
            "供需格局：8/30 中东发运低位,较 2025 年均值明显偏低,供应偏紧延续。",
            "库存与结构：全球原油库存环比小幅累库,幅度温和。",
            "成本与利润：油价上行抬高国内炼厂原料成本,利润承压。",
            "现货与目标价：国内现货维持紧平衡,买盘尚可。",
            "事件与驱动：伊朗称霍尔木兹海峡完全关闭,美伊和谈无积极信号。",
            "观点与依据：短期看多,地缘是当前主要上行驱动。",
        ]
        out = web_app._fit_section_lines(lines, max_len=200)
        assert out.startswith("供需格局：")                       # 首节(依据起点)
        assert "观点与依据：短期看多" in out                       # 末节(方向收口)不丢
        assert out.rstrip().endswith("上行驱动。")                 # 末节内容完整,非半行截断
        assert "库存与结构" in out                                # 优先保真实内容行
        assert len(out) <= 200                                    # 限长
        assert out.split("\n")[0].startswith("供需格局")           # 顺序不重排

    def test_fit_drops_placeholder_lines_first(self):
        # 预算紧张:纯占位「：—」行让位(无信息可丢),真实内容行 + 首末节保全
        lines = [
            "供需格局：美伊互袭推升地缘溢价,SC 供应风险抬升。",
            "库存与结构：—",
            "成本与利润：—",
            "事件与驱动：伊朗 8/29 称霍尔木兹海峡完全关闭。",
            "观点与依据：短期看多,地缘是主要上行驱动。",
        ]
        out = web_app._fit_section_lines(lines, max_len=75)
        assert "库存与结构：—" not in out                          # 占位行先出局
        assert "成本与利润：—" not in out
        assert "事件与驱动" in out                                 # 真实内容行保留
        assert "观点与依据：短期看多" in out
        assert len(out) <= 75

    def test_fit_within_budget_keeps_all_lines(self):
        lines = ["供需格局：供应偏紧", "库存与结构：温和累库", "观点与依据：短期看多"]
        assert web_app._fit_section_lines(lines, max_len=100) == \
            "供需格局：供应偏紧\n库存与结构：温和累库\n观点与依据：短期看多"

    def test_fit_must_keep_trade_row_with_tight_budget(self):
        # 带交易行+预算紧:交易行(首行,must_keep 点名)保、供需保、观点末行保,占位行先出局
        lines = [
            "交易要素与风险：方向:看多;形态与区间:单边看多, 运行区间 540~560;头寸:轻仓;头寸范围:—;风险:OPEC 增产",
            "供需格局：美伊互袭推升地缘溢价,SC 供应风险抬升。",
            "库存与结构：—",
            "成本与利润：—",
            "观点与依据：短期看多,地缘是主要上行驱动。",
        ]
        out = web_app._fit_section_lines(lines, max_len=115, must_keep=(0,))
        assert out.startswith("交易要素与风险：")       # 交易行前置且必保
        assert "供需格局：美伊互袭" in out              # 推理链起点保留
        assert "观点与依据：短期看多" in out            # 末节方向收口保留
        assert "库存与结构：—" not in out               # 占位行最先出局
        assert "成本与利润：—" not in out
        assert len(out) <= 115

    def test_compact_md_strips_decorations(self):
        md = "**看多**: `基差` -35 元/吨\n\n*跟踪* 库存拐点"
        out = web_app._compact_md(md)
        assert "**" not in out and "`" not in out and "*" not in out
        assert "基差 -35 元/吨 跟踪 库存拐点" in out

    def test_view_row_shape(self):
        row = web_app._research_view_row({
            "id": 47, "title": "原油早报", "source": "华泰期货",
            "uploaded_at": "2026-09-02 08:00:00", "direction": "看多",
            "confidence": 0.8, "varieties": ["SC", "LU"],
            "conclusion": self._TWO_PART,
        })
        assert row["id"] == 47 and row["source"] == "华泰期货"
        assert row["direction"] == "看多" and row["confidence"] == 0.8
        assert row["covers"] == ["SC", "LU"]
        assert "SC 看多" in row["key_opinion"]
        # 头寸列(2026-09-05 合并原单边/区间+头寸+研报建议三列);观点要点不再带交易行
        assert row["trade"] == "单边看多; 运行区间 540~560; 轻仓"
        assert "交易要素" not in row["key_opinion"]
        assert row["advice"] == "多头 20%~30%。"

    def test_view_row_blank_conclusion(self):
        row = web_app._research_view_row({"id": 1, "title": "t", "source": "s",
                                          "uploaded_at": "2026-09-02 08:00:00",
                                          "direction": "中性", "confidence": 0.5,
                                          "varieties": [], "conclusion": None})
        assert row["key_opinion"] == ""          # 前端渲染为 (无观点摘要)
        assert row["direction"] == "中性"

    def test_view_row_carries_publish_date(self):
        """观点行透传发布日期(前端日期小字/日期分组消费),缺省给空串。"""
        row = web_app._research_view_row({
            "id": 7, "title": "t", "source": "s", "uploaded_at": "2026-09-04 08:00:00",
            "publish_date": "2026-09-03", "direction": "中性", "confidence": None,
            "varieties": [], "conclusion": "",
        })
        assert row["publish_date"] == "2026-09-03"
        assert web_app._research_view_row({"id": 8, "conclusion": ""})["publish_date"] == ""

    def test_view_row_carries_report_type(self):
        """观点行透传研报类型(前端周报徽标消费),缺省给空串。"""
        row = web_app._research_view_row({
            "id": 9, "title": "t", "source": "s", "uploaded_at": "2026-09-05 08:00:00",
            "publish_date": "2026-09-05", "report_type": "周报", "direction": "中性",
            "confidence": None, "varieties": [], "conclusion": "",
        })
        assert row["report_type"] == "周报"
        assert web_app._research_view_row({"id": 10, "conclusion": ""})["report_type"] == ""


# ---------------------------------------------------------------------------
# 10) 研报文本四类指标具名输出(get_research_report_text,供分析师研报文本)
# ---------------------------------------------------------------------------


def test_research_text_includes_four_typed_metrics(isolated_dirs):
    """研报 data_points 含四类指标对象时,get_research_report_text 输出具名行(value+unit+date+note)。"""
    data = {
        "variety": "RB",
        "updated": "2026-09-01T10:00:00",
        "reports": [
            {
                "id": 1,
                "title": "华泰月报",
                "source": "华泰",
                "uploaded_at": "2026-09-01T09:00:00",
                "direction": "看多",
                "confidence": 0.82,
                "conclusion": "## 核心观点\n钢价偏强。",
                "data_points": {
                    "spot_price": {"value": 3200, "unit": "元/吨", "date": "2026-09-01"},
                    "operating_rate": {"value": 78.5, "unit": "%", "date": "2026-09-01", "note": "唐山高炉"},
                    "processing_margin": {"value": 180, "unit": "元/吨", "date": "2026-09-01"},
                    "basis": {"value": 68, "unit": "元/吨", "date": "2026-09-01", "note": "现货升水"},
                    "warehouse_receipts": {"value": 129662, "unit": "手", "date": "2026-09-02"},
                },
            }
        ],
    }
    rd._save_research("RB", data)
    text = rd.get_research_report_text("RB")
    assert "研报-开工率/负荷率: 78.5% (2026-09-01, 唐山高炉)" in text
    assert "研报-加工利润/加工费: 180元/吨 (2026-09-01)" in text
    assert "研报-基差: 68元/吨 (2026-09-01, 现货升水)" in text
    assert "研报-交易所仓单: 129662手 (2026-09-02)" in text


def test_research_text_omits_typed_lines_when_missing(isolated_dirs):
    """研报未给某类指标(缺键/值为 None)→ 整条不输出(留空),其余文本不受影响。"""
    data = {
        "variety": "RB",
        "updated": "2026-09-01T10:00:00",
        "reports": [
            {
                "id": 1,
                "title": "华泰月报",
                "source": "华泰",
                "uploaded_at": "2026-09-01T09:00:00",
                "direction": "中性",
                "confidence": 0.5,
                "conclusion": "## 核心观点\n震荡。",
                "data_points": {"operating_rate": {"value": None, "date": "2026-09-01"}},
            }
        ],
    }
    rd._save_research("RB", data)
    text = rd.get_research_report_text("RB")
    assert "研报-开工率/负荷率" not in text
    assert "研报-加工利润/加工费" not in text
    assert "研报-基差" not in text
    assert "研报-交易所仓单" not in text
    assert "该品种已上传 1 份研报" in text


# ---------------------------------------------------------------------------
# 11) 观点总览交易要素列(_parse_trade_elements / _extract_advice / _research_view_row)
# ---------------------------------------------------------------------------
def test_parse_trade_elements_full_and_partial():
    """交易要素行五要素解析:兼容中英文冒号/分号;「—」与「未披露」视为缺(键不出现)。"""
    text = (
        "## 观点与依据\n震荡偏强。\n\n"
        "## 交易要素与风险\n"
        "方向：反弹做多;形态与区间: 短期反弹, 区间 2400~2500；头寸:轻仓试多;头寸范围:—;风险:未披露\n"
    )
    te = web_app._parse_trade_elements(text)
    assert te["方向"] == "反弹做多"
    assert te["形态与区间"] == "短期反弹, 区间 2400~2500"
    assert te["头寸"] == "轻仓试多"
    assert "头寸范围" not in te and "风险" not in te  # 「—」/「未披露」= 缺


def test_parse_trade_elements_absent():
    """无交易节(旧口径结论)→ 空表。"""
    assert web_app._parse_trade_elements("## 核心观点\n钢价偏强。") == {}
    assert web_app._parse_trade_elements("") == {}


def test_extract_advice_section():
    """「## 建议权重」节取正文压缩单行;缺节/纯占位 → 空串。"""
    text = "## 交易要素与风险\n方向:看多;\n\n## 建议权重\n多头 20%~30%, 区间操作为主。\n\n## 数据支撑\n- x\n"
    assert web_app._extract_advice(text) == "多头 20%~30%, 区间操作为主。"
    assert web_app._extract_advice("## 建议权重\n—") == ""
    assert web_app._extract_advice("## 核心观点\n无") == ""


def test_view_row_trade_fields_replace_fund_metrics():
    """观点总览行:四类基本面已移除;三列合并为一列「头寸」,头寸缺时回退头寸范围。"""
    row = web_app._research_view_row({
        "id": 1, "title": "t", "source": "s", "uploaded_at": "2026-09-02 08:00:00",
        "direction": "看多", "confidence": 0.8,
        "conclusion": (
            "## 供需格局\n供应收紧。\n\n"
            "## 交易要素与风险\n方向:看多;形态与区间:单边看多;头寸:—;头寸范围:回落加仓;风险:增产\n\n"
            "## 建议权重\n多头 20%~30%。"
        ),
    })
    assert "fund_metrics" not in row  # 四类基本面列已下线(指标仍随 data_points 入库供看板)
    assert row["trade"] == "单边看多; 回落加仓"
    assert row["advice"] == "多头 20%~30%。"


def test_view_row_trade_fields_blank_for_legacy():
    """旧口径结论(无交易节)→ 「头寸」列为空串(前端显示 —)。"""
    row = web_app._research_view_row({"id": 3, "title": "", "conclusion": "## 核心观点\n震荡。"})
    assert row["trade"] == "" and row["advice"] == ""


def test_view_row_report_advice():
    """「头寸」列并入聚合记录 report_advice(研报原文操作建议原句);缺失/空 → 不影响该列其余源。"""
    row = web_app._research_view_row({
        "id": 5, "title": "t", "conclusion": "## 建议权重\n多头 20%。",
        "report_advice": "逢低做多,回落至 7800 附近轻仓试多。",
    })
    assert row["trade"] == "逢低做多; 回落至 7800 附近轻仓试多"
    # LLM 的建议列照旧来自「## 建议权重」
    assert row["advice"] == "多头 20%。"
    # 旧行无 report_advice 键 → 空串(前端显示 —),不报错
    legacy = web_app._research_view_row({"id": 6, "title": "t", "conclusion": ""})
    assert legacy["trade"] == ""


def test_merge_trade_cell_dedup():
    """「头寸」列三源合并去重:完全重复丢弃、整含取信息量大者、「—/未披露」片段出局。"""
    te = {"形态与区间": "单边看多,—", "头寸": "多配为主", "头寸范围": "—"}
    assert web_app._merge_trade_cell(te, "多配为主") == "单边看多; 多配为主"  # 原文建议与头寸同句 → 去重
    assert web_app._merge_trade_cell({"头寸": "轻仓"}, "轻仓试多,回落加仓") == "轻仓试多; 回落加仓"  # 整含 → 取大者
    assert web_app._merge_trade_cell({"形态与区间": "区间震荡(区间 11400~11800)"}, "研报未披露") == \
        "区间震荡(区间 11400~11800)"  # 未披露/「—」片段不进列
    assert web_app._merge_trade_cell({}, "") == ""  # 全缺 → 空串


def test_heuristic_fund_extracts_typed_values():
    """总结文本里"词+数值+单位"相邻 → 提四类指标,note 带"自总结『原文』"片段。"""
    text = (
        "## 供需格局 8/28中国独立炼厂开工率52.69%（环比+2.99%）,需求回暖。\n"
        "## 库存与结构 仓单45839手(+215)压制上行。\n"
        "## 成本与利润 钢厂毛利约120元/吨,仍能覆盖成本。\n"
        "## 现货与目标价 现货贴水25元/吨,期现套利空间收窄。"
    )
    m = {x["k"]: x for x in web_app._research_fund_metrics({})}
    assert not m  # 无结构化 data_points 时为空 → 才会走文本兜底
    out = web_app._heuristic_fund_from_text(text)
    assert out["operating_rate"]["value"] == 52.69
    assert out["operating_rate"]["unit"] == "%"
    assert "自总结" in out["operating_rate"]["note"]
    assert out["warehouse_receipts"]["value"] == "45839"
    assert out["warehouse_receipts"]["unit"] == "手"
    assert out["processing_margin"]["value"] == 120
    assert out["basis"]["value"] == -25  # 现货贴水 → 基差取负


def test_heuristic_fund_avoids_false_positives():
    """防误报:裸"新开工"(地产词)、"基差及1-5月价差"(日期数字)、纯定性(无数值)都不采。"""
    text = (
        "## 供需格局 地产新开工/竣工延续负增长,对应需求偏弱。\n"
        "## 库存与结构 宁夏、江苏、广西硅铁主力基差及1-5月、5-9月、9-1月价差均有波动。\n"
        "## 成本与利润 锂辉石加工费略上行、外购矿成本边际抬升(无具体读数)。\n"
        "## 观点与依据 中性:仓单无起色、基差抬升,期现共振未至。"
    )
    assert web_app._heuristic_fund_from_text(text) == {}


def test_merge_fund_metrics_prefers_structured_over_heuristic():
    """同键两者都有时,结构化 data_points 优先(文本不覆盖);缺项才用文本兜底。"""
    dp = {"basis": {"value": 68, "unit": "元/吨", "date": "2026-09-01"},
          "operating_rate": {"value": 78.5, "unit": "%"}}
    text = "基差200元/吨,开工率99%,仓单45839手,毛利120元/吨"
    merged = web_app._merge_fund_metrics(dp, text)
    byk = {x["k"]: x for x in merged}
    assert byk["basis"]["value"] == 68 and byk["basis"]["date"] == "2026-09-01"  # 结构化覆盖文本
    assert byk["operating_rate"]["value"] == 78.5
    assert byk["warehouse_receipts"]["value"] == "45839"  # 文本兜底补仓单
    assert byk["processing_margin"]["value"] == 120
    # 返回固定序(基差/仓单/开工率/加工利润)
    assert [x["k"] for x in merged] == ["basis", "warehouse_receipts", "operating_rate", "processing_margin"]


def test_row_research_fund_metrics_matches_variety_segment():
    """列表行:从 structured_data.varieties 取与主品种匹配段的四类指标(多品种不串段)。"""
    import json

    r = {
        "variety": "RB",
        "structured_data": json.dumps({
            "varieties": [
                {"variety": "CU", "basis": {"value": -120, "unit": "元/吨"}},
                {"variety": "RB", "operating_rate": {"value": 80.0, "unit": "%"},
                 "data_points": {"basis": {"value": 20, "unit": "元/吨"}}},
            ]
        }),
    }
    m = web_app._row_research_fund_metrics(r)
    # 取 RB 段(嵌套 data_points 的 basis=20 + 段顶层 operating_rate=80),绝不混入 CU 段 -120
    assert [(x["k"], x["value"]) for x in m] == [("basis", 20), ("operating_rate", 80.0)]
    # 段内无 data_points 包裹的顶层展平旧格式 → 直接取段顶层指标键
    r2 = {"variety": "CU", "structured_data": json.dumps({"basis": {"value": 5, "unit": "元/吨"}})}
    assert [(x["k"], x["value"]) for x in web_app._row_research_fund_metrics(r2)] == [("basis", 5)]


# ---------------------------------------------------------------------------
# 新研报入库补强 _ingest_backfill_fund_metrics(直接提自研报原文, 落库即带四键)
# ---------------------------------------------------------------------------
def test_ingest_backfill_single_variety_fills_missing_keeps_existing():
    """单品种新研报:LLM 漏提开工率/仓单但原文有数 → 入库补进 data_points;已有结构化值不动。"""
    text = "中国独立炼厂开工率52.69%，环比回升。\n仓单45839手，压制上行。"
    items = [{"variety": "SC", "basis": {"value": -25, "unit": "元/吨"}, "direction": "中性"}]
    web_app._ingest_backfill_fund_metrics(items, text)
    item = items[0]
    assert item["operating_rate"]["value"] == 52.69 and item["operating_rate"]["unit"] == "%"
    assert "研报原文" in item["operating_rate"]["note"]  # 标来源, 与 LLM 结构化值区分
    assert item["warehouse_receipts"]["value"] == "45839"
    assert item["warehouse_receipts"]["unit"] == "手"
    assert item["basis"] == {"value": -25, "unit": "元/吨"}  # LLM 已给的不覆盖
    assert item["direction"] == "中性"


def test_ingest_backfill_multi_variety_scopes_per_variety():
    """多品种研报:各品种只扫含自己代码/中文名的句子 → 不把别家子品种的开工率串进来。"""
    text = "RB 高炉开工率83.2%，环比回升。\nTA 聚酯开工率91.4%，表现尚可。"
    items = [{"variety": "RB", "direction": "看多"}, {"variety": "TA", "direction": "看空"}]
    web_app._ingest_backfill_fund_metrics(items, text)
    byc = {i["variety"]: i for i in items}
    assert byc["RB"]["operating_rate"]["value"] == 83.2
    assert byc["TA"]["operating_rate"]["value"] == 91.4  # 不是误填 RB 的 83.2


def test_ingest_backfill_untouched_when_body_has_no_number():
    """原文无数值(纯定性)→ 品种项原样保留, 不产生任何指标键。"""
    items = [{"variety": "RB", "direction": "看多"}]
    before = [dict(i) for i in items]
    web_app._ingest_backfill_fund_metrics(items, "宏观情绪回暖，关注库存去化节奏。")
    assert items == before


# ---------------------------------------------------------------------------
# 机构(研报)方向汇总 summarize_research_views / format_research_views_text
# (2026-09-03:情绪分析师"机构群体"取数 + 对比卡数据生产底座, 确定性无 LLM)
# ---------------------------------------------------------------------------


def _write_research_reports(reports):
    rd._research_cache.clear()
    rd._save_research(
        "RB",
        {"variety": "RB", "updated": "2026-09-03T00:00:00", "reports": reports},
    )


class TestResearchViewsSummary:
    def test_empty_returns_skeleton_and_sentinel(self, isolated_dirs):
        s = rd.summarize_research_views("RB")
        assert s["count"] == 0
        assert s["counts"] == {"bull": 0, "neutral": 0, "bear": 0}
        assert s["conf_avg"] == {"bull": None, "neutral": None, "bear": None}
        assert s["net_dir"] == ""
        assert rd.format_research_views_text("RB").startswith("RESEARCH_VIEW_NO_DATA")

    def test_counts_conf_avg_netdir_and_text(self, isolated_dirs):
        _write_research_reports(
            [
                {"id": 1, "title": "A看多", "source": "华泰", "uploaded_at": "2026-09-02",
                 "direction": "看多", "confidence": 0.8,
                 "conclusion": "# 标题\n库存去化,看多。"},
                {"id": 2, "title": "B看空", "source": "东证", "uploaded_at": "2026-09-01",
                 "direction": "偏空", "confidence": 0.6,
                 "conclusion": "需求走弱,偏空。"},
            ]
        )
        s = rd.summarize_research_views("RB")
        assert s["count"] == 2
        assert s["counts"] == {"bull": 1, "neutral": 0, "bear": 1}
        assert s["conf_avg"]["bull"] == 0.8 and s["conf_avg"]["bear"] == 0.6
        assert s["net_dir"] == "中性"
        assert s["items"][0]["one_line"] == "库存去化,看多。"
        t = rd.format_research_views_text("RB")
        assert "看多/偏多 1 份" in t and "看空/偏空 1 份" in t
        assert "A看多" in t and "B看空" in t

    def test_net_dir_bull_when_more_bullish(self, isolated_dirs):
        _write_research_reports(
            [
                {"id": 1, "title": "a", "source": "s", "direction": "看多", "confidence": 0.7},
                {"id": 2, "title": "b", "source": "s", "direction": "看多", "confidence": 0.6},
                {"id": 3, "title": "c", "source": "s", "direction": "看空", "confidence": 0.5},
            ]
        )
        assert rd.summarize_research_views("RB")["net_dir"] == "看多"

    def test_one_line_skips_marker_and_heading_lines(self, isolated_dirs):
        """观点摘要须跳过 【…】模板标记 与 ## 标题,取真正内容句(2026-09-03 复现修复)。"""
        _write_research_reports(
            [
                {"id": 1, "title": "m", "source": "s", "direction": "看多",
                 "conclusion": "【第一部分 · 多角度核心观点】\n## 供需格局\n9月1日期价收涨,委内瑞拉断供加剧供应缺口。"},
                {"id": 2, "title": "empty", "source": "s", "direction": "中性", "conclusion": ""},
            ]
        )
        s = rd.summarize_research_views("RB")
        assert s["items"][0]["one_line"] == "9月1日期价收涨,委内瑞拉断供加剧供应缺口。"
        assert s["items"][1]["one_line"] == ""

    def test_route_registration_vendor_chain(self, isolated_dirs):
        from tradingagents.dataflows.interface import route_to_vendor

        out0 = route_to_vendor("get_research_view_summary", "RB", "", "")
        assert out0.startswith("RESEARCH_VIEW_NO_DATA")
        _write_research_reports(
            [{"id": 1, "title": "A", "source": "s", "direction": "看多", "confidence": 0.8}]
        )
        out = route_to_vendor("get_research_view_summary", "RB", "", "")
        assert out.startswith("# RESEARCH INSTITUTIONAL VIEWS")
        assert "看多/偏多 1 份" in out


# ---------------------------------------------------------------------------
# 研报客观值最高优先级并入(2026-09-03):
#   基差 → merge_basis_data 的 # RESEARCH BASIS 头;交易所仓单 → merge_inventory_data Part 0
# ---------------------------------------------------------------------------


class TestResearchBasisWarehouseMerge:
    def test_merge_basis_prepends_research_basis(self, isolated_dirs):
        _write_research_reports(
            [
                {"id": 1, "title": "r", "source": "s", "direction": "中性",
                 "data_points": {"basis": {"value": -45, "unit": "元/吨",
                                           "date": "2026-09-02", "note": "现货贴水"}}},
            ]
        )
        merged, used = ed.merge_basis_data("RB", "date,spot_price,dom_basis\n2026-09-01,3200,20\n")
        assert used is True
        assert "# RESEARCH BASIS (研报口径 基差≈现货价−近月合约)" in merged
        assert "-45 元/吨" in merged and "现货贴水" in merged

    def test_merge_basis_still_prepends_research_spot(self, isolated_dirs):
        _write_research_reports(
            [
                {"id": 1, "title": "r", "source": "s", "direction": "中性",
                 "data_points": {"spot_price": {"value": 3300, "unit": "元/吨",
                                                "date": "2026-09-02"}}},
            ]
        )
        merged, used = ed.merge_basis_data("RB", "date,spot_price,dom_basis\n2026-09-01,3200,20\n")
        assert used is True and "# RESEARCH SPOT PRICE" in merged

    def test_merge_inventory_prepends_research_warehouse_receipts(self, isolated_dirs):
        _write_research_reports(
            [
                {"id": 1, "title": "r", "source": "s", "direction": "中性",
                 "data_points": {"warehouse_receipts": {"value": 12435, "unit": "张",
                                                        "date": "2026-09-02", "note": "交易所仓单"}}},
            ]
        )
        merged, used = ed.merge_inventory_data("RB", "date,warehouse_receipts\n2026-09-01,9000\n")
        assert used is True
        assert "## Part 0" in merged
        assert "Research Warehouse Receipts (研报交易所仓单)" in merged
        assert "12435 张" in merged

    def test_merge_inventory_no_research_falls_back_free_api(self, isolated_dirs):
        merged, used = ed.merge_inventory_data("RB", "date,warehouse_receipts\n2026-09-01,9000\n")
        assert used is False
        assert merged.splitlines()[0] == "# DATA_SOURCE: FREE_API (AKShare)"


# ---------------------------------------------------------------------------
# 研报宏观事件确定性汇总(2026-09-04,宏观/情绪分析师系统提示注入数据源)
# ---------------------------------------------------------------------------

def _write_research(tmp_path, variety, reports):
    """往隔离目录写某品种研报聚合数据(key_events 事件格式与第一步提取同构)。"""
    payload = {"variety": variety, "updated": "2026-09-04T10:00:00", "reports": reports}
    (tmp_path / f"{variety}_research.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _event_report(rid, title, source, uploaded_at, direction, confidence, events):
    return {
        "id": rid, "title": title, "source": source, "uploaded_at": uploaded_at,
        "direction": direction, "confidence": confidence, "conclusion": "",
        "data_points": {"key_events": events},
    }


def test_macro_events_common_and_variety(isolated_dirs):
    """跨品种共提 → 宏观共性事件(多空票数);单品种事件只进本品种节并挂观点。"""
    # 日期用动态"今天"(days=3 窗口按当前时间算,硬编码日期会随时间推移出窗)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _write_research(isolated_dirs, "SC", [_event_report(
        1, "原油日报", "国君", now, "看多", 0.8,
        [{"event": "中东战事升级", "detail": "供应中断风险上升", "impact": "bullish", "source": ""}],
    )])
    _write_research(isolated_dirs, "PX", [
        _event_report(2, "PX日报", "国君", now, "看多", 0.7,
            [{"event": "中东 战事升级", "detail": "成本端走强", "impact": "bullish", "source": ""}]),
        _event_report(3, "PX周观察", "东证", now, "中性", None,
            [{"event": "PX装置重启", "detail": "供应回归", "impact": "bearish", "source": ""}]),
    ])
    out = rd.summarize_research_macro_events("SC")
    assert "宏观共性事件" in out
    assert "中东战事升级" in out and "利多x2" in out  # 归一化去空白后同一事件,两品种共提
    assert "PX装置重启" not in out  # 单品种事件不进共性节
    assert "SC 品种研报事件与观点" in out
    assert "中东战事升级[利多]" in out and "看多 · 置信度 0.80" in out


def test_macro_events_days_filter(isolated_dirs):
    """days 窗口外的旧研报事件不进入注入文本。"""
    _write_research(isolated_dirs, "SC", [_event_report(
        1, "旧报", "国君", "2026-08-20 09:00:00", "看多", 0.8,
        [{"event": "老旧事件", "detail": "", "impact": "bullish", "source": ""}],
    )])
    assert rd.summarize_research_macro_events("SC", days=3) == ""


def test_macro_events_no_data_returns_empty(isolated_dirs):
    """无研报/无事件返回空串(调用方不注入,提示词教 LLM 如实说明)。"""
    assert rd.summarize_research_macro_events("RB") == ""
    _write_research(isolated_dirs, "RB", [{
        "id": 9, "title": "无事件研报", "source": "国君", "uploaded_at": "2026-09-04 09:00:00",
        "direction": "中性", "confidence": None, "conclusion": "", "data_points": {},
    }])
    assert rd.summarize_research_macro_events("RB") == ""


def test_research_macro_context_silently_degrades(monkeypatch):
    """底层异常 → 桥接函数返回 ""(不阻断分析主线)。"""
    from tradingagents.agents.utils import commodity_futures_tools as cft

    def _boom(sym):
        raise RuntimeError("db down")

    monkeypatch.setattr(rd, "summarize_research_macro_events", _boom)
    assert cft.research_macro_context("SC") == ""


def test_macro_events_window_uses_publish_date(isolated_dirs):
    """days 回看窗口按发布日期(publish_date)判定:今入库但 30 天前发布 → 出窗;
    老入库但今天发布 → 入窗。日期全部动态(窗口按当前时间算,硬编码会过期)。"""
    today = datetime.now().strftime("%Y-%m-%d")
    old = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    _write_research(isolated_dirs, "SC", [
        _event_report(1, "旧报", "国君", f"{today} 09:00:00", "看多", 0.8,
            [{"event": "隔夜旧闻", "detail": "", "impact": "bullish", "source": ""}]),
        _event_report(2, "新报", "东证", f"{old} 09:00:00", "中性", None,
            [{"event": "新鲜事件", "detail": "", "impact": "bearish", "source": ""}]),
    ])
    # 手动补 publish_date:1 号 → 30 天前(出窗),2 号 → 今天(入窗)
    import json as _json
    p = isolated_dirs / "SC_research.json"
    data = _json.loads(p.read_text(encoding="utf-8"))
    data["reports"][0]["publish_date"] = old
    data["reports"][1]["publish_date"] = today
    p.write_text(_json.dumps(data, ensure_ascii=False), encoding="utf-8")
    out = rd.summarize_research_macro_events("SC", days=3)
    assert "隔夜旧闻" not in out
    assert "新鲜事件" in out


def test_views_route_groups_by_publish_date(isolated_dirs):
    """观点总览日期分组按 publish_date:9.3 发布/9.4 入库 → 归 9.3;无发布日期的旧记录回退 uploaded_at。"""
    _write_research(isolated_dirs, "RB", [
        {"id": 1, "title": "隔夜报", "source": "国君", "uploaded_at": "2026-09-04 08:00:00",
         "publish_date": "2026-09-03", "direction": "看多", "confidence": 0.7,
         "conclusion": "## 观点与依据\n偏强。", "data_points": {}},
        {"id": 2, "title": "旧记录", "source": "华泰", "uploaded_at": "2026-09-02 08:00:00",
         "direction": "中性", "confidence": None,
         "conclusion": "## 观点与依据\n震荡。", "data_points": {}},
    ])
    client = web_app.app.test_client()
    body = client.get("/api/research/views?variety=RB").get_json()
    assert body["dates"] == ["2026-09-03", "2026-09-02"]
    assert body["date"] == "2026-09-03"                       # 缺省取最新发布日
    assert [r["id"] for r in body["rows"]] == [1]
    # 切到 9.2 → 命中旧记录(无 publish_date 回退 uploaded_at)
    body2 = client.get("/api/research/views?variety=RB&date=2026-09-02").get_json()
    assert [r["id"] for r in body2["rows"]] == [2]
    assert body2["rows"][0]["publish_date"] == ""


def test_write_aggregates_carries_report_type(isolated_dirs):
    """聚合记录带 report_type 键(前端周报徽标/每日总结过滤的聚合口径)。"""
    web_app._write_research_aggregates(
        33, "2026-09-05 08:00:00", "烧碱、PVC周报", "国君", ["SH", "PVC"],
        [{"variety": "SH", "direction": "中性", "confidence": 0.5},
         {"variety": "PVC", "direction": "看空", "confidence": 0.6}],
        {"SH": "SH 结论。", "PVC": "PVC 结论。"},
        publish_date="2026-09-05", report_type="周报",
    )
    data = rd.load_research_data("SH")
    rec = next(r for r in data["reports"] if r["id"] == 33)
    assert rec["report_type"] == "周报"
    assert rec["publish_date"] == "2026-09-05"


def test_research_list_route_filters_by_report_type(tmp_path, monkeypatch):
    """/api/research 支持 report_type 查询参数(研报管理类型筛选)。"""
    db = database.AgentSenseDB(tmp_path / "test.db")
    db.insert_research_report(variety="MA", title="甲醇日报", source="", filename="", file_path="", report_type="日报")
    db.insert_research_report(variety="SA", title="纯碱周报", source="", filename="", file_path="", report_type="周报")
    db.insert_research_report(variety="UR", title="尿素：区间运行", source="", filename="", file_path="")
    monkeypatch.setattr(web_app, "get_db", lambda: db)
    client = web_app.app.test_client()
    weekly = client.get("/api/research?report_type=周报").get_json()["reports"]
    assert [r["title"] for r in weekly] == ["纯碱周报"]
    daily = client.get("/api/research?report_type=日报").get_json()["reports"]
    assert [r["title"] for r in daily] == ["甲醇日报"]
    allr = client.get("/api/research").get_json()["reports"]
    assert len(allr) == 3  # 不传参数:行为不变
    # 行数据本身带 report_type(前端列表徽标消费)
    assert {r["report_type"] for r in allr} == {"日报", "周报", ""}
