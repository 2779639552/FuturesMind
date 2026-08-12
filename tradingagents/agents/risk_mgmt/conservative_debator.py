"""Conservative risk debator: protects assets and minimizes volatility in the risk debate.

【文件角色】保守风险分析师节点生成器:在"风险辩论"中代表稳健低风险立场。
【位置】交易员提案 → 激进/保守/中性三方风险辩论(本文件为保守方) → 组合经理综合。
"""

from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
)  # 【调用包】标的上下文提取与语言指令工具


# 【功能】创建"保守风险分析师"节点(工厂函数):让 LLM 为交易员提案作稳健低风险审查。
# 【参数】llm: LLM 客户端。
# 【返回】node 函数,签名 node(state) -> dict,兼容 LangGraph StateGraph 节点。
def create_conservative_debator(llm):
    def conservative_node(state) -> dict:
        # 【功能】保守方发言节点本体:读取风险辩论状态与各分析师报告,生成并追加本轮论据。
        # 【参数】state: 图状态字典,含 risk_debate_state、trader_investment_plan、market_report 等。
        # 【返回】dict: {"risk_debate_state": 更新后的风险辩论状态}。
        # 【关键逻辑】把激进/中立方最新论据拼进提示,让 LLM 逐条反驳;论据加 "Conservative Analyst:"
        #            前缀后写回 risk_debate_state,并置 latest_speaker="Conservative"。
        risk_debate_state = state["risk_debate_state"]  # 【变量】风险辩论状态引用,用于读写历史
        history = risk_debate_state.get("history", "")  # 【变量】风险辩论完整历史,追加本轮论据
        conservative_history = risk_debate_state.get("conservative_history", "")  # 【变量】保守方发言历史,追加本轮论据

        current_aggressive_response = risk_debate_state.get("current_aggressive_response", "")  # 【变量】激进方最新论据,供逐条回应
        current_neutral_response = risk_debate_state.get("current_neutral_response", "")  # 【变量】中立方最新论据,供逐条回应

        market_research_report = state["market_report"]  # 【变量】市场分析报告,作为论据来源
        sentiment_report = state["sentiment_report"]  # 【变量】情绪分析报告,作为论据来源
        news_report = state["news_report"]  # 【变量】新闻/宏观报告,作为论据来源
        fundamentals_report = state["fundamentals_report"]  # 【变量】基本面报告,作为论据来源
        instrument_context = get_instrument_context_from_state(state)  # 【调用函数】从图状态提取标的上下文,注入提示

        trader_decision = state["trader_investment_plan"]  # 【变量】交易员提案,作为风险辩论的对象

        prompt = f"""As the Conservative Risk Analyst, your primary objective is to protect assets, minimize volatility, and ensure steady, reliable growth. You prioritize stability, security, and risk mitigation, carefully assessing potential losses, economic downturns, and market volatility. When evaluating the trader's decision or plan, critically examine high-risk elements, pointing out where the decision may expose the firm to undue risk and where more cautious alternatives could secure long-term gains. Here is the trader's decision:

{trader_decision}

Your task is to actively counter the arguments of the Aggressive and Neutral Analysts, highlighting where their views may overlook potential threats or fail to prioritize sustainability. Respond directly to their points, drawing from the following data sources to build a convincing case for a low-risk approach adjustment to the trader's decision:

{instrument_context}
Market Research Report: {market_research_report}
Social Media Sentiment Report: {sentiment_report}
Latest World Affairs Report: {news_report}
Company Fundamentals Report: {fundamentals_report}
Here is the current conversation history: {history} Here is the last response from the aggressive analyst: {current_aggressive_response} Here is the last response from the neutral analyst: {current_neutral_response}. If there are no responses from the other viewpoints yet, present your own argument based on the available data.

Engage by questioning their optimism and emphasizing the potential downsides they may have overlooked. Address each of their counterpoints to showcase why a conservative stance is ultimately the safest path for the firm's assets. Focus on debating and critiquing their arguments to demonstrate the strength of a low-risk strategy over their approaches. Output conversationally as if you are speaking without any special formatting.""" + get_language_instruction()

        response = llm.invoke(prompt)  # 【调用函数】LLM 调用:让保守方生成风险辩论论据

        argument = f"Conservative Analyst: {response.content}"  # 【变量】把 LLM 输出加前缀标签,作为本轮论据写入辩论历史

        new_risk_debate_state = {  # 【变量】更新后的风险辩论状态:追加历史/发言计数,并写 current_conservative_response 供他方引用
            "history": history + "\n" + argument,
            "aggressive_history": risk_debate_state.get("aggressive_history", ""),
            "conservative_history": conservative_history + "\n" + argument,
            "neutral_history": risk_debate_state.get("neutral_history", ""),
            "latest_speaker": "Conservative",
            "current_aggressive_response": risk_debate_state.get("current_aggressive_response", ""),
            "current_conservative_response": argument,
            "current_neutral_response": risk_debate_state.get("current_neutral_response", ""),
            "count": risk_debate_state["count"] + 1,
        }

        return {"risk_debate_state": new_risk_debate_state}

    return conservative_node
