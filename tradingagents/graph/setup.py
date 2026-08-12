# TradingAgents/graph/setup.py

# =============================================================================
# 本文件在整个项目中的角色 —— 股票路径的 LangGraph 图编排
# -----------------------------------------------------------------------------
# 本项目用 LangGraph 的 StateGraph 把众多"智能体节点"编排成一张可执行的
# 有向图。本文件中的 GraphSetup 类就是这张【股票分析工作流图】的"总装车间":
#
#   1. 把分析/研究/交易/风控等智能体注册为图中的节点 (add_node)；
#   2. 用 add_edge 连接"固定顺序"的主干流程 (分析师 → 研究员 → 交易员 → 风控)；
#   3. 用 add_conditional_edges 在"辩论环节"做条件路由, 由上层
#      ConditionalLogic 决定下一步该去哪个节点 (例如辩论是否继续、由谁发言)。
#
# 股票路径 vs 商品路径的区别:
#   - 本文件 (setup.py) 构建的是【股票路径】: 分析师为 market/social/news/
#     fundamentals 四类, 最终走向 Bull/Bear 多空辩论 → Trader → 风险辩论 →
#     Portfolio Manager 出最终决策。
#   - 【商品期货路径】不在这里构建, 而是由根目录的 commodity_demo.py
#     (build_commodity_graph) 单独组图, 其辩论节点由 commodity_debate.py 提供,
#     使用的状态字段 (technical_report / debate_state 等) 也定义在
#     agent_states.py 中。两条路径共用 AgentState, 但图的形状和节点完全不同。
#
# 上层调用方式: TradingAgentsGraph 实例化后调用 self.graph_setup.setup_graph(
# selected_analysts) 即可拿到编译好的 workflow, 再 .compile() 得到可运行 graph。
# =============================================================================

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from tradingagents.agents import (
    create_aggressive_debator,
    create_bear_researcher,
    create_bull_researcher,
    create_conservative_debator,
    create_fundamentals_analyst,
    create_market_analyst,
    create_msg_delete,
    create_neutral_debator,
    create_news_analyst,
    create_portfolio_manager,
    create_research_manager,
    create_sentiment_analyst,
    create_trader,
)
from tradingagents.agents.utils.agent_states import AgentState

from .analyst_execution import build_analyst_execution_plan
from .conditional_logic import ConditionalLogic

# Every target a shared conditional router can return. Each edge driven by the
# router maps all of them, so a fall-through return (e.g. under prompt/i18n/
# refactor drift in the speaker labels) can never hit a missing path_map entry
# and crash LangGraph mid-run (#1088).
#
# 【中文说明】这两个字典是"条件路由"的路径映射表。LangGraph 的条件边
# add_conditional_edges(节点, 路由器函数, path_map) 会把路由器函数返回的
# 字符串当作键, 在这个字典里查到"下一步要去哪个节点"。把"所有可能返回的键"
# 都映射到自身, 相当于路由器返回谁就跳转到谁; 这样即使路由器偶发返回了
# 字典里缺失的标签, 也不会因找不到 path_map 项而在运行中途崩溃 (issue #1088)。
# DEBATE_PATH_MAP 用于【多空辩论环节】(Bull/Bear 研究员 + 研究经理);
# RISK_ANALYSIS_PATH_MAP 用于【风险辩论环节】(激进/保守/中性分析师 + 组合经理)。
DEBATE_PATH_MAP = {
    "Bull Researcher": "Bull Researcher",
    "Bear Researcher": "Bear Researcher",
    "Research Manager": "Research Manager",
}
RISK_ANALYSIS_PATH_MAP = {
    "Aggressive Analyst": "Aggressive Analyst",
    "Conservative Analyst": "Conservative Analyst",
    "Neutral Analyst": "Neutral Analyst",
    "Portfolio Manager": "Portfolio Manager",
}


