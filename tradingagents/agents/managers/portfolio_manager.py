"""Portfolio Manager: synthesises the risk-analyst debate into the final decision.

Uses LangChain's ``with_structured_output`` so the LLM produces a typed
``PortfolioDecision`` directly, in a single call.  The result is rendered
back to markdown for storage in ``final_trade_decision`` so memory log,
CLI display, and saved reports continue to consume the same shape they do
today.  When a provider does not expose structured output, the agent falls
back gracefully to free-text generation.
"""

# =============================================================================
# 【文件角色】投资组合经理节点生成器:把"风险辩论"结论综合成最终交易决策。
# 【位置】分析管线末段:研究经理/交易员/风险辩论 → 本文件(组合经理) → 最终决策落盘。
# 【要点】用 with_structured_output 让 LLM 一次输出结构化 PortfolioDecision;
#         无结构化能力时回退自由文本。最终决策渲染回 markdown 存 final_trade_decision。
# =============================================================================

from __future__ import annotations  # 【调用包】延迟求值注解;允许前向引用的类型提示

from tradingagents.agents.schemas import PortfolioDecision, render_pm_decision  # 【调用包】结构化决策 Pydantic 模型与 markdown 渲染函数(用于落盘/展示)
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
)  # 【调用包】标的上下文提取与语言指令工具
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)  # 【调用包】结构化输出绑定与"结构化优先、自由文本兜底"的调用助手


# 【功能】创建"投资组合经理"节点(工厂函数):把风险分析师辩论历史综合成最终交易评级。
# 【参数】llm: LLM 客户端。
# 【返回】node 函数,签名 node(state) -> dict,兼容 LangGraph StateGraph 节点。
# 【关键逻辑】先用 bind_structured 绑定 PortfolioDecision 模型;内部再定义闭包节点。
def create_portfolio_manager(llm):
    structured_llm = bind_structured(llm, PortfolioDecision, "Portfolio Manager")  # 【调用函数】把 LLM 绑定为结构化输出(直接产出 PortfolioDecision 模型)

    def portfolio_manager_node(state) -> dict:
        # 【功能】组合经理节点本体:读取风险辩论历史与研究/交易计划,用结构化输出生成最终决策。
        # 【参数】state: 图状态字典,含 risk_debate_state、investment_plan、trader_investment_plan、past_context。
        # 【返回】dict: {"risk_debate_state": 更新后的状态, "final_trade_decision": 最终决策文本}。
        # 【关键逻辑】调用 invoke_structured_or_freetext:优先结构化输出,失败时退化为自由文本;
        #            同时把最终决策回写进 risk_debate_state.judge_decision。
        instrument_context = get_instrument_context_from_state(state)  # 【调用函数】从图状态提取标的上下文,注入提示

        history = state["risk_debate_state"]["history"]  # 【变量】风险辩论的历史消息,作为最终决策的依据
        risk_debate_state = state["risk_debate_state"]  # 【变量】风险辩论状态的引用,用于回写最终决策
        research_plan = state["investment_plan"]  # 【变量】研究经理给出的投资计划文本
        trader_plan = state["trader_investment_plan"]  # 【变量】交易员给出的交易提案文本

        past_context = state.get("past_context", "")  # 【变量】历史决策教训(进化记忆),供组合经理参考
        lessons_line = (  # 【变量】把历史教训格式化为提示中的 "Lessons" 段落(为空时为空串)
            f"- Lessons from prior decisions and outcomes:\n{past_context}\n"
            if past_context
            else ""
        )

        prompt = f"""As the Portfolio Manager, synthesize the risk analysts' debate and deliver the final trading decision.

{instrument_context}

---

**Rating Scale** (use exactly one):
- **Buy**: Strong conviction to enter or add to position
- **Overweight**: Favorable outlook, gradually increase exposure
- **Hold**: Maintain current position, no action needed
- **Underweight**: Reduce exposure, take partial profits
- **Sell**: Exit position or avoid entry

**Context:**
- Research Manager's investment plan: **{research_plan}**
- Trader's transaction proposal: **{trader_plan}**
{lessons_line}
**Risk Analysts Debate History:**
{history}

---

Be decisive and ground every conclusion in specific evidence from the analysts.{get_language_instruction()}"""

        final_trade_decision = invoke_structured_or_freetext(  # 【调用函数】结构化优先/自由文本兜底地调用 LLM,产出最终交易决策
            structured_llm,
            llm,
            prompt,
            render_pm_decision,
            "Portfolio Manager",
        )

        new_risk_debate_state = {  # 【变量】更新后的风险辩论状态:写入 judge_decision=最终决策,其余字段透传
            "judge_decision": final_trade_decision,
            "history": risk_debate_state["history"],
            "aggressive_history": risk_debate_state["aggressive_history"],
            "conservative_history": risk_debate_state["conservative_history"],
            "neutral_history": risk_debate_state["neutral_history"],
            "latest_speaker": "Judge",
            "current_aggressive_response": risk_debate_state["current_aggressive_response"],
            "current_conservative_response": risk_debate_state["current_conservative_response"],
            "current_neutral_response": risk_debate_state["current_neutral_response"],
            "count": risk_debate_state["count"],
        }

        return {
            "risk_debate_state": new_risk_debate_state,
            "final_trade_decision": final_trade_decision,
        }

    return portfolio_manager_node
