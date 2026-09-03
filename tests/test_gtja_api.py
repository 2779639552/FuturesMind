"""GTJA(国泰君安)提供者纯函数测试: 归一化映射 + 空→None + 观点 score 归一。

【网络】不触网: normalize_* 是纯函数;fetch_viewpoint 用 monkeypatch 替换
        configured()/_request()。因此无需配 GTJA key,CI/无网也稳定通过。
"""

from tradingagents.dataflows import gtja_api  # 【调用包】被测模块


# ---------------------------------------------------------------------------
# normalize_basis_rows: 基差行 → commodity_futures 英文 schema
# ---------------------------------------------------------------------------
def test_normalize_basis_rows_maps_smm_and_aliases():
    """同一天多现货指数(SMM/长江)→ 保留 SMM;dominant_*/near_* 同值别名;date=YYYYMMDD int。"""
    rows = [
        {"reportDate": "2026-09-01", "spotPrice": 68000, "futuresPrice": 68200,
         "basisValue": -200, "basisPremiumRate": -0.00294, "contractCode": "cu2609",
         "spotIndexName": "长江有色"},
        {"reportDate": "2026-09-01", "spotPrice": 67950, "futuresPrice": 68200,
         "basisValue": -250, "basisPremiumRate": -0.00367, "contractCode": "cu2609",
         "spotIndexName": "SMM 1#电解铜"},
        {"reportDate": "2026-09-02", "spotPrice": 68100, "futuresPrice": 68300,
         "basisValue": -200, "basisPremiumRate": -0.00293, "contractCode": "cu2609",
         "spotIndexName": "SMM 1#电解铜"},
    ]
    df = gtja_api.normalize_basis_rows(rows)
    assert df is not None
    assert list(df.columns) == gtja_api._BASIS_COLUMNS
    assert len(df) == 2
    assert list(df["date"]) == [20260901, 20260902]          # 无横杠 int,升序
    assert list(df["spot_price"]) == [67950.0, 68100.0]      # 09-01 取 SMM 而非长江
    # 近月/主力两套同值别名(国君单一参考合约)
    assert df["dominant_contract"].equals(df["near_contract"])
    assert df["dom_basis"].equals(df["near_basis"])
    assert df["dom_basis_rate"].equals(df["near_basis_rate"])
    assert df.loc[0, "dom_basis"] == -250.0


def test_normalize_basis_rows_empty_or_all_invalid_returns_none():
    """无行 / 行缺 spotPrice → None(调用方回退 AKShare),绝不返回空壳 DataFrame。"""
    assert gtja_api.normalize_basis_rows([]) is None
    assert gtja_api.normalize_basis_rows([{"reportDate": "2026-09-01"}]) is None
    assert gtja_api.normalize_basis_rows([{"reportDate": "2026-09-01", "spotPrice": None}]) is None


# ---------------------------------------------------------------------------
# normalize_inventory_rows: 仓单行 → date/inventory/change
# ---------------------------------------------------------------------------
def test_normalize_inventory_rows_maps_and_diffs():
    """onWarrant(字符串)→inventory float;date 保留 YYYY-MM-DD;change=相邻差值。"""
    rows = [
        {"tradingDay": "2026-08-31", "onWarrant": "128000"},
        {"tradingDay": "2026-09-01", "onWarrant": "128500"},
        {"tradingDay": "2026-09-02", "onWarrant": "129662"},
    ]
    df = gtja_api.normalize_inventory_rows(rows)
    assert df is not None
    assert list(df["date"]) == ["2026-08-31", "2026-09-01", "2026-09-02"]
    assert list(df["inventory"]) == [128000.0, 128500.0, 129662.0]
    assert list(df["change"]) == [0.0, 500.0, 1162.0]


def test_normalize_inventory_rows_empty_returns_none():
    assert gtja_api.normalize_inventory_rows([]) is None
    assert gtja_api.normalize_inventory_rows([{"tradingDay": "2026-09-01", "onWarrant": "x"}]) is None


