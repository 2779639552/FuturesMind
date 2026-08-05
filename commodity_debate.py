"""Multi-round adversarial debate nodes for commodity futures (v2.8).

v2.8: Debate agents now have tool access via _run_tool_loop — they can
fetch live prices, verify claims with data, and check sentiment during
the debate. This replaces the old LLM-only invoke pattern.

Replaces the single-round Discussion node with Bull/Bear/Moderator three-node
debate system, mirroring the original TradingAgents' Bull Researcher/Bear
Researcher/Research Manager pattern.
"""

import logging
from langchain_core.messages import HumanMessage

from tradingagents.agents.analysts.commodity_analysts import _run_tool_loop
from tradingagents.agents.utils.commodity_futures_tools import (
    get_futures_price,
    get_futures_indicators,
    get_futures_sentiment,
    get_realtime_price,
    get_variety_info,
    get_verified_quote,
)

logger = logging.getLogger(__name__)

# Debate tool loop: fewer iterations than analysts (debate needs quick fact-checks)
DEBATE_MAX_ITERATIONS = 3

# Tools available to debaters (fact-checking + live data)
DEBATE_TOOLS = [
    get_realtime_price,       # Live market price
    get_verified_quote,       # Precise historical snapshot
    get_futures_price,        # Recent price history
    get_futures_indicators,   # Technical levels (support/resistance)
    get_futures_sentiment,    # Social sentiment check
    get_variety_info,         # Variety metadata
]

# Moderator tools: narrower set, focused on fact-checking
MODERATOR_TOOLS = [
    get_realtime_price,
    get_verified_quote,
    get_futures_price,
]

# Helper — stage header (shared with commodity_demo.py)
# When a progress_callback is provided, output is routed there;
# otherwise falls back to structured logging.
def _print_stage_header(title, progress_callback=None):
    if progress_callback:
        progress_callback("stage_header", {"title": title})
    else:
        logger.info("=== %s ===", title)


def _debate_progress_callback(event_type, data):
    """Lightweight progress callback for debate tool calls."""
    label = data.get("label", "Debate")
    if event_type == "tool_call":
        logger.debug("[%s] %s(%s)", label, data["tool_name"], data.get("args_brief", ""))
    elif event_type == "tool_result":
        logger.debug("[%s] <- %s chars from %s", label, data.get("result_length", 0), data["tool_name"])


def create_bull_debater(llm, label="Bull"):
    """Build the strongest possible bullish case from all four analyst reports.

    v2.8: Now has TOOL ACCESS — can fetch live prices, verify claims,
    and check sentiment data to strengthen arguments during debate.
    Engages directly with the bear's last argument (multi-round), cites
    specific data points, and acknowledges valid bear concerns while
    arguing the bullish thesis outweighs them.
    """

    def node(state):
        technical = state.get("technical_report", "")
        fundamental = state.get("fundamental_report", "")
        macro = state.get("macro_report", "")
        sentiment = state.get("sentiment_report", "")
        symbol = state["company_of_interest"]
        trade_date = state.get("trade_date", "")
        debate_state = state.get("debate_state", {})
        bear_last = debate_state.get("bear_last", "")
        round_num = debate_state.get("round", 0) + 1

        if bear_last:
            opponent_context = f"空方论点（需要反驳）：\n{bear_last[:1000]}"
        else:
            opponent_context = "请发表你的开篇多方立论。"

        system_message = f"""You are the BULL (多方) debater for commodity futures variety `{symbol}` (as of {trade_date}).

**Your Role**: Build the strongest possible bullish (看涨/看多) case. You have access to data tools — use them to fact-check claims and find supporting evidence.

**Rules**:
1. Cite SPECIFIC data (prices, levels, ratios) — use tools to verify
2. Engage directly with the bear's arguments — point out flaws or missing context
3. Acknowledge valid bear concerns, but argue why the bullish thesis outweighs them
4. Be conversational and persuasive, like a real debate
5. **Use tools BEFORE making claims** — verify with get_realtime_price() or get_verified_quote()

**Tool Guidance**:
- `get_realtime_price("{symbol}")` — check the current live market price
- `get_verified_quote("{symbol}", "{trade_date}")` — get exact OHLCV + key levels for fact-checking
- `get_futures_price("{symbol}", ...) ` — get recent price history
- `get_futures_indicators("{symbol}", ...) ` — get technical levels (SMA, RSI, MACD, Bollinger)
- `get_futures_sentiment("{symbol}")` — check social media sentiment direction
- `get_variety_info("{symbol}")` — variety metadata (specs, trading hours)

**Analyst Reports (for context)**:
---
Technical: {technical[:1200] if technical else 'N/A'}
Fundamental: {fundamental[:1200] if fundamental else 'N/A'}
Macro/News: {macro[:800] if macro else 'N/A'}
Sentiment: {sentiment[:600] if sentiment else 'N/A'}
---

{opponent_context}

**Output**: Write 200-400 characters in Chinese. Start with "多方(R{round_num})：". Structure your argument with data, reasoning, and a rebuttal to the bear case.
"""
        logger.info("Bull R%d (with tools)...", round_num)

        # Build initial message
        initial_msg = HumanMessage(content=system_message)

        # Run tool-calling loop
        try:
            result = _run_tool_loop(
                llm, DEBATE_TOOLS, [initial_msg],
                max_iterations=DEBATE_MAX_ITERATIONS,
                progress_callback=_debate_progress_callback,
                label=f"Bull-R{round_num}",
            )
        except Exception as e:
            logger.error("Bull debater failed: %s", e)
            # Fallback to simple invoke
            fallback = llm.invoke(f"用中文为 {symbol} 商品期货构建最强看涨论点。引用数据。\n技术面：{technical[:1500]}\n基本面：{fundamental[:1500]}\n宏观：{macro[:1000]}")
            result = fallback.content if hasattr(fallback, 'content') else str(fallback)

        if not result:
            logger.warning("Bull returned empty — retrying with simpler prompt")
            fallback = llm.invoke(f"用中文为 {symbol} 商品期货构建最强看涨论点。引用数据。\n技术面：{technical[:1500]}\n基本面：{fundamental[:1500]}\n宏观：{macro[:1000]}")
            result = fallback.content if hasattr(fallback, 'content') else str(fallback)

        new_debate_state = {
            "bull_history": debate_state.get("bull_history", "") + "\n" + result,
            "bear_history": debate_state.get("bear_history", ""),
            "bull_last": result,
            "bear_last": debate_state.get("bear_last", ""),
            "round": round_num,
        }

        return {
            "debate_state": new_debate_state,
            "messages": [HumanMessage(content=f"[Bull R{round_num}]\n{result}")],
        }

    return node


