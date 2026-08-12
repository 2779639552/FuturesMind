"""Market analyst: picks the most relevant technical indicators for a stock/crypto, then writes a detailed trend report.

【文件角色】本文件是"市场分析师"节点生成器(股票/加密路径),产出单一分析节点。
【位置】多 Agent 讨论图的分析师层:本节点 → 多方/空方辩论 → 研究经理综合。
"""

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder  # 【调用包】构建带历史占位符的提示模板;让 LLM 依据对话历史决策

from tradingagents.agents.utils.agent_utils import (
    get_indicators,
    get_instrument_context_from_state,
    get_language_instruction,
    get_stock_data,
    get_verified_market_snapshot,
)  # 【调用包】行情数据/指标计算/行情快照核验工具,以及标的上下文提取与语言指令工具


# 【功能】创建"市场分析师"节点(工厂函数):让 LLM 从指标清单中挑选最相关指标、取数并写趋势报告。
# 【参数】llm: LLM 客户端。
# 【返回】node 函数,签名 node(state),兼容 LangGraph StateGraph 节点。
# 【关键逻辑】本文件是"股票/加密"路径的市场分析师;商品期货路径见 commodity_analysts.py。
def create_market_analyst(llm):

    def market_analyst_node(state):
        # 【功能】市场分析节点本体:被 LangGraph 调用一次,产出市场趋势报告。
        # 【参数】state: 图状态字典,含 trade_date、company_of_interest、messages 等。
        # 【返回】dict: {"messages": [LLM 响应], "market_report": 报告文本}。
        # 【关键逻辑】构造提示 → bind_tools → 单轮 invoke;仅当 LLM 本轮不再调用工具时
        #            才把响应内容写入 market_report,否则报告留空(等后续消息继续)。
        current_date = state["trade_date"]
        instrument_context = get_instrument_context_from_state(state)  # 【调用函数】从图状态提取标的上下文(品种/名称等),注入系统提示

        tools = [  # 【变量】本节点向 LLM 注册的工具白名单(取行情→算指标→核验快照),将被 bind_tools 绑定
            get_stock_data,
            get_indicators,
            get_verified_market_snapshot,
        ]

        system_message = (
            """You are a trading assistant tasked with analyzing financial markets. Your role is to select the **most relevant indicators** for a given market condition or trading strategy from the following list. The goal is to choose up to **8 indicators** that provide complementary insights without redundancy. Categories and each category's indicators are:

Moving Averages:
- close_50_sma: 50 SMA: A medium-term trend indicator. Usage: Identify trend direction and serve as dynamic support/resistance. Tips: It lags price; combine with faster indicators for timely signals.
- close_200_sma: 200 SMA: A long-term trend benchmark. Usage: Confirm overall market trend and identify golden/death cross setups. Tips: It reacts slowly; best for strategic trend confirmation rather than frequent trading entries.
- close_10_ema: 10 EMA: A responsive short-term average. Usage: Capture quick shifts in momentum and potential entry points. Tips: Prone to noise in choppy markets; use alongside longer averages for filtering false signals.

MACD Related:
- macd: MACD: Computes momentum via differences of EMAs. Usage: Look for crossovers and divergence as signals of trend changes. Tips: Confirm with other indicators in low-volatility or sideways markets.
- macds: MACD Signal: An EMA smoothing of the MACD line. Usage: Use crossovers with the MACD line to trigger trades. Tips: Should be part of a broader strategy to avoid false positives.
- macdh: MACD Histogram: Shows the gap between the MACD line and its signal. Usage: Visualize momentum strength and spot divergence early. Tips: Can be volatile; complement with additional filters in fast-moving markets.

Momentum Indicators:
- rsi: RSI: Measures momentum to flag overbought/oversold conditions. Usage: Apply 70/30 thresholds and watch for divergence to signal reversals. Tips: In strong trends, RSI may remain extreme; always cross-check with trend analysis.

Volatility Indicators:
- boll: Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands. Usage: Acts as a dynamic benchmark for price movement. Tips: Combine with the upper and lower bands to effectively spot breakouts or reversals.
- boll_ub: Bollinger Upper Band: Typically 2 standard deviations above the middle line. Usage: Signals potential overbought conditions and breakout zones. Tips: Confirm signals with other tools; prices may ride the band in strong trends.
- boll_lb: Bollinger Lower Band: Typically 2 standard deviations below the middle line. Usage: Indicates potential oversold conditions. Tips: Use additional analysis to avoid false reversal signals.
- atr: ATR: Averages true range to measure volatility. Usage: Set stop-loss levels and adjust position sizes based on current market volatility. Tips: It's a reactive measure, so use it as part of a broader risk management strategy.

Volume-Based Indicators:
- vwma: VWMA: A moving average weighted by volume. Usage: Confirm trends by integrating price action with volume data. Tips: Watch for skewed results from volume spikes; use in combination with other volume analyses.

- Select indicators that provide diverse and complementary information. Avoid redundancy (e.g., do not select both rsi and stochrsi). Also briefly explain why they are suitable for the given market context. When you tool call, please use the exact name of the indicators provided above as they are defined parameters, otherwise your call will fail. Please make sure to call get_stock_data first to retrieve the CSV that is needed to generate indicators. Then use get_indicators with the specific indicator names.

Before writing the final report, call get_verified_market_snapshot for this ticker and the current date, and treat it as the source of truth for any exact OHLCV, price-level, or indicator-value claim. If another tool's output conflicts with the verified snapshot, flag the discrepancy rather than inventing a reconciled number. Do not claim historical validation, support/resistance bounces, or exact percentage moves unless they are directly supported by tool output with concrete dates and prices.

Write a very detailed and nuanced report of the trends you observe. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."""
            + """ Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."""
            + get_language_instruction()
        )

        prompt = ChatPromptTemplate.from_messages(  # 【调用函数】构造提示模板(系统提示 + 消息历史占位符)
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    " You have access to the following tools: {tool_names}."
                    " Today's date is {current_date}; treat it as 'now' for all analysis and tool-call date ranges. {instrument_context}\n"
                    "{system_message}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(tools)  # 【调用函数】组装 LCEL 链:提示模板 → LLM(绑定工具)

        result = chain.invoke(state["messages"])  # 【调用函数】执行链,让 LLM 结合历史消息决策(本轮可能请求调用工具)

        report = ""

        if len(result.tool_calls) == 0:  # 本轮 LLM 未请求调用工具,其输出即最终报告
            report = result.content

        return {  # 【变量】节点输出:messages 保留完整 LLM 响应,market_report 为最终报告文本
            "messages": [result],
            "market_report": report,
        }

    return market_analyst_node
