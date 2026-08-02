"""
Sentiment Analyst for commodity futures — the 4th parallel analyst.
=========================================================================

Consumes social media sentiment data (from 思路2 project) and provides
market psychology analysis: sentiment direction, extreme readings,
sentiment-price divergence detection, platform consistency, and retail
positioning signals.

This is a drop-in addition to the existing 3-analyst architecture:
  [Technical ∥ Fundamental ∥ Macro ∥ Sentiment] → Discussion → Synthesis

Note: This file replaces the original stock-market sentiment_analyst.py
      (which used Yahoo Finance + StockTwits + Reddit). The original
      stock-path analyst continues to work via social_media_analyst.py.
"""

import logging

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.agent_utils import get_language_instruction
from tradingagents.agents.utils.commodity_futures_tools import (
    get_futures_price,
    get_futures_sentiment,
    get_verified_quote,
    get_variety_info,
)

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 4  # Sentiment analyst needs fewer iterations (no heavy data retrieval)


def _run_tool_loop(llm, tools, initial_messages, max_iterations=MAX_TOOL_ITERATIONS,
                   progress_callback=None, label="Sentiment"):
    """Execute a tool-calling loop — mirrors commodity_analysts._run_tool_loop.

    Copied here to keep the analyst module self-contained and avoid
    cross-import between the stock and commodity analyst paths.
    """
    tool_map = {t.name: t for t in tools}
    messages = list(initial_messages)
    iteration = 0

    while iteration < max_iterations:
        iteration += 1
        if progress_callback:
            progress_callback("iteration", {"current": iteration, "max": max_iterations})

        response = llm.bind_tools(tools).invoke(messages)
        messages.append(response)

        if progress_callback and response.content:
            content_preview = response.content[:500] if len(response.content) > 500 else response.content
            progress_callback("llm_thinking", {"content": content_preview, "iteration": iteration})

        if not response.tool_calls:
            if progress_callback:
                progress_callback("report_start", {"label": label})
            return response.content

        for tc in response.tool_calls:
            tool_name = tc.get("name", "")
            tool_args = tc.get("args", {})
            tool_id = tc.get("id", "")

            logger.info("Sentiment tool call: %s(%s)", tool_name, tool_args)

            args_brief = str(tool_args)
            if len(args_brief) > 100:
                args_brief = args_brief[:97] + "..."

            if progress_callback:
                progress_callback("tool_call", {
                    "tool_name": tool_name,
                    "args": tool_args,
                    "args_brief": args_brief,
                    "iteration": iteration,
                    "label": label,
                })

            if tool_name in tool_map:
                try:
                    result = tool_map[tool_name].invoke(tool_args)
                    if isinstance(result, str) and len(result) > 8000:
                        result = result[:8000] + "\n... (truncated for length)"
                except Exception as e:
                    result = f"TOOL_ERROR: {type(e).__name__}: {e}"
            else:
                result = f"Unknown tool: {tool_name}"

            if progress_callback:
                result_str = str(result)
                progress_callback("tool_result", {
                    "tool_name": tool_name,
                    "result_length": len(result_str),
                    "preview": result_str[:300] if len(result_str) > 300 else result_str,
                    "label": label,
                })

            messages.append(ToolMessage(content=str(result), tool_call_id=tool_id))

    logger.warning("Sentiment tool loop hit max iterations (%d). Returning last response.", max_iterations)
    return response.content if hasattr(response, 'content') else str(response)


# ---------------------------------------------------------------------------
# Commodity Sentiment Analyst Node
# ---------------------------------------------------------------------------

