import re  # 【调用包】用正则解析 Claude 模型家族/版本号, 判断是否支持 effort 参数
from typing import Any  # 【调用包】类型标注

from langchain_anthropic import ChatAnthropic  # 【调用包】Anthropic(Claude) 的 LangChain 封装基类

from .base_client import BaseLLMClient, normalize_content  # 【调用包】统一客户端基类 + 内容归一化工具
from .validators import validate_model  # 【调用包】模型名校验

# 【变量】允许从用户配置透传给 ChatAnthropic 的白名单参数名
_PASSTHROUGH_KWARGS = (
    "timeout",
    "max_retries",
    "api_key",
    "max_tokens",
    "temperature",
    "callbacks",
    "http_client",
    "http_async_client",
    "effort",
)

# Anthropic's extended-thinking ``effort`` parameter is accepted by Opus 4.5+,
# Sonnet 4.6+, and the Claude 5 family (Sonnet 5, Fable 5). Sonnet 4.5 and any
# Haiku version 400 with ``"This model does not support the effort parameter"``
# (#831). Versions may be dotted (``opus-4-8``) or single-number (``sonnet-5``,
# ``fable-5``); the per-family minimum below is forward-compatible.
# 【变量】不需要版本规则、直接支持 effort 的非常规模型名白名单
_EFFORT_EXACT = {
    "claude-mythos-preview",  # non-standard preview name; effort-capable
    "claude-mythos-5",  # Fable 5 twin (Project Glasswing); effort-capable
}
_EFFORT_MODEL = re.compile(r"^claude-(opus|sonnet|fable)-(\d+)(?:-(\d+))?$")  # 【变量】解析家族与版本号的正则
# 【变量】各家族支持 effort 的最低版本线(opus>=4.5 / sonnet>=4.6 / fable>=5.0)
_EFFORT_MIN_VERSION = {"opus": (4, 5), "sonnet": (4, 6), "fable": (5, 0)}


# 【功能】判断某 Anthropic 模型是否接受 extended-thinking 的 effort 参数。
# 【关键】opus 4.5+ / sonnet 4.6+ / Claude 5 家族(Sonnet 5, Fable 5)接受;
#         sonnet 4.5 与所有 Haiku 版本会 400("This model does not support the
#         effort parameter", #831)。版本可点分(opus-4-8)或单数字(sonnet-5)。
def _supports_effort(model: str) -> bool:
    """Whether Anthropic accepts the ``effort`` parameter for this model."""
    model_lc = model.lower()
    if model_lc in _EFFORT_EXACT:
        return True
    match = _EFFORT_MODEL.match(model_lc)
    if not match:
        return False
    family = match.group(1)  # 【变量】模型家族名(opus/sonnet/fable)
    major = int(match.group(2))  # 【变量】主版本号
    minor = int(match.group(3)) if match.group(3) else 0  # 【变量】次版本号(无则视为 0)
    return (major, minor) >= _EFFORT_MIN_VERSION[family]


# 【功能】Anthropic 客户端的默认子类: 把分块内容(extended thinking / tool use
#         产生的 list of typed blocks)归一化为字符串。
class NormalizedChatAnthropic(ChatAnthropic):
    """ChatAnthropic with normalized content output.

    Claude models with extended thinking or tool use return content as a
    list of typed blocks. This normalizes to string for consistent
    downstream handling.
    """

    def invoke(self, input, config=None, **kwargs):
        return normalize_content(super().invoke(input, config, **kwargs))  # 【调用函数】归一化父类 invoke 返回的分块内容


# 【功能】Anthropic(Claude) 客户端, 工厂按 provider="anthropic" 时构造。
class AnthropicClient(BaseLLMClient):
    """Client for Anthropic Claude models."""

    def __init__(self, model: str, base_url: str | None = None, **kwargs):
        super().__init__(model, base_url, **kwargs)

    def get_llm(self) -> Any:
        """Return configured ChatAnthropic instance."""
        self.warn_if_unknown_model()  # 【调用函数】模型不在已知列表时发出警告
        llm_kwargs = {"model": self.model}  # 【变量】最终传给 ChatAnthropic 的构造参数

        if self.base_url:
            llm_kwargs["base_url"] = self.base_url

        for key in _PASSTHROUGH_KWARGS:
            if key not in self.kwargs:
                continue
            if key == "effort" and not _supports_effort(self.model):
                continue  # 【调用函数】模型不支持 effort 时丢弃该参数, 避免 400
            llm_kwargs[key] = self.kwargs[key]

        return NormalizedChatAnthropic(**llm_kwargs)  # 【调用函数】构造归一化的 Claude 客户端实例

    def validate_model(self) -> bool:
        """Validate model for Anthropic."""
        return validate_model("anthropic", self.model)  # 【调用函数】委托 validators 校验模型名
