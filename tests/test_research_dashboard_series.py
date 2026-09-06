"""_research_dashboard_series 纯函数测试(研报基本面指标 → 数据看板时序点)。

全离线:不触网、不碰真实 dev DB。主体用 _FakeDB(只实现 list_research_reports
的读取语义)精确控制 uploaded_at/publish_date;另加 1 例真实 tmp_path
AgentSenseDB 集成(monkeypatch web_app.get_db)验证 DB 路径贯通。
"""

import json

import database
import web_app


def _row(rid, sd, uploaded_at, status="done", variety="RB", publish_date=None):
    return {
        "id": rid, "variety": variety, "title": "早报", "source": "华泰期货",
        "status": status, "uploaded_at": uploaded_at, "publish_date": publish_date or "",
        "structured_data": json.dumps(sd, ensure_ascii=False),
    }


def _sd(publish_date, seg_points, seg_variety="RB"):
    """structured_data 构造器:单品种段(指标放段顶层,旧行平铺形态)。"""
    seg = {"variety": seg_variety, **seg_points}
    sd = {"varieties": [seg]}
    if publish_date:
        sd["publish_date"] = publish_date
    return sd


class _FakeDB:
    """只实现 _research_dashboard_series 用到的读取接口。"""

    def __init__(self, rows):
        self._rows = rows

    def list_research_reports(self, variety=None, limit=50, ingest_source=None):
        if variety:
            return [r for r in self._rows if r["variety"].upper() == variety.upper()]
        return list(self._rows)


def test_series_dates_from_publish_date_sorted_and_overlay_split():
    rows = [
        _row(1, _sd("2026-08-05", {"basis": {"value": -20, "unit": "元/吨", "date": "2026-08-04"},
                                   "operating_rate": {"value": 80.0, "unit": "%", "date": "2026-08-04"}}),
             "2026-08-05 08:00:00"),
        _row(2, _sd("2026-08-01", {"basis": {"value": -35, "unit": "元/吨", "date": "2026-07-31"}}),
             "2026-08-01 08:00:00"),
    ]
    out = web_app._research_dashboard_series(_FakeDB(rows), "RB")
    assert out["available"] is True
    # basis(元/吨, 与看板基差轴同口径)进 overlay 且按日期升序
    assert [p["date"] for p in out["overlay"]["basis"]] == ["2026-08-01", "2026-08-05"]
    assert [p["value"] for p in out["overlay"]["basis"]] == [-35.0, -20.0]
    # 其余键(仓单单位口径未实证)一律 standalone
    assert "basis" not in out["standalone"]
    assert out["standalone"]["operating_rate"][0]["value"] == 80.0


def test_series_dedup_same_day_keeps_latest_uploaded():
    rows = [
        _row(1, _sd("2026-08-01", {"basis": {"value": -35, "unit": "元/吨"}}), "2026-08-01 08:00:00"),
        _row(2, _sd("2026-08-01", {"basis": {"value": -40, "unit": "元/吨"}}), "2026-08-01 09:00:00"),
    ]
    out = web_app._research_dashboard_series(_FakeDB(rows), "RB")
    assert len(out["overlay"]["basis"]) == 1  # (键,日期) 去重
    assert out["overlay"]["basis"][0]["value"] == -40.0  # 留 uploaded_at 最新一份
    assert out["overlay"]["basis"][0]["report_id"] == 2


def test_series_db_publish_date_beats_structured_and_uploaded():
    """日期归键优先级:DB publish_date 列 > structured_data.publish_date > uploaded_at。"""
    rows = [_row(1, _sd("2026-08-02", {"basis": {"value": -35, "unit": "元/吨"}}),
                 "2026-08-05 08:00:00", publish_date="2026-08-03")]
    out = web_app._research_dashboard_series(_FakeDB(rows), "RB")
    assert out["overlay"]["basis"][0]["date"] == "2026-08-03"  # DB 列赢过 sd 与 uploaded_at
    # DB 列缺 → 用 sd.publish_date(而非 uploaded_at)
    rows2 = [_row(2, _sd("2026-08-02", {"basis": {"value": -35, "unit": "元/吨"}}), "2026-08-05 08:00:00")]
    out2 = web_app._research_dashboard_series(_FakeDB(rows2), "RB")
    assert out2["overlay"]["basis"][0]["date"] == "2026-08-02"


