"""News analyst: aggregates ticker news, macro news, FRED macro indicators, and prediction markets into a news/macro report.

【文件角色】本文件是"新闻与宏观分析师"节点生成器(股票/加密路径),产出单一分析节点。
【位置】多 Agent 讨论图的分析师层:本节点 → 多方/空方辩论 → 研究经理综合。
"""

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder  # 【调用包】构建带历史占位符的提示模板;让 LLM 依据对话历史决策

from tradingagents.agents.utils.agent_utils import (
    get_global_news,
    get_instrument_context_from_state,
    get_language_instruction,
    get_macro_indicators,
    get_news,
    get_prediction_markets,
)  # 【调用包】新闻/宏观/预测市场工具(个股新闻、全球新闻、FRED 宏观指标、事件概率),及标的上下文与语言指令工具


# 【功能】创建"新闻与宏观分析师"节点(工厂函数):让 LLM 综合新闻、宏观数据与预测市场写报告。
# 【参数】llm: LLM 客户端。
# 【返回】node 函数,签名 node(state),兼容 LangGraph StateGraph 节点。
# 【关键逻辑】本文件是"股票/加密"路径的新闻分析师;商品期货宏观/新闻见 commodity_analysts.py。
def create_news_analyst(llm):
    def news_analyst_node(state):
        # 【功能】新闻分析节点本体:被 LangGraph 调用一次,产出新闻/宏观报告。
        # 【参数】state: 图状态字典,含 trade_date、asset_type、company_of_interest、messages 等。
        # 【返回】dict: {"messages": [LLM 响应], "news_report": 报告文本}。
        # 【关键逻辑】构造提示 → bind_tools → 单轮 invoke;仅当 LLM 本轮不再调用工具时
        #            才把响应内容写入 news_report,否则报告留空(等后续消息继续)。
        current_date = state["trade_date"]
        asset_type = state.get("asset_type", "stock")  # 【变量】标的类型(默认股票),决定新闻标签用"company"还是"asset"
        asset_label = "company" if asset_type == "stock" else "asset"  # 【变量】按标的类型选择的新闻标签词,用于提示文本
        instrument_context = get_instrument_context_from_state(state)  # 【调用函数】从图状态提取标的上下文,注入系统提示

        tools = [  # 【变量】本节点向 LLM 注册的工具白名单(个股新闻/全球新闻/宏观指标/预测市场),将被 bind_tools 绑定
            get_news,
            get_global_news,
            get_macro_indicators,
            get_prediction_markets,
        ]

        system_message = (
            f"You are a news researcher tasked with analyzing recent news and trends over the past week. Please write a comprehensive report of the current state of the world that is relevant for trading and macroeconomics. Use the available tools: get_news(ticker, start_date, end_date) for {asset_label}-specific news by ticker symbol, get_global_news(curr_date, look_back_days, limit) for broader macroeconomic news, get_macro_indicators(indicator, curr_date, look_back_days) to ground macro commentary in actual data from FRED (e.g. 'cpi', 'core_pce', 'unemployment', 'fed_funds_rate', '10y_treasury', 'yield_curve'), and get_prediction_markets(topic, limit) for live market-implied probabilities of forward-looking events (e.g. 'Fed rate cut', 'recession 2026', geopolitical or sector events). Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
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

        return {  # 【变量】节点输出:messages 保留完整 LLM 响应,news_report 为最终报告文本
            "messages": [result],
            "news_report": report,
        }

    return news_analyst_node
