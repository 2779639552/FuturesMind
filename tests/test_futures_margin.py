"""盘面利润模块 futures_margin 测试(不联网,全部 mock get_futures_price)。

覆盖 2026-09-08 的盘面利润功能:
  1. 配方数学:margin = Σ coef×腿收盘(RB − 1.6×I − 0.5×J);
  2. 日期对齐:各腿 inner-join,任一腿缺价的日子不参与;
  3. 无配方/腿缺数据 → None(宁可不给不给半截);
  4. 分位/wow/窗口统计;
  5. 文本出口:公式行、分位描述、哨兵文本;
  6. 工具注册:interface.VENDOR_METHODS 与 commodity_futures_tools 均可路由。
"""

import pandas as pd
import pytest

from tradingagents.dataflows import commodity_futures as cf, futures_margin as fm


def _csv(dates, closes):
    return pd.DataFrame({"date": dates, "close": closes}).to_csv(index=False)


# 【变量】八日对齐窗口(交易日近似):2026-08-03 ~ 2026-08-12 剔除周末
_DATES = [
    "2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06",
    "2026-08-07", "2026-08-10", "2026-08-11", "2026-08-12",
]


@pytest.fixture
def fake_prices(monkeypatch):
    """把 commodity_futures.get_futures_price 替换为内存价目表:code → CSV 文本。

    【关键】futures_margin._fetch_close_series 懒导入 `from .commodity_futures
    import get_futures_price`,call 时才解析,故 monkeypatch 模块属性即可生效。
    """
    table = {
        "RB": _csv(_DATES, [5000.0] * 8),
        "I": _csv(_DATES, [800.0] * 8),
        "J": _csv(_DATES, [2000.0] * 8),
        "HC": _csv(_DATES, [4200.0] * 8),
        # 注意:故意不放 JM —— 让"焦化利润缺焦煤腿"的降级用例可测
    }
    seen = []

    def _fake(code, start_date="", end_date=""):
        seen.append(code)
        return table.get(code, "NO_DATA_AVAILABLE: not covered")

    monkeypatch.setattr(cf, "get_futures_price", _fake)
    return table, seen


def test_margin_formula_text():
    assert fm.margin_formula_text("RB") == (
        "螺纹钢盘面利润 = RB - 1.6*I - 0.5*J"
        "(1吨螺纹≈1.6吨铁矿石+0.5吨焦炭(长流程简化配比,未含合金/加工费))"
    )
    assert fm.margin_formula_text("J") == (
        "焦化盘面利润 = J - 1.3*JM"
        "(1吨焦炭≈1.3吨焦煤(焦比简化,未含焦炉煤气等化产收益))"
    )
    assert fm.margin_formula_text("rb") is not None  # 大小写不敏感
    assert fm.margin_formula_text("CU") is None


def test_compute_series_math(fake_prices):
    """RB=5000, I=800, J=2000 → margin = 5000 - 1.6*800 - 0.5*2000 = 2720。"""
    r = fm.compute_margin_series("RB", "2026-08-01", "2026-08-31")
    assert r is not None
    assert r["code"] == "RB"
    assert len(r["points"]) == 8
    assert all(abs(p["value"] - 2720.0) < 1e-9 for p in r["points"])
    assert r["latest"] == 2720.0
    assert r["latest_date"] == "2026-08-12"
    assert r["legs"] == {"RB": 5000.0, "I": 800.0, "J": 2000.0}
    # 常数序列:分位 1.0(≤最新值占比 100%),wow=0
    assert r["pct_rank"] == 1.0
    assert r["wow"] == 0.0
    assert r["mean"] == r["min"] == r["max"] == 2720.0


def test_compute_series_alignment_drops_missing_days(monkeypatch):
    """I 比 RB 多出最后一天(或 RB 缺最后一天)→ inner join 后只留共同日期。"""
    rb = _csv(_DATES[:-1], [5000.0] * 7)  # RB 缺 08-12
    i = _csv(_DATES, [800.0] * 8)
    j = _csv(_DATES, [2000.0] * 8)
    monkeypatch.setattr(
        cf, "get_futures_price",
        lambda code, *a, **k: {"RB": rb, "I": i, "J": j}[code],
    )
    r = fm.compute_margin_series("RB", "2026-08-01", "2026-08-31")
    assert r is not None
    assert len(r["points"]) == 7
    assert r["latest_date"] == "2026-08-11"
    assert r["legs"]["RB"] == 5000.0


