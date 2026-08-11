"""Trading backtest date filtering — start_date/end_date must actually filter.

Regression test for the bug where the "开始日期/结束日期" controls in the
simulated-trading tab had no effect: the frontend never sent the dates, the
API endpoints dropped them, and the strategy functions only had ``start_date``
with no ``end_date`` at all. All three layers are now wired end-to-end; these
tests pin the strategy layer by mocking the data loaders with a known date
range and asserting that narrowing the window changes which trades appear.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from signal_analyzer import (
    run_momentum_strategy,
    run_simulated_trading,
    run_strategy_comparison,
    run_trailing_strategy,
)

D0 = "2025-01-01"


def _dates(n: int) -> list[str]:
    base = datetime(2025, 1, 1)
    return [(base + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]


def _mock_price(n: int = 60) -> dict:
    dates = _dates(n)
    return {
        "prices": [
            {
                "date": d,
                "open": 100 + i,
                "high": 101 + i,
                "low": 99 + i,
                "close": 100 + i * 1.5,
                "volume": 1000 + i * 10,
            }
            for i, d in enumerate(dates)
        ]
    }


def _mock_sentiment(n: int = 60) -> dict:
    dates = _dates(n)
    return {
        "data": {
            "daily_series": [
                {"date": d, "avg_score": 0.5 if i % 2 == 0 else -0.5} for i, d in enumerate(dates)
            ]
        }
    }


@pytest.mark.unit
class TestTradingBacktestDateFilter:
    @patch("signal_analyzer._load_price")
    @patch("signal_analyzer._load_sentiment")
    def test_fixed_horizon_end_date_limits_trades(self, mock_sent, mock_px):
        mock_px.side_effect = lambda v: _mock_price()
        mock_sent.side_effect = lambda v: _mock_sentiment()

        full = run_simulated_trading(variety="RB", horizon=3, signal_threshold=0.2)
        # end_date inside the window → strictly fewer entry dates after the cutoff
        cut = run_simulated_trading(
            variety="RB", horizon=3, signal_threshold=0.2, end_date="2025-02-01"
        )

        assert full["total_trades"] > 0
        assert cut["total_trades"] < full["total_trades"]
        for t in cut["recent_trades"]:
            assert t["entry"] <= "2025-02-01"

    @patch("signal_analyzer._load_price")
    @patch("signal_analyzer._load_sentiment")
    def test_fixed_horizon_start_date_limits_trades(self, mock_sent, mock_px):
        mock_px.side_effect = lambda v: _mock_price()
        mock_sent.side_effect = lambda v: _mock_sentiment()

        cut = run_simulated_trading(
            variety="RB", horizon=3, signal_threshold=0.2, start_date="2025-02-15"
        )
        for t in cut["recent_trades"]:
            assert t["entry"] >= "2025-02-15"

    @patch("signal_analyzer._load_price")
    def test_momentum_end_date_limits_trades(self, mock_px):
        mock_px.side_effect = lambda v: _mock_price()

        full = run_momentum_strategy(variety="RB", lookback=5, hold=3)
        cut = run_momentum_strategy(variety="RB", lookback=5, hold=3, end_date="2025-02-01")
        assert full["total_trades"] > 0
        assert cut["total_trades"] < full["total_trades"]
        for t in cut["recent_trades"]:
            assert t["entry"] <= "2025-02-01"

    @patch("signal_analyzer._load_price")
    @patch("signal_analyzer._load_sentiment")
    def test_trailing_end_date_limits_trades(self, mock_sent, mock_px):
        mock_px.side_effect = lambda v: _mock_price()
        mock_sent.side_effect = lambda v: _mock_sentiment()

        full = run_trailing_strategy(variety="RB", signal_threshold=0.2, max_holding=5)
        cut = run_trailing_strategy(
            variety="RB",
            signal_threshold=0.2,
            max_holding=5,
            end_date="2025-02-01",
        )
        assert full["total_trades"] > 0
        assert cut["total_trades"] < full["total_trades"]
        for t in cut["recent_trades"]:
            assert t["entry"] <= "2025-02-01"

    @patch("signal_analyzer._load_price")
    @patch("signal_analyzer._load_trends")
    def test_compare_end_date_limits_trades(self, mock_trends, mock_px):
        mock_px.side_effect = lambda v: _mock_price()
        mock_trends.side_effect = lambda v: _mock_sentiment()

        full = run_strategy_comparison(variety="RB", horizon=5)
        cut = run_strategy_comparison(variety="RB", horizon=5, end_date="2025-02-01")

        def _trades(d):
            st = d.get("stats", {})
            return st.get("fund_only", {}).get("trades", 0) + st.get("fund_plus_sentiment", {}).get(
                "trades", 0
            )

        assert _trades(full) > 0
        assert _trades(cut) < _trades(full)
