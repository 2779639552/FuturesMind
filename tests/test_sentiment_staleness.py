"""情绪时效衰减:超 3 天无新帖子视为"数据不足"(worklog/2026-08-31)。

低频品种(如红枣 3 个月仅 20 个情绪日)下,原来无界的前向填充会把过期情绪当
持续情绪——08-02 的"看多 0.5"被当成整个 8 月上旬的看多,哪怕期间价格已大跌。
`_build_forward_filled_sent_map` 增加 max_staleness_days(默认 3):某价格日距最近
一条情绪超过 3 个自然日 → 该日情绪取 0(中性),策略据此回退纯技术。
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

import signal_analyzer as sa


def _dates(n: int) -> list[str]:
    base = datetime(2025, 1, 1)
    return [(base + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]


def _mock_price(n: int = 20, mode: str = "down") -> dict:
    """OHLCV 系列。mode=down:平稳下跌(contrarian 抄底场景的价格面)。"""
    closes = [200 - i * 1.5 for i in range(n)] if mode == "down" else [100.0] * n
    dates = _dates(n)
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


@pytest.mark.unit
class TestForwardFillStaleness:
    def test_fresh_days_use_last_score(self):
        """帖子后 0~3 天内仍沿用该评分(不衰减)。"""
        sent = {"series": [{"date": "2025-01-01", "avg_score": 0.8}]}
        dates = _dates(6)  # 01-01..01-06
        m = sa._build_forward_filled_sent_map(sent, dates)
        assert m["2025-01-01"] == 0.8  # 当天
        assert m["2025-01-02"] == 0.8  # 隔 1 天
        assert m["2025-01-03"] == 0.8  # 隔 2 天
        assert m["2025-01-04"] == 0.8  # 隔 3 天(含)仍有效

    def test_stale_beyond_3_days_is_neutral(self):
        """超过 3 天 → 视为数据不足,评分为 0(中性)。"""
        sent = {"series": [{"date": "2025-01-01", "avg_score": 0.8}]}
        dates = _dates(10)
        m = sa._build_forward_filled_sent_map(sent, dates)
        assert m["2025-01-05"] == 0.0  # 隔 4 天 → 过期
        assert m["2025-01-06"] == 0.0
        assert m["2025-01-10"] == 0.0

    def test_no_sentiment_anywhere_is_neutral(self):
        """从没有任何情绪 → 全程 0(原有行为不变)。"""
        sent = {"series": []}
        dates = _dates(5)
        m = sa._build_forward_filled_sent_map(sent, dates)
        assert set(m.values()) == {0.0}

    def test_staleness_days_override(self):
        """max_staleness_days 可调:0 → 仅当天有效;99 → 几乎永不衰减。"""
        sent = {"series": [{"date": "2025-01-01", "avg_score": 0.8}]}
        dates = _dates(6)
        m0 = sa._build_forward_filled_sent_map(sent, dates, max_staleness_days=0)
        assert m0["2025-01-02"] == 0.0
        assert m0["2025-01-01"] == 0.8
        m99 = sa._build_forward_filled_sent_map(sent, dates, max_staleness_days=99)
        assert m99["2025-01-05"] == 0.8

    def test_later_post_resets_freshness(self):
        """新帖子刷新时效:过期区段后出现新情绪 → 恢复有效。"""
        sent = {"series": [
            {"date": "2025-01-01", "avg_score": 0.8},
            {"date": "2025-01-10", "avg_score": -0.5},
        ]}
        dates = _dates(14)
        m = sa._build_forward_filled_sent_map(sent, dates)
        assert m["2025-01-04"] == 0.8   # 01-01 后 3 天内
        assert m["2025-01-08"] == 0.0   # 01-01 后 7 天(过期)
        assert m["2025-01-10"] == -0.5  # 新帖子当天
        assert m["2025-01-13"] == -0.5  # 新帖子后 3 天内


@pytest.mark.unit
class TestStalenessThroughStrategies:
    def test_stale_bullish_not_used_by_contrarian(self):
        """情绪只有第一天有帖子(之后全过期)→ 逆向策略不再抄底,contrarian 0 笔。

        旧行为:整个回测窗都用 0.8(看多)做背离 → 跌+看多触发抄底多单;
        新行为:>3 天后视为数据不足 → ss=0 → 无法触发 c_long(需 ss>0.1)。
        """
        prices = _mock_price(n=20, mode="down")
        sent = {"series": [{"date": "2025-01-01", "avg_score": 0.8}]}
        with patch("signal_analyzer._load_price", return_value=prices), \
             patch("signal_analyzer._load_trends", return_value=sent):
            r = sa.run_contrarian_sentiment(variety="RB", start_date="2025-01-01", end_date="")
        # 无信号时早退为 {"total_trades": 0};有结构时两条路径 trades 均为 0
        if "total_trades" in r:
            assert r["total_trades"] == 0
        else:
            assert r["contrarian"]["trades"] == 0
            assert r["consensus"]["trades"] == 0

    def test_fresh_daily_sentiment_still_drives_contrarian(self):
        """对照:每天都有帖子(永不过期)→ 逆向照常触发(衰减不破坏正常场景)。"""
        prices = _mock_price(n=20, mode="down")
        dates = _dates(20)
        sent = {"series": [{"date": d, "avg_score": 0.8} for d in dates]}
        with patch("signal_analyzer._load_price", return_value=prices), \
             patch("signal_analyzer._load_trends", return_value=sent):
            r = sa.run_contrarian_sentiment(variety="RB", start_date="2025-01-01", end_date="")
        assert r["contrarian"]["trades"] > 0  # 跌+看多 → 抄底多单正常触发

    def test_adaptive_sentiment_accepts_staleness_param(self):
        """run_adaptive_sentiment 的 sent_map 与共享函数口径一致(超 3 天 → 0)。"""
        prices = _mock_price(n=20, mode="down")
        sent = {"series": [{"date": "2025-01-01", "avg_score": 0.8}]}
        with patch("signal_analyzer._load_price", return_value=prices), \
             patch("signal_analyzer._load_trends", return_value=sent):
            r = sa.run_adaptive_sentiment(variety="RB", start_date="2025-01-01", end_date="")
        # 情绪全程过期 → 无逆向类触发;无信号时早退为 {"total_trades": 0}
        if "total_trades" in r:
            assert r["total_trades"] == 0
        else:
            assert r["adaptive"]["trades"] == 0
            assert r["consensus"]["trades"] == 0
