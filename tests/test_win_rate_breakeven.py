"""胜率口径统一:打平(breakeven)不计入胜率分子分母。

背景(worklog/2026-09-01-纯碱):风控(止损/移动止盈)把 pnl 落在 ±0.15% 的交易
判为 breakeven(保本止损按入场价成交,pnl=0)。旧口径 win_rate = win/全部交易
把打平计入分母 → 纯碱 8 月大量保本止损出现"胜率 0% 但收益 0.00%"的矛盾展示。

本测试锁定新口径:
- ``_win_rate``: 胜率 = 盈利 / (盈利+亏损), 打平不计入分子也不计入分母;
  无胜负样本(全打平或空列表)返回 0.0(前端据此显示"无有效胜负样本",而非误导性 0%)。
- ``_breakeven_count``: 打平笔数单独统计, 供前端"打平 N 笔"标注。
- 各策略返回 dict 的 win_rate / breakeven_count 字段与 recent_trades 口径一致。
- apply_risk_management 保本止损 → 全 breakeven → 胜率 0 但视为"无有效样本"。
"""

from unittest.mock import patch

import pytest

from signal_analyzer import (
    TradeRecord,
    _breakeven_count,
    _win_rate,
    apply_risk_management,
)


def _trade(direction="long", pnl=1.0, entry_="2025-01-02", exit_="2025-01-04"):
    """构造一笔 dict 交易(与 signal_analyzer._trade 输出同形)。"""
    return {
        "entry": entry_,
        "exit": exit_,
        "direction": direction,
        "pnl": pnl,
        "outcome": "win" if pnl > 0.15 else ("loss" if pnl < -0.15 else "breakeven"),
    }


def _rec(entry_date, direction, pnl_pct):
    """构造一条 TradeRecord(dataclass)交易。"""
    return TradeRecord(
        variety="RB",
        entry_date=entry_date,
        exit_date=entry_date,
        direction=direction,
        signal_value=0.0,
        entry_price=100.0,
        exit_price=100.0,
        pnl_pct=pnl_pct,
        outcome="win" if pnl_pct > 0.15 else ("loss" if pnl_pct < -0.15 else "breakeven"),
        horizon=1,
    )


@pytest.mark.unit
class TestWinRate:
    """_win_rate: 打平排除、全打平/空列表归 0、兼容 dict 与 dataclass。"""

    def test_excludes_breakeven_from_denominator(self):
        # 1 赢 / 1 亏 / 1 打平 → 旧口径 0.333, 新口径 0.5
        trades = [_trade(pnl=1.0), _trade(pnl=-1.0), _trade(pnl=0.0)]
        assert _win_rate(trades) == 0.5

    def test_all_breakeven_is_zero(self):
        # 全打平: 无有效胜负样本 → 0(前端显示"无有效胜负", 而非误导性 0%)
        trades = [_trade(pnl=0.0), _trade(pnl=-0.09)]
        assert _win_rate(trades) == 0.0

    def test_empty_list_is_zero(self):
        assert _win_rate([]) == 0.0

    def test_no_breakeven_same_as_before(self):
        # 无打平时新口径与旧口径一致: 2/3
        trades = [_trade(pnl=1.0), _trade(pnl=2.0), _trade(pnl=-1.0)]
        assert _win_rate(trades) == pytest.approx(2 / 3, abs=0.001)

    def test_dataclass_records_accepted(self):
        recs = [
            _rec("2025-01-02", "long", 1.0),
            _rec("2025-01-03", "long", -1.0),
            _rec("2025-01-04", "short", 0.0),
        ]
        assert _win_rate(recs) == 0.5

    def test_breakeven_count_counts_only_breakeven(self):
        trades = [_trade(pnl=1.0), _trade(pnl=0.0), _trade(pnl=-0.05), _trade(pnl=-1.0)]
        assert _breakeven_count(trades) == 2


