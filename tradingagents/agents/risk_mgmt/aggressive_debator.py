"""Aggressive risk debator: champions high-reward, high-risk opportunities in the risk debate.

【文件角色】激进风险分析师节点生成器:在"风险辩论"中代表高风险高回报立场。
【位置】交易员提案 → 激进/保守/中性三方风险辩论(本文件为激进方) → 组合经理综合。
"""

from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
)  # 【调用包】标的上下文提取与语言指令工具


# 【功能】创建"激进风险分析师"节点(工厂函数):让 LLM 为交易员提案作高风险高回报辩护。
# 【参数】llm: LLM 客户端。
# 【返回】node 函数,签名 node(state) -> dict,兼容 LangGraph StateGraph 节点。
def create_aggressive_debator(llm):
    def aggressive_node(state) -> dict:
        # 【功能】激进方发言节点本体:读取风险辩论状态与各分析师报告,生成并追加本轮论据。
        # 【参数】state: 图状态字典,含 risk_debate_state、trader_investment_plan、market_report 等。
        # 【返回】dict: {"risk_debate_state": 更新后的风险辩论状态}。
        # 【关键逻辑】把保守/中立方最新论据拼进提示,让 LLM 逐条反驳;论据加 "Aggressive Analyst:"
        #            前缀后写回 risk_debate_state,并置 latest_speaker="Aggressive"。
        risk_debate_state = state["risk_debate_state"]  # 【变量】风险辩论状态引用,用于读写历史
        history = risk_debate_state.get("history", "")  # 【变量】风险辩论完整历史,追加本轮论据
        aggressive_history = risk_debate_state.get("aggressive_history", "")  # 【变量】激进方发言历史,追加本轮论据

        current_conservative_response = risk_debate_state.get("current_conservative_response", "")  # 【变量】保守方最新论据,供逐条回应
        current_neutral_response = risk_debate_state.get("current_neutral_response", "")  # 【变量】中立方最新论据,供逐条回应

        market_research_report = state["market_report"]  # 【变量】市场分析报告,作为论据来源
        sentiment_report = state["sentiment_report"]  # 【变量】情绪分析报告,作为论据来源
        news_report = state["news_report"]  # 【变量】新闻/宏观报告,作为论据来源
        fundamentals_report = state["fundamentals_report"]  # 【变量】基本面报告,作为论据来源
        instrument_context = get_instrument_context_from_state(state)  # 【调用函数】从图状态提取标的上下文,注入提示

        trader_decision = state["trader_investment_plan"]  # 【变量】交易员提案,作为风险辩论的对象

        prompt = f"""As the Aggressive Risk Analyst, your role is to actively champion high-reward, high-risk opportunities, emphasizing bold strategies and competitive advantages. When evaluating the trader's decision or plan, focus intently on the potential upside, growth potential, and innovative benefits—even when these come with elevated risk. Use the provided market data and sentiment analysis to strengthen your arguments and challenge the opposing views. Specifically, respond directly to each point made by the conservative and neutral analysts, countering with data-driven rebuttals and persuasive reasoning. Highlight where their caution might miss critical opportunities or where their assumptions may be overly conservative. Here is the trader's decision:

{trader_decision}

Your task is to create a compelling case for the trader's decision by questioning and critiquing the conservative and neutral stances to demonstrate why your high-reward perspective offers the best path forward. Incorporate insights from the following sources into your arguments:

{instrument_context}
Market Research Report: {market_research_report}
Social Media Sentiment Report: {sentiment_report}
Latest World Affairs Report: {news_report}
Company Fundamentals Report: {fundamentals_report}
Here is the current conversation history: {history} Here are the last arguments from the conservative analyst: {current_conservative_response} Here are the last arguments from the neutral analyst: {current_neutral_response}. If there are no responses from the other viewpoints yet, present your own argument based on the available data.

Engage actively by addressing any specific concerns raised, refuting the weaknesses in their logic, and asserting the benefits of risk-taking to outpace market norms. Maintain a focus on debating and persuading, not just presenting data. Challenge each counterpoint to underscore why a high-risk approach is optimal. Output conversationally as if you are speaking without any special formatting.""" + get_language_instruction()

        response = llm.invoke(prompt)  # 【调用函数】LLM 调用:让激进方生成风险辩论论据

        argument = f"Aggressive Analyst: {response.content}"  # 【变量】把 LLM 输出加前缀标签,作为本轮论据写入辩论历史

        new_risk_debate_state = {  # 【变量】更新后的风险辩论状态:追加历史/发言计数,并写 current_aggressive_response 供他方引用
            "history": history + "\n" + argument,
            "aggressive_history": aggressive_history + "\n" + argument,
            "conservative_history": risk_debate_state.get("conservative_history", ""),
            "neutral_history": risk_debate_state.get("neutral_history", ""),
            "latest_speaker": "Aggressive",
            "current_aggressive_response": argument,
            "current_conservative_response": risk_debate_state.get(
                "current_conservative_response", ""
            ),
            "current_neutral_response": risk_debate_state.get("current_neutral_response", ""),
            "count": risk_debate_state["count"] + 1,
        }

        return {"risk_debate_state": new_risk_debate_state}

    return aggressive_node
