import os  # 【调用包】读取 Azure 部署相关环境变量
from typing import Any  # 【调用包】类型标注

from langchain_openai import AzureChatOpenAI  # 【调用包】Azure OpenAI 的 LangChain 封装基类

from .base_client import BaseLLMClient, normalize_content  # 【调用包】统一客户端基类 + 内容归一化工具

# 【变量】允许从用户配置透传给 AzureChatOpenAI 的白名单参数名
_PASSTHROUGH_KWARGS = (
    "timeout",
    "max_retries",
    "api_key",
    "reasoning_effort",
    "temperature",
    "callbacks",
    "http_client",
    "http_async_client",
)


# 【功能】Azure OpenAI 客户端的默认子类: 归一化内容输出。
class NormalizedAzureChatOpenAI(AzureChatOpenAI):
    """AzureChatOpenAI with normalized content output."""

    def invoke(self, input, config=None, **kwargs):
        return normalize_content(super().invoke(input, config, **kwargs))  # 【调用函数】归一化父类 invoke 返回的内容


# 【功能】微软 Azure OpenAI 客户端, 工厂按 provider="azure" 时构造。
# 【关键】需环境变量: AZURE_OPENAI_API_KEY / AZURE_OPENAI_ENDPOINT /
#         AZURE_OPENAI_DEPLOYMENT_NAME / OPENAI_API_VERSION。
class AzureOpenAIClient(BaseLLMClient):
    """Client for Azure OpenAI deployments.

    Requires environment variables:
        AZURE_OPENAI_API_KEY: API key
        AZURE_OPENAI_ENDPOINT: Endpoint URL (e.g. https://<resource>.openai.azure.com/)
        AZURE_OPENAI_DEPLOYMENT_NAME: Deployment name
        OPENAI_API_VERSION: API version (e.g. 2025-03-01-preview)
    """

    def __init__(self, model: str, base_url: str | None = None, **kwargs):
        super().__init__(model, base_url, **kwargs)

    def get_llm(self) -> Any:
        """Return configured AzureChatOpenAI instance."""
        self.warn_if_unknown_model()  # 【调用函数】模型不在已知列表时发出警告

        llm_kwargs = {
            "model": self.model,
            "azure_deployment": os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME", self.model),  # 【调用函数】部署名缺省回退到模型名
        }

        for key in _PASSTHROUGH_KWARGS:
            if key in self.kwargs:
                llm_kwargs[key] = self.kwargs[key]

        return NormalizedAzureChatOpenAI(**llm_kwargs)  # 【调用函数】构造归一化的 Azure 客户端实例

    def validate_model(self) -> bool:
        """Azure accepts any deployed model name."""
        return True