def create_bear_debater(llm, label="Bear"):
    """Build the strongest possible bearish case from all four analyst reports.

    v2.8: Now has TOOL ACCESS — can fetch live prices, verify claims,
    and check sentiment data to strengthen arguments during debate.
    Mirrors the bull debater. Must engage with the bull's specific arguments,
    point out flaws or missing context, and explain why the bearish thesis
    is more convincing.
    """

    def node(state):
        technical = state.get("technical_report", "")
        fundamental = state.get("fundamental_report", "")
        macro = state.get("macro_report", "")
        sentiment = state.get("sentiment_report", "")
        symbol = state["company_of_interest"]
        trade_date = state.get("trade_date", "")
        debate_state = state.get("debate_state", {})
        bull_last = debate_state.get("bull_last", "")
        round_num = debate_state.get("round", 0) + 1

        if bull_last:
            opponent_context = f"多方论点（需要反驳）：\n{bull_last[:1000]}"
        else:
            opponent_context = "请发表你的开篇空方立论。"

        system_message = f"""You are the BEAR (空方) debater for commodity futures variety `{symbol}` (as of {trade_date}).

**Your Role**: Build the strongest possible bearish (看跌/看空) case. You have access to data tools — use them to fact-check claims and find supporting evidence.

**Rules**:
1. Cite SPECIFIC data (prices, levels, ratios) — use tools to verify
2. Engage directly with the bull's arguments — point out flaws or missing context
3. Acknowledge valid bull concerns, but argue why the bearish thesis outweighs them
4. Be conversational and persuasive, like a real debate
5. **Use tools BEFORE making claims** — verify with get_realtime_price() or get_verified_quote()

**Tool Guidance**:
- `get_realtime_price("{symbol}")` — check the current live market price
- `get_verified_quote("{symbol}", "{trade_date}")` — get exact OHLCV + key levels for fact-checking
- `get_futures_price("{symbol}", ...) ` — get recent price history
- `get_futures_indicators("{symbol}", ...) ` — get technical levels (SMA, RSI, MACD, Bollinger)
- `get_futures_sentiment("{symbol}")` — check social media sentiment direction
- `get_variety_info("{symbol}")` — variety metadata (specs, trading hours)

**Analyst Reports (for context)**:
---
Technical: {technical[:1200] if technical else 'N/A'}
Fundamental: {fundamental[:1200] if fundamental else 'N/A'}
Macro/News: {macro[:800] if macro else 'N/A'}
Sentiment: {sentiment[:600] if sentiment else 'N/A'}
---

{opponent_context}

**Output**: Write 200-400 characters in Chinese. Start with "空方(R{round_num})：". Structure your argument with data, reasoning, and a rebuttal to the bull case.
"""
        logger.info("Bear R%d (with tools)...", round_num)

        # Build initial message
        initial_msg = HumanMessage(content=system_message)

        # Run tool-calling loop
        try:
            result = _run_tool_loop(
                llm, DEBATE_TOOLS, [initial_msg],
                max_iterations=DEBATE_MAX_ITERATIONS,
                progress_callback=_debate_progress_callback,
                label=f"Bear-R{round_num}",
            )
        except Exception as e:
            logger.error("Bear debater failed: %s", e)
            fallback = llm.invoke(f"用中文为 {symbol} 商品期货构建最强看跌论点。引用数据。\n技术面：{technical[:1500]}\n基本面：{fundamental[:1500]}\n宏观：{macro[:1000]}")
            result = fallback.content if hasattr(fallback, 'content') else str(fallback)

        if not result:
            logger.warning("Bear returned empty — retrying with simpler prompt")
            fallback = llm.invoke(f"用中文为 {symbol} 商品期货构建最强看跌论点。引用数据。\n技术面：{technical[:1500]}\n基本面：{fundamental[:1500]}\n宏观：{macro[:1000]}")
            result = fallback.content if hasattr(fallback, 'content') else str(fallback)

        new_debate_state = {
            "bull_history": debate_state.get("bull_history", ""),
            "bear_history": debate_state.get("bear_history", "") + "\n" + result,
            "bull_last": debate_state.get("bull_last", ""),
            "bear_last": result,
            "round": round_num,
        }

        return {
            "debate_state": new_debate_state,
            "messages": [HumanMessage(content=f"[Bear R{round_num}]\n{result}")],
        }

    return node


