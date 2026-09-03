"""数据看板路由 /api/dashboard/<品种> 装配与降级测试(不联网,全 mock)。

覆盖 2026-09-01 的并行化重构与 2026-09-03 的研报指标第 4 数据源:
  1. 四数据源(价格/库存/基差/研报指标)都发起请求(并行线程池装配正确);
  2. 任一数据源异常只降级该项,接口整体不 500 —— 尤其价格分支(原来裸调用会 500,
     现改为 price_note + 空序列优雅降级,与库存/基差/研报指标同口径)。
"""

import web_app

_PRICE = [{"date": f"2026-08-{i+1:02d}", "close": 100.0 + i} for i in range(8)]
_INV = [{"date": f"2026-08-{i+1:02d}", "inventory": 1000.0 + i, "change": 0} for i in range(8)]
_BASIS = [
    {"date": f"2026-08-{i+1:02d}", "spot_price": 100.0, "near_basis": 1.0, "near_basis_rate": 0.5}
    for i in range(8)
]
# 研报指标第 4 源的空态结构(与 _research_dashboard_series 降级返回一致)
_RES_EMPTY = {"available": False, "note": "", "overlay": {}, "standalone": {}}


def _mock_all_sources(monkeypatch, price_fail: bool = False):
    """把三条数据源与解析函数全部 mock 掉,返回记录调用顺序的列表。"""
    calls = []

    def _price(*_a, **_k):
        calls.append("price")
        if price_fail:
            raise RuntimeError("akshare price down")
        return "FAKE_PRICE"

    def _inv(*_a, **_k):
        calls.append("inv")
        return "FAKE_INV"

    def _basis(*_a, **_k):
        calls.append("basis")
        return "FAKE_BASIS"

    monkeypatch.setattr(web_app, "get_futures_price", _price)
    monkeypatch.setattr(web_app, "get_futures_inventory", _inv)
    monkeypatch.setattr(web_app, "get_futures_basis", _basis)
    monkeypatch.setattr(web_app, "_adjusted_price_points", lambda *_a, **_k: (_PRICE, [], "calendar"))
    monkeypatch.setattr(web_app, "_inventory_points", lambda *_a, **_k: _INV)
    monkeypatch.setattr(web_app, "_basis_points", lambda *_a, **_k: _BASIS)
    # 研报指标第 4 源:默认置空(不碰真实 dev DB;空态与 price/inv/basis 同口径)
    monkeypatch.setattr(web_app, "get_db", lambda: object())
    monkeypatch.setattr(web_app, "_research_dashboard_series", lambda *a, **k: _RES_EMPTY.copy())
    return calls


def test_dashboard_route_parallel_assembly(monkeypatch):
    calls = _mock_all_sources(monkeypatch)
    c = web_app.app.test_client()
    r = c.get("/api/dashboard/RB?days=60")
    assert r.status_code == 200
    d = r.get_json()
    assert set(calls) == {"price", "inv", "basis"}  # 【关键】三数据源都发起了请求
    assert d["_meta"]["inventory_available"] is True
    assert d["_meta"]["basis_available"] is True
    assert len(d["price"]) == len(_PRICE)
    assert d["_meta"]["price_note"] == ""
    # 关联分析在合成数据上算得出来(8 点 → 有 R 与背离)
    assert d["analysis"]["has_price"] is True
    assert d["analysis"]["divergence"] is not None


def test_dashboard_route_price_failure_is_graceful(monkeypatch):
    _mock_all_sources(monkeypatch, price_fail=True)
    c = web_app.app.test_client()
    r = c.get("/api/dashboard/RB")
    assert r.status_code == 200  # 【关键】价格异常不再 500
    d = r.get_json()
    assert d["_meta"]["price_note"].startswith("DATA_ERROR")
    assert d["price"] == []
    assert d["analysis"]["has_price"] is False
    # 库存/基差不受价格失败拖累,仍正常
    assert d["_meta"]["inventory_available"] is True
    assert d["_meta"]["basis_available"] is True


def test_dashboard_route_inventory_note_passthrough(monkeypatch):
    calls = _mock_all_sources(monkeypatch)
    monkeypatch.setattr(
        web_app, "get_futures_inventory",
        lambda *_a, **_k: (calls.append("inv") or "NO_DATA_AVAILABLE: no inv for WR"),
    )
    c = web_app.app.test_client()
    r = c.get("/api/dashboard/WR")
    assert r.status_code == 200
    d = r.get_json()
    assert d["_meta"]["inventory_available"] is False
    assert "NO_DATA_AVAILABLE" in d["_meta"]["inventory_note"]
    assert d["analysis"]["has_inventory"] is False


def test_dashboard_route_research_series_passthrough(monkeypatch):
    # 研报指标第 4 源:mock 返回 basis overlay + operating_rate standalone →
    # 响应透传 research 结构,meta 带 research_available;不碰真实 dev DB
    _mock_all_sources(monkeypatch)
    _res = {
        "available": True,
        "note": "",
        "overlay": {
            "basis": [
                {"date": "2026-08-03", "value": -35.0, "unit": "元/吨", "note": "",
                 "point_date": "2026-08-01", "source": "华泰期货", "title": "原油早报", "report_id": 47},
            ]
        },
        "standalone": {
            "operating_rate": [
                {"date": "2026-08-03", "value": 82.5, "unit": "%", "note": "装置负荷",
                 "point_date": "2026-08-01", "source": "华泰期货", "title": "原油早报", "report_id": 47},
            ]
        },
    }
    monkeypatch.setattr(web_app, "_research_dashboard_series", lambda *a, **k: _res)
    c = web_app.app.test_client()
    r = c.get("/api/dashboard/RB")
    assert r.status_code == 200
    d = r.get_json()
    assert d["_meta"]["research_available"] is True
    assert d["research"]["available"] is True
    assert d["research"]["overlay"]["basis"][0]["value"] == -35.0
    assert d["research"]["standalone"]["operating_rate"][0]["unit"] == "%"


def test_dashboard_route_research_failure_is_graceful(monkeypatch):
    # 研报指标源抛异常(如 DB 损坏)→ 只降级该项,看板整体不 500
    calls = _mock_all_sources(monkeypatch)
    monkeypatch.setattr(
        web_app, "_research_dashboard_series",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db broken")),
    )
    c = web_app.app.test_client()
    r = c.get("/api/dashboard/RB")
    assert r.status_code == 200
    d = r.get_json()
    assert d["_meta"]["research_available"] is False
    assert d["research"]["available"] is False
    assert "DATA_ERROR" in d["_meta"]["research_note"]
    # 其余三源不受拖累
    assert set(calls) == {"price", "inv", "basis"}
    assert d["_meta"]["price_note"] == ""
