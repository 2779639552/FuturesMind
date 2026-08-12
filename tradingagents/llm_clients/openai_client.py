import os  # 【调用包】读取环境变量(API Key、base_url 覆盖)
import re  # 【调用包】用正则识别"原生 OpenAI 推理模型"等模型名模式
from dataclasses import dataclass  # 【调用包】定义 ProviderSpec 数据类
from typing import Any  # 【调用包】类型标注
from urllib.parse import urlparse  # 【调用包】解析 base_url 主机名, 判断是否原生 OpenAI 端点

from langchain_core.messages import AIMessage  # 【调用包】识别 AIMessage 以便回传 DeepSeek 推理内容
from langchain_openai import ChatOpenAI  # 【调用包】OpenAI 兼容客户端的 LangChain 封装基类

from .api_key_env import get_api_key_env  # 【调用包】查厂商 API Key 对应的环境变量名
from .base_client import BaseLLMClient, normalize_content  # 【调用包】统一客户端基类 + 内容归一化工具
from .capabilities import get_capabilities  # 【调用包】按模型名查能力表(结构化输出/工具选择支持)
from .validators import validate_model  # 【调用包】模型名校验


# 【功能】OpenAI 兼容客户端的默认子类: 把 Responses API 返回的分块内容(list of
#         typed blocks)统一归一化为字符串, 并按模型能力表选择结构化输出方式。
# 【关键】with_structured_output 会查能力表: 若模型不支持 tool_choice(如
#         DeepSeek V4/reasoner), 则仍绑定 schema 为工具, 但抑制 tool_choice 参数。
class NormalizedChatOpenAI(ChatOpenAI):
    """ChatOpenAI with normalized content output and capability-aware binding.

    The Responses API returns content as a list of typed blocks
    (reasoning, text, etc.). ``invoke`` normalizes to string for
    consistent downstream handling.

    ``with_structured_output`` consults the per-model capability table
    (``capabilities.get_capabilities``) to pick the method and to decide
    whether ``tool_choice`` may be sent. Models that reject ``tool_choice``
    (e.g. DeepSeek V4 and reasoner — per their official tool-calling
    guide) still bind the schema as a tool, but no ``tool_choice``
    parameter is sent.

    Provider-specific quirks beyond structured-output (e.g. DeepSeek's
    reasoning_content roundtrip) live in subclasses so this base class
    stays small.
    """

    def invoke(self, input, config=None, **kwargs):
        return normalize_content(super().invoke(input, config, **kwargs))  # 【调用函数】归一化父类 invoke 返回的分块内容

    def with_structured_output(self, schema, *, method=None, **kwargs):
        caps = get_capabilities(self.model_name)  # 【调用函数】查该模型的结构化输出能力表
        if caps.preferred_structured_method == "none":
            raise NotImplementedError(
                f"{self.model_name} has no structured-output method available; "
                f"agent factories will fall back to free-text generation."
            )
        method = method or caps.preferred_structured_method  # 【变量】最终采用的结构化输出方式(调用方未指定时取能力表推荐值)
        # When the model rejects tool_choice, suppress langchain's hardcoded
        # value. The schema is still bound as a tool — exactly what
        # DeepSeek's official tool-calling examples do.
        if method == "function_calling" and not caps.supports_tool_choice:
            kwargs.setdefault("tool_choice", None)  # 【调用函数】模型拒绝 tool_choice 时, 抑制 langchain 硬编码值
        return super().with_structured_output(schema, method=method, **kwargs)  # 【调用函数】调用父类完成 schema 绑定与结构化输出


# 【功能】针对任意本地 OpenAI 兼容服务(LM Studio / vLLM / llama.cpp)的客户端子类。
# 【关键】本地服务工具调用支持参差不齐, 多数拒绝 langchain 发送的对象形式
#         tool_choice; 因此无论如何都抑制 tool_choice, 只绑定 schema 为工具。
class LocalCompatibleChatOpenAI(NormalizedChatOpenAI):
    """OpenAI-compatible client for arbitrary local servers (LM Studio, vLLM,
    llama.cpp via the generic ``openai_compatible`` provider).

    Their tool-calling support varies, and many reject the object-form
    ``tool_choice`` langchain sends for function-calling structured output. Bind
    the schema as a tool but don't force tool_choice, so structured output works
    across local servers regardless of the model ID's capabilities (#1057).
    """

    def with_structured_output(self, schema, *, method=None, **kwargs):
        resolved = method or get_capabilities(self.model_name).preferred_structured_method  # 【调用函数】查能力表得到推荐结构化方式
        if resolved == "function_calling":
            kwargs.setdefault("tool_choice", None)  # 【调用函数】本地服务不强制 tool_choice, 兼容性优先
        return super().with_structured_output(schema, method=method, **kwargs)  # 【调用函数】复用父类 schema 绑定逻辑


