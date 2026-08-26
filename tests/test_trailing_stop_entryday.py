"""Trailing stop must never fire on the entry day (#entryday-bug).

Regression test for worklog/2026-08-13-trailing-stop-entryday-bug.md:
`apply_risk_management` included the entry day in the stop-check window. Because
the entry-day close equals ``entry_px`` and the breakeven clamp raises
``trail_level`` up to ``entry_px``, the check ``px <= trail_level`` was always
True on the entry day -- so ANY trade with a trailing stop was flattened at
pnl=0 on the day it entered. Fixed by scanning the left-open interval
(entry, exit] instead of [entry, exit].
"""

import pytest

from signal_analyzer import apply_risk_management


def _trade(entry="2025-01-02", exit="2025-01-06", direction="long", pnl=5.0):
    return {
        "entry": entry,
        "exit": exit,
        "direction": direction,
        "pnl": pnl,
        "outcome": "win",
    }


def _prices(closes):
    """Build OHLCV-ish rows from a {date: close} dict."""
    return [
        {"date": d, "open": c, "high": c, "low": c, "close": c, "volume": 1}
        for d, c in closes.items()
    ]


@pytest.mark.unit
class TestTrailingStopEntryDay:
    def test_long_not_stopped_on_entry_day(self):
        """Flat/slightly-up price, 8% trailing -- entry day must not flatten it."""
        closes = {
            "2025-01-02": 100.0,
            "2025-01-03": 102.0,
            "2025-01-04": 103.0,
            "2025-01-05": 101.0,
            "2025-01-06": 104.0,
        }
        trades = apply_risk_management(
            [_trade()], _prices(closes), stop_loss_pct=0, trailing_stop_pct=8
        )
        t = trades[0]
        assert "stopped_out" not in t
        assert t["exit"] == "2025-01-06"
        assert t["pnl"] == 5.0

    def test_short_not_stopped_on_entry_day(self):
        """Mirror case: short with 8% trailing, price drifting down -- no entry-day stop."""
        closes = {
            "2025-01-02": 100.0,
            "2025-01-03": 98.0,
            "2025-01-04": 96.0,
            "2025-01-05": 97.0,
            "2025-01-06": 95.0,
        }
        trades = apply_risk_management(
            [_trade(direction="short", pnl=5.0)],
            _prices(closes),
            stop_loss_pct=0,
            trailing_stop_pct=8,
        )
        t = trades[0]
        assert "stopped_out" not in t
        assert t["exit"] == "2025-01-06"

    def test_trailing_stop_still_fires_after_peak(self):
        """Price rises to 112 then dives 15% -- trailing stop must still exit."""
        closes = {
            "2025-01-02": 100.0,
            "2025-01-03": 110.0,
            "2025-01-04": 112.0,
            "2025-01-05": 95.0,
            "2025-01-06": 100.0,
        }
        trades = apply_risk_management(
            [_trade(pnl=12.0)],
            _prices(closes),
            stop_loss_pct=0,
            trailing_stop_pct=8,
        )
        t = trades[0]
        assert t["stopped_out"] is True
        assert t["exit"] == "2025-01-05"
        assert t["pnl"] == -5.0

    def test_fixed_stop_loss_still_fires(self):
        """Fixed 5% stop on the day after entry -- unaffected by the interval change."""
        closes = {
            "2025-01-02": 100.0,
            "2025-01-03": 94.0,
            "2025-01-04": 95.0,
        }
        trades = apply_risk_management(
            [_trade(exit="2025-01-04", pnl=-6.0)],
            _prices(closes),
            stop_loss_pct=5,
            trailing_stop_pct=0,
        )
        t = trades[0]
        assert t["stopped_out"] is True
        assert t["exit"] == "2025-01-03"
        assert t["pnl"] == -6.0

    def test_entry_equals_exit_single_day_trade_untouched(self):
        """A trade that opens and closes the same day has an empty check window."""
        closes = {"2025-01-02": 100.0}
        trades = apply_risk_management(
            [_trade(entry="2025-01-02", exit="2025-01-02", pnl=0.0)],
            _prices(closes),
            stop_loss_pct=5,
            trailing_stop_pct=8,
        )
        t = trades[0]
        assert "stopped_out" not in t
        assert t["exit"] == "2025-01-02"
