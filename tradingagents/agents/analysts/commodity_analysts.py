"""
Commodity futures analyst agents for TradingAgents.
Three specialized analysts covering technical, fundamental, and macro/news dimensions.

Each analyst node internally handles the tool-calling loop: LLM decides to call
a tool → node executes it → LLM processes the result → repeat until report is ready.
"""

import logging

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.agent_utils import get_language_instruction
from tradingagents.agents.utils.commodity_futures_tools import (
    get_futures_basis,
    get_futures_indicators,
    get_futures_inventory,
    get_futures_macro,
    get_futures_news,
    get_futures_price,
    get_futures_supply_demand,
    get_variety_info,
    get_verified_quote,
)

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 6  # safety limit


def _run_tool_loop(
    llm,
    tools,
    initial_messages,
    max_iterations=MAX_TOOL_ITERATIONS,
    progress_callback=None,
    label="Analyst",
):
    """Execute a tool-calling loop: LLM decides, we execute tools, LLM processes results.

    Args:
        llm: The LLM client to use.
        tools: List of @tool-decorated functions.
        initial_messages: Initial messages to start the conversation.
        max_iterations: Safety limit on tool-calling rounds.
        progress_callback: Optional callback( event_type, data ) for real-time visibility.
                           event_type: "tool_call" | "tool_result" | "llm_thinking" |
                                       "report_start" | "iteration"
        label: Human-readable label for this analyst (e.g. "Technical").

    Returns the final LLM response (text content) or raises on error.
    """
    # Build a tool map for fast lookup
    tool_map = {t.name: t for t in tools}

    messages = list(initial_messages)  # copy
    iteration = 0

    while iteration < max_iterations:
        iteration += 1
        if progress_callback:
            progress_callback("iteration", {"current": iteration, "max": max_iterations})

        response = llm.bind_tools(tools).invoke(messages)
        messages.append(response)

        # Emit LLM reasoning text between tool calls (if any)
        if progress_callback and response.content:
            content_preview = (
                response.content[:500] if len(response.content) > 500 else response.content
            )
            progress_callback("llm_thinking", {"content": content_preview, "iteration": iteration})

        if not response.tool_calls:
            # No more tool calls — this is the final report
            if progress_callback:
                progress_callback("report_start", {"label": label})
            return response.content

        # Execute tool calls
        for tc in response.tool_calls:
            tool_name = tc.get("name", "")
            tool_args = tc.get("args", {})
            tool_id = tc.get("id", "")

            logger.info("Tool call: %s(%s)", tool_name, tool_args)

            # Format args for display (brief)
            args_brief = str(tool_args)
            if len(args_brief) > 100:
                args_brief = args_brief[:97] + "..."

            if progress_callback:
                progress_callback(
                    "tool_call",
                    {
                        "tool_name": tool_name,
                        "args": tool_args,
                        "args_brief": args_brief,
                        "iteration": iteration,
                        "label": label,
                    },
                )

            if tool_name in tool_map:
                try:
                    result = tool_map[tool_name].invoke(tool_args)
                    # Truncate very long results to avoid token overflow
                    if isinstance(result, str) and len(result) > 8000:
                        result = result[:8000] + "\n... (truncated for length)"
                except Exception as e:
                    result = f"TOOL_ERROR: {type(e).__name__}: {e}"
            else:
                result = f"Unknown tool: {tool_name}"

            if progress_callback:
                result_str = str(result)
                progress_callback(
                    "tool_result",
                    {
                        "tool_name": tool_name,
                        "result_length": len(result_str),
                        "preview": result_str[:300] if len(result_str) > 300 else result_str,
                        "label": label,
                    },
                )

            messages.append(ToolMessage(content=str(result), tool_call_id=tool_id))

    # Hit max iterations
    logger.warning("Tool loop hit max iterations (%d). Returning last response.", max_iterations)
    return response.content if hasattr(response, "content") else str(response)


# ---------------------------------------------------------------------------
# Technical Analyst
# ---------------------------------------------------------------------------


