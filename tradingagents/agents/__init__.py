"""Agent 层包:统一导出全部 Agent 节点工厂与状态类型(仅 re-export)。

【导出内容】
- 分析师: create_fundamentals_analyst / create_market_analyst / create_news_analyst /
  create_sentiment_analyst(含废弃别名 create_social_media_analyst,仅向后兼容)
- 研究方: create_bull_researcher / create_bear_researcher / create_research_manager
- 风险辩论: create_aggressive_debator / create_conservative_debator / create_neutral_debator
- 组合与交易: create_portfolio_manager / create_trader
- 状态类型: AgentState / InvestDebateState / RiskDebateState
- 工具: create_msg_delete

【说明】本文件不包含任何业务逻辑,只做统一 re-export,便于上层 (commodity_demo.py 等) 一处导入。
"""

from .analysts.fundamentals_analyst import create_fundamentals_analyst
from .analysts.market_analyst import create_market_analyst
from .analysts.news_analyst import create_news_analyst
from .analysts.sentiment_analyst import (
    create_sentiment_analyst,
    create_social_media_analyst,  # deprecated alias kept for back-compat
)
from .managers.portfolio_manager import create_portfolio_manager
from .managers.research_manager import create_research_manager
from .researchers.bear_researcher import create_bear_researcher
from .researchers.bull_researcher import create_bull_researcher
from .risk_mgmt.aggressive_debator import create_aggressive_debator
from .risk_mgmt.conservative_debator import create_conservative_debator
from .risk_mgmt.neutral_debator import create_neutral_debator
from .trader.trader import create_trader
from .utils.agent_states import AgentState, InvestDebateState, RiskDebateState
from .utils.agent_utils import create_msg_delete

__all__ = [
    "AgentState",
    "create_msg_delete",
    "InvestDebateState",
    "RiskDebateState",
    "create_bear_researcher",
    "create_bull_researcher",
    "create_research_manager",
    "create_fundamentals_analyst",
    "create_market_analyst",
    "create_neutral_debator",
    "create_news_analyst",
    "create_aggressive_debator",
    "create_portfolio_manager",
    "create_conservative_debator",
    "create_sentiment_analyst",
    "create_social_media_analyst",  # deprecated; will be removed in a future version
    "create_trader",
]