# 【功能】把 langchain LLM 的输入统一归一化为消息对象列表。
# 【参数】input_: 消息列表 / ChatPromptValue(ChatPromptTemplate 产物) / 其他。
# 【返回】list of message 对象; 无法识别时返回空列表。
# 【关键】只把 list 当消息会漏掉一半调用点(走 ChatPromptTemplate 的路径), 故用
#         to_messages() 兼容 prompt 对象。
def _input_to_messages(input_: Any) -> list:
    """Normalise a langchain LLM input to a list of message objects.

    Accepts a list of messages, a ``ChatPromptValue`` (from a
    ChatPromptTemplate), or anything else (treated as no messages).
    Used by providers that need to walk the outgoing message history;
    in particular DeepSeek thinking-mode propagation must work for
    both bare-list invocations and ChatPromptTemplate-driven ones, so
    treating only ``list`` here would silently skip half the call sites.
    """
    if isinstance(input_, list):
        return input_
    if hasattr(input_, "to_messages"):
        return input_.to_messages()  # 【调用函数】ChatPromptValue 等 prompt 对象转换为消息列表
    return []


# 【功能】DeepSeek 专属客户端子类: 处理推理模式(thinking mode)的
#         reasoning_content 往返(round-trip)。
# 【关键】DeepSeek 思维模型返回 reasoning_content 字段, 若下一轮不把它原样回传,
#         请求会报 HTTP 400。_create_chat_result 负责接收捕获, _get_request_payload
#         负责发送前重新挂上。V4/reasoner 的 tool_choice 抑制在能力表逻辑处理, 不在此处。
class DeepSeekChatOpenAI(NormalizedChatOpenAI):
    """DeepSeek-specific overrides on top of the OpenAI-compatible client.

    Thinking-mode round-trip is the only DeepSeek-specific behavior that
    stays here. When DeepSeek's thinking models return a response with
    ``reasoning_content``, that field must be echoed back as part of the
    assistant message on the next turn or the API fails with HTTP 400.
    ``_create_chat_result`` captures it on receive and
    ``_get_request_payload`` re-attaches it on send.

    Tool-choice handling for V4 and reasoner — those models reject the
    ``tool_choice`` parameter — is handled by the capability dispatch in
    ``NormalizedChatOpenAI.with_structured_output``, not here.
    """

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)  # 【调用函数】获取父类构造的请求体
        outgoing = payload.get("messages", [])  # 【变量】待发出的消息数组
        for message_dict, message in zip(outgoing, _input_to_messages(input_), strict=False):
            if not isinstance(message, AIMessage):
                continue
            reasoning = message.additional_kwargs.get("reasoning_content")  # 【变量】上一轮收到的推理内容
            if reasoning is not None:
                message_dict["reasoning_content"] = reasoning  # 【调用函数】把 reasoning_content 回传, 避免 HTTP 400
        return payload

    def _create_chat_result(self, response, generation_info=None):
        chat_result = super()._create_chat_result(response, generation_info)  # 【调用函数】调用父类生成 ChatResult
        response_dict = (
            response
            if isinstance(response, dict)
            else response.model_dump(exclude={"choices": {"__all__": {"message": {"parsed"}}}})
        )
        for generation, choice in zip(
            chat_result.generations, response_dict.get("choices", []), strict=False
        ):
            reasoning = choice.get("message", {}).get("reasoning_content")  # 【变量】响应中的推理内容
            if reasoning is not None:
                generation.message.additional_kwargs["reasoning_content"] = reasoning  # 【调用函数】捕获并存回消息附加字段
        return chat_result


