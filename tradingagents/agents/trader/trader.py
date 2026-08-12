"""Trader: turns the Research Manager's investment plan into a concrete transaction proposal.

【文件角色】交易员节点生成器:把研究经理的投资计划转成具体的买/卖/持有提案。
【位置】研究经理 → 本文件(交易员) → 风险辩论/组合经理。
"""

from __future__ import annotations  # 【调用包】延迟求值注解;允许前向引用的类型提示

import functools  # 【调用包】预绑定参数;把 trader_node 的 name 参数固定为 "Trader"

from langchain_core.messages import AIMessage  # 【调用包】LangChain 消息类型;把交易提案包装为 AIMessage 写入图状态

from tradingagents.agents.schemas import TraderProposal, render_trader_proposal  # 【调用包】结构化交易提案模型与 markdown 渲染函数
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
)  # 【调用包】标的上下文提取与语言指令工具
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)  # 【调用包】结构化输出绑定与"结构化优先、自由文本兜底"的调用助手


# 【功能】创建"交易员"节点(工厂函数):让 LLM 依据投资计划给出具体的买/卖/持有提案。
# 【参数】llm: LLM 客户端。
# 【返回】node 函数,签名 node(state) -> dict,兼容 LangGraph StateGraph 节点。
# 【关键逻辑】返回 functools.partial 预绑定了 name="Trader" 的节点,统一节点签名。
def create_trader(llm):
    structured_llm = bind_structured(llm, TraderProposal, "Trader")  # 【调用函数】把 LLM 绑定为结构化输出(直接产出 TraderProposal 模型)

    def trader_node(state, name):
        # 【功能】交易员节点本体:把研究经理的投资计划转成具体交易提案。
        # 【参数】state: 图状态字典,含 company_of_interest、investment_plan;name: 节点名(经 partial 固定为 "Trader")。
        # 【返回】dict: {"messages": [AIMessage(交易提案)], "trader_investment_plan": 提案文本, "sender": 节点名}。
        # 【关键逻辑】构造 system/user 两轮消息注入投资计划与标的上下文,再走结构化/自由文本兜底调用。
        company_name = state["company_of_interest"]  # 【变量】标的名称(公司/合约),用于提示文本
        instrument_context = get_instrument_context_from_state(state)  # 【调用函数】从图状态提取标的上下文,注入提示
        investment_plan = state["investment_plan"]  # 【变量】研究经理的投资计划,作为交易决策的依据

        messages = [  # 【变量】构造给交易员的 system/user 两轮消息,包含投资计划与标的上下文
            {
                "role": "system",
                "content": (
                    "You are a trading agent analyzing market data to make investment decisions. "
                    "Based on your analysis, provide a specific recommendation to buy, sell, or hold. "
                    "Anchor your reasoning in the analysts' reports and the research plan."
                    + get_language_instruction()
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Based on a comprehensive analysis by a team of analysts, here is an investment "
                    f"plan tailored for {company_name}. {instrument_context} This plan incorporates "
                    f"insights from current technical market trends, macroeconomic indicators, and "
                    f"social media sentiment. Use this plan as a foundation for evaluating your next "
                    f"trading decision.\n\nProposed Investment Plan: {investment_plan}\n\n"
                    f"Leverage these insights to make an informed and strategic decision."
                ),
            },
        ]

        trader_plan = invoke_structured_or_freetext(  # 【调用函数】结构化优先/自由文本兜底地调用 LLM,产出交易提案
            structured_llm,
            llm,
            messages,
            render_trader_proposal,
            "Trader",
        )

        return {  # 【变量】节点输出:提案包装为 AIMessage 写入 messages,并存入 trader_investment_plan 供后续节点使用
            "messages": [AIMessage(content=trader_plan)],
            "trader_investment_plan": trader_plan,
            "sender": name,
        }

    return functools.partial(trader_node, name="Trader")  # 【调用函数】预绑定节点名 "Trader",使返回节点签名统一为 node(state)