class GraphSetup:
    """Handles the setup and configuration of the agent graph.

    【中文说明】
    【功能】负责把"股票路径"的全部智能体节点注册进 LangGraph 的 StateGraph,
    并用 add_edge / add_conditional_edges 把它们连接成完整的工作流图。
    本类只负责"图长什么样", 不负责真正跑图 (跑图由 TradingAgentsGraph 调用)。
    【关键逻辑】setup_graph() 是核心入口; 通过传入不同的 selected_analysts 可以
    动态决定注册哪些分析师节点 (市场/情绪/新闻/基本面四选或组合)。
    """

    def __init__(
        self,
        quick_thinking_llm: Any,
        deep_thinking_llm: Any,
        tool_nodes: dict[str, ToolNode],
        conditional_logic: ConditionalLogic,
    ):
        """Initialize with required components.

        【中文说明】
        【功能】保存搭建图所需的所有"零件"。
        【参数】
            quick_thinking_llm: "快速思考"LLM, 用于分析师/研究员/交易员等
                需要快速响应的节点 (由上层 TradingAgentsGraph 传入)。
            deep_thinking_llm: "深度思考"LLM, 用于需要更长推理的节点
                (研究经理 Research Manager、组合经理 Portfolio Manager)。
            tool_nodes: 一个字典, 键是分析师类型 (market/social/news/fundamentals),
                值是对应的 ToolNode (LangGraph 预置的"工具调用节点", 让 LLM 可以
                在运行中调用真实数据工具, 如拉行情、查财报)。
            conditional_logic: ConditionalLogic 实例, 提供一系列"路由器函数"
                (should_continue_*), 在图运行中决定下一步条件路由的走向。
        【返回】无 (构造器)。
        """
        self.quick_thinking_llm = quick_thinking_llm
        self.deep_thinking_llm = deep_thinking_llm
        self.tool_nodes = tool_nodes
        self.conditional_logic = conditional_logic

    def setup_graph(self, selected_analysts=("market", "social", "news", "fundamentals")):
        """Set up and compile the agent workflow graph.

        Args:
            selected_analysts (list): List of analyst types to include. Options are:
                - "market": Market analyst
                - "social": Social media analyst
                - "news": News analyst
                - "fundamentals": Fundamentals analyst

        【中文说明】
        【功能】组装并返回一张编译前的 LangGraph 工作流 (StateGraph 对象)。
        【参数】
            selected_analysts: 要启用的分析师类型元组/列表, 默认四种全开。
                注意: 顺序会决定图中"分析师串行执行"的先后顺序。
        【返回】workflow: 一个 StateGraph 实例 (尚未 compile), 调用方之后
            执行 workflow.compile() 即可得到可运行 graph。
        【关键逻辑】分三步理解 ——
            1. add_node 注册: 把每个分析师拆成三个节点 (分析师智能体节点 /
               清空消息节点 / 工具节点), 再加研究员、交易员、风控等固定节点;
            2. add_edge 固定主干: 按 plan.specs 顺序串起分析师, 最后接
               Bull/Bear 辩论 → Research Manager → Trader → 风险辩论 → 结束;
            3. add_conditional_edges 条件路由: 分析师之后、辩论环节、风险环节
               都通过 conditional_logic 的路由函数决定"下一步去哪个节点"。
        """
        plan = build_analyst_execution_plan(selected_analysts)
        # 【关键逻辑】build_analyst_execution_plan 根据选中的分析师类型生成一份
        # "执行计划", 其中 plan.specs 是有序列表, 每个 spec 描述一位分析师的
        # 三个节点名: agent_node(分析师本体) / tool_node(工具节点) /
        # clear_node(清空消息节点)。顺序就决定了主流程的先后。

        analyst_factories = {
            "market": lambda: create_market_analyst(self.quick_thinking_llm),
            "social": lambda: create_sentiment_analyst(self.quick_thinking_llm),
            "news": lambda: create_news_analyst(self.quick_thinking_llm),
            "fundamentals": lambda: create_fundamentals_analyst(self.quick_thinking_llm),
        }
        # 【中文说明】analyst_factories: "分析师工厂字典"。键是分析师类型名,
        # 值是"延迟创建"的 lambda 工厂函数。每次调用工厂都会 new 出一个全新的
        # 分析师智能体实例——因为图可能被多次编译/运行, 每个节点需要独立实例。
        # 用 lambda 延迟创建 (而不是直接创建) 是为了在遍历 plan.specs 时才按需
        # 实例化, 避免过早创建用不到的节点。

        # Create researcher and manager nodes
        # 【中文说明】先实例化"固定节点"——无论选哪些分析师, 以下节点都存在:
        #   牛方研究员 / 熊方研究员 (多空辩论双方), 研究经理 (裁定辩论),
        #   交易员 (根据研究报告生成交易计划)。研究经理用 deep_thinking_llm,
        # 其余用 quick_thinking_llm。
        bull_researcher_node = create_bull_researcher(self.quick_thinking_llm)
        bear_researcher_node = create_bear_researcher(self.quick_thinking_llm)
        research_manager_node = create_research_manager(self.deep_thinking_llm)
        trader_node = create_trader(self.quick_thinking_llm)

        # Create risk analysis nodes
        # 【中文说明】风险辩论环节的三个"性格"分析师 + 组合经理:
        #   激进 / 保守 / 中性三位分析师各自给出风险评估, 组合经理 (Portfolio
        #   Manager) 作为最终拍板人。组合经理用 deep_thinking_llm。
        aggressive_analyst = create_aggressive_debator(self.quick_thinking_llm)
        neutral_analyst = create_neutral_debator(self.quick_thinking_llm)
        conservative_analyst = create_conservative_debator(self.quick_thinking_llm)
        portfolio_manager_node = create_portfolio_manager(self.deep_thinking_llm)

        # Create workflow
        # 【关键逻辑】StateGraph 就是这张图的"画布", AgentState 是整张图共享的
        # 状态类型 (一个字典, 所有节点读/写同一个状态对象, 节点间通过它传递数据)。
        workflow = StateGraph(AgentState)

        # Add analyst nodes to the graph
        # 【关键逻辑】add_node(name, node) = 把某个可调用对象注册成图中一个节点。
        # 每位分析师被拆成三个节点:
        #   spec.agent_node —— 分析师智能体本身 (llm + prompt 包装成的可调用对象);
        #   spec.clear_node —— 清空消息节点 (避免上下文无限累积, 用 create_msg_delete());
        #   spec.tool_node  —— 工具节点 (ToolNode, 让 LLM 能调用真实数据工具)。
        for spec in plan.specs:
            workflow.add_node(spec.agent_node, analyst_factories[spec.key]())
            workflow.add_node(spec.clear_node, create_msg_delete())
            workflow.add_node(spec.tool_node, self.tool_nodes[spec.key])

        # Add other nodes
        # 【中文说明】把前面实例化的"固定节点"也注册进图。名字 (字符串) 是节点
        # 在图中的唯一标识, 后续 add_edge 都用这个名字来引用节点。
        workflow.add_node("Bull Researcher", bull_researcher_node)
        workflow.add_node("Bear Researcher", bear_researcher_node)
        workflow.add_node("Research Manager", research_manager_node)
        workflow.add_node("Trader", trader_node)
        workflow.add_node("Aggressive Analyst", aggressive_analyst)
        workflow.add_node("Neutral Analyst", neutral_analyst)
        workflow.add_node("Conservative Analyst", conservative_analyst)
        workflow.add_node("Portfolio Manager", portfolio_manager_node)

        # Define edges
        # Start with the first analyst
        # 【关键逻辑】add_edge(起点, 终点) = 加一条"无条件"的有向边: 起点节点
        # 执行完必然走到终点。START 是 LangGraph 提供的图入口哨兵节点, 这里把
        # 图入口接到"第一位分析师"节点, 整张图从此处开始运行。
        workflow.add_edge(START, plan.specs[0].agent_node)

        # Connect analysts in sequence
        # 【关键逻辑】这段循环是"分析师串行主流程"的核心。对每一位分析师 i:
        #   1) 条件边: agent_node 完成后调用 should_continue_{key}, 返回
        #      "tool" 则去工具节点 current_tools, 否则去清空节点 current_clear
        #      (即该分析师分析完成, 进入下一步)。
        #   2) 工具节点执行完, 无条件回到 agent_node (工具结果要送回给分析师
        #      让它继续分析, 形成"分析师↔工具"的内部小循环)。
        #   3) 清空节点之后: 若不是最后一位分析师, 就无条件连到下一位分析师
        #      的 agent_node; 若是最后一位, 则连到 "Bull Researcher" 开启多空辩论。
        for i, spec in enumerate(plan.specs):
            current_analyst = spec.agent_node
            current_tools = spec.tool_node
            current_clear = spec.clear_node

            # Add conditional edges for current analyst
            workflow.add_conditional_edges(
                current_analyst,
                getattr(self.conditional_logic, f"should_continue_{spec.key}"),
                [current_tools, current_clear],
            )
            # 【中文说明】add_conditional_edges(节点, 路由函数, 目标列表) 与
            # add_edge 的区别: 前者是"条件路由"——路由函数运行时返回目标列表里
            # 的某一个名字 (这里返回 current_tools 或 current_clear), 图据此
            # 选择下一步; 后者是"无条件跳转"。getattr(...) 是按分析师类型名
            # 动态取对应的路由方法, 如 should_continue_market。
            workflow.add_edge(current_tools, current_analyst)

            # Connect to next analyst or to Bull Researcher if this is the last analyst
            if i < len(plan.specs) - 1:
                workflow.add_edge(current_clear, plan.specs[i + 1].agent_node)
            else:
                workflow.add_edge(current_clear, "Bull Researcher")

        # Both research-debate edges share the complete DEBATE_PATH_MAP (#1088).
        # 【关键逻辑】多空辩论环节: 无论 Bull 还是 Bear 研究员发言完毕, 都走同
        # 一个条件路由 should_continue_debate。路由函数会依据辩论历史决定下一轮
        # 由谁发言 (Bull/Bear) 还是由研究经理 Research Manager 裁定并结束辩论。
        # path_map 填完整的 DEBATE_PATH_MAP, 保证任何返回键都有对应去向 (#1088)。
        for debate_node in ("Bull Researcher", "Bear Researcher"):
            workflow.add_conditional_edges(
                debate_node,
                self.conditional_logic.should_continue_debate,
                DEBATE_PATH_MAP,
            )
        # 【中文说明】辩论结束 (Research Manager 被路由选中) 后无条件走到交易员,
        # 交易员再无条件走进风险辩论环节的起点 (Aggressive Analyst)。
        workflow.add_edge("Research Manager", "Trader")
        workflow.add_edge("Trader", "Aggressive Analyst")
        # All three risk edges share the complete RISK_ANALYSIS_PATH_MAP (#1088).
        # 【关键逻辑】风险辩论环节: 激进/保守/中性三位分析师发言完毕都走同一个
        # 条件路由 should_continue_risk_analysis, 决定下一位发言者, 或轮到
        # Portfolio Manager 汇总并拍板。
        for risk_node in ("Aggressive Analyst", "Conservative Analyst", "Neutral Analyst"):
            workflow.add_conditional_edges(
                risk_node,
                self.conditional_logic.should_continue_risk_analysis,
                RISK_ANALYSIS_PATH_MAP,
            )

        # 【关键逻辑】END 是 LangGraph 的出口哨兵节点。组合经理 (Portfolio
        # Manager) 一旦被风险路由选中并执行完, 整张图就到这里终止。
        workflow.add_edge("Portfolio Manager", END)

        # 【返回】返回的是"未编译"的 workflow。编译 (compile) 是 LangGraph 的
        # 一个独立步骤 (通常由 TradingAgentsGraph 完成), 编译后才能真正运行。
        return workflow
