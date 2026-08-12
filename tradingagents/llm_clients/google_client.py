from typing import Any  # 【调用包】类型标注

from langchain_google_genai import ChatGoogleGenerativeAI  # 【调用包】Google Gemini 的 LangChain 封装基类

from .base_client import BaseLLMClient, normalize_content  # 【调用包】统一客户端基类 + 内容归一化工具
from .validators import validate_model  # 【调用包】模型名校验


# 【功能】Google Gemini 客户端的默认子类: 把 Gemini 3 的分块内容(list of typed
#         blocks)归一化为字符串。
class NormalizedChatGoogleGenerativeAI(ChatGoogleGenerativeAI):
    """ChatGoogleGenerativeAI with normalized content output.

    Gemini 3 models return content as list of typed blocks.
    This normalizes to string for consistent downstream handling.
    """

    def invoke(self, input, config=None, **kwargs):
        return normalize_content(super().invoke(input, config, **kwargs))  # 【调用函数】归一化父类 invoke 返回的分块内容


# 【功能】Google(Gemini) 客户端, 工厂按 provider="google" 时构造。
class GoogleClient(BaseLLMClient):
    """Client for Google Gemini models."""

    def __init__(self, model: str, base_url: str | None = None, **kwargs):
        super().__init__(model, base_url, **kwargs)

    def get_llm(self) -> Any:
        """Return configured ChatGoogleGenerativeAI instance."""
        self.warn_if_unknown_model()  # 【调用函数】模型不在已知列表时发出警告
        llm_kwargs = {"model": self.model}  # 【变量】最终传给 ChatGoogleGenerativeAI 的构造参数

        if self.base_url:
            llm_kwargs["base_url"] = self.base_url

        for key in (
            "timeout",
            "max_retries",
            "temperature",
            "callbacks",
            "http_client",
            "http_async_client",
        ):
            if key in self.kwargs:
                llm_kwargs[key] = self.kwargs[key]

        # Unified api_key maps to provider-specific google_api_key
        google_api_key = self.kwargs.get("api_key") or self.kwargs.get("google_api_key")  # 【变量】统一的 api_key 映射到 google_api_key
        if google_api_key:
            llm_kwargs["google_api_key"] = google_api_key

        # Gemini 3.x takes the string ``thinking_level`` (the integer
        # ``thinking_budget`` was for the now-retired 2.5 line). Pro accepts
        # low/high; Flash also accepts minimal/medium — so map an unsupported
        # "minimal" on Pro to the nearest level it does accept.
        thinking_level = self.kwargs.get("thinking_level")  # 【变量】用户配置的 thinking_level 字符串
        if thinking_level:
            if "pro" in self.model.lower() and thinking_level == "minimal":
                thinking_level = "low"  # 【调用函数】Pro 不支持 minimal, 就近映射为 low
            llm_kwargs["thinking_level"] = thinking_level

        return NormalizedChatGoogleGenerativeAI(**llm_kwargs)  # 【调用函数】构造归一化的 Gemini 客户端实例

    def validate_model(self) -> bool:
        """Validate model for Google."""
        return validate_model("google", self.model)  # 【调用函数】委托 validators 校验模型名
