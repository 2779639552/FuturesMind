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


# 【2026-09-03】模块可选:build_commodity_graph 支持按 include_debate/synthesis/scenario
# 跳过辩论/综合研判/情景。compile() 不抛错即证明每个已注册节点都可达且有到 END 的路径,
# 因此"节点集断言 + 能编译"就能覆盖各跳档的接线正确性。
DEBATE_NODES = ("bull_opening", "bear_refute", "bull_rebuttal", "debate_moderator")
SYNTH_NODES = ("synthesis",)
SCENARIO_NODES = ("scenario_analysis",)


@pytest.mark.unit
class TestCommodityGraphModuleSkips:
    def _build(self, **kw):
        with patch("commodity_demo.create_llm_client", return_value=_fake_llm_client()):
            app, _ = build_commodity_graph(
                _config(), enable_feedback=False, include_sentiment=False, **kw
            )
        return set(app.get_graph().nodes.keys())

    def test_default_full_keeps_debate_synthesis_scenario(self):
        nodes = self._build()
        assert all(n in nodes for n in DEBATE_NODES)
        assert "synthesis" in nodes
        assert "scenario_analysis" in nodes

    def test_skip_debate_keeps_synthesis_and_scenario(self):
        nodes = self._build(include_debate=False)
        assert all(n not in nodes for n in DEBATE_NODES)  # 辩论 4 节点全移除
        assert "synthesis" in nodes
        assert "scenario_analysis" in nodes

    def test_skip_scenario_keeps_debate_and_synthesis(self):
        nodes = self._build(include_scenario=False)
        assert all(n in nodes for n in DEBATE_NODES)
        assert "synthesis" in nodes
        assert "scenario_analysis" not in nodes

    def test_skip_synthesis_forces_debate_and_scenario_off(self):
        nodes = self._build(include_synthesis=False)
        assert all(n not in nodes for n in DEBATE_NODES)
        assert "synthesis" not in nodes
        assert "scenario_analysis" not in nodes
        # 极简档:三位分析师仍在(compile 不抛错 ⇒ 各分析师分支可达且能汇入 END)。
        for analyst in ("technical_analyst", "fundamental_analyst", "macro_analyst"):
            assert analyst in nodes

    def test_skip_debate_and_scenario_keeps_synthesis(self):
        nodes = self._build(include_debate=False, include_scenario=False)
        assert all(n not in nodes for n in DEBATE_NODES)
        assert "synthesis" in nodes
        assert "scenario_analysis" not in nodes

    def test_module_skips_combine_with_sentiment(self):
        # 跳过辩论且带情绪:sentiment 仍在,且 compile 通过(情绪分支正确汇入 synthesis)。
        with patch("commodity_demo.create_llm_client", return_value=_fake_llm_client()):
            app, _ = build_commodity_graph(
                _config(),
                enable_feedback=False,
                include_sentiment=True,
                include_debate=False,
                include_synthesis=True,
                include_scenario=True,
            )
        nodes = set(app.get_graph().nodes.keys())
        assert "sentiment_analyst" in nodes
        assert all(n not in nodes for n in DEBATE_NODES)
        assert "synthesis" in nodes
