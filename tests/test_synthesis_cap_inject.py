"""Tests for the hard weight-cap injection into the synthesis prompt (2026-08-25).

The synthesis node parses `[SENTIMENT_QUALITY] weight_cap` from the sentiment
report and injects an unbreakable "Sentiment weight HARD CAP" line into the LLM
prompt, overriding the dynamic weight formula and the special rules (including
the divergence-doubling rule). This is the "强制门控" landing point.

No real LLM is invoked — `llm` is a MagicMock whose `.invoke` is captured.
"""

from unittest.mock import MagicMock

from commodity_demo import _parse_sentiment_cap, create_synthesis_node

_BANNER_30 = (
    "[SENTIMENT_QUALITY] level=MEDIUM posts=16 data_points=13 "
    "acc=0.615 weight_cap=0.3 platforms=3\n"
)
_BANNER_100 = (
    "[SENTIMENT_QUALITY] level=HIGH posts=60 data_points=20 "
    "acc=0.58 weight_cap=1.0 platforms=3\n"
)


def _state(sentiment):
    return {
        "company_of_interest": "RB",
        "technical_report": "tech",
        "fundamental_report": "fund",
        "macro_report": "macro",
        "sentiment_report": sentiment,
        "discussion_summary": "disc",
    }


def _capture_prompt(sentiment):
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="RATING: 中性 | CONFIDENCE: 中 | SCORE: 5")
    node = create_synthesis_node(llm)
    node(_state(sentiment))
    assert llm.invoke.call_count == 1
    return llm.invoke.call_args[0][0]


# ---------------------------------------------------------------------------
# _parse_sentiment_cap unit tests
# ---------------------------------------------------------------------------

def test_parse_cap_valid():
    assert _parse_sentiment_cap(_BANNER_30) == 0.3


def test_parse_cap_missing_banner_returns_none():
    assert _parse_sentiment_cap("BIAS: 看多 | CONFIDENCE: 中\n\nreport body") is None


def test_parse_cap_malformed_returns_none():
    bad = "[SENTIMENT_QUALITY] level=HIGH posts=50 weight_cap=abc platforms=3\n"
    assert _parse_sentiment_cap(bad) is None


def test_parse_cap_missing_weight_cap_returns_none():
    bad = "[SENTIMENT_QUALITY] level=HIGH posts=50 platforms=3\n"
    assert _parse_sentiment_cap(bad) is None


# ---------------------------------------------------------------------------
# Prompt injection
# ---------------------------------------------------------------------------

def test_cap_injected_as_hard_number():
    prompt = _capture_prompt(_BANNER_30)
    assert "Sentiment weight HARD CAP" in prompt
    assert "不得超过 3/10" in prompt       # 0.3 * 10 = 3
    assert "（30%）" in prompt


def test_no_banner_falls_back_to_sparse_rule():
    prompt = _capture_prompt("BIAS: 看多 | CONFIDENCE: 中\n\nno quality banner")
    assert "Sparse sentiment data (<10 posts/day)" in prompt
    assert "HARD CAP" not in prompt


def test_cap_one_keeps_sparse_fallback():
    # cap=1.0 means "no hard cap" → keep the old sparse-sentiment wording
    prompt = _capture_prompt(_BANNER_100)
    assert "Sparse sentiment data (<10 posts/day)" in prompt
    assert "HARD CAP" not in prompt