def create_commodity_sentiment_analyst(llm, label="Sentiment", progress_callback=None):
    """Create the 4th parallel analyst: Market Sentiment / Social Psychology.

    This analyst consumes social media sentiment data (from 思路2 project)
    and evaluates market psychology — a dimension not covered by the other
    three analysts (Technical = price structure, Fundamental = supply/demand,
    Macro = policy/economic cycles).

    Args:
        llm: The LLM client.
        label: Display label for progress reporting (default "Sentiment").
        progress_callback: Optional callback(event_type, data) for streaming.

    Returns:
        A node function compatible with LangGraph StateGraph.
    """

    def node(state):
        current_date = state["trade_date"]
        symbol = state["company_of_interest"]

        tools = [
            get_variety_info,
            get_futures_price,
            get_futures_sentiment,
            get_verified_quote,
        ]

        system_message = (
            """You are a commodity futures market sentiment analyst specializing in Chinese futures markets.

**Your Role**: Analyze social media sentiment (market psychology) for the given commodity futures contract. You fill a gap the other analysts miss: what are market participants *feeling* and *saying* — not just what prices and fundamentals show.

**Data Source**: Call `get_futures_sentiment` for social media sentiment data collected from Weibo, Zhihu, and Xiaohongshu (XHS). Also call `get_variety_info` for variety context and `get_futures_price` for price context.

**Analysis Framework**:

**1. Sentiment Direction & Strength** (from `get_futures_sentiment`):
   - Overall sentiment label (看多/偏多/中性/偏空/看空) and score
   - Bullish/Bearish/Neutral ratio — where does the crowd stand?
   - Sentiment trend: Is mood improving or deteriorating? Changes in mood often PRECEDE price moves by 1-3 days.

**2. Extreme Sentiment Detection** (CONTRARIAN SIGNAL — most valuable):
   - **Sentiment > 70% bullish → DANGER ZONE**: When retail/retail-adjacent social media is overwhelmingly bullish, the easy money has been made. This is often a TOP signal. Everyone who wants to be long IS long — who's left to buy?
   - **Sentiment > 70% bearish → OPPORTUNITY ZONE**: Panic and despair on social media. When everyone has given up, selling pressure is exhausted. This is often a BOTTOM signal.
   - The data will explicitly flag extreme readings — take them seriously.
   - Judge based on context: extreme sentiment + low volume = noise; extreme sentiment + high volume = signal.

**3. Sentiment-Price Divergence** (YOUR KEY CONTRIBUTION to the team):
   - **Bullish price + weakening sentiment**: Price rising but social mood turning cautious/skeptical. The rally is losing crowd support → potential TOP.
   - **Bearish price + improving sentiment**: Price falling but social mood turning optimistic/calm. Selling climax may be near → potential BOTTOM.
   - **Sideways price + extreme sentiment**: Consolidation with one-sided crowd opinion → BREAKOUT is coming (direction often OPPOSITE to crowd consensus).
   - Call `get_futures_price` and CROSS-REFERENCE with sentiment data to detect these divergences.

**4. Platform Consistency Check**:
   - Multi-platform agreement (e.g., Weibo + Zhihu + XHS all bullish) → higher signal confidence.
   - Platform divergence (e.g., Weibo bullish but Zhihu bearish) → market is divided → higher uncertainty → wider range expected.
   - Zhihu tends to attract more analytical/institutional-adjacent voices; Weibo is more retail. Divergence between them is informative.

**5. Retail Positioning as Contrarian Indicator**:
   - Social media sentiment is inherently RETAIL-skewed. Treat strong consensus as a contrarian signal.
   - The most profitable trades often come from fading extreme retail sentiment — but ONLY when fundamentals are also supportive.
   - If sentiment is extremely one-sided AND fundamentals (from the Fundamental analyst) point the same way, the trend may still have room to run. The contrarian signal is strongest when sentiment diverges from fundamentals.

**6. Low-Data Handling**:
   - If `total_posts_analyzed` < 10: Acknowledge data sparsity. Lower confidence. Suggested weighting: sentiment dimension ≤ 15%.
   - If < 3 posts: State clearly "社交媒体情绪数据不足，无法提供可靠的情绪分析。建议此维度权重为 0%。"
   - If data is marked as stale (>48h old): Note the staleness and reduce confidence further.

**7. Key Topics & Narratives**:
   - From the sentiment data, identify DOMINANT narratives driving sentiment. What stories are being told?
   - Distinguish between structural narratives (e.g., "房地产长期下行") vs event-driven narratives (e.g., "唐山限产").
   - Structural narratives are higher-confidence drivers of sentiment.

**Workflow**:
1. Call `get_variety_info` for variety context.
2. Call `get_futures_sentiment` for social sentiment data.
3. Call `get_futures_price` for recent price data to cross-reference.
4. Produce your analysis report.

**Output Format**:
Write a detailed sentiment analysis report (350-500 words). Structure:
- **情绪概况**: Overall sentiment direction, strength, trend, key stats.
- **极端信号检测**: Any extreme readings? Contrarian implications?
- **情绪-价格背离分析**: Cross-reference sentiment with price. Any divergences?
- **平台一致性**: Are platforms agreeing or diverging?
- **关键叙事**: Dominant narratives driving current sentiment.
- **数据质量**: Sample size, staleness, confidence assessment.

**CRITICAL — First line after your title MUST be exactly:**
```
BIAS: [看多/偏多/中性/偏空/看空] | CONFIDENCE: [高/中/低]
```

End with:
1. **Sentiment Bias**: 看多/偏多/中性/偏空/看空, Confidence: 高/中/低, and a short justification.
2. **Recommended Weight**: X% for the final synthesis.
3. **Key Signals Summary Table** (Markdown):

| 关键信号 | 方向 | 数值/状态 | 置信度 | 数据来源 |
|---------|------|----------|--------|---------|
| (至少填写5行) | 利多/利空 | 具体数值 | 高/中/低 | 数据源 |

"""
            + get_language_instruction()
        )

        # --- Self-Evolution Injection ---
        evolution_ctx = state.get("past_context", "")
        if evolution_ctx:
            system_message = evolution_ctx + "\n\n" + system_message
        # --- End Injection ---

        prompt = ChatPromptTemplate.from_messages([
            ("system",
             "You are a helpful AI assistant collaborating with other analysts."
             " Use the provided tools to gather data, then write your full analysis report."
             " You have access to: {tool_names}."
             " Today is {current_date}. Analyze commodity futures variety: {symbol}.\n{system_message}"),
            MessagesPlaceholder(variable_name="messages"),
        ])

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join(t.name for t in tools))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(symbol=symbol)

        initial_messages = [prompt.format_prompt(messages=state["messages"]).to_messages()[0]]

        try:
            report = _run_tool_loop(llm, tools, initial_messages,
                                    progress_callback=progress_callback, label=label)
        except Exception as e:
            logger.error("Sentiment analyst failed: %s", e)
            report = f"ANALYSIS_ERROR: Sentiment analysis failed: {e}"

        return {
            "messages": [HumanMessage(content=f"[Sentiment Analyst Report]\n{report}")],
            "sentiment_report": report,
        }

    return node


