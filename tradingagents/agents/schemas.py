"""Pydantic schemas used by agents that produce structured output.

The framework's primary artifact is still prose: each agent's natural-language
reasoning is what users read in the saved markdown reports and what the
downstream agents read as context.  Structured output is layered onto the
three decision-making agents (Research Manager, Trader, Portfolio Manager)
so that:

- Their outputs follow consistent section headers across runs and providers
- Each provider's native structured-output mode is used (json_schema for
  OpenAI/xAI, response_schema for Gemini, tool-use for Anthropic)
- Schema field descriptions become the model's output instructions, freeing
  the prompt body to focus on context and the rating-scale guidance
- A render helper turns the parsed Pydantic instance back into the same
  markdown shape the rest of the system already consumes, so display,
  memory log, and saved reports keep working unchanged
"""

from __future__ import annotations  # 【调用包】延迟求值注解:允许前向引用(Pydantic 模型互相引用)

from enum import Enum  # 【调用包】枚举基类:定义五档评级/交易方向/情绪档位等枚举
from typing import Literal  # 【调用包】字面量类型:限定字段只能取指定字符串(如置信度档位)

from pydantic import BaseModel, Field, field_validator  # 【调用包】Pydantic 模型/字段/校验器:定义结构化输出 schema 并校验

# LLMs sometimes write a placeholder string ("None", "N/A", ...) into an optional
# numeric field instead of omitting it. Coerce those to None so the structured
# call validates instead of erroring (#1058). Pydantic still parses real numeric
# strings ("189.5") to float.
_NULLISH_FLOAT = {"", "none", "n/a", "na", "null", "nil", "-", "tbd", "unknown"}  # 【变量】视为"空值"的占位字符串集合:LLM 写出的 None/N/A 等归一化为 None


# 【功能】把 LLM 可能写出的占位字符串(如 "N/A")转成 None,确保可选数值字段校验通过。
# 【参数】value: 原始字段值(字符串或数字)。
# 【返回】占位字符串→None;其余值原样返回。
# 【关键】真实数字字符串("189.5")仍由 Pydantic 解析为 float。
def _coerce_optional_float(value):
    if isinstance(value, str) and value.strip().lower() in _NULLISH_FLOAT:
        return None
    return value


# ---------------------------------------------------------------------------
# Shared rating types
# ---------------------------------------------------------------------------


# 【功能】五档投资评级枚举(研究经理与投资组合经理共用),序列化值为字符串 "Buy" 等。
class PortfolioRating(str, Enum):
    """5-tier rating used by the Research Manager and Portfolio Manager."""

    BUY = "Buy"  # 【变量】买入(最看多)
    OVERWEIGHT = "Overweight"  # 【变量】超配(看多,但弱于 Buy)
    HOLD = "Hold"  # 【变量】持有/中性
    UNDERWEIGHT = "Underweight"  # 【变量】低配(看空,但弱于 Sell)
    SELL = "Sell"  # 【变量】卖出(最看空)


# 【功能】三档交易方向枚举(交易员专用):Buy/Hold/Sell。
class TraderAction(str, Enum):
    """3-tier transaction direction used by the Trader.

    The Trader's job is to translate the Research Manager's investment plan
    into a concrete transaction proposal: should the desk execute a Buy, a
    Sell, or sit on Hold this round.  Position sizing and the nuanced
    Overweight / Underweight calls happen later at the Portfolio Manager.
    """

    BUY = "Buy"  # 【变量】买入方向
    HOLD = "Hold"  # 【变量】持仓不动
    SELL = "Sell"  # 【变量】卖出方向


# ---------------------------------------------------------------------------
# Research Manager
# ---------------------------------------------------------------------------


# 【功能】研究经理产出的结构化投资计划,交接给交易员执行。
class ResearchPlan(BaseModel):
    """Structured investment plan produced by the Research Manager.

    Hand-off to the Trader: the recommendation pins the directional view,
    the rationale captures which side of the bull/bear debate carried the
    argument, and the strategic actions translate that into concrete
    instructions the trader can execute against.
    """

    recommendation: PortfolioRating = Field(  # 【变量】投资建议方向:五档评级之一,决定多空立场
        description=(
            "The investment recommendation. Exactly one of Buy / Overweight / "
            "Hold / Underweight / Sell. Reserve Hold for situations where the "
            "evidence on both sides is genuinely balanced; otherwise commit to "
            "the side with the stronger arguments."
        ),
    )
    rationale: str = Field(  # 【变量】辩论双方要点总结,结尾说明哪个论点导致该建议
        description=(
            "Conversational summary of the key points from both sides of the "
            "debate, ending with which arguments led to the recommendation. "
            "Speak naturally, as if to a teammate."
        ),
    )
    strategic_actions: str = Field(  # 【变量】给交易员的具体操作步骤(含与评级一致的仓位建议)
        description=(
            "Concrete steps for the trader to implement the recommendation, "
            "including position sizing guidance consistent with the rating."
        ),
    )


