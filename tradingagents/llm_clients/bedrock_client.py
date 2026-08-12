import os  # 【调用包】读取 AWS_REGION / AWS_BEARER_TOKEN_BEDROCK 等环境变量
from typing import Any  # 【调用包】类型标注

from .base_client import BaseLLMClient, normalize_content  # 【调用包】统一客户端基类 + 内容归一化工具
from .validators import validate_model  # 【调用包】模型名校验

# Bedrock has no global default region; us-west-2 hosts the broadest model set.
# 【变量】Bedrock 无全局默认区域; us-west-2 托管的模型最全
_DEFAULT_REGION = "us-west-2"
# 【变量】延迟导入的 ChatBedrockConverse 子类缓存(首次导入后缓存, 避免重复 import)
_BEDROCK_CLASS = None


# 【功能】延迟导入 langchain-aws(可选 [bedrock] 依赖), 返回带归一化输出的
#         ChatBedrockConverse 子类。
# 【关键】按需导入使可选依赖(及 boto3)不会强加给包的其他部分; 首次调用后缓存。
def _bedrock_class():
    """Lazily import langchain-aws (the optional ``[bedrock]`` extra) and return a
    ChatBedrockConverse subclass with normalized content output.

    Imported on demand so the optional dependency (and boto3) isn't required by
    the rest of the package; cached after the first call.
    """
    global _BEDROCK_CLASS
    if _BEDROCK_CLASS is not None:
        return _BEDROCK_CLASS

    try:
        from langchain_aws import ChatBedrockConverse  # 【调用包】延迟导入 langchain-aws 的 Converse 客户端
    except ImportError as exc:
        raise ImportError(
            "AWS Bedrock support requires the optional 'langchain-aws' dependency. "
            'Install it with: pip install "tradingagents[bedrock]"'
        ) from exc

    class NormalizedChatBedrockConverse(ChatBedrockConverse):
        """ChatBedrockConverse with normalized (string) content output."""

        def invoke(self, input, config=None, **kwargs):
            return normalize_content(super().invoke(input, config, **kwargs))  # 【调用函数】归一化父类 invoke 返回的内容

    _BEDROCK_CLASS = NormalizedChatBedrockConverse
    return _BEDROCK_CLASS


# 【功能】AWS Bedrock 客户端(走 langchain-aws 的 Converse API), 工厂按
#         provider="bedrock" 时构造。
# 【关键】鉴权两种方式: ① Bedrock API Key(bearer token, 经 AWS_BEARER_TOKEN_BEDROCK,
#         无需 AWS 访问密钥); ② 标准 AWS 凭证链(环境变量 / ~/.aws/credentials /
#         IAM 角色, 可配 AWS_PROFILE)。两种方式都应设 AWS_REGION / AWS_DEFAULT_REGION
#         (token 本身不带区域)。模型名是 Bedrock model ID 或跨区域推理 profile ID。
class BedrockClient(BaseLLMClient):
    """Client for Amazon Bedrock via the Converse API (langchain-aws).

    Authentication is either a Bedrock API key (bearer token) via
    ``AWS_BEARER_TOKEN_BEDROCK`` — no AWS access keys required — or the standard
    AWS credential chain (env vars, ``~/.aws/credentials``, or an IAM role) with
    optional ``AWS_PROFILE``. Set ``AWS_REGION`` / ``AWS_DEFAULT_REGION`` either
    way (the token carries no region). The model name is a Bedrock model ID or
    cross-region inference profile ID, e.g. ``us.anthropic.claude-opus-4-8-v1:0``.
    """

    def get_llm(self) -> Any:
        """Return a configured ChatBedrockConverse instance."""
        self.warn_if_unknown_model()  # 【调用函数】模型不在已知列表时发出警告
        chat_cls = _bedrock_class()  # 【调用函数】取得(缓存后的)归一化 Converse 客户端类

        region = (
            os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or _DEFAULT_REGION
        )  # 【变量】区域解析: AWS_REGION > AWS_DEFAULT_REGION > 默认 us-west-2
        llm_kwargs = {"model": self.model, "region_name": region}
        # A Bedrock API key authenticates without AWS access keys. Passing it as
        # api_key makes langchain-aws prefer bearer auth, so an ambient
        # AWS_PROFILE / SigV4 credentials can't override it (#1103).
        bearer_token = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")  # 【调用函数】读 bearer token(若设置则走无 AK/SK 鉴权)
        if bearer_token:
            llm_kwargs["api_key"] = bearer_token
        for key in ("temperature", "max_tokens", "max_retries", "callbacks"):
            if key in self.kwargs:
                llm_kwargs[key] = self.kwargs[key]
        return chat_cls(**llm_kwargs)  # 【调用函数】构造 Converse 客户端实例

    def validate_model(self) -> bool:
        """Validate model for Bedrock (any model ID accepted)."""
        return validate_model("bedrock", self.model)  # 【调用函数】Bedrock 属于任意模型名接受类厂商
