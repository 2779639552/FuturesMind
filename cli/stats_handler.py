import threading  # 【调用包】线程锁,保证回调计数在并发节点下线程安全
from typing import Any  # 【调用包】类型注解

from langchain_core.callbacks import BaseCallbackHandler  # 【调用包】LangChain 回调基类,挂在 LLM 上触发
from langchain_core.messages import AIMessage  # 【调用包】AI 消息类型,从中读取 usage_metadata 用量
from langchain_core.outputs import LLMResult  # 【调用包】LLM 输出结果类型,解析 token 用量


# 【功能】LangChain 回调处理器:统计一次分析中的 LLM 调用次数、工具调用次数与 token 用量,
#        供 Rich Live 仪表板的 footer 实时展示。
# 【关键】所有计数都在线程锁保护下累加,避免 LangGraph 并发节点同时写入导致计数错乱。
class StatsCallbackHandler(BaseCallbackHandler):
    """Callback handler that tracks LLM calls, tool calls, and token usage."""

    # 【功能】初始化统计回调:清零各计数器,并创建线程锁。
    # 【参数】无
    # 【返回】无
    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()  # 【变量】线程锁,保护下面四个计数器
        self.llm_calls = 0  # 【变量】LLM 调用次数
        self.tool_calls = 0  # 【变量】工具调用次数
        self.tokens_in = 0  # 【变量】累计输入 token 数
        self.tokens_out = 0  # 【变量】累计输出 token 数

    # 【功能】LLM 开始调用时计数 +1(普通 LLM 路径)。
    # 【参数】serialized / prompts / kwargs:LangChain 回调标准参数,本实现不使用。
    # 【返回】无
    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        **kwargs: Any,
    ) -> None:
        """Increment LLM call counter when an LLM starts."""
        with self._lock:
            self.llm_calls += 1

    # 【功能】Chat 模型开始调用时计数 +1(对话模型路径,与 on_llm_start 等效)。
    # 【参数】serialized / messages / kwargs:LangChain 回调标准参数,本实现不使用。
    # 【返回】无
    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        **kwargs: Any,
    ) -> None:
        """Increment LLM call counter when a chat model starts."""
        with self._lock:
            self.llm_calls += 1

    # 【功能】LLM 返回后,从生成结果中解析 token 用量并累加。
    # 【参数】response:LLMResult,含 generations 列表与可能的 usage_metadata。
    # 【返回】无
    # 【关键】取不到 usage_metadata(旧版/结构异常)时静默跳过,不影响计数。
    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """Extract token usage from LLM response."""
        try:
            generation = response.generations[0][0]  # 【变量】首个生成结果(AIMessage 或文本)
        except (IndexError, TypeError):
            return

        usage_metadata = None  # 【变量】token 用量元数据(模型端可能不返回)
        if hasattr(generation, "message"):
            message = generation.message
            if isinstance(message, AIMessage) and hasattr(message, "usage_metadata"):
                usage_metadata = message.usage_metadata

        if usage_metadata:
            with self._lock:
                self.tokens_in += usage_metadata.get("input_tokens", 0)  # 【变量】缺省按 0 计
                self.tokens_out += usage_metadata.get("output_tokens", 0)  # 【变量】缺省按 0 计

    # 【功能】工具开始调用时计数 +1。
    # 【参数】serialized / input_str / kwargs:LangChain 回调标准参数,本实现不使用。
    # 【返回】无
    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        **kwargs: Any,
    ) -> None:
        """Increment tool call counter when a tool starts."""
        with self._lock:
            self.tool_calls += 1

    # 【功能】返回当前累计统计快照,供仪表板渲染使用。
    # 【参数】无
    # 【返回】dict:含 llm_calls / tool_calls / tokens_in / tokens_out 四个键。
    def get_stats(self) -> dict[str, Any]:
        """Return current statistics."""
        with self._lock:
            return {
                "llm_calls": self.llm_calls,
                "tool_calls": self.tool_calls,
                "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out,
            }