def create_debate_moderator(llm):
    """Review the complete bull/bear debate and produce a structured summary.

    v2.8: Now has TOOL ACCESS — can fact-check claims made by debaters
    using live prices and verified quotes. Uses a narrower tool set
    focused on data verification.

    Replaces the old Discussion node output. Takes the full debate history
    plus the original analyst reports for fact-checking.
    """

    def node(state):
        technical = state.get("technical_report", "")
        fundamental = state.get("fundamental_report", "")
        macro = state.get("macro_report", "")
        symbol = state["company_of_interest"]
        trade_date = state.get("trade_date", "")
        debate_state = state.get("debate_state", {})
        bull_history = debate_state.get("bull_history", "")
        bear_history = debate_state.get("bear_history", "")

        system_message = f"""You are the debate MODERATOR for {symbol} commodity futures (as of {trade_date}).

A bull vs bear debate just finished. Your job: summarize, fact-check, and judge.

**You have FACT-CHECKING TOOLS** — use them to verify disputed claims:
- `get_realtime_price("{symbol}")` — check the current market price
- `get_verified_quote("{symbol}", "{trade_date}")` — get exact OHLCV + key levels
- `get_futures_price("{symbol}", ...) ` — recent price history for trend verification

**Debate Transcript**:

BULL ARGUMENTS:
{bull_history[:2000] if bull_history else 'None.'}

BEAR ARGUMENTS:
{bear_history[:2000] if bear_history else 'None.'}

**Analyst Reports (for context)**:
Technical: {technical[:500] if technical else 'N/A'}
Fundamental: {fundamental[:500] if fundamental else 'N/A'}
Macro: {macro[:400] if macro else 'N/A'}

**Tasks**:
1. FACT-CHECK: Use tools to verify 1-2 key factual claims from the debate (price levels, direction claims)
2. WINNER: Declare winner — bull / bear / draw — with reasoning
3. CONSENSUS: 2-4 points both sides agree on
4. DIVERGENCE: 2-4 points of disagreement, and why
5. KEY RISK: The single most important risk factor
6. COUNTERFACTUAL: If recent prices moved the opposite direction, would the conclusion change?

**Output Format** (in Chinese markdown):

## 辩论裁决
**Winner**: [bull/bear/draw]

### 事实核查
[Verified claims with tools — which claims were accurate, which were not]

### 共识点
### 分歧点
### 关键风险
### 反事实检验
### 辩论总结
"""
        _print_stage_header("[Moderator] Reviewing debate (with fact-check tools)...")

        initial_msg = HumanMessage(content=system_message)

        # Run tool-calling loop for fact-checking
        try:
            result = _run_tool_loop(
                llm, MODERATOR_TOOLS, [initial_msg],
                max_iterations=DEBATE_MAX_ITERATIONS,
                progress_callback=_debate_progress_callback,
                label="Moderator",
            )
        except Exception as e:
            logger.error("Moderator failed: %s", e)
            fallback = llm.invoke(f"Summarize the bull vs bear debate for {symbol}. Bull: {bull_history[:1000]} Bear: {bear_history[:1000]}")
            result = fallback.content if hasattr(fallback, 'content') else str(fallback)

        if not result:
            logger.warning("Moderator returned empty — retrying")
            fallback = llm.invoke(f"Summarize the bull vs bear debate for {symbol}. Bull: {bull_history[:1000]} Bear: {bear_history[:1000]}")
            result = fallback.content if hasattr(fallback, 'content') else str(fallback)

        logger.info("Moderator done.")

        return {
            "discussion_summary": result,
            "messages": [HumanMessage(content=f"[Debate Moderator]\n{result}")],
        }

    return node
