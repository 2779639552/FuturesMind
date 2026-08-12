# =============================================================================
# 本文件在整个项目中的角色 —— LLM 多厂商适配工厂 (Factory)
# -----------------------------------------------------------------------------
# 项目里的所有智能体 (分析师/研究员/交易员等) 都需要调用大语言模型, 但底层
# 提供商可能随时更换: OpenAI、Anthropic(Claude)、Google(Gemini)、Azure、
# AWS Bedrock, 以及任何 OpenAI 兼容服务。如果每个智能体都直接 new 某个厂商
# 的客户端, 换厂商就得改一堆业务代码。
#
# 本文件的 create_llm_client() 就是解决这个问题的"工厂"——它像 4S 店前台:
#   你只要告诉它 provider(厂商)、model(模型名)、base_url、以及可选的厂商专属
#   参数, 它就返回一个"统一接口"的客户端对象 (BaseLLMClient 子类);
#   上层业务代码只调用 .get_llm() 拿到 LLM, 完全不关心底层是哪家 SDK。
#
# 因此: "换厂商不改业务代码" = 改配置里的 llm_provider, 让工厂分发给另一家
# 客户端; 业务代码 (graph/setup.py、trading_graph.py) 一行都不用动。
#
# 设计要点:
#   1) 各家客户端模块【延迟导入】(在分支内 import), 这样仅仅导入本工厂 (例如
#      测试收集阶段) 不会拉入重量级 SDK, 也不会因为缺 API Key 而失败;
#   2) 原生 (非 OpenAI 兼容) 的厂商 (Anthropic/Google/Azure/Bedrock) 先单独
#      匹配, 剩余的走 OpenAI 兼容判定再统一交给 OpenAIClient。
# =============================================================================

from .base_client import BaseLLMClient  # 【调用包】统一客户端基类(所有厂商客户端的公共父类)


def create_llm_client(
    provider: str,
    model: str,
    base_url: str | None = None,
    **kwargs,
) -> BaseLLMClient:
    """Create an LLM client for the specified provider.

    Provider modules are imported lazily so that simply importing this
    factory (e.g. during test collection) does not pull in heavy LLM SDKs
    or fail when their API keys are absent.

    Args:
        provider: LLM provider name
        model: Model name/identifier
        base_url: Optional base URL for API endpoint
        **kwargs: Additional provider-specific arguments

    Returns:
        Configured BaseLLMClient instance

    Raises:
        ValueError: If provider is not supported

    【中文说明】
    【功能】工厂入口: 根据 provider 字符串分发到对应厂商的客户端构造器, 返回
        一个统一接口的 BaseLLMClient 子类实例。
    【参数】
        provider: 厂商名, 大小写不敏感 (函数内会先 .lower() 归一化), 例如
            "openai" / "anthropic" / "google" / "azure" / "bedrock", 或任何
            OpenAI 兼容服务的名字;
        model: 模型名/标识, 例如 "gpt-4o"、"claude-3-5-sonnet"、"gemini-1.5-pro";
        base_url: 可选, API 服务地址。用于自建代理 / OpenAI 兼容服务;
        **kwargs: 各家厂商专属参数 (如 thinking_level、reasoning_effort、effort、
            temperature、max_retries 等), 由上层透传进来。
    【返回】配置好的 BaseLLMClient 实例 (上层调用 .get_llm() 拿到真正 LLM)。
    【异常】ValueError: 当 provider 不是任何受支持/可判定的厂商时抛出。
    【关键逻辑】分发顺序 (注意顺序是刻意的):
        1) 先逐个判断"原生厂商" (anthropic/google/azure/bedrock)——它们的字符串
           判定不能经过 OpenAI 兼容逻辑, 而且每个分支【延迟 import】对应客户端;
        2) 其余 provider 统一走 is_openai_compatible() 判定; 若是 OpenAI 兼容,
           全部交给 OpenAIClient (它内部按 provider 处理不同兼容服务);
        3) 都不满足 → 抛 ValueError 明确报错 "不支持的厂商"。
        这样新增一家厂商 = 在工厂里加一个分支 + 写一个 BaseLLMClient 子类,
        业务代码完全不动。
    """
    provider_lower = provider.lower()  # 【变量】归一化后的厂商名(小写), 供后续分支逐一比对

    # Native (non-OpenAI) APIs are matched first so their string check doesn't
    # import the OpenAI client. Everything else is OpenAI-compatible and routes
    # through the provider registry (single source of truth).
    # 【中文说明】原生 (非 OpenAI 兼容) 厂商先匹配, 目的是让它们的字符串判断不
    # 触发 OpenAI 客户端的导入 (延迟导入的副作用: 只要不 import 就不加载 SDK)。
    # 其余厂商都是 OpenAI 兼容的, 统一走下面 provider registry 判定。
    if provider_lower == "anthropic":
        from .anthropic_client import AnthropicClient  # 【调用包】延迟导入 Anthropic 原生客户端(仅选中 anthropic 才加载)

        # 【中文说明】Anthropic(Claude) 原生 SDK。只有真的选了 anthropic 才会
        # import anthropic_client, 避免测试/未用场景下引入重 SDK 或触发鉴权。
        return AnthropicClient(model, base_url, **kwargs)  # 【调用函数】构造 Anthropic 客户端(Claude)

    if provider_lower == "google":
        from .google_client import GoogleClient  # 【调用包】延迟导入 Google(Gemini) 原生客户端

        # 【中文说明】Google(Gemini) 原生 SDK 分支。
        return GoogleClient(model, base_url, **kwargs)  # 【调用函数】构造 Google Gemini 客户端

    if provider_lower == "azure":
        from .azure_client import AzureOpenAIClient  # 【调用包】延迟导入微软 Azure OpenAI 客户端

        # 【中文说明】微软 Azure OpenAI 分支 (虽属 OpenAI 兼容族, 但连接参数不同,
        # 有独立客户端)。
        return AzureOpenAIClient(model, base_url, **kwargs)  # 【调用函数】构造 Azure OpenAI 客户端(需 AZURE_* 环境变量)

    if provider_lower == "bedrock":
        from .bedrock_client import BedrockClient  # 【调用包】延迟导入 AWS Bedrock 客户端(含可选 langchain-aws 依赖)

        # 【中文说明】AWS Bedrock 分支。
        return BedrockClient(model, base_url, **kwargs)  # 【调用函数】构造 AWS Bedrock 客户端(Converse API)

    # 【中文说明】走到这里说明不是原生厂商。is_openai_compatible() 会用白名单/
    # 规则判断该 provider 是否走 OpenAI 兼容协议 (含 "openai" 本身及各种兼容
    # 中转服务); OpenAIClient 是"单一事实来源", 负责统一承载所有兼容厂商。
    from .openai_client import OpenAIClient, is_openai_compatible  # 【调用包】延迟导入 OpenAI 兼容客户端及兼容性判定函数

    if is_openai_compatible(provider_lower):  # 【调用函数】查 OpenAI 兼容厂商注册表, 判定是否走 Chat Completions 兼容协议
        # 【关键逻辑】把 provider_lower 一并传给 OpenAIClient, 让它在内部区分
        # 具体是哪家兼容服务 (不同兼容服务的 base_url / 鉴权方式可能不同)。
        return OpenAIClient(model, base_url, provider=provider_lower, **kwargs)  # 【调用函数】统一交给 OpenAIClient(单一事实来源)

    # 【中文说明】兜底: 不是任何已知厂商 → 抛异常, 让配置错误在启动/建图时立刻
    # 暴露, 而不是在运行中途才莫名其妙地失败。
    raise ValueError(f"Unsupported LLM provider: {provider}")
