"""Model name validators for each provider."""

from .model_catalog import get_known_models  # 【调用包】从共享 CLI 目录取已知模型列表

# 【变量】模型名由用户自定义的厂商集合(本地服务/中转/多模型托管端点):
#         任意模型字符串都接受、不告警。
_ANY_MODEL_PROVIDERS = (
    "ollama",
    "openrouter",
    "openai_compatible",
    "mistral",
    "kimi",
    "groq",
    "nvidia",
    "bedrock",
)

# 【变量】可校验的厂商 -> 已知模型名集合(剔除 _ANY_MODEL_PROVIDERS 中的厂商)
VALID_MODELS = {
    provider: models
    for provider, models in get_known_models().items()
    if provider not in _ANY_MODEL_PROVIDERS
}


# 【功能】校验模型名是否属于该厂商的已知列表。
# 【参数】provider: 厂商名; model: 待校验的模型名。
# 【返回】True = 合法; 厂商在 _ANY_MODEL_PROVIDERS 或不在 VALID_MODELS 时一律
#         返回 True(无法校验时不误报)。
# 【关键】ollama/openrouter/openai_compatible 等接受任意模型名。
def validate_model(provider: str, model: str) -> bool:
    """Check if model name is valid for the given provider.

    For ollama, openrouter, and openai_compatible - any model is accepted.
    """
    provider_lower = provider.lower()  # 【变量】归一化后的厂商名

    if provider_lower in _ANY_MODEL_PROVIDERS:
        return True

    if provider_lower not in VALID_MODELS:
        return True

    return model in VALID_MODELS[provider_lower]