# 【功能】把 ResearchPlan 渲染回 markdown,供落盘存储与交易员提示词使用。
def render_research_plan(plan: ResearchPlan) -> str:
    """Render a ResearchPlan to markdown for storage and the trader's prompt context."""
    return "\n".join(
        [
            f"**Recommendation**: {plan.recommendation.value}",
            "",
            f"**Rationale**: {plan.rationale}",
            "",
            f"**Strategic Actions**: {plan.strategic_actions}",
        ]
    )


# ---------------------------------------------------------------------------
# Trader
# ---------------------------------------------------------------------------


# 【功能】交易员产出的结构化交易提案:把投资计划转成具体可执行的动作。
class TraderProposal(BaseModel):
    """Structured transaction proposal produced by the Trader.

    The trader reads the Research Manager's investment plan and the analyst
    reports, then turns them into a concrete transaction: what action to
    take, the reasoning that justifies it, and the practical levels for
    entry, stop-loss, and sizing.
    """

    action: TraderAction = Field(  # 【变量】交易方向:Buy/Hold/Sell 三选一
        description="The transaction direction. Exactly one of Buy / Hold / Sell.",
    )
    reasoning: str = Field(  # 【变量】交易理由(2-4 句,锚定分析师报告与研究计划)
        description=(
            "The case for this action, anchored in the analysts' reports and "
            "the research plan. Two to four sentences."
        ),
    )
    entry_price: float | None = Field(  # 【变量】入场目标价(单位:标的报价货币)
        default=None,
        description="Optional entry price target in the instrument's quote currency.",
    )
    stop_loss: float | None = Field(  # 【变量】止损价(单位:标的报价货币)
        default=None,
        description="Optional stop-loss price in the instrument's quote currency.",
    )
    position_sizing: str | None = Field(  # 【变量】仓位建议文本(如 '5% of portfolio')
        default=None,
        description="Optional sizing guidance, e.g. '5% of portfolio'.",
    )

    @field_validator("entry_price", "stop_loss", mode="before")
    @classmethod
    def _nullish_float_to_none(cls, v):
        return _coerce_optional_float(v)