def test_no_formula_returns_none(fake_prices):
    assert fm.compute_margin_series("CU", "2026-08-01", "2026-08-31") is None


def test_missing_leg_returns_none(fake_prices):
    """CU 不在价目表(哨兵文本)→ 腿缺失 → 整体 None,不给半截利润。"""
    assert fm.compute_margin_series("J", "2026-08-01", "2026-08-31") is None


def test_leg_fetch_exception_returns_none(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(cf, "get_futures_price", _boom)
    assert fm.compute_margin_series("RB", "2026-08-01", "2026-08-31") is None


def test_percentile_and_wow(monkeypatch):
    """构造递增序列验证分位=最新占比、wow=latest−5 个交易日前。"""
    closes = [3000.0 + 50 * i for i in range(8)]  # 3000..3350 递增
    table = {
        "RB": _csv(_DATES, closes),
        "I": _csv(_DATES, [800.0] * 8),
        "J": _csv(_DATES, [2000.0] * 8),
    }
    monkeypatch.setattr(cf, "get_futures_price", lambda c, *a, **k: table[c])
    r = fm.compute_margin_series("RB", "2026-08-01", "2026-08-31")
    latest = r["latest"]
    assert r["pct_rank"] == pytest.approx(sum(1 for c in r["points"] if c["value"] <= latest) / 8)
    # margin 亦递增:wow = margin[08-12] − margin[08-05] = 50*5 = 250
    assert r["wow"] == pytest.approx(250.0)
    assert r["min"] == pytest.approx(r["points"][0]["value"])
    assert r["max"] == pytest.approx(latest)


def test_short_window_wow_is_none(monkeypatch):
    """窗口不足 6 个点 → wow 为 None(前端显示 —)。"""
    dates = _DATES[:4]
    table = {
        "RB": _csv(dates, [5000.0] * 4),
        "I": _csv(dates, [800.0] * 4),
        "J": _csv(dates, [2000.0] * 4),
    }
    monkeypatch.setattr(cf, "get_futures_price", lambda c, *a, **k: table[c])
    r = fm.compute_margin_series("RB", "2026-08-01", "2026-08-31")
    assert r is not None and r["wow"] is None


def test_format_margin_text_block(fake_prices):
    text = fm.format_margin_text("RB")
    assert text.startswith("# FUTURES MARGIN(螺纹钢盘面利润)")
    assert "最新值: 2720.00 元/吨 (2026-08-12)" in text
    assert "历史分位: 100%" in text
    assert "RB=5000" in text
    assert "解读提示" in text


def test_format_margin_text_low_percentile(monkeypatch):
    """递减序列 → 最新值为极低分位(<10%)。"""
    closes = [3350.0 - 50 * i for i in range(8)]
    table = {
        "RB": _csv(_DATES, closes),
        "I": _csv(_DATES, [800.0] * 8),
        "J": _csv(_DATES, [2000.0] * 8),
    }
    monkeypatch.setattr(cf, "get_futures_price", lambda c, *a, **k: table[c])
    text = fm.format_margin_text("RB")
    assert "历史分位: 12%" in text  # 8 点中只有 1 点 ≤ 最新 → 1/8=12.5%(.0% 半数取偶)
    assert "偏低(<25%)" in text


def test_format_margin_text_sentinels(fake_prices):
    assert fm.format_margin_text("CU").startswith("MARGIN_NO_FORMULA: CU")
    assert "get_futures_price" in fm.format_margin_text("CU")  # 降级指引
    assert fm.format_margin_text("").startswith("MARGIN_NO_FORMULA")
    # J 的腿 JM 不在价目表 → 数据缺失哨兵
    text = fm.format_margin_text("J", "2026-08-01", "2026-08-31")
    assert text.startswith("NO_DATA_AVAILABLE:")
    assert "配方腿" in text


def test_tool_registration():
    """工具链三处注册齐全:@tool 包装、VENDOR_METHODS 路由、工具清单引用。"""
    from tradingagents.agents.utils import commodity_futures_tools as tools
    from tradingagents.dataflows import interface

    assert "get_futures_margin" in interface.VENDOR_METHODS
    assert "get_futures_margin" in interface.TOOLS_CATEGORIES["futures_margin"]["tools"]
    # 供应商实现指向 futures_margin.get_futures_margin(普通函数,非 dict)
    assert callable(interface.VENDOR_METHODS["get_futures_margin"]["commodity_futures"])
    # @tool 包装对象存在且名字正确(Agent 工具面板里的"按钮")
    assert getattr(tools.get_futures_margin, "name", "") == "get_futures_margin"
