"""Research Manager: turns the bull/bear debate into a structured investment plan for the trader.

【文件角色】研究经理节点生成器:担任多空辩论的裁判/主持人,输出结构化投资计划。
【位置】分析师层 → 多方/空方辩论 → 本文件(研究经理) → 交易员。
"""

from __future__ import annotations  # 【调用包】延迟求值注解;允许前向引用的类型提示

from tradingagents.agents.schemas import ResearchPlan, render_research_plan  # 【调用包】结构化投资计划模型与 markdown 渲染函数
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
)  # 【调用包】标的上下文提取与语言指令工具
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)  # 【调用包】结构化输出绑定与"结构化优先、自由文本兜底"的调用助手


# 【功能】创建"研究经理"节点(工厂函数):把多空辩论历史综合成结构化投资计划。
# 【参数】llm: LLM 客户端。
# 【返回】node 函数,签名 node(state) -> dict,兼容 LangGraph StateGraph 节点。
# 【关键逻辑】先绑定 ResearchPlan 结构化模型;节点内把辩论历史与投资计划回写进 investment_debate_state。
def create_research_manager(llm):
    structured_llm = bind_structured(llm, ResearchPlan, "Research Manager")  # 【调用函数】把 LLM 绑定为结构化输出(直接产出 ResearchPlan 模型)

    def research_manager_node(state) -> dict:
        # 【功能】研究经理节点本体:评估多空辩论并产出清晰、可执行的投资计划。
        # 【参数】state: 图状态字典,含 investment_debate_state。
        # 【返回】dict: {"investment_debate_state": 更新后的状态, "investment_plan": 投资计划文本}。
        # 【关键逻辑】invoke_structured_or_freetext 优先结构化输出;最终计划写入 judge_decision 与 current_response。
        instrument_context = get_instrument_context_from_state(state)  # 【调用函数】从图状态提取标的上下文,注入提示
        history = state["investment_debate_state"].get("history", "")  # 【变量】多空辩论完整历史,作为投资计划的依据

        investment_debate_state = state["investment_debate_state"]  # 【变量】多空辩论状态引用,用于回写投资计划

        prompt = f"""As the Research Manager and debate facilitator, your role is to critically evaluate this round of debate and deliver a clear, actionable investment plan for the trader.

{instrument_context}

---

**Rating Scale** (use exactly one):
- **Buy**: Strong conviction in the bull thesis; recommend taking or growing the position
- **Overweight**: Constructive view; recommend gradually increasing exposure
- **Hold**: Balanced view; recommend maintaining the current position
- **Underweight**: Cautious view; recommend trimming exposure
- **Sell**: Strong conviction in the bear thesis; recommend exiting or avoiding the position

Commit to a clear stance whenever the debate's strongest arguments warrant one; reserve Hold for situations where the evidence on both sides is genuinely balanced.

---

**Debate History:**
{history}""" + get_language_instruction()

        investment_plan = invoke_structured_or_freetext(  # 【调用函数】结构化优先/自由文本兜底地调用 LLM,产出投资计划
            structured_llm,
            llm,
            prompt,
            render_research_plan,
            "Research Manager",
        )

        new_investment_debate_state = {  # 【变量】更新后的辩论状态:写入 judge_decision/current_response=投资计划,其余字段透传
            "judge_decision": investment_plan,
            "history": investment_debate_state.get("history", ""),
            "bear_history": investment_debate_state.get("bear_history", ""),
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": investment_plan,
            "count": investment_debate_state["count"],
        }

        return {
            "investment_debate_state": new_investment_debate_state,
            "investment_plan": investment_plan,
        }

    return research_manager_node
