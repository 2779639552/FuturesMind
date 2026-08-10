"""Tests for the optional Sentiment analyst in the commodity graph.

``build_commodity_graph(include_sentiment=False)`` must omit the
sentiment_analyst node entirely so a no-sentiment-data run degrades to the
3-analyst flow (Technical / Fundamental / Macro) while the debate, synthesis,
and scenario stages still run.
"""

from unittest.mock import MagicMock, patch

import pytest

from commodity_demo import build_commodity_graph


def _config() -> dict:
    return {"llm_provider": "mock", "quick_think_llm": "q", "deep_think_llm": "d"}


def _fake_llm_client():
    client = MagicMock()
    client.get_llm.return_value = MagicMock()
    return client


@pytest.mark.unit
class TestCommodityGraphSentimentOptional:
    def test_exclude_sentiment_drops_node_and_edges(self):
        with patch("commodity_demo.create_llm_client", return_value=_fake_llm_client()):
            app, _ = build_commodity_graph(
                _config(), enable_feedback=False, include_sentiment=False
            )
        nodes = set(app.get_graph().nodes.keys())
        assert "sentiment_analyst" not in nodes
        for analyst in (
            "technical_analyst",
            "fundamental_analyst",
            "macro_analyst",
            "bull_opening",
            "synthesis",
            "scenario_analysis",
        ):
            assert analyst in nodes, f"expected node {analyst!r} in {sorted(nodes)}"

    def test_include_sentiment_keeps_node(self):
        with patch("commodity_demo.create_llm_client", return_value=_fake_llm_client()):
            app, _ = build_commodity_graph(_config(), enable_feedback=False, include_sentiment=True)
        nodes = set(app.get_graph().nodes.keys())
        assert "sentiment_analyst" in nodes
