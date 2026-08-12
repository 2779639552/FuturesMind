# TradingAgents/graph/propagation.py

from typing import Any  # 【调用包】类型标注支持

from tradingagents.agents.utils.agent_states import (  # 【调用包】图状态中的两个辩论子状态类型
    InvestDebateState,
    RiskDebateState,
)


class Propagator:
    """Handles state initialization and propagation through the graph."""

    # 【功能】负责"图运行前的初始状态组装"与"图运行参数生成"两件事。
    # 【参数】max_recur_limit: 图的最大递归层数上限 (默认 100), 传给 LangGraph 防止图死循环。
    def __init__(self, max_recur_limit=100):
        """Initialize with configuration parameters."""
        self.max_recur_limit = max_recur_limit  # 【变量】recursion_limit 上限 (防死循环)

    # 【功能】创建整张图运行的初始状态字典。
    # 【参数】
    #     company_name: 股票代码; trade_date: 分析日期;
    #     asset_type: 资产类型 ("stock" 默认 / "crypto");
    #     past_context: 记忆日志给出的历史决策上下文 (供组合经理等参考);
    #     instrument_context: 确定性解析的公司身份文本; 为空时 agent 回落为
    #         仅用 ticker 的上下文 (见 get_instrument_context_from_state)。
    # 【返回】初始状态字典: 含 (human, company_name) 首条消息、空的辩论状态、
    #     各报告字段初始为空字符串。
    # 【关键】InvestDebateState / RiskDebateState 为带默认值的 TypedDict,
    #     这里显式给出全零初值, 保证图运行中各节点读到的字段一定存在。
    def create_initial_state(
        self,
        company_name: str,
        trade_date: str,
        asset_type: str = "stock",
        past_context: str = "",
        instrument_context: str = "",
    ) -> dict[str, Any]:
        """Create the initial state for the agent graph.

        ``instrument_context`` is the deterministic ticker-identity string
        resolved once at run start (see
        ``TradingAgentsGraph.resolve_instrument_context``). When empty, agents
        fall back to ticker-only context via
        ``get_instrument_context_from_state``.
        """
        return {
            "messages": [("human", company_name)],  # 【变量】图收到的首条用户消息
            "company_of_interest": company_name,
            "asset_type": asset_type,
            "instrument_context": instrument_context,
            "trade_date": str(trade_date),
            "past_context": past_context,
            "investment_debate_state": InvestDebateState(  # 【变量】多空辩论子状态 (Bull/Bear 历史 + 轮数)
                {
                    "bull_history": "",
                    "bear_history": "",
                    "history": "",
                    "current_response": "",
                    "judge_decision": "",
                    "count": 0,
                }
            ),
            "risk_debate_state": RiskDebateState(  # 【变量】风险辩论子状态 (激进/保守/中性历史 + 轮数)
                {
                    "aggressive_history": "",
                    "conservative_history": "",
                    "neutral_history": "",
                    "history": "",
                    "latest_speaker": "",
                    "current_aggressive_response": "",
                    "current_conservative_response": "",
                    "current_neutral_response": "",
                    "judge_decision": "",
                    "count": 0,
                }
            ),
            "market_report": "",  # 【变量】各分析师报告字段, 初始为空字符串, 节点运行后填充
            "fundamentals_report": "",
            "sentiment_report": "",
            "news_report": "",
        }

    # 【功能】生成调用 graph.invoke()/stream() 所需的运行参数。
    # 【参数】callbacks: 可选回调列表, 用于统计工具执行; LLM 回调由 LLM 构造器单独处理。
    # 【返回】{"stream_mode": "values", "config": {"recursion_limit": ...}}, 其中
    #     stream_mode="values" 让 stream() 每个节点后返回完整状态而非增量。
    def get_graph_args(self, callbacks: list | None = None) -> dict[str, Any]:
        """Get arguments for the graph invocation.

        Args:
            callbacks: Optional list of callback handlers for tool execution tracking.
                       Note: LLM callbacks are handled separately via LLM constructor.
        """
        config = {"recursion_limit": self.max_recur_limit}
        if callbacks:
            config["callbacks"] = callbacks
        return {
            "stream_mode": "values",
            "config": config,
        }
