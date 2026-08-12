"""Bull researcher: builds an evidence-based bull case in the investment debate.

【文件角色】多方(看多)研究员节点生成器:在多空辩论中代表多方发言。
【位置】分析师层产出报告 → 多方/空方轮流辩论(本文件为多方) → 研究经理综合。
"""

from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
)  # 【调用包】标的上下文提取与语言指令工具


# 【功能】创建"多方研究员"节点(工厂函数):让 LLM 基于各分析师报告构建看多论证。
# 【参数】llm: LLM 客户端。
# 【返回】node 函数,签名 node(state) -> dict,兼容 LangGraph StateGraph 节点。
def create_bull_researcher(llm):
    def bull_node(state) -> dict:
        # 【功能】多方发言节点本体:读取辩论状态与各分析师报告,生成并追加本轮看多论据。
        # 【参数】state: 图状态字典,含 investment_debate_state、market_report、sentiment_report、
        #               news_report、fundamentals_report、asset_type 等。
        # 【返回】dict: {"investment_debate_state": 更新后的辩论状态(追加 history/bull_history/current_response/count)}。
        # 【关键逻辑】把空方最新论据(current_response)与各报告拼进提示,让 LLM 逐条反驳;论据加
        #            "Bull Analyst:" 前缀后写回辩论状态,供下一位发言人引用。
        investment_debate_state = state["investment_debate_state"]  # 【变量】多空辩论状态引用,用于读写历史
        history = investment_debate_state.get("history", "")  # 【变量】辩论完整历史,追加本轮论据
        bull_history = investment_debate_state.get("bull_history", "")  # 【变量】多方发言历史,追加本轮论据

        current_response = investment_debate_state.get("current_response", "")  # 【变量】上一位发言人(空方)的最新论据,供逐条反驳
        market_research_report = state["market_report"]  # 【变量】市场分析报告,作为多方论据来源
        sentiment_report = state["sentiment_report"]  # 【变量】情绪分析报告,作为多方论据来源
        news_report = state["news_report"]  # 【变量】新闻/宏观报告,作为多方论据来源
        fundamentals_report = state["fundamentals_report"]  # 【变量】基本面报告,作为多方论据来源
        instrument_context = get_instrument_context_from_state(state)  # 【调用函数】从图状态提取标的上下文,注入提示
        asset_type = state.get("asset_type", "stock")  # 【变量】标的类型(默认股票),决定提示中的措辞
        target_label = "stock" if asset_type == "stock" else "asset"  # 【变量】辩论对象标签(股票/资产),用于提示文本
        fundamentals_label = (  # 【变量】基本面报告在提示中的名称(加密场景可能无数据)
            "Company fundamentals report"
            if asset_type == "stock"
            else "Asset fundamentals report (may be unavailable for crypto)"
        )

        prompt = f"""You are a Bull Analyst advocating for investing in the {target_label}. Your task is to build a strong, evidence-based case emphasizing growth potential, competitive advantages, and positive market indicators. Leverage the provided research and data to address concerns and counter bearish arguments effectively.

Key points to focus on:
- Growth Potential: Highlight the company's market opportunities, revenue projections, and scalability.
- Competitive Advantages: Emphasize factors like unique products, strong branding, or dominant market positioning.
- Positive Indicators: Use financial health, industry trends, and recent positive news as evidence.
- Bear Counterpoints: Critically analyze the bear argument with specific data and sound reasoning, addressing concerns thoroughly and showing why the bull perspective holds stronger merit.
- Engagement: Present your argument in a conversational style, engaging directly with the bear analyst's points and debating effectively rather than just listing data.

Resources available:
{instrument_context}
Market research report: {market_research_report}
Social media sentiment report: {sentiment_report}
Latest world affairs news: {news_report}
{fundamentals_label}: {fundamentals_report}
Conversation history of the debate: {history}
Last bear argument: {current_response}
Use this information to deliver a compelling bull argument, refute the bear's concerns, and engage in a dynamic debate that demonstrates the strengths of the bull position.
""" + get_language_instruction()

        response = llm.invoke(prompt)  # 【调用函数】LLM 调用:让多方研读论据并生成反驳论证

        argument = f"Bull Analyst: {response.content}"  # 【变量】把 LLM 输出加前缀标签,作为本轮论据写入辩论历史

        new_investment_debate_state = {  # 【变量】更新后的辩论状态:追加历史/发言计数,并写 current_response 供下一位发言人引用
            "history": history + "\n" + argument,
            "bull_history": bull_history + "\n" + argument,
            "bear_history": investment_debate_state.get("bear_history", ""),
            "current_response": argument,
            "count": investment_debate_state["count"] + 1,
        }

        return {"investment_debate_state": new_investment_debate_state}

    return bull_node
