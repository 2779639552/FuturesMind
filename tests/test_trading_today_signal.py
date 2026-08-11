"""Today-signal computation for the simulated-trading strategies.

``latest_trading_signal`` computes what each backtest strategy would do on the
data's latest available trading day (prices[-1]["date"]) — 买入/卖出/不动 —
independent of the user's chosen backtest start/end dates. This file pins the
per-strategy decision rules by mocking the data loaders with known shapes and
asserting the resulting action (buy/sell/hold), plus the multi-sub-strategy
``today_signals`` key structure and the None guards (empty variety / missing
data / insufficient data).
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from signal_analyzer import latest_trading_signal


def _dates(n: int) -> list[str]:
    base = datetime(2025, 1, 1)
    return [(base + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]


def _mock_price(n: int = 60, mode: str = "up", breakout: str | None = None) -> dict:
    """OHLCV series. mode: up=steady rise, down=steady fall, flat=flat closes.

    breakout="high"/"low" pushes the last close above all prior highs /
    below all prior lows (for the Donchian channel breakout rules).
    """
    dates = _dates(n)
    if mode == "up":
        closes = [100 + i * 1.5 for i in range(n)]
    elif mode == "down":
        closes = [200 - i * 1.5 for i in range(n)]
    else:
        closes = [100.0] * n
    prices = [
        {
            "date": d,
            "open": closes[i],
            "high": closes[i] + 1,
            "low": closes[i] - 1,
            "close": closes[i],
            "volume": 1000 + i * 10,
        }
        for i, d in enumerate(dates)
    ]
    if breakout == "high":
        prices[-1]["close"] = max(p["high"] for p in prices[:-1]) + 5
        prices[-1]["high"] = prices[-1]["close"]
    elif breakout == "low":
        prices[-1]["close"] = min(p["low"] for p in prices[:-1]) - 5
        prices[-1]["low"] = prices[-1]["close"]
    return {"prices": prices}


def _mock_sentiment(n: int = 60, last_score: float = 0.5) -> dict:
    """Daily sentiment series; only the last day carries the given score."""
    dates = _dates(n)
    return {
        "data": {
            "daily_series": [
                {"date": d, "avg_score": last_score if i == n - 1 else 0.1}
                for i, d in enumerate(dates)
            ]
        }
    }


@pytest.mark.unit
class TestTradingTodaySignal:
    @patch("signal_analyzer._load_price")
    @patch("signal_analyzer._load_sentiment")
    def test_fixed_threshold_buy_sell_hold(self, mock_sent, mock_px):
        mock_px.side_effect = lambda v: _mock_price(30)
        mock_sent.side_effect = lambda v: _mock_sentiment(30, last_score=0.5)
        assert latest_trading_signal("fixed", variety="RB")["today_signal"]["action"] == "buy"

        mock_sent.side_effect = lambda v: _mock_sentiment(30, last_score=-0.5)
        assert latest_trading_signal("fixed", variety="RB")["today_signal"]["action"] == "sell"

        mock_sent.side_effect = lambda v: _mock_sentiment(30, last_score=0.1)
        assert latest_trading_signal("fixed", variety="RB")["today_signal"]["action"] == "hold"

    @patch("signal_analyzer._load_price")
    @patch("signal_analyzer._load_sentiment")
    def test_trailing_same_entry_rule(self, mock_sent, mock_px):
        mock_px.side_effect = lambda v: _mock_price(30)
        mock_sent.side_effect = lambda v: _mock_sentiment(30, last_score=0.5)
        assert latest_trading_signal("trailing", variety="RB")["today_signal"]["action"] == "buy"

        mock_sent.side_effect = lambda v: _mock_sentiment(30, last_score=-0.5)
        assert latest_trading_signal("trailing", variety="RB")["today_signal"]["action"] == "sell"

    @patch("signal_analyzer._load_price")
    def test_momentum_ret_signal(self, mock_px):
        mock_px.side_effect = lambda v: _mock_price(30, mode="up")
        assert latest_trading_signal("momentum", variety="RB")["today_signal"]["action"] == "buy"

        mock_px.side_effect = lambda v: _mock_price(30, mode="down")
        assert latest_trading_signal("momentum", variety="RB")["today_signal"]["action"] == "sell"

        mock_px.side_effect = lambda v: _mock_price(30, mode="flat")
        assert latest_trading_signal("momentum", variety="RB")["today_signal"]["action"] == "hold"

    @patch("signal_analyzer._load_price")
    def test_donchian_breakout(self, mock_px):
        mock_px.side_effect = lambda v: _mock_price(30, mode="up", breakout="high")
        sig = latest_trading_signal("donchian", variety="RB")["today_signal"]
        assert sig["action"] == "buy"

        mock_px.side_effect = lambda v: _mock_price(30, mode="down", breakout="low")
        sig = latest_trading_signal("donchian", variety="RB")["today_signal"]
        assert sig["action"] == "sell"

    @patch("signal_analyzer._load_price")
    @patch("signal_analyzer._load_trends")
    @patch("signal_analyzer._load_sentiment")
    def test_contrarian_divergence(self, mock_sent, mock_trends, mock_px):
        # 价格下跌 + 情绪看多 → 主策略逆向买入;子策略 consensus 顺势 → 不动
        mock_px.side_effect = lambda v: _mock_price(30, mode="down")
        mock_trends.side_effect = lambda v: _mock_sentiment(30, last_score=0.5)
        mock_sent.side_effect = lambda v: None
        r = latest_trading_signal("contrarian", variety="RB")
        assert set(r["today_signals"].keys()) == {"contrarian", "consensus"}
        assert r["today_signals"]["contrarian"]["action"] == "buy"
        assert r["today_signals"]["consensus"]["action"] == "hold"

    @patch("signal_analyzer._load_price")
    @patch("signal_analyzer._load_trends")
    @patch("signal_analyzer._load_sentiment")
    def test_adaptive_sent_decision(self, mock_sent, mock_trends, mock_px):
        # 价格涨 + 情绪看空(背离)→ 卖出
        mock_px.side_effect = lambda v: _mock_price(30, mode="up")
        mock_trends.side_effect = lambda v: _mock_sentiment(30, last_score=-0.5)
        mock_sent.side_effect = lambda v: None
        assert (
            latest_trading_signal("adaptive_sent", variety="RB")["today_signal"]["action"] == "sell"
        )

        # 强趋势 + 情绪看多 → 顺势买入
        mock_trends.side_effect = lambda v: _mock_sentiment(30, last_score=0.5)
        assert (
            latest_trading_signal("adaptive_sent", variety="RB")["today_signal"]["action"] == "buy"
        )

    @patch("signal_analyzer._load_price")
    @patch("signal_analyzer._load_trends")
    @patch("signal_analyzer._load_sentiment")
    def test_momentum_ad_subkeys(self, mock_sent, mock_trends, mock_px):
        # 价格涨 + 情绪看空(背离)→ adaptive 卖出;momentum_baseline 纯动量 → 买入
        mock_px.side_effect = lambda v: _mock_price(30, mode="up")
        mock_trends.side_effect = lambda v: _mock_sentiment(30, last_score=-0.5)
        mock_sent.side_effect = lambda v: None
        r = latest_trading_signal("momentum_ad", variety="RB")
        assert set(r["today_signals"].keys()) == {"adaptive", "momentum_baseline"}
        assert r["today_signal"]["action"] == "sell"
        assert r["today_signals"]["adaptive"]["action"] == "sell"
        assert r["today_signals"]["momentum_baseline"]["action"] == "buy"

    @patch("signal_analyzer._load_price")
    @patch("signal_analyzer._load_trends")
    @patch("signal_analyzer._load_sentiment")
    def test_compare_fund_and_combo(self, mock_sent, mock_trends, mock_px):
        mock_px.side_effect = lambda v: _mock_price(30, mode="up")
        mock_sent.side_effect = lambda v: None

        # 上升价格 + 情绪看多 → fund_only 买入 且 combo 买入(主信号 = combo)
        mock_trends.side_effect = lambda v: _mock_sentiment(30, last_score=0.5)
        r = latest_trading_signal("compare", variety="RB")
        assert set(r["today_signals"].keys()) == {"fund_only", "fund_plus_sentiment"}
        assert r["today_signals"]["fund_only"]["action"] == "buy"
        assert r["today_signal"]["action"] == "buy"

        # 上升价格 + 情绪看空 → fund_only 仍买入;combo 因情绪反向 → 不动
        mock_trends.side_effect = lambda v: _mock_sentiment(30, last_score=-0.5)
        r2 = latest_trading_signal("compare", variety="RB")
        assert r2["today_signals"]["fund_only"]["action"] == "buy"
        assert r2["today_signals"]["fund_plus_sentiment"]["action"] == "hold"

    @patch("signal_analyzer._load_price")
    @patch("signal_analyzer._load_sentiment")
    def test_strength_pct_normalized(self, mock_sent, mock_px):
        # 情绪类策略:strength_pct = |情绪分|/1.0,直接反映得分
        mock_px.side_effect = lambda v: _mock_price(30)
        mock_sent.side_effect = lambda v: _mock_sentiment(30, last_score=0.8)
        sig = latest_trading_signal("fixed", variety="RB")["today_signal"]
        assert sig["strength"] == pytest.approx(0.8)
        assert sig["strength_pct"] == pytest.approx(0.8)

        # 动量类策略:strength_pct = |收益率|/5%(封顶 1.0),与情绪分同尺度
        mock_px.side_effect = lambda v: _mock_price(
            30, mode="up"
        )  # 每期 +1.5 元/起点100 → 5日收益 >5%
        mock_sent.side_effect = lambda v: None
        sig = latest_trading_signal("momentum", variety="RB")["today_signal"]
        assert 0 < sig["strength_pct"] <= 1.0
        # _mock_price(mode="up") 5日收益 = (100+29*1.5-100-24*1.5)/(100+24*1.5) ≈ 6.6% > 5% → 封顶 1.0
        assert sig["strength_pct"] == pytest.approx(1.0)

        # 唐奇安突破:突破幅度/5%(封顶)
        mock_px.side_effect = lambda v: _mock_price(30, mode="flat", breakout="high")
        sig = latest_trading_signal("donchian", variety="RB")["today_signal"]
        assert 0 < sig["strength_pct"] <= 1.0

    def test_empty_variety_returns_none(self):
        assert latest_trading_signal("fixed", variety="") is None

    @patch("signal_analyzer._load_price")
    def test_no_price_data_returns_none(self, mock_px):
        mock_px.side_effect = lambda v: None
        assert latest_trading_signal("momentum", variety="RB") is None

    @patch("signal_analyzer._load_price")
    def test_insufficient_data_returns_none(self, mock_px):
        mock_px.side_effect = lambda v: _mock_price(3)
        assert latest_trading_signal("momentum", variety="RB") is None

    @patch("signal_analyzer._load_price")
    @patch("signal_analyzer._load_trends")
    @patch("signal_analyzer._load_sentiment")
    def test_multi_sub_primary_matches(self, mock_sent, mock_trends, mock_px):
        mock_px.side_effect = lambda v: _mock_price(30, mode="up")
        mock_trends.side_effect = lambda v: _mock_sentiment(30, last_score=-0.5)
        mock_sent.side_effect = lambda v: None
        for strat, primary in [
            ("contrarian", "contrarian"),
            ("adaptive_sent", "adaptive"),
            ("momentum_ad", "adaptive"),
            ("compare", "fund_plus_sentiment"),
        ]:
            r = latest_trading_signal(strat, variety="RB")
            assert r is not None
            assert r["today_signal"]["action"] == r["today_signals"][primary]["action"]