# ---------------------------------------------------------------------------
# fetch_viewpoint: score 归一 + 未配置/失败降级(2026-09-03 起只拉周度,晨报下线)
# ---------------------------------------------------------------------------
def test_fetch_viewpoint_coerces_string_score(monkeypatch):
    """周度接口偶发把 score 回成字符串"0" → 统一 int,前端强度/着色才稳定。"""
    monkeypatch.setattr(gtja_api, "configured", lambda: True)
    fake = {"score": "0", "reason": "市场谨慎", "reportDate": "2026-09-03",
            "codeName": "铜", "code": "CU"}

    def fake_request(endpoint, body):
        return [dict(fake)]

    monkeypatch.setattr(gtja_api, "_request", fake_request)
    res = gtja_api.fetch_viewpoint("CU")
    assert res["error"] is None
    assert res["weekly"]["score"] == 0 and isinstance(res["weekly"]["score"], int)
    assert "daily" not in res  # 晨报已下线,payload 只有 weekly


def test_fetch_viewpoint_not_configured(monkeypatch):
    """未配 key → weekly None + error 提示(绝不请求网络)。"""
    monkeypatch.setattr(gtja_api, "configured", lambda: False)
    res = gtja_api.fetch_viewpoint("CU")
    assert res["weekly"] is None and res["error"]


def test_fetch_viewpoint_failure(monkeypatch):
    """周度端点失败 → weekly None + error 记首错(UI 可区分无信号/失败)。"""
    monkeypatch.setattr(gtja_api, "configured", lambda: True)

    def fake_request(endpoint, body):
        raise gtja_api.GTJAError("周度接口不可用")

    monkeypatch.setattr(gtja_api, "_request", fake_request)
    res = gtja_api.fetch_viewpoint("CU")
    assert res["weekly"] is None
    assert res["error"] and "周度接口不可用" in res["error"]


# ---------------------------------------------------------------------------
# fetch_inventory_df: 显式区间覆盖 / 默认近 240 天窗口(2026-09-03 数据仓库数据集)
# ---------------------------------------------------------------------------
def test_fetch_inventory_df_defaults_to_240d_window(monkeypatch):
    """两端都空 → 请求体用 [今天-240天, 今天] 默认窗口(兼容 commodity_futures 下游)。"""
    from datetime import date, timedelta

    monkeypatch.setattr(gtja_api, "configured", lambda: True)
    seen = {}

    def fake_request(endpoint, body):
        seen["body"] = body
        return [{"tradingDay": "2026-09-01", "onWarrant": "100"}]

    monkeypatch.setattr(gtja_api, "_request", fake_request)
    res = gtja_api.fetch_inventory_df("CU")
    assert res is not None
    b = seen["body"]
    assert b["code"] == "CU"
    assert b["endReportDate"] == date.today().strftime("%Y-%m-%d")
    assert b["startReportDate"] == (date.today() - timedelta(days=240)).strftime("%Y-%m-%d")


def test_fetch_inventory_df_explicit_range_passed_through(monkeypatch):
    """显式区间 → 请求体原样用该区间,不套默认窗口。"""
    monkeypatch.setattr(gtja_api, "configured", lambda: True)
    seen = {}

    def fake_request(endpoint, body):
        seen["body"] = body
        return [{"tradingDay": "2026-08-01", "onWarrant": "50"}]

    monkeypatch.setattr(gtja_api, "_request", fake_request)
    res = gtja_api.fetch_inventory_df("RB", "2026-07-01", "2026-08-31")
    assert res is not None
    b = seen["body"]
    assert b["startReportDate"] == "2026-07-01" and b["endReportDate"] == "2026-08-31"


def test_fetch_inventory_df_start_after_end_returns_none(monkeypatch):
    """start > end → 直接 None 且不发请求(底层与路由双重兜底,前端传反不炸)。"""
    monkeypatch.setattr(gtja_api, "configured", lambda: True)
    called = []

    def fake_request(endpoint, body):
        called.append(body)
        return []

    monkeypatch.setattr(gtja_api, "_request", fake_request)
    assert gtja_api.fetch_inventory_df("RB", "2026-09-02", "2026-09-01") is None
    assert called == []
