"""国泰君安云 API 研报采集器(research_collector_gtja)纯函数单元测试。

覆盖:合集/周报/英文晨报排除、tag→品种精确映射、同日重发标题去重、
摘要 HTML→文本、跨日同名日报删旧迎新。不触网络/LLM/DB。
"""

from research_collector_gtja import (
    TAG_TO_CODE,
    _html_to_text,
    _norm_title,
    _supersede_same_title,
    is_collection,
    is_weekly,
    match_codes,
)


def _row(title="尿素：区间运行", tags=None, **kw):
    return {"title": title, "infoTags": [{"tagName": t} for t in (tags or ["尿素", "日报"])], **kw}


def test_is_collection_excludes_compilations():
    assert is_collection(_row("2026年9月4日农产品晨报合集", ["日报合集", "合集"]))
    assert is_collection(_row("2026年9月4日能源化工晨报合集", ["晨报合集_pdf"]))
    # 周报不再是排除类(2026-09-07):单品种周报照常采集打标;仅周报合集仍排除
    assert is_collection(_row("2026年9月4日品种周报合集", ["周报合集", "合集"]))
    # 全国碳市场周报等无品种 tag 的周报由 match_codes 拦截(此处只判合集语义)
    assert not is_collection(_row("全国碳市场周报", ["周报", "绿色金融"]))
    assert is_collection(_row("2026年9月4日 期货行情前瞻报告", ["期货行情前瞻报告"]))


def test_is_weekly_detection():
    assert is_weekly(_row("烧碱、PVC周报", ["周报", "能源化工", "烧碱", "PVC"]))
    assert is_weekly(_row("尿素周报", ["日报", "尿素"]))  # 标题以「周报」结尾
    assert not is_weekly(_row("尿素：区间运行", ["日报", "能源化工", "尿素"]))
    assert not is_weekly(_row("碳酸锂日报20260904：周度去库", ["日报", "碳酸锂"]))


def test_is_collection_excludes_english_morning_insight():
    assert is_collection(_row("Morning Insight: September 4, 2026", ["Morning Insight"]))


def test_is_collection_keeps_single_variety_daily():
    assert not is_collection(_row("尿素：区间运行", ["日报", "日报", "能源化工", "尿素"]))


def test_match_codes_exact_tag_mapping():
    assert match_codes(_row(tags=["日报", "能源化工", "尿素"])) == {"UR"}
    # 多品种研报:对二甲苯/PTA/MEG 三个 tag 全命中
    assert match_codes(_row(tags=["PTA", "MEG", "对二甲苯"])) == {"TA", "EG", "PX"}
    # 低硫燃料油 vs 燃料油:枚举 token 精确匹配,不互相误命中
    assert match_codes(_row(tags=["低硫燃料油"])) == {"LU"}
    assert match_codes(_row(tags=["燃料油"])) == {"FU"}
    # 合成橡胶(BR,非目标)不得误命中 橡胶(RU)
    assert match_codes(_row(tags=["合成橡胶"])) == set()


def test_match_codes_respects_requested_subset():
    row = _row(tags=["PTA", "MEG", "对二甲苯"])
    assert match_codes(row, requested={"TA"}) == {"TA"}
    assert match_codes(row, requested=set()) == set()


def test_match_codes_all_target_codes_have_mapping():
    from research_collector_gtja import TARGET_VARIETIES

    mapped = set(TAG_TO_CODE.values())
    assert mapped == set(TARGET_VARIETIES)


def test_html_to_text_strips_tags():
    html = '<li>本周价格走势：偏强</li><div class="block-content">收盘价99.50元/吨。</div>'
    text = _html_to_text(html)
    assert "本周价格走势：偏强" in text
    assert "收盘价99.50元/吨" in text
    assert "<" not in text and "div" not in text


def test_norm_title_ignores_whitespace():
    assert _norm_title("尿素：区间运行") == "尿素：区间运行"
    assert _norm_title("PVC：短期偏强\n但空间有限。") == "PVC：短期偏强但空间有限。"
    assert _norm_title("") == ""


class _FakeDB:
    """list_research_reports_since 打桩:返回预置行,校验 since 参数已传。"""

    def __init__(self, rows):
        self.rows = rows
        self.since = None

    def list_research_reports_since(self, since):
        self.since = since
        return self.rows