def create_commodity_technical_analyst(llm, label="Technical", progress_callback=None):
    """Technical analyst for commodity futures: price action, indicators, volume/OI."""

    def node(state):
        current_date = state["trade_date"]
        symbol = state["company_of_interest"]

        tools = [
            get_variety_info,
            get_futures_price,
            get_futures_indicators,
            get_verified_quote,
        ]

        system_message = """You are a commodity futures technical analyst specializing in Chinese futures markets.

**Your Role**: Analyze price trends, technical indicators, and market microstructure for the given commodity futures contract.

**Analysis Framework**:
1. **Price Trend**: Identify the primary trend (bullish/bearish/range-bound) using multiple timeframes. Note key support and resistance levels with exact price values from the data.
2. **Moving Averages**: Analyze SMA crossovers (5/10/20/50). Price relative to key MAs indicates trend strength. Report the exact SMA values.
3. **MACD**: Check MACD line vs signal line crossovers. Note histogram momentum — widening histogram = strengthening trend, narrowing = weakening. Report exact MACD values.
4. **RSI(14)**: Overbought >70, oversold <30. In strong trends, RSI can stay extreme for extended periods. Report exact RSI value.
5. **Bollinger Bands**: Price near upper band = overbought pressure; near lower band = oversold. Report exact band values.
6. **Volume Analysis**: Volume confirms price moves. Report volume trends with specific numbers.
7. **Open Interest (OI)**: **CRITICAL for futures!** OI + price direction reveals money flow:
   - Price up + OI up = new longs entering (bullish)
   - Price up + OI down = shorts covering (bearish, rally unsustainable)
   - Price down + OI up = new shorts entering (bearish)
   - Price down + OI down = longs liquidating (potentially near bottom)
   Report exact OI values and changes.
8. **ATR(14)**: Use for volatility context. Higher ATR = wider stops needed.

**Futures-Specific Notes**:
- Chinese futures have daily price limits. Note when price approaches the limit.
- Trading hours include night sessions (夜盘). Overnight gaps are common.
- Contract rollover near delivery month affects volume/OI patterns.

**⚠️ ANTI-RECENCY-BIAS RULE**:
- A single-day sharp move (>2%) does NOT constitute a trend reversal.
- For any large single-day move, check: (a) Is volume confirming or anomalous? (b) Is OI confirming new positions or just position-squaring? (c) What does the 5-day and 20-day trend show?
- If the move is driven by short-covering (涨+OI减) or profit-taking (跌+OI减), it signals position adjustment, NOT directional commitment.
- Always place the most recent day in the context of the multi-week trend. The last data point carries no more weight than the pattern it sits within.

**Workflow**: Call `get_variety_info` first, then `get_futures_price`, then `get_futures_indicators`.

Write a detailed technical analysis report (300-500 words) with specific price levels, indicator values, and clear conclusions.

**CRITICAL — First line after your title MUST be exactly:**
```
BIAS: [看多/偏多/中性/偏空/看空] | CONFIDENCE: [高/中/低]
```
This is machine-parsed for backtesting. Choose your bias and confidence based on YOUR OWN analysis.

End your report with:
1. **Technical Bias**: 看多/偏多/中性/偏空/看空, with a short justification.
2. **Key Signals Summary Table** (Markdown):

| 关键信号 | 方向 | 数值/状态 | 置信度 | 数据来源 |
|---------|------|----------|--------|---------|
| (至少填写5行关键发现) | 利多/利空 | 具体数值 | 高/中/低 | 工具名 |

""" + get_language_instruction()

        # --- Self-Evolution Injection ---
        # Prepend evolution memory context so the analyst sees user preferences
        # and past lessons BEFORE the analysis framework instructions.
        evolution_ctx = state.get("past_context", "")
        if evolution_ctx:
            system_message = evolution_ctx + "\n\n" + system_message
        # --- End Injection ---

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant collaborating with other analysts."
                    " Use the provided tools to gather data, then write your full analysis report."
                    " You have access to: {tool_names}."
                    " Today is {current_date}. Analyze commodity futures variety: {symbol}.\n{system_message}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join(t.name for t in tools))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(symbol=symbol)

        # Build initial messages
        initial_messages = [prompt.format_prompt(messages=state["messages"]).to_messages()[0]]

        # Run tool-calling loop
        try:
            report = _run_tool_loop(
                llm, tools, initial_messages, progress_callback=progress_callback, label=label
            )
        except Exception as e:
            logger.error("Technical analyst failed: %s", e)
            report = f"ANALYSIS_ERROR: Technical analysis failed: {e}"

        return {
            "messages": [HumanMessage(content=f"[Technical Analyst Report]\n{report}")],
            "technical_report": report,
        }

    return node


