"""数据仓库「国君数据集」路由测试: /api/gtja/dataset/basis 与 /inventory(不联网,全 mock)。

覆盖 2026-09-03 数据仓库页的基差/仓单数据集浏览链路四态:
  1. 未配 GTJA key → {ok:False, error:未配置...}(conftest 默认置空 key,本用例 monkeypatch
     configured()=False 显式走该分支);
  2. 缺品种参 / start>end → 400;
  3. 拉取成功 → {ok:True, count, rows} 且 date 统一 YYYY-MM-DD;
  4. 源返回 None/空(SC 等无现货指数品种)→ {ok:True, rows:[]} 不给 500。

GTJA 分支语义: 端点内运行时 `from tradingagents.dataflows import gtja_api`,monkeypatch
该模块对象上的 configured()/fetch_* 即可拦截(不触网,依赖 conftest 置空 key 亦可隔离)。
"""

import pandas as pd

import tradingagents.dataflows.gtja_api as gtja_api
import web_app

web_app.app.config["TESTING"] = True


def _basis_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"date": 20260901, "spot_price": 3200.0, "dominant_contract": "rb2610",
             "dominant_contract_price": 3210.0, "dom_basis": -10.0, "dom_basis_rate": -0.003},
            {"date": 20260902, "spot_price": 3195.0, "dominant_contract": "rb2610",
             "dominant_contract_price": 3215.0, "dom_basis": -20.0, "dom_basis_rate": -0.006},
        ]
    )


def _inv_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"date": "2026-08-31", "inventory": 128000.0, "change": 0.0},
            {"date": "2026-09-01", "inventory": 128500.0, "change": 500.0},
        ]
    )


def _on(monkeypatch, **attrs):
    """把 gtja_api.configured / fetch_* 替换成给定桩,返回调用记录列表。"""
    calls = []

    def _cfg():
        return attrs.get("configured", True)

    def _basis(code, start, end):
        calls.append(("basis", code, start, end))
        if attrs.get("basis_fail"):
            raise gtja_api.GTJAError("basis boom")
        return attrs.get("basis_df", _basis_df())

    def _inv(code, start, end):
        calls.append(("inventory", code, start, end))
        return attrs.get("inv_df", _inv_df())

    monkeypatch.setattr(gtja_api, "configured", _cfg)
    monkeypatch.setattr(gtja_api, "fetch_basis_df", _basis)
    monkeypatch.setattr(gtja_api, "fetch_inventory_df", _inv)
    return calls


def test_basis_not_configured(monkeypatch):
    _on(monkeypatch, configured=False)
    d = web_app.app.test_client().get("/api/gtja/dataset/basis?variety=RB").get_json()
    assert d["ok"] is False and "未配置" in d["error"]


def test_basis_missing_variety_is_400():
    r = web_app.app.test_client().get("/api/gtja/dataset/basis")
    assert r.status_code == 400 and "缺少品种参数" in r.get_json()["error"]


def test_basis_start_after_end_is_400(monkeypatch):
    _on(monkeypatch)
    r = web_app.app.test_client().get(
        "/api/gtja/dataset/basis?variety=RB&start=2026-09-02&end=2026-09-01"
    )
    assert r.status_code == 400 and "不能晚于" in r.get_json()["error"]


def test_basis_success_rows_and_date_format(monkeypatch):
    calls = _on(monkeypatch)
    d = web_app.app.test_client().get(
        "/api/gtja/dataset/basis?variety=RB&start=2026-09-01&end=2026-09-30"
    ).get_json()
    assert calls == [("basis", "RB", "2026-09-01", "2026-09-30")]
    assert d["ok"] is True and d["count"] == 2 and d["name"] and d["source"]
    row = d["rows"][0]
    assert row["date"] == "2026-09-01"          # int 20260901 → YYYY-MM-DD
    assert row["spot_price"] == 3200.0
    assert row["dominant_contract"] == "rb2610"
    assert row["dom_basis"] == -10.0
    assert row["dom_basis_rate"] == -0.003
    assert d["rows"][1]["date"] == "2026-09-02"


def test_basis_empty_or_none_returns_ok_empty(monkeypatch):
    _on(monkeypatch, basis_df=None)  # SC 等无现货指数 → fetch 返回 None
    d = web_app.app.test_client().get(
        "/api/gtja/dataset/basis?variety=SC&start=2026-09-01&end=2026-09-30"
    ).get_json()
    assert d["ok"] is True and d["count"] == 0 and d["rows"] == []


def test_inventory_success_explicit_range(monkeypatch):
    calls = _on(monkeypatch)
    d = web_app.app.test_client().get(
        "/api/gtja/dataset/inventory?variety=CU&start=2026-08-01&end=2026-09-01"
    ).get_json()
    assert calls == [("inventory", "CU", "2026-08-01", "2026-09-01")]
    assert d["ok"] is True and d["count"] == 2
    assert d["rows"][0]["date"] == "2026-08-31"
    assert d["rows"][1]["change"] == 500.0


def test_inventory_no_range_passes_none_to_fetch(monkeypatch):
    """两端都空 → web_app 透传 None/None,交 fetch_inventory_df 240 天默认(兼容下游)。"""
    calls = _on(monkeypatch)
    d = web_app.app.test_client().get("/api/gtja/dataset/inventory?variety=RB").get_json()
    assert calls == [("inventory", "RB", None, None)]
    assert d["ok"] is True and d["count"] == 2
