"""Pydantic schemas for commodity futures analysts — v2.4 structured output.

Mirrors the original TradingAgents' schemas.py pattern: each decision-making
agent produces a validated Pydantic model so the output header is guaranteed
parseable across runs and LLM providers.

For DeepSeek: uses OpenAI-compatible json_schema response_format.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Shared enums
# ---------------------------------------------------------------------------


class CommodityBias(str, Enum):
    """5-tier directional bias for commodity futures."""

    STRONG_BULLISH = "强烈看多"
    SLIGHTLY_BULLISH = "偏多"
    NEUTRAL = "中性"
    SLIGHTLY_BEARISH = "偏空"
    STRONG_BEARISH = "强烈看空"


class Confidence(str, Enum):
    HIGH = "高"
    MEDIUM = "中"
    LOW = "低"


# ---------------------------------------------------------------------------
# Analyst Report (4 analysts — same schema, different narratives)
# ---------------------------------------------------------------------------


class AnalystBiasReport(BaseModel):
    """Structured bias header produced by each of the 4 commodity analysts.

    The narrative field carries the full markdown report body; the bias and
    confidence fields provide a deterministic, machine-parseable header that
    survives model drift and provider switches.
    """

    bias: CommodityBias = Field(
        description=(
            "Your directional bias for this commodity variety. Choose exactly one: "
            "强烈看多 (multiple signals strongly bullish), "
            "偏多 (net bullish tilt with caveats), "
            "中性 (balanced or conflicting signals), "
            "偏空 (net bearish tilt with caveats), "
            "强烈看空 (multiple signals strongly bearish)."
        ),
    )
    confidence: Confidence = Field(
        description=(
            "Confidence in your bias. 高=data-rich with aligned signals, "
            "中=some data gaps or moderate divergence, "
            "低=significant data gaps or strong conflicting signals."
        ),
    )
    narrative: str = Field(
        description=(
            "Your complete analysis report in Chinese markdown. Include all "
            "sections as specified in your system prompt (data analysis, "
            "key findings, evidence, and conclusions)."
        ),
    )


# ---------------------------------------------------------------------------
# Synthesis Report (chief strategist)
# ---------------------------------------------------------------------------


class SynthesisReport(BaseModel):
    """Structured synthesis produced by the chief commodity strategist.

    Combines the four analyst reports and discussion summary into a final
    weighted recommendation with explicit numeric score and weight allocation.
    """

    rating: CommodityBias = Field(
        description=(
            "Final directional rating. 强烈看多/偏多/中性/偏空/强烈看空. "
            "Must be consistent with the score (0-10 scale)."
        ),
    )
    confidence: Confidence = Field(
        description="Overall confidence after reviewing all four dimensions."
    )
    score: float = Field(
        ge=0.0,
        le=10.0,
        description=(
            "Numeric conviction on a 0-10 scale. 0=max bearish, 5=neutral, "
            "10=max bullish. Guideline: 强烈看多~7.5-10, 偏多~5.5-7.4, "
            "中性~4-6, 偏空~2.5-4.4, 强烈看空~0-2.5."
        ),
    )
    tech_weight: int = Field(
        ge=0, le=10, description="Technical analyst weight, 0-10, must sum with others to 10."
    )
    fund_weight: int = Field(ge=0, le=10, description="Fundamental analyst weight, 0-10.")
    macro_weight: int = Field(ge=0, le=10, description="Macro/news analyst weight, 0-10.")
    sentiment_weight: int = Field(ge=0, le=10, description="Sentiment analyst weight, 0-10.")
    key_support: str | None = Field(
        default=None,
        description="Key support level(s) with price values, e.g. '3050-3053 (前低+布林下轨)'.",
    )
    key_resistance: str | None = Field(
        default=None, description="Key resistance level(s) with price values."
    )
    risk_factors: str | None = Field(
        default=None, description="Primary risk factors that could invalidate the recommendation."
    )
    narrative: str = Field(
        description=(
            "Full synthesis report in Chinese markdown. Include: 四维度一致性分析, "
            "近因偏差检查, 加权判断理由, 最终建议详情, 操作参考."
        ),
    )


# ---------------------------------------------------------------------------
# Debate Moderator Report
# ---------------------------------------------------------------------------


class DebateModeratorReport(BaseModel):
    """Structured output from the debate moderator after bull/bear rounds."""

    winner: Literal["bull", "bear", "draw"] = Field(
        description="Which side carried the stronger arguments."
    )
    consensus_points: list[str] = Field(
        default_factory=list, description="Points both sides agreed on (2-4 items)."
    )
    divergence_points: list[str] = Field(
        default_factory=list,
        description="Key disagreements that could not be resolved (2-4 items).",
    )
    key_risk: str = Field(
        description="The single most important risk identified during the debate."
    )
    narrative: str = Field(description="Complete debate summary in Chinese markdown.")


# ---------------------------------------------------------------------------
# Render helpers — turn Pydantic instances back to markdown
# ---------------------------------------------------------------------------


def render_analyst_bias(report: AnalystBiasReport) -> str:
    """Render analyst structured header + narrative to markdown."""
    header = f"BIAS: {report.bias.value} | CONFIDENCE: {report.confidence.value}"
    return header + "\n\n" + report.narrative


def render_synthesis(report: SynthesisReport) -> str:
    """Render synthesis structured output to markdown."""
    header = (
        f"RATING: {report.rating.value} | "
        f"CONFIDENCE: {report.confidence.value} | "
        f"SCORE: {int(report.score)}"
    )
    parts = [header, "", report.narrative]
    return "\n".join(parts)


def render_debate_moderator(report: DebateModeratorReport) -> str:
    """Render debate moderator output to markdown."""
    parts = [
        f"**Debate Winner**: {report.winner}",
        "",
        "**Consensus Points**:",
    ]
    for p in report.consensus_points:
        parts.append(f"- {p}")
    parts.append("")
    parts.append("**Divergence Points**:")
    for p in report.divergence_points:
        parts.append(f"- {p}")
    parts.extend(
        [
            "",
            f"**Key Risk**: {report.key_risk}",
            "",
            report.narrative,
        ]
    )
    return "\n".join(parts)