# ---------------------------------------------------------------------------
# Fundamental Analyst (Supply/Demand/Basis/Inventory)
# ---------------------------------------------------------------------------


def create_commodity_fundamental_analyst(llm, label="Fundamental", progress_callback=None):
    """Fundamental analyst for commodity futures: supply/demand, inventory, basis, industrial chain."""

    def node(state):
        current_date = state["trade_date"]
        symbol = state["company_of_interest"]

        tools = [
            get_variety_info,
            get_futures_price,
            get_futures_basis,
            get_futures_inventory,
            get_futures_supply_demand,
            get_verified_quote,
        ]

        system_message = """You are a commodity futures fundamental analyst specializing in Chinese futures markets.

**Your Role**: Analyze supply-demand dynamics, inventory cycles, basis structure, and industrial chain relationships.

**Analysis Framework**:

1. **Price Context**: Call `get_futures_price` for recent OHLCV data as context.

2. **Basis Analysis** (call `get_futures_basis` — MOST IMPORTANT indicator):
   - **Basis = Spot - Futures**. This is THE key indicator for futures fundamentals.
   - **Backwardation** (现货升水, positive basis): Spot > Futures. Indicates tight nearby supply. Usually bullish.
   - **Contango** (期货升水, negative basis): Futures > Spot. Indicates ample supply or carry costs. Usually bearish.
   - Basis trend: Is it strengthening or weakening? A strengthening basis often leads price higher.
   - Report exact basis values and rates from the data.

3. **Inventory Analysis** (call `get_futures_inventory`):
   - **Absolute level**: Current inventory vs 3-month average — percentile ranking
   - **Direction**: Building (累库) or draining (去库)?
   - **Velocity (CRITICAL)**: Calculate the WEEKLY rate of change. Is the accumulation accelerating or decelerating?
     - E.g. "累库速度从+15万吨/周降至+5万吨/周 → 累库放缓，边际改善"
     - E.g. "去库速度从-3万吨/周加速至-10万吨/周 → 去库加速，供给收紧"
   - **Structure**: Where is inventory accumulating? (mill vs social vs warehouse receipts). Different locations tell different stories.
   - Report exact numbers with week-over-week velocity calculations.

4. **Supply-Side Factors** (infer from data + variety info):
   - Production constraints: capacity utilization, maintenance, policy curbs
   - Import/export dynamics
   - Raw material cost transmission
   - Use variety info to understand the industrial chain position.

5. **Demand-Side Factors**:
   - Downstream industry health (from variety info's key_factors)
   - Seasonal patterns (note the current date's seasonal context)
   - Substitution effects

6. **Industrial Chain & Supply-Demand Details** (call `get_futures_supply_demand`):
   - **Weekly production**: Rebar weekly output and WoW change — supply trend
   - **Daily transaction volume**: National building materials daily transaction — real-time demand signal. Compare to 5d/20d averages to gauge demand strength.
   - **Hot metal output**: Daily pig iron output — upstream supply indicator
   - **BF/EAF rates and mill profits**: Capacity utilization and profitability — supply response
   - **Construction & Real Estate indices**: Downstream industry health
   - Cross-reference production vs transaction data: if production > transaction, inventory builds (bearish); if transaction recovers while production is cut, tightness develops (bullish).

7. **Upstream Cost Transmission** (CRITICAL for margin analysis):
   - From `get_variety_info`, check the `related_varieties` field for upstream raw materials.
   - For each key upstream variety, call `get_futures_price` to get recent price trends.
     - Example for RB: call `get_futures_price` for I (iron ore) and J (coke) to understand cost drivers.
   - **Cost structure**: Iron ore (~50% of BF cost) + coke/coal (~30%) + scrap + others.
   - **Margin analysis**: Compare rebar price trend vs raw material price trends.
     - If rebar falls but raw materials fall faster → margins IMPROVE (bearish rebar — room to cut prices further)
     - If rebar rises but raw materials rise faster → margins SQUEEZE (bullish rebar — cost push)
     - If rebar falls while raw materials hold → margins ERODE (potential supply cut → bullish medium-term)
   - **Iron ore port inventory**: From `get_futures_supply_demand`, check iron ore inventory level and trend.
     - High + rising ore inventory → ore price pressure → lower BF costs → more rebar supply headroom
     - Tight + falling ore inventory → ore price support → cost floor for rebar
   - **Coke/coal dynamics**: Policy-driven production curbs on coke → cost support; loose supply → cost weakness.

8. **Industrial Chain Profit Distribution**:
   - Map profit at each node: mine → steel mill → trader → end user
   - Identify bottlenecks: which node is absorbing the most pressure?
   - BF profit vs EAF profit divergence is a key arb signal

**Key Principles**:
- Basis + inventory = most reliable short-medium term signal
- Cost transmission = determines margin direction and supply response
- Inventory VELOCITY matters more than absolute level — accelerating change precedes price moves

**Workflow**: Call `get_variety_info` → `get_futures_price` (for target variety AND key upstream varieties from related_varieties) → `get_futures_basis` → `get_futures_inventory` → `get_futures_supply_demand`.

Write a comprehensive fundamental analysis (450-650 words) with specific data points.
Include: (a) inventory velocity calculation, (b) upstream cost transmission analysis, (c) margin direction.

**CRITICAL — First line after your title MUST be exactly:**
```
BIAS: [看多/偏多/中性/偏空/看空] | CONFIDENCE: [高/中/低]
```

End with:
1. **Fundamental Bias**: 看多/偏多/中性/偏空/看空, with a short justification.
2. **Key Signals Summary Table** (Markdown):

| 关键信号 | 方向 | 数值/状态 | 置信度 | 数据来源 |
|---------|------|----------|--------|---------|
| (至少填写5行) | 利多/利空 | 具体数值 | 高/中/低 | 工具名 |

""" + get_language_instruction()

        # --- Self-Evolution Injection ---
        evolution_ctx = state.get("past_context", "")
        if evolution_ctx:
            system_message = evolution_ctx + "\n\n" + system_message
        # --- End Injection ---

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant collaborating with other analysts."
                    " Use the provided tools to gather data, then write your full analysis report."
                    " You have access to: {tool_names}."
                    " Today is {current_date}. Analyze commodity futures variety: {symbol}.\n{system_message}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join(t.name for t in tools))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(symbol=symbol)

        initial_messages = [prompt.format_prompt(messages=state["messages"]).to_messages()[0]]

        try:
            report = _run_tool_loop(
                llm, tools, initial_messages, progress_callback=progress_callback, label=label
            )
        except Exception as e:
            logger.error("Fundamental analyst failed: %s", e)
            report = f"ANALYSIS_ERROR: Fundamental analysis failed: {e}"

        return {
            "messages": [HumanMessage(content=f"[Fundamental Analyst Report]\n{report}")],
            "fundamental_report": report,
        }

    return node