def test_series_date_falls_back_to_uploaded_at():
    # publish_date 缺 → 回退 uploaded_at[:10]
    rows = [_row(1, _sd(None, {"warehouse_receipts": {"value": 12345, "unit": "张"}}),
                 "2026-08-11 10:30:00")]
    out = web_app._research_dashboard_series(_FakeDB(rows), "RB")
    assert out["available"] is True
    assert out["standalone"]["warehouse_receipts"][0]["date"] == "2026-08-11"


def test_series_skips_rows_without_whitelisted_metrics():
    # 无四键/白名单指标的旧行不进 series;available=False + 中文 note(不 500)
    rows = [_row(1, _sd("2026-08-01", {"direction": "看多", "confidence": 0.8}), "2026-08-01 08:00:00")]
    out = web_app._research_dashboard_series(_FakeDB(rows), "RB")
    assert out["available"] is False
    assert out["overlay"] == {} and out["standalone"] == {}
    assert out["note"]


def test_series_skips_non_numeric_values():
    # 文本值(区间描述等启发式内容)不进时序:只收确定性数值
    rows = [_row(1, _sd("2026-08-01", {"basis": "低位运行", "operating_rate": "约八成"}),
                 "2026-08-01 08:00:00")]
    out = web_app._research_dashboard_series(_FakeDB(rows), "RB")
    assert out["available"] is False


def test_series_ignores_non_done_rows():
    rows = [
        _row(1, _sd("2026-08-01", {"basis": {"value": -35}}), "2026-08-01 08:00:00", status="processing"),
        _row(2, _sd("2026-08-02", {"basis": {"value": -30}}), "2026-08-02 08:00:00", status="done"),
    ]
    out = web_app._research_dashboard_series(_FakeDB(rows), "RB")
    assert [p["report_id"] for p in out["overlay"]["basis"]] == [2]


def test_series_multivariety_row_uses_matching_segment():
    # 多品种研报:取 variety 匹配段,不误拿其它品种的读数
    sd = {
        "publish_date": "2026-08-01",
        "varieties": [
            {"variety": "CU", "basis": {"value": 999, "unit": "元/吨"}},
            {"variety": "RB", "basis": {"value": -10, "unit": "元/吨"}},
        ],
    }
    out = web_app._research_dashboard_series(_FakeDB([_row(1, sd, "2026-08-01 08:00:00")]), "RB")
    assert out["overlay"]["basis"][0]["value"] == -10.0


def test_series_exception_degrades_gracefully():
    class _BoomDB:
        def list_research_reports(self, *a, **k):
            raise RuntimeError("db broken")

    out = web_app._research_dashboard_series(_BoomDB(), "RB")
    assert out["available"] is False
    assert out["note"].startswith("DATA_ERROR")


def test_series_against_real_tmp_db(monkeypatch, tmp_path):
    """集成:tmp_path AgentSenseDB → monkeypatch web_app.get_db → 时序点贯通。"""
    db = database.AgentSenseDB(tmp_path / "test.db")
    rid = db.insert_research_report(
        variety="RB", title="早报", source="华泰期货",
        filename="r.md", file_path=str(tmp_path / "r.md"), status="done",
    )
    db.update_research_report(
        rid, status="done",
        structured_data=json.dumps(
            _sd("2026-08-01", {"basis": {"value": -35, "unit": "元/吨", "date": "2026-07-31"},
                               "processing_margin": {"value": 120.0, "unit": "元/吨", "date": "2026-07-31"}}),
            ensure_ascii=False,
        ),
    )
    monkeypatch.setattr(web_app, "get_db", lambda: db)
    out = web_app._research_dashboard_series(web_app.get_db(), "RB")
    assert out["available"] is True
    assert out["overlay"]["basis"][0]["value"] == -35.0
    assert out["standalone"]["processing_margin"][0]["date"] == "2026-08-01"