# 【功能】MiniMax 专属客户端子类: 为 M2.x 推理模型附加 reasoning_split 参数。
# 【关键】M2.x 默认把 <think>...</think> 块塞进 message.content, 污染报告;
#         reasoning_split=True 把它转到 reasoning_details。该参数须经 extra_body
#         传递(顶层参数会被 openai SDK 校验拒绝, #826), 且仅当能力表标记
#         requires_reasoning_split 时才发送。
class MinimaxChatOpenAI(NormalizedChatOpenAI):
    """MiniMax-specific overrides on top of the OpenAI-compatible client.

    M2.x reasoning models embed ``<think>...</think>`` blocks directly in
    ``message.content`` by default, which would pollute saved reports.
    Per platform.minimax.io/docs/api-reference/text-openai-api,
    ``reasoning_split=True`` redirects the thinking block into
    ``reasoning_details`` so ``content`` stays clean. It is sent via
    ``extra_body`` (not a top-level kwarg) because the openai SDK validates
    top-level params and rejects unknown ones like reasoning_split (#826).

    The flag is gated by ``ModelCapabilities.requires_reasoning_split`` so
    only M2.x reasoning models receive it; non-reasoning MiniMax endpoints
    (Coding Plan, MiniMax-Text-01) never see it.

    Tool-choice handling for M2.x — those models accept only the string
    enum ``{"none", "auto"}`` and reject langchain's function-spec dict —
    is handled by the capability dispatch in
    ``NormalizedChatOpenAI.with_structured_output``, not here.
    """

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)  # 【调用函数】获取父类构造的请求体
        if get_capabilities(self.model_name).requires_reasoning_split:  # 【调用函数】查能力表是否需 reasoning_split
            # Pass via extra_body, not as a top-level kwarg: the openai SDK
            # (>=1.56) validates top-level params against Completions.create
            # and rejects unknown ones like reasoning_split (#826). extra_body
            # is forwarded into the request body untouched.
            extra_body = payload.setdefault("extra_body", {})  # 【变量】请求体的 extra_body 扩展字段容器
            extra_body.setdefault("reasoning_split", True)
        return payload


# Kwargs forwarded from user config to ChatOpenAI
# 【变量】允许从用户配置透传给 ChatOpenAI 的白名单参数名
_PASSTHROUGH_KWARGS = (
    "timeout",
    "max_retries",
    "reasoning_effort",
    "temperature",
    "api_key",
    "callbacks",
    "http_client",
    "http_async_client",
)

# OpenAI's ``reasoning_effort`` is only accepted by reasoning models — the GPT-5
# family and the o-series. Non-reasoning models (gpt-4.1, gpt-4o, ...) 400 with
# "Unsupported parameter: 'reasoning.effort' is not supported with this model".
# Drop the kwarg for those rather than crash the run.
_OPENAI_REASONING_MODEL = re.compile(r"^(gpt-5|o[1-9])")  # 【变量】匹配原生 OpenAI 推理模型家族(gpt-5 / o 系列)的正则


# 【功能】判断某个(原生 OpenAI)模型是否接受 reasoning_effort 参数。
# 【关键】非推理模型(gpt-4.1/gpt-4o 等)传该参数会报 400, 故按模型家族提前过滤。
def _supports_reasoning_effort(model: str) -> bool:
    """Whether the (native OpenAI) model accepts ``reasoning_effort``."""
    return bool(_OPENAI_REASONING_MODEL.match(model.lower().strip()))


# 【功能】声明式描述一个 OpenAI 兼容厂商的配置(单行注册表)。
# 【关键】OpenAI 兼容家族(OpenAI/xAI/DeepSeek/Qwen/GLM/MiniMax/OpenRouter/
#         Ollama 等)都讲同一套 Chat Completions 协议, 差异仅收敛到本类字段,
#         取代了历史上散落的 base_url 字典/鉴权处理/客户端分支。
#         原生 Anthropic/Google 走各自客户端, 故意不进入本注册表。
@dataclass(frozen=True)
class ProviderSpec:
    """Declarative config for one OpenAI-compatible provider.

    The OpenAI-compatible family (OpenAI, xAI, DeepSeek, Qwen, GLM, MiniMax,
    OpenRouter, Ollama, and any user endpoint) all speak the same Chat
    Completions API and differ only by these fields — so one row here replaces
    the former per-provider base-URL dict, auth handling, and client-class
    branches. Native Anthropic / Google use their own clients (genuinely
    different APIs) and are intentionally NOT in this registry.

    The API-key env var stays in ``api_key_env.PROVIDER_API_KEY_ENV`` (the single
    source consulted by both this client and the CLI prompt); only behavior that
    is provider-specific (base URL, key optionality, wire-format quirks via
    ``chat_class``) lives here.
    """

    chat_class: type = NormalizedChatOpenAI  # 【变量】provider quirks 所在的子类(如 DeepSeekChatOpenAI)
    base_url: str | None = None  # 【变量】默认端点(None -> 用 SDK 默认地址)
    base_url_env: str | None = None  # 【变量】覆盖 base_url 的环境变量名(如 OLLAMA_BASE_URL)
    key_optional: bool = False  # 【变量】是否必须 API Key; 为 False 时未配置则报错
    placeholder_key: str = "EMPTY"  # 【变量】无 Key 时发送的占位值(无鉴权本地服务)
    require_base_url: bool = False  # 【变量】是否强制要求 base_url(通用端点), 否则报错
    use_responses_api: bool = False  # 【变量】是否走原生 OpenAI Responses API(/v1/responses)


