# TradingAgents/graph/reflection.py

from typing import Any  # 【调用包】类型标注支持


class Reflector:
    """Handles reflection on trading decisions."""

    # 【功能】对"已见实际结果"的交易决策做事后反思 (Phase B 延迟反思)。
    # 【参数】quick_thinking_llm: 用于生成反思文本的 LLM 对象。
    def __init__(self, quick_thinking_llm: Any):
        """Initialize the reflector with an LLM."""
        self.quick_thinking_llm = quick_thinking_llm  # 【变量】生成反思文本所用的 LLM
        self.log_reflection_prompt = self._get_log_reflection_prompt()  # 【变量】反思提示词 (固定, 构造时一次性生成)

    # 【功能】构造反思提示词: 要求 LLM 用 2-4 句纯文本, 依次覆盖方向判断对错
    #     (引用 alpha 数字)、论点哪部分成立/失效、一条对下次分析的教训。
    # 【返回】提示词字符串。
    # 【关键】刻意要求"紧凑纯文本", 让反思结果能回注到未来 agent 的提示中而不撑爆上下文窗口。
    def _get_log_reflection_prompt(self) -> str:
        """Concise prompt for reflect_on_final_decision (Phase B log entries).

        Produces 2-4 sentences of plain prose — compact enough to be re-injected
        into future agent prompts without bloating the context window.
        """
        return (
            "You are a trading analyst reviewing your own past decision now that the outcome is known.\n"
            "Write exactly 2-4 sentences of plain prose (no bullets, no headers, no markdown).\n\n"
            "Cover in order:\n"
            "1. Was the directional call correct? (cite the alpha figure)\n"
            "2. Which part of the investment thesis held or failed?\n"
            "3. One concrete lesson to apply to the next similar analysis.\n\n"
            "Be specific and terse. Your output will be stored verbatim in a decision log "
            "and re-read by future analysts, so every word must earn its place."
        )

    # 【功能】基于已知的实际收益/超额收益, 对一次最终交易决策做单次反思调用。
    # 【参数】
    #     final_decision: 最终交易决策文本;
    #     raw_return: 原始收益率; alpha_return: 相对基准的超额收益率;
    #     benchmark_name: 基准标签 (默认 "SPY"; 美股默认, ".T" 为 "^N225")。
    # 【返回】LLM 输出的反思文本 (content)。
    # 【关键】final_trade_decision 已综合所有分析师观点, 故无需再拼接市场上下文。
    def reflect_on_final_decision(
        self,
        final_decision: str,
        raw_return: float,
        alpha_return: float,
        benchmark_name: str = "SPY",
    ) -> str:
        """Single reflection call on the final trade decision with outcome context.

        Used by Phase B deferred reflection. The final_trade_decision already
        synthesises all analyst insights, so no separate market context is needed.
        ``benchmark_name`` is the label used for the alpha line (e.g. ``"SPY"``
        for US tickers, ``"^N225"`` for ``.T`` listings); defaults to SPY for
        callers that haven't been updated to thread the benchmark through.
        """
        messages = [
            ("system", self.log_reflection_prompt),
            (
                "human",
                (
                    f"Raw return: {raw_return:+.1%}\n"
                    f"Alpha vs {benchmark_name}: {alpha_return:+.1%}\n\n"
                    f"Final Decision:\n{final_decision}"
                ),
            ),
        ]
        return self.quick_thinking_llm.invoke(messages).content  # 【调用函数】跨模块 LLM 调用 (生成反思文本)