# 【功能】把 TraderProposal 渲染为 markdown;末尾保留 "FINAL TRANSACTION PROPOSAL:" 行以兼容外部解析。
def render_trader_proposal(proposal: TraderProposal) -> str:
    """Render a TraderProposal to markdown.

    The trailing ``FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**`` line is
    preserved for backward compatibility with the analyst stop-signal text
    and any external code that greps for it.
    """
    parts = [
        f"**Action**: {proposal.action.value}",
        "",
        f"**Reasoning**: {proposal.reasoning}",
    ]
    if proposal.entry_price is not None:
        parts.extend(["", f"**Entry Price**: {proposal.entry_price}"])
    if proposal.stop_loss is not None:
        parts.extend(["", f"**Stop Loss**: {proposal.stop_loss}"])
    if proposal.position_sizing:
        parts.extend(["", f"**Position Sizing**: {proposal.position_sizing}"])
    parts.extend(
        [
            "",
            f"FINAL TRANSACTION PROPOSAL: **{proposal.action.value.upper()}**",
        ]
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Portfolio Manager
# ---------------------------------------------------------------------------


# 【功能】投资组合经理产出的结构化最终决定;字段由主 LLM 调用一次填满,无需二次抽取。
class PortfolioDecision(BaseModel):
    """Structured output produced by the Portfolio Manager.

    The model fills every field as part of its primary LLM call; no separate
    extraction pass is required. Field descriptions double as the model's
    output instructions, so the prompt body only needs to convey context and
    the rating-scale guidance.
    """

    rating: PortfolioRating = Field(  # 【变量】最终仓位评级(五档之一,依据分析师辩论选定)
        description=(
            "The final position rating. Exactly one of Buy / Overweight / Hold / "
            "Underweight / Sell, picked based on the analysts' debate."
        ),
    )
    executive_summary: str = Field(  # 【变量】简明行动计划:入场策略/仓位/关键风险位/时间跨度(2-4 句)
        description=(
            "A concise action plan covering entry strategy, position sizing, "
            "key risk levels, and time horizon. Two to four sentences."
        ),
    )
    investment_thesis: str = Field(  # 【变量】详细投资逻辑,锚定分析师辩论中的具体证据
        description=(
            "Detailed reasoning anchored in specific evidence from the analysts' "
            "debate. If prior lessons are referenced in the prompt context, "
            "incorporate them; otherwise rely solely on the current analysis."
        ),
    )
    price_target: float | None = Field(  # 【变量】目标价(单位:标的报价货币)
        default=None,
        description="Optional target price in the instrument's quote currency.",
    )
    time_horizon: str | None = Field(  # 【变量】建议持有期文本(如 '3-6 months')
        default=None,
        description="Optional recommended holding period, e.g. '3-6 months'.",
    )

    @field_validator("price_target", mode="before")
    @classmethod
    def _nullish_float_to_none(cls, v):
        return _coerce_optional_float(v)


# 【功能】把 PortfolioDecision 渲染为 markdown;节头固定(**Rating** 等)供下游解析与落盘。
def render_pm_decision(decision: PortfolioDecision) -> str:
    """Render a PortfolioDecision back to the markdown shape the rest of the system expects.

    Memory log, CLI display, and saved report files all read this markdown,
    so the rendered output preserves the exact section headers (``**Rating**``,
    ``**Executive Summary**``, ``**Investment Thesis**``) that downstream
    parsers and the report writers already handle.
    """
    parts = [
        f"**Rating**: {decision.rating.value}",
        "",
        f"**Executive Summary**: {decision.executive_summary}",
        "",
        f"**Investment Thesis**: {decision.investment_thesis}",
    ]
    if decision.price_target is not None:
        parts.extend(["", f"**Price Target**: {decision.price_target}"])
    if decision.time_horizon:
        parts.extend(["", f"**Time Horizon**: {decision.time_horizon}"])
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Sentiment Analyst
# ---------------------------------------------------------------------------


# 【功能】情绪分析师产出的离散情绪档位(六档),粒度可操作且各家 provider 都能稳定映射。
class SentimentBand(str, Enum):
    """Discrete sentiment direction produced by the Sentiment Analyst.

    Six tiers keep the signal granular enough to be actionable while remaining
    small enough for every provider to map reliably from its JSON output.
    """

    BULLISH = "Bullish"  # 【变量】看多
    MILDLY_BULLISH = "Mildly Bullish"  # 【变量】轻度看多
    NEUTRAL = "Neutral"  # 【变量】中性(各来源都沉默/不表态时使用)
    MIXED = "Mixed"  # 【变量】混合(来源指向明显不同方向时使用)
    MILDLY_BEARISH = "Mildly Bearish"  # 【变量】轻度看空
    BEARISH = "Bearish"  # 【变量】看空


# 【功能】情绪分析师产出的结构化情绪报告,替代原自由文本,供下游(看板/审计/其他代理)稳定解析。
class SentimentReport(BaseModel):
    """Structured sentiment report produced by the Sentiment Analyst.

    Replaces the previous free-form prose output so downstream consumers
    (dashboards, audit logs, PDF renderers, other agents) can read
    ``overall_band`` and ``overall_score`` without maintaining fragile regex
    fallbacks that drift with every model release. ``narrative`` preserves the
    rich source-by-source analysis; ``render_sentiment_report`` prepends a
    deterministic header so the saved report stays human-readable.
    """

    overall_band: SentimentBand = Field(  # 【变量】整体情绪方向(六档之一)
        description=(
            "Overall sentiment direction. Exactly one of: "
            "Bullish / Mildly Bullish / Neutral / Mixed / Mildly Bearish / Bearish. "
            "Use Mixed when sources point in clearly different directions. "
            "Use Neutral only when all sources are genuinely silent or non-committal."
        ),
    )
    overall_score: float = Field(  # 【变量】情绪强度 0-10(0=极度看空,5=中性,10=极度看多)
        ge=0.0,
        le=10.0,
        description=(
            "Numeric sentiment intensity on a 0–10 scale. "
            "0 = maximally bearish, 5 = neutral, 10 = maximally bullish. "
            "Guideline for consistency with overall_band: "
            "Bullish ~6.5–10, Mildly Bullish ~5.5–6.4, Neutral/Mixed ~4.5–5.5, "
            "Mildly Bearish ~3.5–4.4, Bearish ~0–3.4. "
            "Only the 0–10 bounds are enforced."
        ),
    )
    confidence: Literal["low", "medium", "high"] = Field(  # 【变量】置信度:由数据质量与样本量决定(low/medium/high)
        description=(
            "Confidence in the assessment based on data quality and sample size. "
            "Use 'low' when one or more sources returned a placeholder or fewer "
            "than 5 data points; 'medium' when data is present but sparse; "
            "'high' when all three sources returned substantive data."
        ),
    )
    narrative: str = Field(  # 【变量】完整情绪报告正文:分来源明细/分歧/主题/催化与风险/信号汇总表
        description=(
            "Full sentiment report covering, in order: "
            "(1) source-by-source breakdown with specific evidence (cite message "
            "counts, ratios, notable posts); "
            "(2) cross-source divergences and alignments; "
            "(3) dominant narrative themes; "
            "(4) catalysts and risks surfaced by the data; "
            "(5) a markdown table summarising key sentiment signals, their "
            "direction, source, and supporting evidence. "
            "Keep it informative and substantive: develop each section thoroughly "
            "with concrete evidence so every point adds new signal for the trader."
        ),
    )


# 【功能】把 SentimentReport 渲染为 markdown;前置确定性结构化头(波段+评分+置信度)供机器解析。
def render_sentiment_report(report: SentimentReport) -> str:
    """Render a SentimentReport to the markdown shape the rest of the system expects.

    The structured header (band + score + confidence) is prepended to the
    narrative so the saved report is both human-readable and machine-parseable
    without regex.
    """
    return "\n".join(
        [
            f"**Overall Sentiment:** **{report.overall_band.value}** "
            f"(Score: {report.overall_score:.1f}/10)",
            f"**Confidence:** {report.confidence.capitalize()}",
            "",
            report.narrative,
        ]
    )
