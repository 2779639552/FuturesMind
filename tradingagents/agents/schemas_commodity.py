"""Pydantic schemas for commodity futures analysts — v2.4 structured output.

Mirrors the original TradingAgents' schemas.py pattern: each decision-making
agent produces a validated Pydantic model so the output header is guaranteed
parseable across runs and LLM providers.

For DeepSeek: uses OpenAI-compatible json_schema response_format.
"""

from __future__ import annotations  # 【调用包】延迟求值注解:允许前向引用

from enum import Enum  # 【调用包】枚举基类:定义商品方向/置信度枚举
from typing import Literal  # 【调用包】字面量类型:限定字段取值(如辩论胜方)

from pydantic import BaseModel, Field  # 【调用包】Pydantic 模型/字段:定义商品期货结构化输出 schema

# ---------------------------------------------------------------------------
# Shared enums
# ---------------------------------------------------------------------------


# 【功能】商品期货五档方向性偏见枚举,序列化值为中文(与分析师中文报告一致)。
class CommodityBias(str, Enum):
    """5-tier directional bias for commodity futures."""

    STRONG_BULLISH = "强烈看多"  # 【变量】强烈看多(多重信号强烈偏多)
    SLIGHTLY_BULLISH = "偏多"  # 【变量】偏多(净偏多但有保留)
    NEUTRAL = "中性"  # 【变量】中性(信号均衡或冲突)
    SLIGHTLY_BEARISH = "偏空"  # 【变量】偏空(净偏空但有保留)
    STRONG_BEARISH = "强烈看空"  # 【变量】强烈看空(多重信号强烈偏空)


# 【功能】置信度三档枚举(高/中/低)。
class Confidence(str, Enum):
    HIGH = "高"  # 【变量】数据充足且信号一致
    MEDIUM = "中"  # 【变量】部分数据缺口或中等分歧
    LOW = "低"  # 【变量】显著数据缺口或强冲突信号


# ---------------------------------------------------------------------------
# Analyst Report (4 analysts — same schema, different narratives)
# ---------------------------------------------------------------------------


# 【功能】四位商品分析师(技术/基本面/宏观/情绪)共用的结构化偏见头报告。
class AnalystBiasReport(BaseModel):
    """Structured bias header produced by each of the 4 commodity analysts.

    The narrative field carries the full markdown report body; the bias and
    confidence fields provide a deterministic, machine-parseable header that
    survives model drift and provider switches.
    """

    bias: CommodityBias = Field(  # 【变量】方向性判断(五档:强烈看多/偏多/中性/偏空/强烈看空)
        description=(
            "Your directional bias for this commodity variety. Choose exactly one: "
            "强烈看多 (multiple signals strongly bullish), "
            "偏多 (net bullish tilt with caveats), "
            "中性 (balanced or conflicting signals), "
            "偏空 (net bearish tilt with caveats), "
            "强烈看空 (multiple signals strongly bearish)."
        ),
    )
    confidence: Confidence = Field(  # 【变量】对该偏见的置信度(高/中/低,由数据量与信号一致性决定)
        description=(
            "Confidence in your bias. 高=data-rich with aligned signals, "
            "中=some data gaps or moderate divergence, "
            "低=significant data gaps or strong conflicting signals."
        ),
    )
    narrative: str = Field(  # 【变量】完整中文分析报告正文(数据分析/关键发现/证据/结论)
        description=(
            "Your complete analysis report in Chinese markdown. Include all "
            "sections as specified in your system prompt (data analysis, "
            "key findings, evidence, and conclusions)."
        ),
    )


# ---------------------------------------------------------------------------
# Synthesis Report (chief strategist)
# ---------------------------------------------------------------------------


