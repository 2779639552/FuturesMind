"""Unit tests for the HTFC research collector (2026-09-02, Phase B).

The collector module (`research_collector_htfc.py`) is pure at import time —
htfc_api / database / web_app are lazy-imported only inside functions that hit
the network or DB — so these tests exercise the matching/selection/cleaning/
state logic with NO network calls and no DB rows.

Covered: html_to_text cleaning; CODE_ALIASES token matching over real
subclassCodeName shapes ('燃油,低硫燃油', '橡胶,合成橡胶,20号胶', ...); title
fallback with Latin word boundaries (no 'FU' hit in 'Futures') and Chinese
overlap masking ('合成橡胶' must not become RU, '低硫燃料油' must not become FU);
match_codes target filtering; select_today_items per-variety-newest union +
requested-subset; sanitize_filename; state file round-trip + MAX_SEEN cap.
"""


import pytest

import research_collector_htfc as rc


def _item(id_: str, subclass: str = "", title: str = "") -> dict:
    """Synthetic feed item (fields the matcher actually reads)."""
    return {"id": id_, "itemValue": "10070", "subclassCodeName": subclass,
            "title": title, "publishDateTime": "2026-09-02 08:00:00"}


class TestHtmlToText:
    def test_strips_tags_and_collapses_blank_lines(self):
        html = "<h1>标题</h1>\n<p>正文段落</p>\n<div><br><br></div>\n<p>再来一段</p>"
        assert rc.html_to_text(html) == "标题\n正文段落\n再来一段"

    def test_drops_script_and_style(self):
        html = "<script>var x=1;</script><style>.a{}</style><p>正文</p>"
        assert "var" not in rc.html_to_text(html)
        assert "正文" in rc.html_to_text(html)

    def test_unescapes_html_entities(self):
        assert rc.html_to_text("<p>PTA&amp;EG&lt;400</p>") == "PTA&EG<400"

    def test_empty_input(self):
        assert rc.html_to_text("") == ""
        assert rc.html_to_text(None) == ""


class TestTokenMatching:
    @pytest.mark.parametrize(
        ("subclass", "expected"),
        [
            ("燃油,低硫燃油", {"FU", "LU"}),            # FU/LU 合并日报
            ("低硫燃油", {"LU"}),                        # 单 token 精确,不误拉 FU
            ("橡胶,合成橡胶,20号胶", {"RU", "NR"}),       # 合成橡胶非目标 → 不产生 BR
            ("纯苯,沥青", {"BZ", "BU"}),
            ("对二甲苯,PTA,乙二醇", {"PX", "TA", "EG"}),  # 聚酯链日报
            ("塑料,聚丙烯", {"L", "PP"}),
            ("烧碱,PVC", {"SH", "V"}),
            ("LPG", {"PG"}),
            ("碳酸锂", {"LC"}),
            ("原油", {"SC"}),
            ("多晶硅,工业硅", {"PS", "SI"}),              # 2026-09-09 新入池
            ("沪铜,沪铅,沪锌", set()),                    # 有色不在映射表
            ("焦煤,焦炭,螺纹钢", set()),                   # 黑色不在映射表
            ("", set()),
        ],
    )
    def test_token_exact_match(self, subclass, expected):
        # 注意:_match_tokens 只做映射不做池过滤(池过滤在 match_codes 里)
        assert rc._match_tokens(subclass) == expected

    def test_token_split_separators(self):
        assert rc._match_tokens("玻璃；纯碱") == {"FG", "SA"}
        assert rc._match_tokens("尿素/纯碱") == {"UR", "SA"}


