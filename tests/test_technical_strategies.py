"""12 个市场/机构常用技术策略(6 指标 × 纯价格 + 情绪确认)。

覆盖三个层面:
- 通用回测引擎 ``_run_technical_backtest``(经 12 个 run_*_strategy 薄包装):
  趋势/区间/突破价格下是否产生交易、返回 dict 字段齐全、日期窗口收窄生效、空数据守卫。
- 情绪自适应包装 ``_adapt_sentiment_signal``(纯函数):无技术信号逆向、同向确认、
  分歧(强趋势信技术 / 弱趋势逆情绪 / 中等趋势信技术)、情绪中性跟随技术。
- 12 个策略的"今日操作"信号(``latest_trading_signal``):结构与 strength_pct 范围、
  情绪版弱趋势逆向分支、RSI 同向确认分支、守卫(variety 空 / 无数据 / 数据不足 / 无情绪)。

沿用餐具测试的 `_mock_price`/`_mock_sentiment` + `@patch` 风格,纯 unit 不触真数据。
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

import signal_analyzer as sa
from signal_analyzer import latest_trading_signal

INDICATORS = ["ma_cross", "macd", "rsi", "bollinger", "turtle", "atr"]
# RSI 对全平数据返回 100(avg_loss==0 → 恒超买),flat 下仍会开空 —— 不参与"flat→0 交易"断言
FLAT_ZERO_INDICATORS = ["ma_cross", "macd", "bollinger", "turtle", "atr"]
# 全平 + 看空情绪下 tsig==0 → 逆向做多(RSI 是看空技术信号→同向确认做空,单独测)
CONTRARIAN_LONG_INDICATORS = ["ma_cross", "macd", "bollinger", "turtle", "atr"]


def _dates(n: int) -> list[str]:
    base = datetime(2025, 1, 1)
    return [(base + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]


def _mock_price(n: int = 60, mode: str = "up", breakout: str | None = None) -> dict:
    """OHLCV 系列。mode: up=平稳上涨 / down=平稳下跌 / flat=收盘持平。
    breakout="high"/"low" 把最后一根收盘推到全部前高之上/前低之下(触发布林带突破)。"""
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


def _mock_price_regime(
    n: int = 60, switch: int = 40, direction: int = 1, step: float = 3.0
) -> dict:
    """先走平(switch 根)后单边移动 —— 让交叉/突破发生在引擎循环窗口内。

    纯线性趋势下,双均线/MACD 的首次交叉发生在慢线还在 warmup 预热时
    (循环从 warmup 才开始),会被系统性地漏掉;平→动的"制度切换"序列
    保证触发点落在循环内,同时仍产生单边持仓。
    """
    dates = _dates(n)
    closes = [100.0] * n
    for i in range(switch, n):
        closes[i] = 100.0 + (i - switch) * step * direction
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
    return {"prices": prices}


def _mock_sentiment(n: int = 60, last_score: float = 0.5) -> dict:
    """情绪系列;仅最后一天携带给定分数(其余 0.1,落在 ±0.1 中性带内)。"""
    dates = _dates(n)
    return {
        "data": {
            "daily_series": [
                {"date": d, "avg_score": last_score if i == n - 1 else 0.1}
                for i, d in enumerate(dates)
            ]
        }
    }


def _mock_sentiment_all(n: int = 60, score: float = -0.5) -> dict:
    """情绪系列;每天都携带相同分数(用于持续逆向/确认入场的回测)。"""
    dates = _dates(n)
    return {"data": {"daily_series": [{"date": d, "avg_score": score} for d in dates]}}


def _assert_backtest_shape(r: dict) -> None:
    """单策略回测返回 dict 字段齐全(镜像 donchian 形状)。"""
    assert "total_trades" in r
    assert "strategy" in r
    if r["total_trades"] > 0:
        for k in (
            "win_count",
            "loss_count",
            "win_rate",
            "avg_pnl_pct",
            "sharpe_like",
            "max_drawdown_pct",
            "long_trades",
            "short_trades",
            "advanced_metrics",
            "recent_trades",
        ):
            assert k in r, f"缺少字段 {k}"
        assert all(t["variety"] == "RB" for t in r["recent_trades"])
        assert all(t["direction"] in ("long", "short") for t in r["recent_trades"])


# ═══════════════════════════════════════════════════════════════════
# 1. 纯价格回测
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestTechnicalBacktest:
    @pytest.mark.parametrize("indicator", INDICATORS)
    def test_pure_backtest_up_trend_trades(self, indicator):
        run = getattr(sa, f"run_{indicator}_strategy")
        with patch("signal_analyzer._load_price") as m:
            m.side_effect = lambda v: _mock_price_regime(direction=1)
            r = run("RB")
        assert r["total_trades"] >= 1, indicator
        _assert_backtest_shape(r)

    @pytest.mark.parametrize("indicator", INDICATORS)
    def test_pure_backtest_down_trend_trades(self, indicator):
        run = getattr(sa, f"run_{indicator}_strategy")
        with patch("signal_analyzer._load_price") as m:
            m.side_effect = lambda v: _mock_price_regime(direction=-1)
            r = run("RB")
        assert r["total_trades"] >= 1, indicator
        _assert_backtest_shape(r)

    @pytest.mark.parametrize("indicator", FLAT_ZERO_INDICATORS)
    def test_pure_backtest_flat_no_trades(self, indicator):
        # 收盘持平:无均线交叉 / 无 MACD 金叉死叉 / 无布林带突破 → 不应产生交易
        run = getattr(sa, f"run_{indicator}_strategy")
        with patch("signal_analyzer._load_price") as m:
            m.side_effect = lambda v: _mock_price(60, mode="flat")
            assert run("RB")["total_trades"] == 0

    @pytest.mark.parametrize("breakout", ["high", "low"])
    def test_bollinger_breakout_trades(self, breakout):
        # 布林带需要价格剧烈偏离均线;平稳上涨不触发(带随价格上移),用突破构造
        with patch("signal_analyzer._load_price") as m:
            m.side_effect = lambda v: _mock_price(60, mode="flat", breakout=breakout)
            r = sa.run_bollinger_strategy("RB")
        assert r["total_trades"] >= 1

    @patch("signal_analyzer._load_price")
    def test_backtest_date_window_narrows(self, mock_px):
        # 收窄 end_date → 交易数不增,且每笔 entry 都在窗口内
        mock_px.side_effect = lambda v: _mock_price_regime(direction=1)
        full = sa.run_macd_strategy("RB")
        cut = sa.run_macd_strategy("RB", end_date="2025-02-20")
        assert cut["total_trades"] <= full["total_trades"]
        assert cut["total_trades"] >= 1  # 制度切换点(≈02-13)在窗口内,应保留
        assert all(t["entry"] <= "2025-02-20" for t in cut["recent_trades"])

    @patch("signal_analyzer._load_price")
    def test_backtest_forced_close_respects_end_date(self, mock_px):
        # 回归:窗口末仍有持仓时,强平应在窗口内最后一根,而非全数据最后一根。
        # 修复前 exit 落在 dates[-1](2025-03-01),pnl 用窗口外未来价格(前视偏差)。
        mock_px.side_effect = lambda v: _mock_price_regime(direction=1)  # 60 根,末日 2025-03-01
        r = sa.run_ma_cross_strategy("RB", end_date="2025-02-20")
        assert r["total_trades"] >= 1
        assert all(t["entry"] <= "2025-02-20" for t in r["recent_trades"])
        assert all(t["exit"] <= "2025-02-20" for t in r["recent_trades"])
        # 末笔为窗口末强平:退出日恰为窗口内最后一根
        assert r["recent_trades"][-1]["exit"] == "2025-02-20"

    @patch("signal_analyzer._load_price")
    def test_backtest_forced_close_respects_end_date_donchian(self, mock_px):
        # 唐奇安共享同一强平模式,同样受窗口约束
        mock_px.side_effect = lambda v: _mock_price_regime(direction=1)
        r = sa.run_donchian_strategy("RB", period=20, end_date="2025-02-20")
        assert r["total_trades"] >= 1
        assert all(t["exit"] <= "2025-02-20" for t in r["recent_trades"])

    @patch("signal_analyzer._load_price")
    def test_backtest_forced_close_respects_end_date_momentum(self, mock_px):
        mock_px.side_effect = lambda v: _mock_price_regime(direction=1)
        r = sa.run_momentum_strategy("RB", lookback=5, hold=3, end_date="2025-02-20")
        assert r["total_trades"] >= 1
        assert all(t["exit"] <= "2025-02-20" for t in r["recent_trades"])

    @patch("signal_analyzer._load_price")
    def test_backtest_no_data_empty(self, mock_px):
        mock_px.side_effect = lambda v: None
        assert sa.run_macd_strategy("RB") == {"total_trades": 0}

    @patch("signal_analyzer._load_price")
    def test_backtest_insufficient_data_empty(self, mock_px):
        # 5 根 < rsi warmup(14)→ 跳过
        mock_px.side_effect = lambda v: _mock_price(5, mode="up")
        assert sa.run_rsi_strategy("RB") == {"total_trades": 0}

    @patch("signal_analyzer._get_all_varieties_with_data")
    @patch("signal_analyzer._load_price")
    def test_backtest_empty_variety_runs_all(self, mock_px, mock_get):
        mock_get.return_value = ["RB", "CU"]
        mock_px.side_effect = lambda v: _mock_price_regime(direction=1)
        assert sa.run_ma_cross_strategy()["total_trades"] >= 1


# ═══════════════════════════════════════════════════════════════════
# 2. 情绪自适应包装(_adapt_sentiment_signal 纯函数)
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestAdaptSentimentSignal:
    def test_no_tech_weak_trend_contrarian(self):
        # 无技术触发 + 弱趋势 + 极端情绪 → 逆向入场(镜像 adaptive ④)
        assert sa._adapt_sentiment_signal(0, 0.0, 1.0, 0.003, -0.5)[0] == "buy"
        assert sa._adapt_sentiment_signal(0, 0.0, 1.0, 0.003, 0.5)[0] == "sell"

    def test_no_tech_neutral_sentiment_holds(self):
        assert sa._adapt_sentiment_signal(0, 0.0, 1.0, 0.003, 0.05)[0] == "hold"
        assert (
            sa._adapt_sentiment_signal(0, 0.0, 1.0, 0.05, -0.05)[0] == "hold"
        )  # 强趋势但无技术信号

    def test_same_direction_confirms(self):
        action, strength, scale, _ = sa._adapt_sentiment_signal(1, 0.05, 0.05, 0.01, 0.5)
        assert action == "buy"
        assert strength == pytest.approx(0.5)  # 情绪更强 → 强度取情绪
        assert scale == pytest.approx(1.0)

    def test_divergence_strong_trend_follows_tech(self):
        action, strength, scale, _ = sa._adapt_sentiment_signal(1, 0.05, 0.05, 0.05, -0.5)
        assert action == "buy"  # 趋势 5% > 3% → 动量市信技术
        assert strength == pytest.approx(0.05)
        assert scale == pytest.approx(0.05)

    def test_divergence_weak_trend_fades(self):
        action, strength, scale, _ = sa._adapt_sentiment_signal(1, 0.05, 0.05, 0.003, -0.5)
        assert action == "sell"  # 趋势 0.3% < 1% → 逆向市逆情绪
        assert scale == pytest.approx(1.0)

    def test_divergence_mid_trend_follows_tech(self):
        action, _, _, _ = sa._adapt_sentiment_signal(1, 0.05, 0.05, 0.02, -0.5)
        assert action == "buy"  # 趋势 2% ∈ [1,3] → 信技术

    def test_neutral_sentiment_follows_tech(self):
        action, strength, scale, _ = sa._adapt_sentiment_signal(1, 0.05, 0.05, 0.003, 0.05)
        assert action == "buy"
        assert strength == pytest.approx(0.05)


# ═══════════════════════════════════════════════════════════════════
# 3. 情绪版回测:flat + 持续看空情绪 → 逆向/确认产生交易
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestSentBacktest:
    @pytest.mark.parametrize("indicator", CONTRARIAN_LONG_INDICATORS)
    def test_sent_backtest_contrarian_long_on_flat_bearish(self, indicator):
        run = getattr(sa, f"run_{indicator}_sent_strategy")
        with patch("signal_analyzer._load_price") as m:
            m.side_effect = lambda v: _mock_price(60, mode="flat")
            pure = getattr(sa, f"run_{indicator}_strategy")("RB")
            assert pure["total_trades"] == 0  # 纯价格版无信号

        with (
            patch("signal_analyzer._load_price") as m,
            patch("signal_analyzer._load_trends") as t,
            patch("signal_analyzer._load_sentiment") as s,
        ):
            m.side_effect = lambda v: _mock_price(60, mode="flat")
            t.side_effect = lambda v: _mock_sentiment_all(60, score=-0.5)
            s.side_effect = lambda v: None
            r = run("RB")
        assert r["total_trades"] >= 1, indicator  # 情绪自适应把无信号转化为逆向买入
        assert r["long_trades"] >= 1, indicator
        _assert_backtest_shape(r)

    @patch("signal_analyzer._load_price")
    @patch("signal_analyzer._load_trends")
    @patch("signal_analyzer._load_sentiment")
    def test_sent_backtest_confirm_direction(self, mock_sent, mock_trends, mock_px):
        # 上涨 + 看多情绪 → 顺势确认做多(非纯技术也能成立,但确认路径生效)
        mock_px.side_effect = lambda v: _mock_price(60, mode="up")
        mock_trends.side_effect = lambda v: _mock_sentiment_all(60, score=0.5)
        mock_sent.side_effect = lambda v: None
        r = sa.run_rsi_sent_strategy("RB")
        assert r["total_trades"] >= 1


# ═══════════════════════════════════════════════════════════════════
# 4. 今日信号(12 键)
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestTechnicalTodaySignal:
    @patch("signal_analyzer._load_price")
    @patch("signal_analyzer._load_trends")
    @patch("signal_analyzer._load_sentiment")
    def test_today_signal_all_12_keys(self, mock_sent, mock_trends, mock_px):
        mock_px.side_effect = lambda v: _mock_price(60, mode="up")
        mock_trends.side_effect = lambda v: _mock_sentiment(60, last_score=0.5)
        mock_sent.side_effect = lambda v: None
        for key in sa.TECH_KEYS:
            sig = latest_trading_signal(key, variety="RB")
            assert sig is not None, key
            assert set(sig.keys()) == {"today_signal", "today_signals"}, key
            assert sig["today_signals"] == {}, key  # 单策略形状
            ts = sig["today_signal"]
            assert ts["action"] in ("buy", "sell", "hold"), key
            assert ts["date"] == "2025-03-01", key
            assert ts["variety"] == "RB", key
            assert 0.0 <= ts["strength_pct"] <= 1.0, key
            assert isinstance(ts["reason"], str) and ts["reason"], key

    @patch("signal_analyzer._load_price")
    @patch("signal_analyzer._load_trends")
    @patch("signal_analyzer._load_sentiment")
    def test_today_sent_flat_bearish_contrarian_buy(self, mock_sent, mock_trends, mock_px):
        # 弱趋势 + 看空情绪 + 无技术触发 → 逆向买入(自适应第①分支)
        mock_px.side_effect = lambda v: _mock_price(60, mode="flat")
        mock_trends.side_effect = lambda v: _mock_sentiment(60, last_score=-0.5)
        mock_sent.side_effect = lambda v: None
        for key in ("ma_cross_sent", "macd_sent", "bollinger_sent", "turtle_sent", "atr_sent"):
            ts = latest_trading_signal(key, variety="RB")["today_signal"]
            assert ts["action"] == "buy", key
            assert ts["strength_pct"] == pytest.approx(0.5), key

    @patch("signal_analyzer._load_price")
    @patch("signal_analyzer._load_trends")
    @patch("signal_analyzer._load_sentiment")
    def test_today_sent_rsi_same_direction_confirm(self, mock_sent, mock_trends, mock_px):
        # 全平 → RSI=100(超卖?超买)→ 看空技术信号;看空情绪同向 → 顺势确认卖出
        mock_px.side_effect = lambda v: _mock_price(60, mode="flat")
        mock_trends.side_effect = lambda v: _mock_sentiment(60, last_score=-0.5)
        mock_sent.side_effect = lambda v: None
        ts = latest_trading_signal("rsi_sent", variety="RB")["today_signal"]
        assert ts["action"] == "sell"

    @patch("signal_analyzer._load_price")
    @patch("signal_analyzer._load_trends")
    @patch("signal_analyzer._load_sentiment")
    def test_today_sent_no_sentiment_returns_none(self, mock_sent, mock_trends, mock_px):
        mock_px.side_effect = lambda v: _mock_price(60, mode="up")
        mock_trends.side_effect = lambda v: None
        mock_sent.side_effect = lambda v: None
        assert latest_trading_signal("ma_cross_sent", variety="RB") is None

    def test_today_tech_empty_variety_none(self):
        assert latest_trading_signal("ma_cross", variety="") is None

    @patch("signal_analyzer._load_price")
    def test_today_tech_no_price_none(self, mock_px):
        mock_px.side_effect = lambda v: None
        assert latest_trading_signal("macd", variety="RB") is None

    @patch("signal_analyzer._load_price")
    def test_today_tech_insufficient_data_none(self, mock_px):
        mock_px.side_effect = lambda v: _mock_price(5, mode="up")
        assert latest_trading_signal("rsi", variety="RB") is None  # 5 < warmup 14
        assert latest_trading_signal("ma_cross", variety="RB") is None  # 5 < slow 30
