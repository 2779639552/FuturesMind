"""
Commodity futures analyst agents for TradingAgents.
Three specialized analysts covering technical, fundamental, and macro/news dimensions.

Each analyst node internally handles the tool-calling loop: LLM decides to call
a tool → node executes it → LLM processes the result → repeat until report is ready.
"""

# =============================================================================
# 【文件角色】本文件是"分析师节点生成器"模块，产出 3 位商品期货分析师节点。
#
# 【在分析管线中的位置】
#   数据采集(dataflows) → 图编排(commodity_demo.py) → 分析师并行分析(本文件)
#   → 辩论(commodity_debate.py) → 综合结论。
#   本文件只负责"单点分析"：把"LLM + 一组工具"封装成一个可被 LangGraph 调用的节点。
#
# 【四位分析师分工】（前 3 位在本文件生成，第 4 位在 sentiment_analyst.py 生成）
#   1. Technical   技术面 : 价格趋势、均线、MACD、RSI、布林带、成交量、持仓量(OI)。
#   2. Fundamental 基本面 : 供需平衡、库存周期、基差结构、产业链利润传导。
#   3. Macro/News  宏观面 : 宏观经济数据、政策、新闻、地缘政治。
#   4. Sentiment   情绪面 : 社交媒体情绪与市场心理（由 sentiment_analyst.py 提供）。
#   四者并行产出各自报告（报告以 BIAS 观点行开头），供后续讨论与综合使用。
#
# 【与其它文件的关系】
#   - dataflows/ 负责数据采集/清洗与品种元数据；本文件不直接调用它，而是通过
#     tradingagents/agents/utils/commodity_futures_tools.py 中的 @tool 工具读取数据
#     （这些工具内部再调用 dataflows.interface 的路由）。
#   - commodity_demo.py  负责把分析师节点接入 LangGraph 图、串联整体执行流程。
#   - commodity_debate.py 负责让多位分析师的报告进入"辩论"环节并做综合。
# =============================================================================

import logging  # 【调用包】标准库日志;记录工具调用信息与分析失败异常

from langchain_core.messages import (  # 【调用包】LangChain 消息类型;HumanMessage 承载最终报告、ToolMessage 回填工具结果给 LLM
    HumanMessage,
    ToolMessage,
)
from langchain_core.prompts import (  # 【调用包】构建带历史占位符的提示模板;让 LLM 依据对话历史决策
    ChatPromptTemplate,
    MessagesPlaceholder,
)

from tradingagents.agents.utils.agent_utils import (
    get_language_instruction,  # 【调用包】语言指令;在系统提示末尾追加"用中文输出"约束
)
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
)  # 【调用包】商品期货行情/指标/库存/基差/宏观/新闻/供需/品种信息/核验报价工具;由 LLM 通过 _run_tool_loop 调度取数

logger = logging.getLogger(__name__)

