# TradingAgents/graph/conditional_logic.py

from tradingagents.agents.utils.agent_states import AgentState  # 【调用包】图共享状态类型 (AgentState)


class ConditionalLogic:
    """Handles conditional logic for determining graph flow."""

    # 【功能】条件路由逻辑的载体: 提供一系列"下一步去哪个节点"的纯函数,
    #     供 setup.py 的 add_conditional_edges 注册为路由函数, 由图在运行时调用。
    # 【参数】
    #     max_debate_rounds: 多空辩论最大往返轮数 (默认 1);
    #     max_risk_discuss_rounds: 风险辩论最大往返轮数 (默认 1)。
    def __init__(self, max_debate_rounds=1, max_risk_discuss_rounds=1):
        """Initialize with configuration parameters."""
        self.max_debate_rounds = max_debate_rounds  # 【变量】多空辩论轮数上限 (决定研究经理何时收尾)
        self.max_risk_discuss_rounds = max_risk_discuss_rounds  # 【变量】风险辩论轮数上限 (决定组合经理何时拍板)

    # 【功能】市场分析师的条件路由: 看最后一条消息是否请求了工具调用。
    # 【参数】state: 当前图共享状态 (读 messages)。
    # 【返回】"tools_market" (需要调工具) 或 "Msg Clear Market" (分析完成, 进入清空/下一步)。
    def should_continue_market(self, state: AgentState):
        """Determine if market analysis should continue."""
        messages = state["messages"]
        last_message = messages[-1]
        if last_message.tool_calls:
            return "tools_market"
        return "Msg Clear Market"

    # 【功能】情绪分析师的条件路由 (与市场分析师同构, 但节点名对应情绪路径)。
    # 【参数】state: 当前图共享状态。
    # 【返回】"tools_social" 或 "Msg Clear Sentiment"。
    # 【关键】方法名保留旧后缀 social 以兼容已保存配置; 返回的清空节点标签用 v0.2.5
    #     改名后的 "Msg Clear Sentiment", 与执行计划注册的节点名一致。
    def should_continue_social(self, state: AgentState):
        """Determine if sentiment-analyst tool round should continue.

        Method name keeps the legacy ``social`` suffix to match the
        ``AnalystType.SOCIAL = "social"`` wire value (saved-config
        back-compat); the returned ``clear_node`` label uses the v0.2.5
        rename so it matches the node registered by the execution plan.
        """
        messages = state["messages"]
        last_message = messages[-1]
        if last_message.tool_calls:
            return "tools_social"
        return "Msg Clear Sentiment"

    # 【功能】新闻分析师的条件路由 (与市场分析师同构, 但节点名对应新闻路径)。
    # 【参数】state: 当前图共享状态。
    # 【返回】"tools_news" 或 "Msg Clear News"。
    def should_continue_news(self, state: AgentState):
        """Determine if news analysis should continue."""
        messages = state["messages"]
        last_message = messages[-1]
        if last_message.tool_calls:
            return "tools_news"
        return "Msg Clear News"

    # 【功能】基本面分析师的条件路由 (与市场分析师同构, 但节点名对应基本面路径)。
    # 【参数】state: 当前图共享状态。
    # 【返回】"tools_fundamentals" 或 "Msg Clear Fundamentals"。
    def should_continue_fundamentals(self, state: AgentState):
        """Determine if fundamentals analysis should continue."""
        messages = state["messages"]
        last_message = messages[-1]
        if last_message.tool_calls:
            return "tools_fundamentals"
        return "Msg Clear Fundamentals"

    # 【功能】多空辩论环节的条件路由: 决定下一位发言者, 或轮到研究经理收尾。
    # 【参数】state: 当前图共享状态 (读 investment_debate_state)。
    # 【返回】"Research Manager" (辩论轮数打满) / "Bear Researcher" / "Bull Researcher"。
    # 【关键】双方 (Bull/Bear) 各发言 max_debate_rounds 轮, 故用 count >= 2 * 轮数;
    #     未打满时依据最后发言方 (current_response 前缀) 决定下一位是对方。
    def should_continue_debate(self, state: AgentState) -> str:
        """Determine if debate should continue."""

        if (
            state["investment_debate_state"]["count"] >= 2 * self.max_debate_rounds
        ):  # 3 rounds of back-and-forth between 2 agents
            return "Research Manager"
        if state["investment_debate_state"]["current_response"].startswith("Bull"):
            return "Bear Researcher"
        return "Bull Researcher"

    # 【功能】风险辩论环节的条件路由: 决定下一位风控分析师, 或轮到组合经理拍板。
    # 【参数】state: 当前图共享状态 (读 risk_debate_state)。
    # 【返回】"Portfolio Manager" (轮数打满) / 下一位分析师节点名。
    # 【关键】三位风控分析师按 激进→保守→中性 循环; 各发言 max_risk_discuss_rounds 轮,
    #     故用 count >= 3 * 轮数判定是否收尾。
    def should_continue_risk_analysis(self, state: AgentState) -> str:
        """Determine if risk analysis should continue."""
        if (
            state["risk_debate_state"]["count"] >= 3 * self.max_risk_discuss_rounds
        ):  # 3 rounds of back-and-forth between 3 agents
            return "Portfolio Manager"
        if state["risk_debate_state"]["latest_speaker"].startswith("Aggressive"):
            return "Conservative Analyst"
        if state["risk_debate_state"]["latest_speaker"].startswith("Conservative"):
            return "Neutral Analyst"
        return "Aggressive Analyst"
