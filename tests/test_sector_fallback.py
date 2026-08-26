"""Tests for `get_sector_sentiment_fallback` (板块复合降级, 2026-08-25).

When a variety's own sentiment is too sparse (or absent), the pipeline aggregates
same-sector siblings' sentiment as a degraded reference, capped at 15% weight.
These tests mock `load_sentiment_data` (the only data source) and the sector map,
so they never touch disk or the real VARIETY_METADATA.

Rules locked:
  - high-quality siblings are aggregated with posts-weighted average score,
  - INSUFFICIENT / IGNORE / below-min_posts siblings are dropped,
  - no usable sibling → None (fully skip the sentiment dimension).
"""

from unittest.mock import patch

from tradingagents.dataflows.sentiment_data import get_sector_sentiment_fallback

_SECTOR_MAP = {"农产品": ["AP", "M", "CF", "CJ", "PK"]}


def _sib(code, posts, avg_score, acc=None, data_points=0):
    return {
        "variety_name": code,
        "data": {
            "social_sentiment": {
                "total_posts_analyzed": posts,
                "avg_score": avg_score,
                "platforms": {"weibo": posts},
            },
            "sentiment_price_correlation": {
                "data_points": data_points,
                "direction_accuracy": acc,
            },
        },
    }


def _run(sib_map):
    """Call get_sector_sentiment_fallback('AP') with the sibling data mocked."""
    with patch(
        "tradingagents.dataflows.sentiment_data.build_sector_to_varieties",
        return_value=_SECTOR_MAP,
    ), patch(
        "tradingagents.dataflows.sentiment_data.load_sentiment_data",
        side_effect=lambda code: sib_map.get(code),
    ):
        return get_sector_sentiment_fallback("AP")


def test_siblings_aggregated_weighted():
    fb = _run({
        "M": _sib("M", posts=20, avg_score=0.10),
        "CF": _sib("CF", posts=40, avg_score=-0.30),
        "CJ": _sib("CJ", posts=15, avg_score=0.20),
        "PK": None,
    })
    assert fb is not None
    assert fb["sector"] == "农产品"
    assert fb["total_posts"] == 75
    assert fb["n_siblings"] == 3
    # weighted = (0.1*20 + -0.3*40 + 0.2*15) / 75 = -7/75 ≈ -0.0933
    assert fb["weighted_avg_score"] == round(-7 / 75, 4)
    assert fb["weight_cap"] == 0.15
    assert [s["code"] for s in fb["siblings"]] == ["CF", "M", "CJ"]  # posts desc


def test_all_siblings_insufficient_returns_none():
    fb = _run({
        "M": _sib("M", posts=2, avg_score=0.1),    # INSUFFICIENT
        "CF": _sib("CF", posts=1, avg_score=0.1),  # INSUFFICIENT
        "CJ": _sib("CJ", posts=0, avg_score=0.1),  # INSUFFICIENT
        "PK": None,
    })
    assert fb is None


def test_sibling_below_min_posts_dropped():
    fb = _run({
        "M": _sib("M", posts=20, avg_score=0.1),
        "CF": _sib("CF", posts=5, avg_score=0.3),   # < min_sibling_posts=10 → dropped
        "CJ": None,
        "PK": None,
    })
    assert fb is not None
    assert fb["n_siblings"] == 1
    assert fb["siblings"][0]["code"] == "M"
    assert fb["total_posts"] == 20


def test_contrarian_sibling_ignored():
    # CF is a systematic contrarian (acc<0.40, N>=10) → must be dropped
    fb = _run({
        "M": _sib("M", posts=20, avg_score=0.1),
        "CF": _sib("CF", posts=50, avg_score=0.9, acc=0.30, data_points=12),
        "CJ": None,
        "PK": None,
    })
    assert fb is not None
    assert [s["code"] for s in fb["siblings"]] == ["M"]


def test_avg_direction_accuracy_reported():
    fb = _run({
        "M": _sib("M", posts=20, avg_score=0.1, acc=0.60, data_points=8),
        "CF": _sib("CF", posts=40, avg_score=0.1, acc=0.55, data_points=8),
        "CJ": _sib("CJ", posts=15, avg_score=0.1),  # no acc → excluded from avg
        "PK": None,
    })
    assert fb["avg_direction_accuracy"] == round((0.60 + 0.55) / 2, 4)


def test_variety_not_in_any_sector_returns_none():
    with patch(
        "tradingagents.dataflows.sentiment_data.build_sector_to_varieties",
        return_value={"黑色系": ["RB", "HC", "I"]},
    ), patch(
        "tradingagents.dataflows.sentiment_data.load_sentiment_data",
        side_effect=lambda code: None,
    ):
        assert get_sector_sentiment_fallback("AP") is None


def test_string_avg_score_does_not_crash():
    # avg_score 若为字符串必须 float() 强转, 不能 TypeError
    fb = _run({
        "M": _sib("M", posts=20, avg_score="0.10"),
        "CF": _sib("CF", posts=40, avg_score="-0.30"),
        "CJ": None,
        "PK": None,
    })
    assert fb is not None
    assert fb["total_posts"] == 60
    # weighted = (0.10*20 + -0.30*40) / 60 = -10/60 ≈ -0.1667
    assert fb["weighted_avg_score"] == round(-10 / 60, 4)