@pytest.mark.unit
class TestApplyRiskBreakeven:
    """apply_risk_management 保本止损产出 breakeven 后, 胜率口径正确。"""

    def _row(self, date, open_, high, low, close):
        return {"date": date, "open": open_, "high": high, "low": low, "close": close, "volume": 1}

    def test_all_breakeven_stops_give_zero_winrate(self):
        # 两笔交易各自独立日期窗口, 保本止损按入场价平仓 → 都判 breakeven。
        # 多单: 冲高 103 后回落到保本线 100 → 按 min(100, open=101)=100 平仓 pnl=0;
        # 空单: 探低 98 后反弹到保本线 100 → 按 max(100, open=99)=100 平仓 pnl=0。
        prices = [
            self._row("2025-01-02", 100, 100, 100, 100),  # 多单入场日
            self._row("2025-01-03", 101, 103, 100, 102),  # 多单: 冲高后回落到保本线
            self._row("2025-01-05", 100, 100, 100, 100),  # 空单入场日
            self._row("2025-01-06", 99, 101, 98, 99),     # 空单: 探低后反弹到保本线
        ]
        trades = [
            _trade(direction="long", entry_="2025-01-02", exit_="2025-01-03"),
            _trade(direction="short", entry_="2025-01-05", exit_="2025-01-06"),
        ]
        risked = apply_risk_management(trades, prices, stop_loss_pct=0, trailing_stop_pct=8)
        assert all(t["outcome"] == "breakeven" for t in risked)
        assert all(t["stopped_out"] for t in risked)
        assert _win_rate(risked) == 0.0
        assert _breakeven_count(risked) == 2

    def test_mixed_breakeven_excluded_from_winrate(self):
        # 1 打平 + 1 盈利 + 1 亏损 → 胜率 1/(1+1)=0.5, 打平不计入分母
        prices = [
            self._row("2025-01-02", 100, 100, 100, 100),        # 多单A入场
            self._row("2025-01-03", 101, 103, 100, 102),        # 多单A: 保本平 pnl=0
            self._row("2025-01-05", 100, 100, 100, 100),        # 多单B入场
            self._row("2025-01-06", 101, 110, 105, 108),        # 多单B: 冲高110 → 移动线101.2
            self._row("2025-01-07", 101.2, 101.5, 101.0, 101.2),  # 多单B: 回撤触线 → 赢1.2%
            self._row("2025-01-09", 100, 100, 100, 100),        # 空单入场
            self._row("2025-01-10", 101, 102, 99, 101),         # 空单: 反弹触保本线101 → 亏1%
        ]
        trades = [
            _trade(direction="long", entry_="2025-01-02", exit_="2025-01-03"),
            _trade(direction="long", entry_="2025-01-05", exit_="2025-01-07"),
            _trade(direction="short", entry_="2025-01-09", exit_="2025-01-10"),
        ]
        risked = apply_risk_management(trades, prices, stop_loss_pct=0, trailing_stop_pct=8)
        assert [t["outcome"] for t in risked] == ["breakeven", "win", "loss"]
        assert _win_rate(risked) == 0.5
        assert _breakeven_count(risked) == 1


@pytest.mark.unit
class TestStrategyResultFields:
    """各策略返回 dict 的 win_rate / breakeven_count 与 recent_trades 口径一致。"""

    def _dates(self, n):
        from datetime import datetime, timedelta

        base = datetime(2025, 1, 1)
        return [(base + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]

    def _mock_price_regime(self, n=60, switch=40, direction=1, step=3.0):
        dates = self._dates(n)
        closes = [100.0] * n
        for i in range(switch, n):
            closes[i] = 100.0 + (i - switch) * step * direction
        return {
            "prices": [
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
        }

    @pytest.mark.parametrize("indicator", ["ma_cross", "macd", "turtle"])
    def test_win_rate_and_breakeven_count_consistent(self, indicator):
        import signal_analyzer as sa

        run = getattr(sa, f"run_{indicator}_strategy")
        with patch("signal_analyzer._load_price") as m:
            m.side_effect = lambda v: self._mock_price_regime(direction=1)
            r = run("RB")
        if not r.get("total_trades"):
            pytest.skip(f"{indicator}: 该模拟行情未产生交易")
        trades = r["recent_trades"]
        wins = sum(1 for t in trades if t["outcome"] == "win")
        losses = sum(1 for t in trades if t["outcome"] == "loss")
        bes = sum(1 for t in trades if t["outcome"] == "breakeven")
        expected_wr = round(wins / (wins + losses), 3) if (wins + losses) else 0.0
        assert r["win_rate"] == expected_wr
        assert r["breakeven_count"] == bes
        assert r["win_count"] == wins
        assert r["loss_count"] == losses

    def test_donchian_breakeven_count_field(self):
        import signal_analyzer as sa

        with patch("signal_analyzer._load_price") as m:
            m.side_effect = lambda v: self._mock_price_regime(direction=1)
            r = sa.run_donchian_strategy("RB")
        if not r.get("total_trades"):
            pytest.skip("donchian: 该模拟行情未产生交易")
        trades = r["recent_trades"]
        assert r["breakeven_count"] == sum(
            1 for t in trades if t["outcome"] == "breakeven"
        )