class TestTitleFallback:
    def test_latin_word_boundary_no_futures_hit(self):
        # 'FU' 不能命中 English "Futures";词边界保证只认独立代码
        assert rc._match_title("China Futures Weekly Summary") == set()

    def test_chinese_substring(self):
        assert rc._match_title("华泰期货原油日报") == {"SC"}
        assert rc._match_title("华泰期货碳酸锂日报") == {"LC"}

    def test_overlap_mask_synthetic_rubber_not_ru(self):
        # 标题只有合成橡胶(BR,非目标)时不得因 '橡胶' 子串误命中 RU
        assert rc._match_title("华泰期货合成橡胶日报") == set()

    def test_overlap_mask_low_sulfur_not_fu(self):
        # '低硫燃料油' 含 '燃料油' 子串,标题兜底必须挡掉 FU
        assert rc._match_title("华泰期货低硫燃料油日报") == {"LU"}

    def test_overlap_mask_still_keeps_legit_ru(self):
        # 橡胶与合成橡胶并列的标题:RU 保留,合成橡胶不产生任何目标码
        assert rc._match_title("橡胶和合成橡胶走势分化") == {"RU"}


class TestMatchCodes:
    def test_filters_to_target_varieties(self):
        item = _item("RE1", subclass="PTA", title="华泰期货PTA日报")
        assert rc.match_codes(item) == {"TA"}

    def test_subclass_primary_title_not_used_when_hit(self):
        # subclass 已命中(FU)时标题里的无关词不追加命中
        item = _item("RE2", subclass="燃料油", title="宏观与商品观察")
        assert rc.match_codes(item) == {"FU"}

    def test_title_fallback_when_subclass_empty(self):
        item = _item("RE3", subclass="", title="华泰期货原油周度展望")
        assert rc.match_codes(item) == {"SC"}

    def test_non_target_returns_empty(self):
        item = _item("RE4", subclass="铁矿石", title="铁矿石日报")
        assert rc.match_codes(item) == set()


class TestSelectTodayItems:
    def test_newest_report_per_variety(self):
        # items 最新在前:同一品种只挑最新一篇(首个命中)
        old_sc = _item("RE-A", subclass="原油", title="原油早报 A")
        new_sc = _item("RE-B", subclass="原油", title="原油日报 B")
        res = rc.select_today_items([new_sc, old_sc])
        assert [i["id"] for i in res] == ["RE-B"]
        assert res[0]["codes"] == ["SC"]

    def test_multi_variety_report_covers_many(self):
        items = [
            _item("RE-1", subclass="燃油,低硫燃油", title="燃料油日报"),
            _item("RE-2", subclass="原油", title="原油日报"),
            _item("RE-3", subclass="LPG", title="LPG 日报"),
        ]
        res = rc.select_today_items(items)
        ids = {i["id"] for i in res}
        assert ids == {"RE-1", "RE-2", "RE-3"}
        # 每份报告只出现一次,且覆盖品种并集=3 码
        assert {c for i in res for c in i["codes"]} == {"FU", "LU", "SC", "PG"}

    def test_requested_subset(self):
        items = [_item("RE-1", subclass="甲醇"), _item("RE-2", subclass="原油")]
        res = rc.select_today_items(items, requested={"SC"})
        assert [i["id"] for i in res] == ["RE-2"]

    def test_overlapping_reports_both_picked_for_distinct_varieties(self):
        # 最新 RE-NEW 只覆盖 SC;次新 RE-WIDE 覆盖 SC+EG → SC 归 RE-NEW,EG 归 RE-WIDE
        wide = _item("RE-WIDE", subclass="原油,乙二醇")
        new = _item("RE-NEW", subclass="原油")
        res = rc.select_today_items([new, wide])
        assert {i["id"] for i in res} == {"RE-NEW", "RE-WIDE"}

    def test_empty_items(self):
        assert rc.select_today_items([]) == []

    def test_never_exceeds_requested_count(self):
        many = [_item(f"RE-{i:03d}", subclass="原油") for i in range(30)]
        res = rc.select_today_items(many, requested={"SC"})
        assert len(res) == 1  # 同品种只取最新一篇


class TestSanitizeFilename:
    def test_invalid_and_space_chars_replaced(self):
        assert rc._sanitize_filename('a/b\\c:d*e?f"g<h>i|j k') == "a_b_c_d_e_f_g_h_i_j_k"

    def test_truncated_to_60(self):
        name = "x" * 200
        assert len(rc._sanitize_filename(name)) == 60

    def test_empty_fallback(self):
        assert rc._sanitize_filename("") == "report"


