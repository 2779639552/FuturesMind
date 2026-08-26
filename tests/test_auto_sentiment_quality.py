"""Tests for `should_include_sentiment` (auto 模式质量感知, 2026-08-25).

auto mode decides whether to include the sentiment analyst:
  - own data usable (level not INSUFFICIENT/IGNORE)          -> True
  - own data unusable but same-sector composite fallback ok  -> True
  - neither                                                   -> False (3-analyst)

`load_sentiment_data` and `get_sector_sentiment_fallback` are mocked, so no
disk access and no real VARIETY_METADATA / sibling loading happens.
"""

from unittest.mock import patch

from tradingagents.dataflows.sentiment_data import should_include_sentiment


def _data(posts=40, platforms=None, data_points=0, acc=None):
    return {
        "variety_name": "RB",
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


def _run(data, fallback):
    with patch(
        "tradingagents.dataflows.sentiment_data.load_sentiment_data",
        return_value=data,
    ), patch(
        "tradingagents.dataflows.sentiment_data.get_sector_sentiment_fallback",
        return_value=fallback,
    ):
        return should_include_sentiment("RB")


def test_high_quality_own_data_true():
    # usable own data → include; fallback must not even be consulted
    with patch(
        "tradingagents.dataflows.sentiment_data.load_sentiment_data",
        return_value=_data(posts=40, platforms={"weibo": 20, "zhihu": 20}),
    ) as mock_load, patch(
        "tradingagents.dataflows.sentiment_data.get_sector_sentiment_fallback",
        return_value=None,
    ) as mock_fallback:
        assert should_include_sentiment("RB") is True
    mock_fallback.assert_not_called()


def test_insufficient_own_data_with_fallback_true():
    assert _run(_data(posts=2), {"sector": "农产品", "total_posts": 100}) is True


def test_insufficient_own_data_no_fallback_false():
    assert _run(_data(posts=2), None) is False


def test_no_data_with_fallback_true():
    assert _run(None, {"sector": "能化", "total_posts": 80}) is True


def test_no_data_no_fallback_false():
    assert _run(None, None) is False


def test_contrarian_own_data_no_fallback_false():
    # acc<0.40 with N>=10 → IGNORE → only the fallback could rescue it
    assert _run(
        _data(posts=50, data_points=12, acc=0.30),
        None,
    ) is False
