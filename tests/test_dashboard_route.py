"""数据看板路由 /api/dashboard/<品种> 装配与降级测试(不联网,全 mock)。

覆盖 2026-09-01 的并行化重构:
  1. 三数据源(价格/库存/基差)都发起请求(并行线程池装配正确);
  2. 任一数据源异常只降级该项,接口整体不 500 —— 尤其价格分支(原来裸调用会 500,
     现改为 price_note + 空序列优雅降级,与库存/基差同口径)。
"""

import web_app

_PRICE = [{"date": f"2026-08-{i+1:02d}", "close": 100.0 + i} for i in range(8)]
_INV = [{"date": f"2026-08-{i+1:02d}", "inventory": 1000.0 + i, "change": 0} for i in range(8)]
_BASIS = [
    {"date": f"2026-08-{i+1:02d}", "spot_price": 100.0, "near_basis": 1.0, "near_basis_rate": 0.5}
    for i in range(8)
]


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
