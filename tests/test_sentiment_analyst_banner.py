"""Dedicated banner-injection tests for the sentiment analyst node (2026-08-25).

Complementary to `test_structured_agents.py`, which patches `sentiment_quality`
entirely. These tests instead drive the REAL `sentiment_quality` from synthetic
loaded data to lock the banner's exact machine-readable format, and verify the
graceful-degradation path when quality computation itself fails (banner dropped,
report passed through unchanged).

No LLM is invoked — `_run_tool_loop` is patched out.
"""

from unittest.mock import MagicMock, patch

from tradingagents.agents.analysts.sentiment_analyst import (
    create_commodity_sentiment_analyst,
)


def _state():
    return {"company_of_interest": "RB", "trade_date": "2026-08-25", "messages": []}


def _data(posts=16, platforms=None, acc=None, data_points=0):
    return {
        "variety_name": "RB",
        "data": {
            "social_sentiment": {
                "total_posts_analyzed": posts,
                "platforms": platforms or {"weibo": 9, "zhihu": 7},
            },
            "sentiment_price_correlation": {
                "data_points": data_points,
                "direction_accuracy": acc,
            },
        },
    }


def test_banner_built_from_real_sentiment_quality():
    report = "BIAS: 看多 | CONFIDENCE: 中\n\n**情绪概况**: mildly bullish."
    llm = MagicMock()
    with patch(
        "tradingagents.agents.analysts.sentiment_analyst._run_tool_loop",
        return_value=report,
    ), patch(
        "tradingagents.agents.analysts.sentiment_analyst.load_sentiment_data",
        return_value=_data(posts=16, platforms={"weibo": 9, "zhihu": 7}),
    ):
        result = create_commodity_sentiment_analyst(llm)(_state())

    # 16 posts + 2 platforms + acc=None → real quality = MEDIUM cap 0.30
    # 2026-08-26: 横幅包成 HTML 注释,前端 marked 渲染不可见;合成节点正则照常匹配。
    expected_banner = (
        "<!-- [SENTIMENT_QUALITY] level=MEDIUM posts=16 data_points=0 "
        "acc=None weight_cap=0.3 platforms=2 -->\n"
    )
    assert result["sentiment_report"] == expected_banner + report


def test_insufficient_quality_yields_cap_zero_banner():
    report = "BIAS: 中性 | CONFIDENCE: 低"
    llm = MagicMock()
    with patch(
        "tradingagents.agents.analysts.sentiment_analyst._run_tool_loop",
        return_value=report,
    ), patch(
        "tradingagents.agents.analysts.sentiment_analyst.load_sentiment_data",
        return_value=_data(posts=2),  # < QUALITY_MIN_POSTS
    ):
        result = create_commodity_sentiment_analyst(llm)(_state())

    assert result["sentiment_report"].startswith(
        "<!-- [SENTIMENT_QUALITY] level=INSUFFICIENT posts=2 data_points=0 "
        "acc=None weight_cap=0.0"
    )


def test_banner_skipped_when_quality_raises():
    # Quality computation failure must not block the analyst report.
    report = "BIAS: 看空 | CONFIDENCE: 高"
    llm = MagicMock()
    with patch(
        "tradingagents.agents.analysts.sentiment_analyst._run_tool_loop",
        return_value=report,
    ), patch(
        "tradingagents.agents.analysts.sentiment_analyst.sentiment_quality",
        side_effect=RuntimeError("boom"),
    ):
        result = create_commodity_sentiment_analyst(llm)(_state())

    assert result["sentiment_report"] == report  # unchanged, no banner