# 工具调用循环的最大轮数（安全上限）。
# 防止 LLM 无休止地调用工具导致死循环或 token 超限；到达该上限后强制结束。
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

    # 【中文说明】本函数是 Agent 的"核心引擎"——工具调用循环。
    # 整体思路：让 LLM 自己决定"下一步要查什么"→ 本函数代为执行该工具 → 把结果回填给 LLM
    # → LLM 基于新信息再次决策 …… 直到 LLM 认为信息足够、不再要求调用工具（输出最终报告），
    # 或达到最大轮数上限。
    # 这相当于让 LLM 像"分析师查资料"一样：一手调取行情/库存/新闻等数据，一手写分析报告。
    #
    # 【功能】循环执行"LLM 决策 → 执行工具 → 回填结果"直到产出最终报告。
    # 【参数】llm: LLM 客户端；tools: @tool 装饰的工具函数列表；
    #         initial_messages: 对话初始消息（含系统提示与历史消息）；
    #         max_iterations: 最大轮数；progress_callback: 进度回调(事件, 数据)；
    #         label: 该分析师的显示名。
    # 【返回】LLM 最终输出的报告文本（response.content）。
    # 【关键逻辑】两个终止条件：①LLM 不再请求调用工具；②达到 max_iterations。
    #            单个工具异常不中断整体，而是包装成 TOOL_ERROR 文本回填给 LLM。

    # Build a tool map for fast lookup
    # 建立"工具名 → 工具函数"的查找表，之后按名字快速定位要执行的工具。
    tool_map = {t.name: t for t in tools}

    messages = list(initial_messages)  # copy
    # 复制一份初始消息，避免直接改动调用方传入的列表（不污染外部状态）。
    iteration = 0
    # 已执行的分析轮数计数（每轮循环先 +1）。

    # ----- 循环主体：只要没达到最大轮数，就一直让 LLM 思考并(可能)调用工具 -----
    while iteration < max_iterations:
        iteration += 1
        if progress_callback:
            # 通知外部（如 Web 前端）：新一轮开始，便于展示"正在思考第几轮"。
            progress_callback("iteration", {"current": iteration, "max": max_iterations})

        # 关键一步：把全部历史消息（含此前工具结果）连同工具定义一起交给 LLM。
        # LLM 返回的 response 中可能带 tool_calls（它想调用哪些工具及其参数）。
        response = llm.bind_tools(tools).invoke(messages)  # 【调用函数】LLM 核心调用:绑定工具后让 LLM 决策(是否调用工具/调用哪些)
        # 把 LLM 本轮的回答追加进对话历史，保持上下文连贯（含它的思考过程）。
        messages.append(response)

        # Emit LLM reasoning text between tool calls (if any)
        # 若 LLM 在调用工具前先输出了思考文字，截取前 500 字符推送给外部作实时展示
        # （纯展示用途，不影响后续逻辑）。
        if progress_callback and response.content:
            content_preview = (
                response.content[:500] if len(response.content) > 500 else response.content
            )
            progress_callback("llm_thinking", {"content": content_preview, "iteration": iteration})

        # ---- 循环终止条件之一：LLM 不再请求调用工具 ----
        # 说明 LLM 认为已收集到足够信息，本次输出就是它的最终分析报告，直接返回。
        if not response.tool_calls:
            # No more tool calls — this is the final report
            if progress_callback:
                progress_callback("report_start", {"label": label})
            return response.content

        # Execute tool calls
        # 逐条执行 LLM 本轮请求的所有工具调用（一轮可能同时请求多个工具）。
        for tc in response.tool_calls:
            tool_name = tc.get("name", "")  # 工具名，如 get_futures_price
            tool_args = tc.get("args", {})  # 传给工具的参数（字典）
            tool_id = tc.get("id", "")      # 本次调用的唯一 ID，后续靠它把结果"对上号"

            logger.info("Tool call: %s(%s)", tool_name, tool_args)

            # Format args for display (brief)
            # 参数可能很长，压缩到 100 字符内，仅供前端展示使用（不影响真实传参）。
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

            # 只在"白名单"内执行工具：防止 LLM 调用到不存在的工具或误拼写。
            if tool_name in tool_map:
                try:
                    # 真正执行工具函数（@tool 装饰的函数可通过 .invoke(args) 调用）。
                    result = tool_map[tool_name].invoke(tool_args)  # 【调用函数】调度执行 LLM 请求的工具(实际取数),结果将回填给 LLM
                    # Truncate very long results to avoid token overflow
                    # 工具返回可能很长（如新闻/库存列表），截断到 8000 字符，避免撑爆 LLM 上下文。
                    if isinstance(result, str) and len(result) > 8000:
                        result = result[:8000] + "\n... (truncated for length)"
                except Exception as e:
                    # 错误处理：单个工具失败不应让整个分析崩溃，
                    # 而是把错误信息包装成一段"工具返回文本"，让 LLM 自行判断如何处理。
                    result = f"TOOL_ERROR: {type(e).__name__}: {e}"
            else:
                # LLM "幻觉"调用了不存在的工具：回填一条提示文本，避免程序中断。
                result = f"Unknown tool: {tool_name}"

            if progress_callback:
                # 把工具执行结果的长度与前 300 字符预览推送给外部（展示"已取回数据"）。
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

            # 关键一步：把工具执行结果以 ToolMessage 形式"回填"给 LLM。
            # tool_call_id 必须与上文 LLM 请求中的 id 完全一致，LLM 才知道这段结果对应哪次调用。
            messages.append(ToolMessage(content=str(result), tool_call_id=tool_id))  # 【调用函数】把工具结果包装为 ToolMessage 回填,按 tool_call_id 与 LLM 请求配对

    # Hit max iterations
    # 循环终止条件之二：达到最大轮数仍未结束。强制返回最后一次 LLM 输出，避免无限循环。
    logger.warning("Tool loop hit max iterations (%d). Returning last response.", max_iterations)
    return response.content if hasattr(response, "content") else str(response)


# ---------------------------------------------------------------------------
# Technical Analyst
# ---------------------------------------------------------------------------


