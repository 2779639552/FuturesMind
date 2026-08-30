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


@pytest.mark.unit
class TestBreakevenStopOOHLC:
    """OHLC 触发判定 + 触发价成交(worklog/2026-08-31 红枣案例)。

    旧实现用收盘价判定 + 收盘价成交:止损"晚一天确认",且成交价远离触发线,
    导致空单"保本止损"实际按反弹收盘价成交变成亏损。新实现改用盘中 high/low
    判定触发、按 max/min(触发线, 开盘价) 近似成交价。
    """

    def _row(self, date, open_, high, low, close):
        return {"date": date, "open": open_, "high": high, "low": low, "close": close, "volume": 1}

    def test_short_breakeven_stop_fills_at_entry(self):
        """空单反弹触发保本:按保本线(入场价)成交,pnl=0,而非反弹收盘价。"""
        prices = [
            self._row("2025-01-02", 100, 100, 100, 100),  # 入场日(收盘价=entry)
            self._row("2025-01-03", 99, 99, 98, 98),      # 盈利:peak=98 → 保本线=100
            self._row("2025-01-04", 99, 101, 99, 101),   # 反弹触线 → 按 max(100, open=99)=100 成交
        ]
        trades = apply_risk_management(
            [_trade(direction="short", pnl=1.0, exit="2025-01-04")],
            prices,
            stop_loss_pct=0,
            trailing_stop_pct=8,
        )
        t = trades[0]
        assert t["stopped_out"] is True
        assert t["exit"] == "2025-01-04"
        assert t["pnl"] == 0.0  # 保本,而非按 101 成交亏 1%

    def test_short_gap_open_above_breakeven_fills_at_open(self):
        """空单跳空高开越过保本线:开盘价成交(近似),仍亏但按触发日开盘价而非反弹收盘。"""
        prices = [
            self._row("2025-01-02", 100, 100, 100, 100),
            self._row("2025-01-03", 98, 98, 97, 97),      # 盈利:peak=97 → 保本线=100
            self._row("2025-01-04", 101, 102, 101, 101),  # 高开 101 越过保本线 → 按 max(100, 101)=101 成交
        ]
        trades = apply_risk_management(
            [_trade(direction="short", pnl=3.0, exit="2025-01-04")],
            prices,
            stop_loss_pct=0,
            trailing_stop_pct=8,
        )
        t = trades[0]
        assert t["stopped_out"] is True
        assert t["pnl"] == -1.0  # (100-101)/100

    def test_intraday_low_triggers_trailing_stop(self):
        """收盘价回到保本线上方,但盘中 low 已破线 → 仍触发止损(旧收盘逻辑不会触发)。"""
        prices = [
            self._row("2025-01-02", 100, 100, 100, 100),
            self._row("2025-01-03", 105, 108, 104, 106),  # 峰值 108 → 保本线=100
            self._row("2025-01-04", 105, 106, 99, 105),   # 盘中 99 破保本线,收盘 105 收回
        ]
        trades = apply_risk_management(
            [_trade(pnl=6.0, exit="2025-01-04")],
            prices,
            stop_loss_pct=0,
            trailing_stop_pct=8,
        )
        t = trades[0]
        assert t["stopped_out"] is True
        assert t["exit"] == "2025-01-04"
        assert t["pnl"] == 0.0  # 按 min(100, open=105)=100 成交

    def test_fixed_stop_gap_down_fills_at_open(self):
        """固定止损跳空低开:按开盘价(更差)成交,而非触发线。"""
        prices = [
            self._row("2025-01-02", 100, 100, 100, 100),
            self._row("2025-01-03", 93, 96, 92, 94),  # 开盘 93 已破止损线 95
        ]
        trades = apply_risk_management(
            [_trade(exit="2025-01-03", pnl=-7.0)],
            prices,
            stop_loss_pct=5,
            trailing_stop_pct=0,
        )
        t = trades[0]
        assert t["stopped_out"] is True
        assert t["exit"] == "2025-01-03"
        assert t["pnl"] == -7.0  # (93-100)/100
