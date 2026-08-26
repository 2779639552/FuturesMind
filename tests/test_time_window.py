"""Tests for the daily time-series aggregation fix in `get_futures_sentiment`
(2026-08-25).

Previously the section title said "最近30天" but the slice was ds[-14:], so the
title and the table disagreed. Now:
  - the title stays "最近30天" and the table slices ds[-30:],
  - a "近7日均值" summary line is emitted over ds[-7:].

These tests build a synthetic high-quality data dict (posts>=30, 2+ platforms →
HIGH, no sector-fallback branch) so only the formatting path is exercised.
"""

import re
from datetime import datetime, timedelta
from unittest.mock import patch

from tradingagents.dataflows.sentiment_data import get_futures_sentiment


def _daily_series(n):
    base = datetime(2026, 8, 25)
    return [
        {
            "date": (base - timedelta(days=n - 1 - i)).strftime("%Y-%m-%d"),
            "avg_score": 0.1 + i * 0.001,
            "note_count": 10 + i,
            "bull_count": 5,
            "bear_count": 3,
            "platforms": {"weibo": 5},
        }
        for i in range(n)
    ]


def _high_quality_data(ds):
    return {
        "variety_name": "RB",
        "source": "test",
        "source_platforms": ["weibo", "zhihu"],
        "updated": "2026-08-25 08:00:00",
        "data": {
            "social_sentiment": {
                "total_posts_analyzed": 40,  # >= QUALITY_HIGH_POSTS
                "platforms": {"weibo": 20, "zhihu": 20},  # >= 2 platforms
                "overall_sentiment_label": "看多",
                "avg_score": 0.1,
                "bullish_ratio": 0.6,
                "bearish_ratio": 0.2,
                "neutral_ratio": 0.2,
            },
            "sentiment_price_correlation": {"data_points": 0, "direction_accuracy": None},
            "daily_series": ds,
            "platform_weights": {},
            "methodology": {"limitations": []},
        },
    }


def _render(ds):
    with patch(
        "tradingagents.dataflows.sentiment_data.load_sentiment_data",
        return_value=_high_quality_data(ds),
    ):
        return get_futures_sentiment("RB")


def _date_rows(text):
    return re.findall(r"^\s{2}(\d{4}-\d{2}-\d{2})", text, re.M)


def test_title_and_slice_both_30_days():
    text = _render(_daily_series(40))
    assert "## 3. 每日情绪时序（最近30天）" in text
    rows = _date_rows(text)
    assert len(rows) == 30
    # the most recent date must be the last row
    assert rows[-1] == _daily_series(40)[-1]["date"]


def test_seven_day_summary_line_present():
    text = _render(_daily_series(40))
    assert "近7日均值" in text
    # line contains avg score / daily posts / bull / bear
    line = next(l for l in text.splitlines() if "近7日均值" in l)
    assert "平均分" in line and "日均帖子" in line


def test_seven_day_average_value_matches():
    ds = _daily_series(40)
    last7 = ds[-7:]
    expected = round(sum(d["avg_score"] for d in last7) / len(last7), 3)
    text = _render(ds)
    line = next(l for l in text.splitlines() if "近7日均值" in l)
    assert f"{expected:+.3f}" in line


def test_short_series_still_renders():
    # Fewer than 30 days: the table prints what exists (ds[-30:]) without crashing
    text = _render(_daily_series(6))
    assert "## 3. 每日情绪时序（最近30天）" in text
    assert "近7日均值" in text
    assert len(_date_rows(text)) == 6


def test_quality_gate_section_present():
    text = _render(_daily_series(40))
    assert "## 0. 数据质量门控" in text
    assert "质量等级: HIGH" in text
    assert "权重上限: 100%" in text