def test_supersede_deletes_same_title_old_row(monkeypatch):
    # 采集器在函数内 `from web_app import _delete_research_report_full`,桩打 web_app 侧
    import web_app

    deleted = []
    monkeypatch.setattr(web_app, "_delete_research_report_full",
                        lambda rid: deleted.append(rid) or True)
    db = _FakeDB([
        {"id": 81, "title": "纯碱短期震荡市"},
        {"id": 90, "title": "甲醇：价格中枢上移"},  # 不同标题,不得误删
    ])
    _supersede_same_title(db, "纯碱短期震荡市")
    assert deleted == [81]
    assert db.since is not None  # 时间下界已传入


def test_supersede_normalizes_whitespace_title(monkeypatch):
    import web_app

    deleted = []
    monkeypatch.setattr(web_app, "_delete_research_report_full",
                        lambda rid: deleted.append(rid) or True)
    db = _FakeDB([{"id": 94, "title": "PVC：短期偏强\n但空间有限。"}])
    _supersede_same_title(db, "PVC：短期偏强但空间有限。")
    assert deleted == [94]


def test_supersede_survives_db_failure(monkeypatch):
    import web_app

    deleted = []
    monkeypatch.setattr(web_app, "_delete_research_report_full",
                        lambda rid: deleted.append(rid) or True)

    class _BrokenDB:
        def list_research_reports_since(self, since):
            raise RuntimeError("db down")

    _supersede_same_title(_BrokenDB(), "尿素：区间运行")  # 不抛异常,跳过删旧
    assert deleted == []


class TestIngestOnePublishDate:
    """_ingest_one 入库透传:接口 publishTime(真实发布日)须写进 publish_date 列。

    database / web_app 懒导入用 stub 顶掉 sys.modules 同名模块(与
    test_htfc_collector.TestIngestOneIdempotency 同套路):无附件 → 走 summary
    兜底 .md 路径,不下载、不调 LLM、不落真库。
    """

    def _install(self, monkeypatch, tmp_path):
        import sys
        import types

        class FakeDB:
            def __init__(self):
                self.inserted = []

            def get_research_report_by_filename(self, fname):
                return None

            def list_research_reports_since(self, since):
                return []

            def insert_research_report(self, **kw):
                self.inserted.append(kw)
                return 301

        class FakeWebApp:
            RESEARCH_UPLOAD_DIR = tmp_path

            @staticmethod
            def _extract_report_text(path):
                return "", False  # 正文过短 → 走 summary 兜底 .md

            @staticmethod
            def _process_research_report(rid):
                pass

            @staticmethod
            def _delete_research_report_full(rid):
                return True

        db = FakeDB()
        database_mod = types.ModuleType("database")
        database_mod.get_db = lambda: db
        web_app_mod = types.ModuleType("web_app")
        for attr in ("RESEARCH_UPLOAD_DIR", "_extract_report_text",
                     "_process_research_report", "_delete_research_report_full"):
            setattr(web_app_mod, attr, getattr(FakeWebApp, attr))
        monkeypatch.setitem(sys.modules, "database", database_mod)
        monkeypatch.setitem(sys.modules, "web_app", web_app_mod)
        return db

    def _row(self):
        return {
            "infoId": "GTJA-9",
            "title": "纯碱日报",
            "publishTime": "2026-09-03 16:30:00",
            "infoTags": [{"tagName": "日报"}, {"tagName": "纯碱"}],
            "attachments": [],
            "summary": "<p>" + "库存连续去化,现货报价上调。" * 30 + "</p>",  # 摘要 ≥ MIN_BODY_CHARS
        }

    def test_publish_time_ingested_as_publish_date(self, monkeypatch, tmp_path):
        from research_collector_gtja import _ingest_one

        db = self._install(monkeypatch, tmp_path)
        assert _ingest_one(self._row()) is True
        assert len(db.inserted) == 1
        assert db.inserted[0]["publish_date"] == "2026-09-03"  # 回看窗口跨日,入库≠发布
        assert db.inserted[0]["report_type"] == "日报"  # tag 精确含「日报」→ 打日报标

    def test_weekly_row_ingested_as_weekly(self, monkeypatch, tmp_path):
        """单品种周报(tags 带「周报」)照常采集并打「周报」标(2026-09-07 起)。"""
        from research_collector_gtja import _ingest_one

        db = self._install(monkeypatch, tmp_path)
        row = self._row()
        row["infoId"] = "GTJA-10"
        row["title"] = "烧碱、PVC周报"
        row["infoTags"] = [{"tagName": "周报"}, {"tagName": "烧碱"}, {"tagName": "PVC"}]
        row["publishTime"] = "2026-09-05 10:00:00"
        assert _ingest_one(row) is True
        assert len(db.inserted) == 1
        assert db.inserted[0]["report_type"] == "周报"
        assert db.inserted[0]["publish_date"] == "2026-09-05"
