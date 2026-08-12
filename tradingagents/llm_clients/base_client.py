import warnings  # 【调用包】发出模型未知的 RuntimeWarning
from abc import ABC, abstractmethod  # 【调用包】定义抽象基类 BaseLLMClient
from typing import Any  # 【调用包】类型标注


# 【功能】把 LLM 响应内容归一化为纯字符串。
# 【参数】response: 各家 SDK 的响应对象(其 .content 可能是 list)。
# 【返回】原地修改并返回同一个 response, 使 .content 变成字符串。
# 【关键】多个厂商(OpenAI Responses API、Google Gemini 3)把内容返回为分块列表
#         [{'type':'reasoning'...},{'type':'text','text':'...'}]; 下游智能体
#         期望 .content 是字符串, 故只拼接 text 块、丢弃 reasoning/元数据块。
def normalize_content(response):
    """Normalize LLM response content to a plain string.

    Multiple providers (OpenAI Responses API, Google Gemini 3) return content
    as a list of typed blocks, e.g. [{'type': 'reasoning', ...}, {'type': 'text', 'text': '...'}].
    Downstream agents expect response.content to be a string. This extracts
    and joins the text blocks, discarding reasoning/metadata blocks.
    """
    content = response.content  # 【变量】原始响应内容(list of typed blocks 或字符串)
    if isinstance(content, list):
        texts = [
            item.get("text", "")
            if isinstance(item, dict) and item.get("type") == "text"
            else item
            if isinstance(item, str)
            else ""
            for item in content
        ]  # 【变量】从分块中抽取所有 text 文本(其余块视为空)
        response.content = "\n".join(t for t in texts if t)  # 【调用函数】拼接成单个字符串写回响应
    return response


# 【功能】所有 LLM 客户端的抽象基类, 定义统一接口(get_llm / validate_model)。
# 【关键】子类只需实现 get_llm(构造 SDK 实例)与 validate_model(模型名校验);
#         基类提供警告辅助, 并持有 model / base_url / kwargs 三个公共属性。
class BaseLLMClient(ABC):
    """Abstract base class for LLM clients."""

    def __init__(self, model: str, base_url: str | None = None, **kwargs):
        self.model = model  # 【变量】模型名/标识
        self.base_url = base_url  # 【变量】可选的服务端点地址
        self.kwargs = kwargs  # 【变量】厂商专属透传参数(温度、重试、超时等)

    def get_provider_name(self) -> str:
        """Return the provider name used in warning messages."""
        provider = getattr(self, "provider", None)  # 【变量】子类可能设置的厂商名(如 OpenAIClient.provider)
        if provider:
            return str(provider)
        # 【变量】缺省回退: 由类名去掉 "Client" 后缀得到厂商名, 如 AnthropicClient -> "anthropic"
        return self.__class__.__name__.removesuffix("Client").lower()

    def warn_if_unknown_model(self) -> None:
        """Warn when the model is outside the known list for the provider."""
        if self.validate_model():
            return

        warnings.warn(  # 【调用函数】发出 RuntimeWarning, 提示模型不在已知列表但继续运行
            (
                f"Model '{self.model}' is not in the known model list for "
                f"provider '{self.get_provider_name()}'. Continuing anyway."
            ),
            RuntimeWarning,
            stacklevel=2,
        )

    @abstractmethod
    def get_llm(self) -> Any:
        """Return the configured LLM instance."""
        pass

    @abstractmethod
    def validate_model(self) -> bool:
        """Validate that the model is supported by this client."""
        pass