# 【功能】首席商品策略师产出的结构化综合报告:合并四分析师报告与讨论纪要,给加权建议与权重分配。
class SynthesisReport(BaseModel):
    """Structured synthesis produced by the chief commodity strategist.

    Combines the four analyst reports and discussion summary into a final
    weighted recommendation with explicit numeric score and weight allocation.
    """

    rating: CommodityBias = Field(  # 【变量】最终方向评级,须与 0-10 score 一致
        description=(
            "Final directional rating. 强烈看多/偏多/中性/偏空/强烈看空. "
            "Must be consistent with the score (0-10 scale)."
        ),
    )
    confidence: Confidence = Field(  # 【变量】综合四个维度后的总体置信度
        description="Overall confidence after reviewing all four dimensions."
    )
    score: float = Field(  # 【变量】数值信心分 0-10(0=极度看空,5=中性,10=极度看多)
        ge=0.0,
        le=10.0,
        description=(
            "Numeric conviction on a 0-10 scale. 0=max bearish, 5=neutral, "
            "10=max bullish. Guideline: 强烈看多~7.5-10, 偏多~5.5-7.4, "
            "中性~4-6, 偏空~2.5-4.4, 强烈看空~0-2.5."
        ),
    )
    tech_weight: int = Field(  # 【变量】技术面分析师权重 0-10,四项之和须为 10
        ge=0, le=10, description="Technical analyst weight, 0-10, must sum with others to 10."
    )
    fund_weight: int = Field(ge=0, le=10, description="Fundamental analyst weight, 0-10.")  # 【变量】基本面分析师权重 0-10
    macro_weight: int = Field(ge=0, le=10, description="Macro/news analyst weight, 0-10.")  # 【变量】宏观/新闻分析师权重 0-10
    sentiment_weight: int = Field(ge=0, le=10, description="Sentiment analyst weight, 0-10.")  # 【变量】情绪分析师权重 0-10
    key_support: str | None = Field(  # 【变量】关键支撑位(含价位,如 '3050-3053 (前低+布林下轨)')
        default=None,
        description="Key support level(s) with price values, e.g. '3050-3053 (前低+布林下轨)'.",
    )
    key_resistance: str | None = Field(  # 【变量】关键阻力位(含价位)
        default=None, description="Key resistance level(s) with price values."
    )
    risk_factors: str | None = Field(  # 【变量】可能使建议失效的主要风险因素
        default=None, description="Primary risk factors that could invalidate the recommendation."
    )
    narrative: str = Field(  # 【变量】综合中文报告正文:四维度一致性/近因偏差/加权理由/最终建议/操作参考
        description=(
            "Full synthesis report in Chinese markdown. Include: 四维度一致性分析, "
            "近因偏差检查, 加权判断理由, 最终建议详情, 操作参考."
        ),
    )


# ---------------------------------------------------------------------------
# Debate Moderator Report
# ---------------------------------------------------------------------------


# 【功能】辩论主持人(多空对决)产出的结构化报告。
class DebateModeratorReport(BaseModel):
    """Structured output from the debate moderator after bull/bear rounds."""

    winner: Literal["bull", "bear", "draw"] = Field(  # 【变量】辩论胜方:bull(多方)/bear(空方)/draw(平局)
        description="Which side carried the stronger arguments."
    )
    consensus_points: list[str] = Field(  # 【变量】双方一致同意的观点(2-4 条)
        default_factory=list, description="Points both sides agreed on (2-4 items)."
    )
    divergence_points: list[str] = Field(  # 【变量】未能解决的关键分歧(2-4 条)
        default_factory=list,
        description="Key disagreements that could not be resolved (2-4 items).",
    )
    key_risk: str = Field(  # 【变量】辩论中识别出的最重要风险
        description="The single most important risk identified during the debate."
    )
    narrative: str = Field(description="Complete debate summary in Chinese markdown.")  # 【变量】中文完整辩论总结正文


# ---------------------------------------------------------------------------
# Render helpers — turn Pydantic instances back to markdown
# ---------------------------------------------------------------------------


# 【功能】把分析师结构化头(BIAS|CONFIDENCE)+ 叙述拼成 markdown 返回。
def render_analyst_bias(report: AnalystBiasReport) -> str:
    """Render analyst structured header + narrative to markdown."""
    header = f"BIAS: {report.bias.value} | CONFIDENCE: {report.confidence.value}"  # 【变量】确定性可解析的分析师头部行
    return header + "\n\n" + report.narrative


# 【功能】把综合报告渲染为 markdown;头部含 RATING/CONFIDENCE/SCORE 三字段。
def render_synthesis(report: SynthesisReport) -> str:
    """Render synthesis structured output to markdown."""
    header = (  # 【变量】确定性可解析的综合报告头部行
        f"RATING: {report.rating.value} | "
        f"CONFIDENCE: {report.confidence.value} | "
        f"SCORE: {int(report.score)}"
    )
    parts = [header, "", report.narrative]
    return "\n".join(parts)


# 【功能】把辩论主持人结果渲染为 markdown(胜方/共识点/分歧点/关键风险 + 叙述)。
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