# Single source of truth for the OpenAI-compatible provider family. Dual-region
# providers (qwen/glm/minimax) keep separate endpoints because international and
# China accounts cannot share credentials (#758).
# 【变量】OpenAI 兼容厂商注册表(单一事实来源)。双区域厂商(qwen/glm/minimax)
#         各自保留独立端点, 因国际版与中国版账号凭证不可通用(#758)。
OPENAI_COMPATIBLE_PROVIDERS: dict[str, ProviderSpec] = {
    "openai": ProviderSpec(use_responses_api=True),
    "xai": ProviderSpec(base_url="https://api.x.ai/v1"),
    "deepseek": ProviderSpec(base_url="https://api.deepseek.com", chat_class=DeepSeekChatOpenAI),
    "qwen": ProviderSpec(base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
    "qwen-cn": ProviderSpec(base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"),
    "glm": ProviderSpec(base_url="https://api.z.ai/api/paas/v4/"),
    "glm-cn": ProviderSpec(base_url="https://open.bigmodel.cn/api/paas/v4/"),
    "minimax": ProviderSpec(base_url="https://api.minimax.io/v1", chat_class=MinimaxChatOpenAI),
    "minimax-cn": ProviderSpec(
        base_url="https://api.minimaxi.com/v1", chat_class=MinimaxChatOpenAI
    ),
    "openrouter": ProviderSpec(base_url="https://openrouter.ai/api/v1"),
    "mistral": ProviderSpec(base_url="https://api.mistral.ai/v1"),
    "kimi": ProviderSpec(base_url="https://api.moonshot.ai/v1"),
    "groq": ProviderSpec(base_url="https://api.groq.com/openai/v1"),
    "nvidia": ProviderSpec(base_url="https://integrate.api.nvidia.com/v1"),
    "ollama": ProviderSpec(
        base_url="http://localhost:11434/v1",
        base_url_env="OLLAMA_BASE_URL",
        key_optional=True,
        placeholder_key="ollama",
    ),
    # Generic endpoint: user supplies base_url; key optional (keyless local).
    "openai_compatible": ProviderSpec(
        require_base_url=True, key_optional=True, chat_class=LocalCompatibleChatOpenAI
    ),
}


# 【功能】判断某个 provider 是否由 OpenAI 兼容注册表服务。
# 【返回】布尔值: 在 OPENAI_COMPATIBLE_PROVIDERS 中则为 True。
def is_openai_compatible(provider: str) -> bool:
    """Whether ``provider`` is served by the OpenAI-compatible registry."""
    return provider.lower() in OPENAI_COMPATIBLE_PROVIDERS


# 【功能】判断 base_url 未设置或指向 api.openai.com(即"原生 OpenAI")。
# 【关键】Responses API(/v1/responses)只在原生 OpenAI 上存在; 若用户在 openai
#         provider 上配了自定义 base_url(代理/网关/本地服务), 那里只讲 Chat
#         Completions, 即使厂商 spec 允许 Responses 也必须关闭(#1024)。
def _is_native_openai_base_url(base_url: str | None) -> bool:
    """True when ``base_url`` is unset or points at api.openai.com.

    The Responses API (/v1/responses) only exists on native OpenAI. A custom
    base_url on the ``openai`` provider (a proxy, gateway, or local server)
    speaks only Chat Completions, so the Responses API must stay off there even
    though the provider spec enables it (#1024).
    """
    if not base_url:
        return True
    if "://" not in base_url:
        base_url = "https://" + base_url
    host = urlparse(base_url).hostname or ""  # 【调用函数】解析主机名, 与官方域名比对
    return host == "api.openai.com" or host.endswith(".openai.com")


# 【功能】OpenAI 兼容族客户端(OpenAI/Ollama/OpenRouter/xAI 等)的统一入口。
# 【关键】原生 OpenAI 用 Responses API(/v1/responses, 支持全家族的 reasoning_effort
#         与 function tools); 第三方兼容厂商(xAI/OpenRouter/Ollama)用标准
#         Chat Completions。具体行为由 ProviderSpec 注册表驱动。
class OpenAIClient(BaseLLMClient):
    """Client for OpenAI, Ollama, OpenRouter, and xAI providers.

    For native OpenAI models, uses the Responses API (/v1/responses) which
    supports reasoning_effort with function tools across all model families
    (GPT-4.1, GPT-5). Third-party compatible providers (xAI, OpenRouter,
    Ollama) use standard Chat Completions.
    """

    def __init__(
        self,
        model: str,
        base_url: str | None = None,
        provider: str = "openai",
        **kwargs,
    ):
        super().__init__(model, base_url, **kwargs)
        self.provider = provider.lower()  # 【变量】记录所属厂商(小写), 供 get_llm 查注册表

    def get_llm(self) -> Any:
        """Return a configured ChatOpenAI instance, driven by the provider registry."""
        self.warn_if_unknown_model()  # 【调用函数】模型不在已知列表时发出警告(不阻断)
        llm_kwargs = {"model": self.model}  # 【变量】最终传给 ChatOpenAI 的构造参数
        spec = OPENAI_COMPATIBLE_PROVIDERS.get(self.provider)  # 【变量】查注册表取该厂商的 ProviderSpec
        chat_cls = NormalizedChatOpenAI

        if spec is not None:
            chat_cls = spec.chat_class

            # base_url precedence: explicit client base_url (carries the config /
            # TRADINGAGENTS_LLM_BACKEND_URL value) > provider env override (e.g.
            # OLLAMA_BASE_URL) > provider default. None means use the SDK default.
            env_base_url = os.environ.get(spec.base_url_env) if spec.base_url_env else None  # 【调用函数】读环境变量覆盖地址
            base_url = self.base_url or env_base_url or spec.base_url  # 【变量】base_url 优先级: 显式客户端地址 > 环境变量 > 厂商默认
            if spec.require_base_url and not base_url:
                raise ValueError(
                    f"Provider '{self.provider}' requires a base_url. Set it via "
                    "backend_url / TRADINGAGENTS_LLM_BACKEND_URL to your endpoint, "
                    "e.g. http://localhost:8000/v1 (vLLM) or http://localhost:1234/v1 "
                    "(LM Studio)."
                )
            if base_url:
                llm_kwargs["base_url"] = base_url

            # API key: required unless key_optional; keyless local servers get a
            # placeholder. The env-var name is the single source in api_key_env.
            api_key_env = get_api_key_env(self.provider)  # 【调用函数】查该厂商 API Key 的环境变量名
            api_key = os.environ.get(api_key_env) if api_key_env else None  # 【调用函数】从环境变量读取实际 Key
            if api_key:
                llm_kwargs["api_key"] = api_key
            elif spec.key_optional:
                llm_kwargs["api_key"] = spec.placeholder_key
            elif api_key_env:
                raise ValueError(
                    f"API key for provider '{self.provider}' is not set. "
                    f"Please set the {api_key_env} environment variable "
                    f"(e.g. add {api_key_env}=your_key to your .env file)."
                )

            # The Responses API only exists on native OpenAI; if the user points
            # the openai provider at a custom base_url (proxy/gateway/local), it
            # only speaks Chat Completions, so keep Responses off there (#1024).
            if spec.use_responses_api and _is_native_openai_base_url(base_url):
                llm_kwargs["use_responses_api"] = True  # 【变量】仅在原生 OpenAI 端点上开启 Responses API
        elif self.base_url:
            llm_kwargs["base_url"] = self.base_url

        # Forward user-provided kwargs
        for key in _PASSTHROUGH_KWARGS:
            if key not in self.kwargs:
                continue
            if key == "reasoning_effort" and not _supports_reasoning_effort(self.model):
                continue
            llm_kwargs[key] = self.kwargs[key]

        # The subclass (provider quirks) comes from the registry spec.
        return chat_cls(**llm_kwargs)  # 【调用函数】按注册表中的子类构造最终的 LLM 实例

    def validate_model(self) -> bool:
        """Validate model for the provider."""
        return validate_model(self.provider, self.model)  # 【调用函数】委托 validators 校验模型名是否在已知列表