# ---------------------------------------------------------------------------
# Stock-path backwards-compatibility shims
# ---------------------------------------------------------------------------
# The stock trading path (original v0.3.1) imports create_sentiment_analyst
# and create_social_media_analyst from this module. These shims keep those
# imports working while the commodity path uses create_commodity_sentiment_analyst.
#
# These are thin wrappers that delegate to the original stock-path
# implementation in the social_media_analyst module if needed, or provide
# a no-op placeholder until the stock path is refactored.

def create_sentiment_analyst(llm):
    """Stock-path sentiment analyst — placeholder shim.

    The original stock sentiment analyst (Yahoo Finance + StockTwits + Reddit)
    has been replaced by the commodity sentiment analyst. This shim keeps the
    import chain intact for the stock trading path.

    To restore the original stock sentiment analyst, reinstate the pre-v2.3
    implementation from git history.
    """
    import warnings
    warnings.warn(
        "create_sentiment_analyst: stock-path sentiment analyst is a shim. "
        "Use create_commodity_sentiment_analyst for commodity futures analysis.",
        FutureWarning,
        stacklevel=2,
    )

    # Simple pass-through: returns a node that reports unavailability
    def sentiment_analyst_node(state):
        from langchain_core.messages import AIMessage
        ticker = state.get("company_of_interest", "unknown")
        msg = (
            f"[Sentiment Analyst] Social sentiment data not available for {ticker}. "
            "The stock-path sentiment analyst has been deprecated. "
            "Commodity futures sentiment analysis is available via the 思路2 project integration."
        )
        return {
            "messages": [AIMessage(content=msg)],
            "sentiment_report": msg,
        }
    return sentiment_analyst_node


def create_social_media_analyst(llm):
    """Deprecated alias for :func:`create_sentiment_analyst`.

    Kept so existing code that imports ``create_social_media_analyst``
    continues to work.
    """
    import warnings
    warnings.warn(
        "create_social_media_analyst is deprecated. Use create_sentiment_analyst instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return create_sentiment_analyst(llm)
