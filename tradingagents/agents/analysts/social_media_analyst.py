"""Backwards-compatibility shim for the renamed module.

The agent is now ``sentiment_analyst`` and aggregates Yahoo Finance news,
StockTwits cashtag streams, and Reddit posts into a single sentiment
report. Import from ``tradingagents.agents.analysts.sentiment_analyst``
going forward; this module will be removed in a future release.

See: https://github.com/TauricResearch/TradingAgents/issues/557
"""

import warnings as _warnings  # 【调用包】标准库警告;模块导入时立即发出弃用警告

from tradingagents.agents.analysts.sentiment_analyst import (  # noqa: F401  # 【调用包】旧路径别名:从 sentiment_analyst 重新导出两个工厂函数,保持旧 import 链可用
    create_sentiment_analyst,
    create_social_media_analyst,
)

_warnings.warn(  # 【调用函数】模块导入即警告用户改用新模块路径(本文件仅作向后兼容垫片)
    "tradingagents.agents.analysts.social_media_analyst is deprecated. "
    "Import from tradingagents.agents.analysts.sentiment_analyst instead.",
    DeprecationWarning,
    stacklevel=2,
)