def create_commodity_technical_analyst(llm, label="Technical", progress_callback=None):
    """Technical analyst for commodity futures: price action, indicators, volume/OI."""

    # 【中文说明】
    # 【功能】创建"技术面分析师"节点（工厂函数）。
    # 【立场/关注点】只看价格行为与市场微观结构：趋势/支撑阻力、均线、MACD、RSI、
    #               布林带、成交量、持仓量(OI)、ATR。刻意不看基本面与消息面。
    # 【工具清单】get_variety_info, get_futures_price, get_futures_indicators, get_verified_quote
    # 【输出结构】报告第一行必须是
    #            "BIAS: 看多/偏多/中性/偏空/看空 | CONFIDENCE: 高/中/低"
    #            （机器解析用，供回测/辩论使用），末尾附关键信号汇总表；
    #            节点返回 {"messages": [HumanMessage], "technical_report": 报告文本}。
    # 【参数】llm: LLM 客户端；label: 进度显示名（默认 "Technical"）；
    #         progress_callback: 进度回调 callback(event_type, data)。
    # 【返回】node 函数，签名 node(state)，兼容 LangGraph StateGraph 节点。
    # 注：返回的是闭包 node——工厂函数只负责"造"节点，真正的执行逻辑在 node 内部。

    def node(state):
        # 【功能】技术面分析节点本体：被 LangGraph 调用一次，产出技术面报告。
        # 【参数】state: 图状态字典，其中 trade_date 为当前交易日、company_of_interest 为品种代码。
        # 【返回】dict：{"messages": [HumanMessage(技术报告)], "technical_report": 报告文本}。
        # 【关键逻辑】组装系统提示(中文 System Prompt) → 构造 prompt → 调用 _run_tool_loop
        #            让 LLM 自行调工具取数并写报告 → 报告出错时兜底为 ANALYSIS_ERROR 文本。
        current_date = state["trade_date"]
        symbol = state["company_of_interest"]

        tools = [  # 【变量】本节点向 LLM 注册的工具白名单(技术/基本面/宏观各 4-6 个),将被 bind_tools 绑定
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
        evolution_ctx = state.get("past_context", "")  # 【变量】进化记忆上下文;非空时前置到系统提示,让分析师参考历史教训与用户偏好
        if evolution_ctx:
            system_message = evolution_ctx + "\n\n" + system_message
        # --- End Injection ---

        prompt = ChatPromptTemplate.from_messages(  # 【调用函数】构造提示模板(系统提示 + 消息历史占位符)
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
        initial_messages = [prompt.format_prompt(messages=state["messages"]).to_messages()[0]]  # 【调用函数】用历史消息填充模板,生成对话初始消息

        # Run tool-calling loop
        try:
            report = _run_tool_loop(
                llm, tools, initial_messages, progress_callback=progress_callback, label=label  # 【调用函数】进入工具循环:LLM 决策→取数→写报告,直至完成或达轮数上限
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

    # 【中文说明】
    # 【功能】创建"基本面分析师"节点（工厂函数）。
    # 【立场/关注点】供需平衡、库存周期（重点看库存"速度"而非绝对值）、基差结构、
    #               产业链成本传导与利润分配。不看价格图形，也不看新闻情绪。
    # 【工具清单】get_variety_info, get_futures_price, get_futures_basis, get_futures_inventory,
    #            get_futures_supply_demand, get_verified_quote
    # 【输出结构】报告第一行必须是
    #            "BIAS: 看多/偏多/中性/偏空/看空 | CONFIDENCE: 高/中/低"（机器解析用）；
    #            末尾附关键信号汇总表；节点返回
    #            {"messages": [HumanMessage], "fundamental_report": 报告文本}。
    # 【参数】llm: LLM 客户端；label: 进度显示名（默认 "Fundamental"）；
    #         progress_callback: 进度回调 callback(event_type, data)。
    # 【返回】node 函数，签名 node(state)，兼容 LangGraph StateGraph 节点。

    def node(state):
        # 【功能】基本面分析节点本体：被 LangGraph 调用一次，产出基本面报告。
        # 【参数】state: 图状态字典，其中 trade_date 为当前交易日、company_of_interest 为品种代码。
        # 【返回】dict：{"messages": [HumanMessage(基本面报告)], "fundamental_report": 报告文本}。
        # 【关键逻辑】与技术面节点一致：构造提示 → _run_tool_loop 取数写报告 → 异常兜底。
        current_date = state["trade_date"]
        symbol = state["company_of_interest"]

        tools = [  # 【变量】本节点向 LLM 注册的工具白名单(技术/基本面/宏观各 4-6 个),将被 bind_tools 绑定
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
        evolution_ctx = state.get("past_context", "")  # 【变量】进化记忆上下文;非空时前置到系统提示,让分析师参考历史教训与用户偏好
        if evolution_ctx:
            system_message = evolution_ctx + "\n\n" + system_message
        # --- End Injection ---

        prompt = ChatPromptTemplate.from_messages(  # 【调用函数】构造提示模板(系统提示 + 消息历史占位符)
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

        initial_messages = [prompt.format_prompt(messages=state["messages"]).to_messages()[0]]  # 【调用函数】用历史消息填充模板,生成对话初始消息

        try:
            report = _run_tool_loop(
                llm, tools, initial_messages, progress_callback=progress_callback, label=label  # 【调用函数】进入工具循环:LLM 决策→取数→写报告,直至完成或达轮数上限
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

    # 【中文说明】
    # 【功能】创建"宏观与新闻分析师"节点（工厂函数）。
    # 【立场/关注点】宏观经济数据（GDP/PMI/固投/房地产指数等）、政策面（供给端改革、
    #               环保限产、货币/财政政策）、新闻叙事与地缘政治事件。
    # 【工具清单】get_variety_info, get_futures_news, get_futures_price, get_futures_macro,
    #            get_verified_quote
    # 【输出结构】报告第一行必须是
    #            "BIAS: 看多/偏多/中性/偏空/看空 | CONFIDENCE: 高/中/低"（机器解析用）；
    #            末尾附关键信号汇总表；节点返回
    #            {"messages": [HumanMessage], "macro_report": 报告文本}。
    # 【参数】llm: LLM 客户端；label: 进度显示名（默认 "Macro/News"）；
    #         progress_callback: 进度回调 callback(event_type, data)。
    # 【返回】node 函数，签名 node(state)，兼容 LangGraph StateGraph 节点。

    def node(state):
        # 【功能】宏观与新闻分析节点本体：被 LangGraph 调用一次，产出宏观面报告。
        # 【参数】state: 图状态字典，其中 trade_date 为当前交易日、company_of_interest 为品种代码。
        # 【返回】dict：{"messages": [HumanMessage(宏观报告)], "macro_report": 报告文本}。
        # 【关键逻辑】与技术/基本面节点一致：构造提示 → _run_tool_loop 取数写报告 → 异常兜底。
        current_date = state["trade_date"]
        symbol = state["company_of_interest"]

        tools = [  # 【变量】本节点向 LLM 注册的工具白名单(技术/基本面/宏观各 4-6 个),将被 bind_tools 绑定
            get_variety_info,
            get_futures_news,
            get_futures_price,
            get_futures_macro,
            get_verified_quote,
        ]

        system_message = """You are a commodity futures macro & policy analyst specializing in Chinese markets.

**Your Role**: Analyze macroeconomic conditions, government policies, and geopolitical events that drive commodity prices.

**Analysis Framework**:

**Data Availability (MUST follow — never fabricate data)**:
- `get_futures_macro` returns ONLY Chinese domestic indicators: GDP, PMI, FAI, Real Estate Climate Index, Industrial Production, Construction Index.
- Foreign/global data — USD index, Fed rate decisions, US nonfarm payrolls, EIA/API inventories, BDI shipping index, Singapore/Fujairah prices — has NO quantitative tool. Only mention such factors if they appear in the `get_futures_news` feed; otherwise state explicitly "该数据本项目无法获取" rather than guessing numbers.
- If a variety's key_factors reference a data source you cannot call, flag the limitation in your report instead of estimating.

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
   - Currency impact (USD/CNY on imported commodities) — 仅当新闻明确提及时分析,系统无外汇定量数据
   - Equity/bond market signals relevant to this commodity — 仅当新闻明确提及时分析

5. **Geopolitical Factors** (仅依据 `get_futures_news` 新闻报道,无独立数据源):
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
        evolution_ctx = state.get("past_context", "")  # 【变量】进化记忆上下文;非空时前置到系统提示,让分析师参考历史教训与用户偏好
        if evolution_ctx:
            system_message = evolution_ctx + "\n\n" + system_message
        # --- End Injection ---

        prompt = ChatPromptTemplate.from_messages(  # 【调用函数】构造提示模板(系统提示 + 消息历史占位符)
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

        initial_messages = [prompt.format_prompt(messages=state["messages"]).to_messages()[0]]  # 【调用函数】用历史消息填充模板,生成对话初始消息

        try:
            report = _run_tool_loop(
                llm, tools, initial_messages, progress_callback=progress_callback, label=label  # 【调用函数】进入工具循环:LLM 决策→取数→写报告,直至完成或达轮数上限
            )
        except Exception as e:
            logger.error("Macro analyst failed: %s", e)
            report = f"ANALYSIS_ERROR: Macro analysis failed: {e}"

        return {
            "messages": [HumanMessage(content=f"[Macro Analyst Report]\n{report}")],
            "macro_report": report,
        }

    return node
