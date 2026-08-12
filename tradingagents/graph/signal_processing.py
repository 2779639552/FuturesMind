"""Extract the 5-tier portfolio rating from the Portfolio Manager's decision.

The Portfolio Manager produces a typed ``PortfolioDecision`` via structured
output and renders it to markdown that always carries a ``**Rating**: X``
header (see :func:`tradingagents.agents.schemas.render_pm_decision`).  The
deterministic heuristic in :mod:`tradingagents.agents.utils.rating` is more
than sufficient to extract that rating; no extra LLM call is needed.

This module exists for backwards compatibility with callers that expect a
``SignalProcessor.process_signal(text)`` interface.
"""

from __future__ import annotations  # 【调用包】前向引用类型标注支持

from typing import Any  # 【调用包】类型标注支持

from tradingagents.agents.utils.rating import parse_rating  # 【调用包】确定性 5 档评级提取启发式


class SignalProcessor:
    """Read the 5-tier rating out of a Portfolio Manager decision."""

    # 【功能】从 Portfolio Manager 决策文本中读取 5 档评级 (为兼容旧接口保留本类)。
    # 【参数】quick_thinking_llm: 兼容旧接口而保留的 LLM 参数, 已不再使用。
    # 【关键】PM 采用结构化输出 (PortfolioDecision), 渲染后的 markdown 必带
    #     **Rating**: X 头, 因此用确定性启发式 parse_rating 即可, 无需第二次 LLM 调用。
    def __init__(self, quick_thinking_llm: Any = None):
        # The LLM argument is accepted for backwards compatibility but no
        # longer used: the PM's structured output guarantees the rating is
        # parseable from the rendered markdown without a second LLM call.
        self.quick_thinking_llm = quick_thinking_llm

    # 【功能】把完整决策信号文本解析为 5 档评级之一。
    # 【参数】full_signal: 完整决策文本 (含 Rating 标记)。
    # 【返回】"Buy"/"Overweight"/"Hold"/"Underweight"/"Sell" 之一。
    def process_signal(self, full_signal: str) -> str:
        """Return one of Buy / Overweight / Hold / Underweight / Sell."""
        return parse_rating(full_signal)  # 【调用函数】确定性评级解析 (非 LLM)
