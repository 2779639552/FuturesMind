"""Bear researcher: builds an evidence-based bear case in the investment debate.

【文件角色】空方(看空)研究员节点生成器:在多空辩论中代表空方发言。
【位置】分析师层产出报告 → 多方/空方轮流辩论(本文件为空方) → 研究经理综合。
"""

from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
)  # 【调用包】标的上下文提取与语言指令工具


# 【功能】创建"空方研究员"节点(工厂函数):让 LLM 基于各分析师报告构建看空论证。
# 【参数】llm: LLM 客户端。
# 【返回】node 函数,签名 node(state) -> dict,兼容 LangGraph StateGraph 节点。
def create_bear_researcher(llm):
    def bear_node(state) -> dict:
        # 【功能】空方发言节点本体:读取辩论状态与各分析师报告,生成并追加本轮看空论据。
        # 【参数】state: 图状态字典,含 investment_debate_state、market_report、sentiment_report、
        #               news_report、fundamentals_report、asset_type 等。
        # 【返回】dict: {"investment_debate_state": 更新后的辩论状态(追加 history/bear_history/current_response/count)}。
        # 【关键逻辑】把多方最新论据(current_response)与各报告拼进提示,让 LLM 逐条反驳;论据加
        #            "Bear Analyst:" 前缀后写回辩论状态,供下一位发言人引用。
        investment_debate_state = state["investment_debate_state"]  # 【变量】多空辩论状态引用,用于读写历史
        history = investment_debate_state.get("history", "")  # 【变量】辩论完整历史,追加本轮论据
        bear_history = investment_debate_state.get("bear_history", "")  # 【变量】空方发言历史,追加本轮论据

        current_response = investment_debate_state.get("current_response", "")  # 【变量】上一位发言人(多方)的最新论据,供逐条反驳
        market_research_report = state["market_report"]  # 【变量】市场分析报告,作为空方论据来源
        sentiment_report = state["sentiment_report"]  # 【变量】情绪分析报告,作为空方论据来源
        news_report = state["news_report"]  # 【变量】新闻/宏观报告,作为空方论据来源
        fundamentals_report = state["fundamentals_report"]  # 【变量】基本面报告,作为空方论据来源
        instrument_context = get_instrument_context_from_state(state)  # 【调用函数】从图状态提取标的上下文,注入提示
        asset_type = state.get("asset_type", "stock")  # 【变量】标的类型(默认股票),决定提示中的措辞
        target_label = "stock" if asset_type == "stock" else "asset"  # 【变量】辩论对象标签(股票/资产),用于提示文本
        fundamentals_label = (  # 【变量】基本面报告在提示中的名称(加密场景可能无数据)
            "Company fundamentals report"
            if asset_type == "stock"
            else "Asset fundamentals report (may be unavailable for crypto)"
        )

        prompt = f"""You are a Bear Analyst making the case against investing in the {target_label}. Your goal is to present a well-reasoned argument emphasizing risks, challenges, and negative indicators. Leverage the provided research and data to highlight potential downsides and counter bullish arguments effectively.

Key points to focus on:

- Risks and Challenges: Highlight factors like market saturation, financial instability, or macroeconomic threats that could hinder the stock's performance.
- Competitive Weaknesses: Emphasize vulnerabilities such as weaker market positioning, declining innovation, or threats from competitors.
- Negative Indicators: Use evidence from financial data, market trends, or recent adverse news to support your position.
- Bull Counterpoints: Critically analyze the bull argument with specific data and sound reasoning, exposing weaknesses or over-optimistic assumptions.
- Engagement: Present your argument in a conversational style, directly engaging with the bull analyst's points and debating effectively rather than simply listing facts.

Resources available:

{instrument_context}
Market research report: {market_research_report}
Social media sentiment report: {sentiment_report}
Latest world affairs news: {news_report}
{fundamentals_label}: {fundamentals_report}
Conversation history of the debate: {history}
Last bull argument: {current_response}
Use this information to deliver a compelling bear argument, refute the bull's claims, and engage in a dynamic debate that demonstrates the risks and weaknesses of investing in the {target_label}.
""" + get_language_instruction()

        response = llm.invoke(prompt)  # 【调用函数】LLM 调用:让空方研读论据并生成反驳论证

        argument = f"Bear Analyst: {response.content}"  # 【变量】把 LLM 输出加前缀标签,作为本轮论据写入辩论历史

        new_investment_debate_state = {  # 【变量】更新后的辩论状态:追加历史/发言计数,并写 current_response 供下一位发言人引用
            "history": history + "\n" + argument,
            "bear_history": bear_history + "\n" + argument,
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": argument,
            "count": investment_debate_state["count"] + 1,
        }

        return {"investment_debate_state": new_investment_debate_state}

    return bear_node
