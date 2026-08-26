"""Tests for the confidence-quality formula `sentiment_quality` (2026-08-25 置信度公式化).

The function converts raw sentiment data into a hard quality level + weight cap,
replacing the old "LLM-suggested weighting" guidance (previously only written in
prompts as advice). These tests lock the hard rules in code:

  - data=None                       -> IGNORE / cap 0
  - posts < 3                       -> INSUFFICIENT / cap 0
  - acc < 0.40 with N >= 10         -> IGNORE / cap 0 (systematic contrarian)
  - HIGH (posts>=30, platforms>=2) / MEDIUM (posts>=10) / LOW tiers
  - stale -> cap halves, HIGH downgrades to MEDIUM
  - accuracy boost (>=0.55, N>=5) / penalty (<0.45, N>=5)

Pure function, no network. `data` mirrors the shape of `load_sentiment_data`.
"""

from tradingagents.dataflows.sentiment_data import (
    QUALITY_LOW_CAP,
    QUALITY_MEDIUM_CAP,
    sentiment_quality,
)


def _data(posts=20, platforms=None, data_points=0, acc=None, stale=False):
    """Build a minimal sentiment-data dict shaped like load_sentiment_data() output."""
    return {
        "variety_name": "RB",
        "_stale": stale,
        "data": {
            "social_sentiment": {
                "total_posts_analyzed": posts,
                "platforms": platforms or {"weibo": posts},
            },
            "sentiment_price_correlation": {
                "data_points": data_points,
                "direction_accuracy": acc,
            },
        },
    }


def test_data_none_ignored():
    q = sentiment_quality("PK", None)
    assert q["level"] == "IGNORE"
    assert q["weight_cap"] == 0.0


def test_posts_below_min_insufficient():
    q = sentiment_quality("AP", _data(posts=2))
    assert q["level"] == "INSUFFICIENT"
    assert q["weight_cap"] == 0.0
    assert q["posts"] == 2


def test_contrarian_ignored_when_enough_samples():
    # acc < 0.40 with N>=10 → systematic contrarian, sentiment must not be used
    q = sentiment_quality("AU", _data(posts=50, data_points=12, acc=0.30))
    assert q["level"] == "IGNORE"
    assert q["weight_cap"] == 0.0


def test_small_sample_contrarian_does_not_fire():
    # PK has few backtest points: acc low but N=1 must NOT trigger the contrarian ban
    q = sentiment_quality("PK", _data(posts=4, data_points=1, acc=0.30))
    assert q["level"] != "IGNORE"
    assert q["level"] == "LOW"
    assert q["weight_cap"] == QUALITY_LOW_CAP


def test_high_tier_cap_one():
    q = sentiment_quality("RB", _data(posts=40, platforms={"weibo": 20, "zhihu": 20}))
    assert q["level"] == "HIGH"
    assert q["weight_cap"] == 1.0


def test_high_requires_two_platforms():
    # 40 posts but a single platform → not HIGH (platform diversity is required)
    q = sentiment_quality("RB", _data(posts=40, platforms={"weibo": 40}))
    assert q["level"] == "MEDIUM"
    assert q["weight_cap"] == QUALITY_MEDIUM_CAP


def test_medium_tier():
    q = sentiment_quality("I", _data(posts=15))
    assert q["level"] == "MEDIUM"
    assert q["weight_cap"] == QUALITY_MEDIUM_CAP


def test_low_tier():
    q = sentiment_quality("AP", _data(posts=5))
    assert q["level"] == "LOW"
    assert q["weight_cap"] == QUALITY_LOW_CAP


def test_stale_halves_high_and_downgrades():
    q = sentiment_quality(
        "RB", _data(posts=40, platforms={"weibo": 20, "zhihu": 20}, stale=True)
    )
    assert q["level"] == "MEDIUM"  # HIGH → MEDIUM when stale
    assert q["weight_cap"] == 0.5  # 1.0 * 0.5


def test_stale_halves_medium():
    q = sentiment_quality("I", _data(posts=15, stale=True))
    assert q["weight_cap"] == round(QUALITY_MEDIUM_CAP * 0.5, 3)


def test_accuracy_boost():
    # acc >= 0.55 with N>=5 → cap x1.25
    q = sentiment_quality("I", _data(posts=15, data_points=8, acc=0.60))
    assert q["weight_cap"] == round(QUALITY_MEDIUM_CAP * 1.25, 3)


def test_accuracy_boost_caps_at_one():
    q = sentiment_quality(
        "RB",
        _data(posts=40, platforms={"weibo": 20, "zhihu": 20}, data_points=8, acc=0.60),
    )
    assert q["weight_cap"] == 1.0  # 1.0 * 1.25 clamped to 1.0


def test_accuracy_penalty():
    # acc < 0.45 with N>=5 → cap x0.5
    q = sentiment_quality("J", _data(posts=15, data_points=8, acc=0.40))
    assert q["weight_cap"] == round(QUALITY_MEDIUM_CAP * 0.5, 3)


def test_accuracy_ignored_when_few_samples():
    # N<5 → accuracy signal is too weak to trust for adjustment
    q = sentiment_quality("I", _data(posts=15, data_points=2, acc=0.70))
    assert q["weight_cap"] == QUALITY_MEDIUM_CAP


def test_reason_is_human_readable():
    q = sentiment_quality("RB", _data(posts=40, platforms={"weibo": 20, "zhihu": 20}))
    assert q["reason"] and isinstance(q["reason"], str)
    assert "HIGH" in q["reason"] or "充足" in q["reason"]


def test_string_platform_counts_are_coerced():
    # 平台计数若意外为字符串(如从 JSON 读出),必须 int() 强转而非 TypeError
    q = sentiment_quality(
        "RB", _data(posts=40, platforms={"weibo": "20", "zhihu": "20"})
    )
    assert q["level"] == "HIGH"  # 2 个平台 + 40 帖 → HIGH
    assert q["platforms"] == 2