class TestStateFile:
    def test_round_trip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rc, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr(rc, "_STATE_DIR", tmp_path)
        rc._save_state({"seen": ["RE1", "RE2"], "last_run": "t"})
        assert rc._load_state()["seen"] == ["RE1", "RE2"]

    def test_missing_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rc, "STATE_FILE", tmp_path / "nope.json")
        assert rc._load_state() == {}

    def test_corrupt_file_returns_empty(self, tmp_path, monkeypatch):
        f = tmp_path / "state.json"
        f.write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(rc, "STATE_FILE", f)
        assert rc._load_state() == {}

    def test_seen_capped_at_max(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rc, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr(rc, "_STATE_DIR", tmp_path)
        big = [f"RE{i:05d}" for i in range(rc.MAX_SEEN + 50)]
        rc._save_state({"seen": big, "last_run": "t"})
        seen = rc._load_state()["seen"]
        assert len(seen) == rc.MAX_SEEN
        # 保留最新 MAX_SEEN 条(big 本身有序递增,丢弃最旧 50 条)
        assert seen[0] == "RE00050"
        assert seen[-1] == big[-1]


class TestIngestOneIdempotency:
    """_ingest_one 的入库幂等:以 DB 行(文件名精确)为准,processing 残留自愈。

    database / web_app 在模块里是懒导入,这里用 stub 顶掉 sys.modules 里的
    同名模块:不联网、不落真库,只验证 insert/重跑决策与调用次数。
    """

    def _install(self, monkeypatch, tmp_path):
        import sys
        import types

        class FakeDB:
            def __init__(self):
                self.row = None
                self.inserted = []
                self._id = 200

            def get_research_report_by_filename(self, fname):
                return self.row

            def insert_research_report(self, **kw):
                self.inserted.append(kw)
                self._id += 1
                return self._id

        class FakeWebApp:
            def __init__(self):
                self.processed = []

            def _process_research_report(self, rid):
                self.processed.append(rid)

        db, web = FakeDB(), FakeWebApp()
        # 用真 ModuleType + 实例属性装 stub:函数放实例(而非类字典)不会变 bound method,
        # 否则 from database import get_db 拿到的是已绑定 self 的方法,get_db() 会多传一参。
        database_mod = types.ModuleType("database")
        database_mod.get_db = lambda: db
        web_app_mod = types.ModuleType("web_app")
        web_app_mod.RESEARCH_UPLOAD_DIR = tmp_path
        web_app_mod._process_research_report = web._process_research_report
        monkeypatch.setitem(sys.modules, "database", database_mod)
        monkeypatch.setitem(sys.modules, "web_app", web_app_mod)
        return db, web

    def _run(self, monkeypatch, tmp_path, status=None):
        db, web = self._install(monkeypatch, tmp_path)
        if status is not None:
            db.row = {"id": 58, "status": status}
        item = {"id": "RE17428", "title": "EB 日报", "reportType": "日报", "publishDateTime": "2026-09-02 08:00:00"}
        ok = rc._ingest_one(item, "。" * 300)  # body ≥ MIN_BODY_CHARS
        return db, web, ok

    def test_done_row_skipped_no_reprocess(self, monkeypatch, tmp_path):
        db, web, ok = self._run(monkeypatch, tmp_path, status="done")
        assert ok is False          # 已在库(done)→ 跳过
        assert web.processed == []  # 不再触发 LLM
        assert db.inserted == []    # 不重复 insert

    def test_processing_row_self_heals_reusing_id(self, monkeypatch, tmp_path):
        db, web, ok = self._run(monkeypatch, tmp_path, status="processing")
        assert ok is True           # 崩溃残留 → 视为待补
        assert web.processed == [58]  # 复用原行 id 重跑处理
        assert db.inserted == []      # 不产生第二行

    def test_no_row_inserts_then_processes(self, monkeypatch, tmp_path):
        db, web, ok = self._run(monkeypatch, tmp_path, status=None)
        assert ok is True
        assert len(db.inserted) == 1
        ins = db.inserted[0]
        # 文件名以 articleId 为前缀,与 sanitize 后的标题拼出(即库内查询键)
        assert ins["filename"] == f"RE17428_{rc._sanitize_filename('EB 日报')}.md"
        assert ins["variety"] == ""
        assert ins["publish_date"] == "2026-09-02"  # 接口 publishDateTime 真实发布日透传入库
        assert ins["report_type"] == "日报"  # 接口 reportType 字段直接透传
        assert len(web.processed) == 1  # insert 返回的自增 id 被送去 LLM 处理
        assert web.processed[0] > 200

    def test_orphan_file_without_row_still_ingests(self, monkeypatch, tmp_path):
        # 崩溃只留下孤儿 md(无 DB 行)时,重跑必须照常 insert——不能因文件在而跳过
        fname = f"RE17428_{rc._sanitize_filename('EB 日报')}.md"
        orphan = tmp_path / rc.SOURCE_ORG / fname
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_text("孤儿残留", encoding="utf-8")
        db, web, ok = self._run(monkeypatch, tmp_path, status=None)
        assert ok is True
        assert len(db.inserted) == 1   # 孤儿文件被覆盖重写并入库
        assert len(web.processed) == 1


class TestWeeklyChannel:
    """周报频道(10075)接入:7 天回看窗口 + 两频道独立选品合并(2026-09-07)。

    fetch_* 里的 htfc_api 是懒导入 → 用 stub 顶掉 sys.modules,不联网。
    """

    def _stub_htfc_api(self, monkeypatch, result_list):
        import sys
        import types

        calls = []

        def search_reports(item_value, cur_page=1, page_size=10):
            calls.append(item_value)
            resp = types.SimpleNamespace(raw={"resultList": result_list})
            return resp

        def data_of(resp):
            return resp.raw

        mod = types.ModuleType("htfc_api")
        mod.search_reports = search_reports
        mod.data_of = data_of
        monkeypatch.setitem(sys.modules, "htfc_api", mod)
        return calls

    @staticmethod
    def _feed_item(id_, pub, subclass="原油", rtype="周报"):
        # 2026-09-09 起默认用池内品种(原油 SC);甲醇已随品种池收缩出池
        return {"id": id_, "itemValue": "10070", "reportType": rtype,
                "publishDateTime": f"{pub} 08:00:00", "subclassCodeName": subclass,
                "title": f"华泰期货{subclass}{rtype}{id_}"}

    def test_weekly_window_inclusive_last10(self, monkeypatch):
        # 窗口 = [target-(WEEKLY_WINDOW_DAYS-1), target] 闭区间:
        # 周报集中在周日/周一发布,"上周日→本周一"最大跨 8 天 ⇒ 窗口 10 天留裕量
        self._stub_htfc_api(monkeypatch, [
            self._feed_item("RE1", "2026-09-07"),   # 当日
            self._feed_item("RE2", "2026-08-29"),   # 窗口首日(target-9)
            self._feed_item("RE3", "2026-08-28"),   # 窗口外(target-10)
            self._feed_item("RE4", "2026-09-08"),   # 未来日期剔除
        ])
        items = rc.fetch_weekly_items("2026-09-07")
        assert [i["id"] for i in items] == ["RE1", "RE2"]
        # 周报项的 reportType 原样透传(入库即接上周报总结口径)
        assert all(i["reportType"] == "周报" for i in items)

    def test_weekly_window_covers_sunday_to_monday_gap(self, monkeypatch):
        # 实际场景回归:周一(09-07)跑,必须覆盖上周日(08-30,跨 8 天)发布的周报
        self._stub_htfc_api(monkeypatch, [
            self._feed_item("RE-SUN", "2026-08-30", subclass="原油"),
        ])
        items = rc.fetch_weekly_items("2026-09-07")
        assert [i["id"] for i in items] == ["RE-SUN"]

    def test_weekly_channel_hits_api_10075(self, monkeypatch):
        calls = self._stub_htfc_api(monkeypatch, [])
        rc.fetch_weekly_items("2026-09-07")
        assert calls == [rc.FEED_CHANNEL_WEEKLY]

    def test_daily_still_exact_date_on_10074(self, monkeypatch):
        calls = self._stub_htfc_api(monkeypatch, [
            self._feed_item("RE1", "2026-09-07", rtype="日报"),
            self._feed_item("RE2", "2026-09-06", rtype="日报"),
        ])
        items = rc.fetch_today_items("2026-09-07")
        assert [i["id"] for i in items] == ["RE1"]
        assert calls == [rc.FEED_CHANNEL_DAILY]

    def test_ingest_merges_both_channels_independently(self, monkeypatch):
        # 同品种日报+周报都命中:两频道独立选品,两篇都进(dry_run 不落库)
        daily = [dict(self._feed_item("RE-D", "2026-09-07", rtype="日报"))]
        weekly = [dict(self._feed_item("RE-W", "2026-09-06", rtype="周报"))]
        monkeypatch.setattr(rc, "fetch_today_items", lambda d: daily)
        monkeypatch.setattr(rc, "fetch_weekly_items", lambda d: weekly)
        monkeypatch.setattr(rc, "_load_state", lambda: {"seen": []})
        res = rc.ingest_today("2026-09-07", dry_run=True)
        assert res["collected"] == 2
        assert res["items"] == 2

    def test_ingest_daily_only_channel_filter(self, monkeypatch):
        daily = [dict(self._feed_item("RE-D", "2026-09-07", rtype="日报"))]
        weekly = [dict(self._feed_item("RE-W", "2026-09-06", rtype="周报"))]
        monkeypatch.setattr(rc, "fetch_today_items", lambda d: daily)
        monkeypatch.setattr(rc, "fetch_weekly_items", lambda d: weekly)
        monkeypatch.setattr(rc, "_load_state", lambda: {"seen": []})
        res = rc.ingest_today("2026-09-07", dry_run=True, channels={"日报"})
        assert res["collected"] == 1
        assert res["items"] == 1

    def test_ingest_dedupes_cross_channel_ids(self, monkeypatch):
        # 防御兜底:同一 articleId 跨频道重复只保留首个
        same = dict(self._feed_item("RE-SAME", "2026-09-07"))
        monkeypatch.setattr(rc, "fetch_today_items", lambda d: [same])
        monkeypatch.setattr(rc, "fetch_weekly_items", lambda d: [dict(same)])
        monkeypatch.setattr(rc, "_load_state", lambda: {"seen": []})
        res = rc.ingest_today("2026-09-07", dry_run=True)
        assert res["collected"] == 1

    def test_ingest_seen_filters_rerun(self, monkeypatch):
        # 周报窗口跨次重跑:seen 里已有的不再进 work
        daily = [dict(self._feed_item("RE-D", "2026-09-07", rtype="日报"))]
        monkeypatch.setattr(rc, "fetch_today_items", lambda d: daily)
        monkeypatch.setattr(rc, "fetch_weekly_items", lambda d: [])
        monkeypatch.setattr(rc, "_load_state", lambda: {"seen": ["RE-D"]})
        res = rc.ingest_today("2026-09-07", dry_run=True)
        assert res["collected"] == 0


class TestModuleConstants:
    def test_target_varieties_is_active_pool(self):
        """2026-09-09 品种池收缩:TARGET 统一引用 ACTIVE_VARIETIES(20 品种)。"""
        from tradingagents.dataflows.commodity_futures import ACTIVE_VARIETIES

        assert set(rc.TARGET_VARIETIES) == set(ACTIVE_VARIETIES)
        assert len(rc.TARGET_VARIETIES) == 20
        # 池内新入品种的别名表必须齐备(标题兜底匹配依赖)
        for code in ["M", "CF", "CJ", "LH", "BZ", "BR", "PS", "SI"]:
            assert code in rc.CODE_ALIASES

    def test_sh_is_caustic_not_synthetic_rubber(self):
        # SH=烧碱(氯碱);合成橡胶=BR 非目标。若未来把 SH 映射错成合成橡胶会双漏
        assert "烧碱" in rc.CODE_ALIASES["SH"]
        assert "合成橡胶" not in rc.CODE_ALIASES["SH"]