# ---------------------------------------------------------------------------
# Macro & News Analyst
# ---------------------------------------------------------------------------


def create_commodity_macro_analyst(llm, label="Macro/News", progress_callback=None):
    """Macro & news analyst for commodity futures: policy, macro cycles, geopolitical events."""

    def node(state):
        current_date = state["trade_date"]
        symbol = state["company_of_interest"]

        tools = [
            get_variety_info,
            get_futures_news,
            get_futures_price,
            get_futures_macro,
            get_verified_quote,
        ]

        system_message = """You are a commodity futures macro & policy analyst specializing in Chinese markets.

**Your Role**: Analyze macroeconomic conditions, government policies, and geopolitical events that drive commodity prices.

**Analysis Framework**:

1. **Macroeconomic Data** (call `get_futures_macro` — CRITICAL, your primary quantitative data):
   - **GDP**: Current growth rate and recent trend. Below-expected GDP is bearish for industrial commodities.
   - **PMI**: Manufacturing PMI value and trend. Below 50 = economic contraction = bearish for steel demand.
   - **Fixed Asset Investment (FAI)**: YoY change and trend. Falling FAI directly depresses construction steel demand.
   - **Real Estate Climate Index**: Level and 6-month trend. This is THE key driver for rebar (~60% of demand). A declining index signals structural demand weakness.
   - **Industrial Production**: YoY growth. Decelerating IP = weakening industrial commodity demand.
   - **Construction Index**: Daily/weekly trend. Direct proxy for construction activity.
   - Report exact values and trends from all indicators. Quantify the macro headwinds/tailwinds.

2. **News Sentiment** (call `get_futures_news` — qualitative context):
   - Identify dominant narratives and market-moving headlines
   - Distinguish between short-term noise and structural shifts
   - Look for policy-related news: production curbs, environmental regulations, trade policies, infrastructure spending
   - Note any geopolitical tensions or supply chain disruptions mentioned.

2. **Policy Analysis** (政策面):
   - **Supply-side reform**: capacity replacement, environmental production curbs (环保限产)
   - **Industry policy**: steel capacity swaps, new infrastructure standards
   - **Trade policy**: export tax rebates, import tariffs, anti-dumping duties
   - **Real estate policy**: the property sector's impact on commodity demand
   - **Monetary policy**: RRR cuts, LPR/interest rate direction, credit impulse
   - Use variety info's key_factors to identify policy-sensitive areas.

3. **Macro Cycle Positioning**:
   - Where are we in the economic cycle? (expansion/peak/contraction/trough)
   - Key indicators context: PMI, industrial production, fixed asset investment
   - Fiscal policy: infrastructure spending, special bonds issuance

4. **Inter-Market Analysis**:
   - Related commodity price movements (from variety info's related_varieties)
   - Currency impact (USD/CNY on imported commodities)
   - Equity/bond market signals relevant to this commodity

5. **Geopolitical Factors**:
   - Trade tensions and sanctions affecting commodity flows
   - Supply disruptions (weather, conflict, logistics)
   - Global commodity cycle coordination

**Workflow**: Call `get_variety_info` → `get_futures_macro` → `get_futures_news` → `get_futures_price` (for price context).

Write a detailed macro/policy analysis (400-600 words) with specific macro data points.
Connect each macro indicator to the specific commodity's demand outlook.

**CRITICAL — First line after your title MUST be exactly:**
```
BIAS: [看多/偏多/中性/偏空/看空] | CONFIDENCE: [高/中/低]
```

End with:
1. **Macro Bias**: 看多/偏多/中性/偏空/看空, with a short justification.
2. **Key Signals Summary Table** (Markdown):

| 关键信号 | 方向 | 数值/状态 | 置信度 | 数据来源 |
|---------|------|----------|--------|---------|
| (至少填写5行) | 利多/利空 | 具体数值 | 高/中/低 | 工具名 |

""" + get_language_instruction()

        # --- Self-Evolution Injection ---
        evolution_ctx = state.get("past_context", "")
        if evolution_ctx:
            system_message = evolution_ctx + "\n\n" + system_message
        # --- End Injection ---

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant collaborating with other analysts."
                    " Use the provided tools to gather data, then write your full analysis report."
                    " You have access to: {tool_names}."
                    " Today is {current_date}. Analyze commodity futures variety: {symbol}.\n{system_message}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join(t.name for t in tools))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(symbol=symbol)

        initial_messages = [prompt.format_prompt(messages=state["messages"]).to_messages()[0]]

        try:
            report = _run_tool_loop(
                llm, tools, initial_messages, progress_callback=progress_callback, label=label
            )
        except Exception as e:
            logger.error("Macro analyst failed: %s", e)
            report = f"ANALYSIS_ERROR: Macro analysis failed: {e}"

        return {
            "messages": [HumanMessage(content=f"[Macro Analyst Report]\n{report}")],
            "macro_report": report,
        }

    return node
