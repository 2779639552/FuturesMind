"""Declarative per-model capability table for OpenAI-compatible providers.

This is the single place that knows which model IDs reject which API
parameters or require which structured-output method. The LLM client
subclasses consult ``get_capabilities(model_name)`` instead of hardcoding
model-name ``if`` ladders, so adding a new model (or a new provider quirk)
means editing this table — not the client code.

Pattern adapted from the per-model ``compat:`` flags DeepSeek themselves
publish in their integration guides (e.g. the Oh My Pi config schema
documents ``supportsToolChoice``, ``requiresReasoningContentForToolCalls``
as declarative per-model fields).
"""

from __future__ import annotations  # 【调用包】启用延迟求值的类型注解

import re  # 【调用包】用正则做前向兼容的模型名模式匹配
from dataclasses import dataclass  # 【调用包】定义 ModelCapabilities 数据类
from typing import Literal  # 【调用包】定义结构化输出方式的字面量类型

# 【变量】结构化输出方式枚举: function_calling(工具+尊重 supports_tool_choice) /
#         json_mode(response_format=json_object) / json_schema(response_format=
#         json_schema) / none(无可用方式, 调用方退化为自由文本)
StructuredMethod = Literal[
    "function_calling",  # uses tools; respects supports_tool_choice
    "json_mode",  # uses response_format={"type":"json_object"}
    "json_schema",  # uses response_format={"type":"json_schema",...}
    "none",  # no structured output available; caller falls back to free-text
]


# 【功能】声明式描述一个 OpenAI 兼容模型在 API 层面的能力。
# 【变量】supports_tool_choice / supports_json_mode / supports_json_schema:
#         模型是否接受对应参数; preferred_structured_method: 首选结构化方式;
#         requires_reasoning_content_roundtrip: DeepSeek 思维模型要求下一轮回传
#         reasoning_content, 否则 400; requires_reasoning_split: MiniMax M2.x
#         推理模型需 reasoning_split=True, 让 <think> 块落入 reasoning_details。
@dataclass(frozen=True)
class ModelCapabilities:
    """What an OpenAI-compatible model accepts at the API level."""

    supports_tool_choice: bool
    supports_json_mode: bool
    supports_json_schema: bool
    preferred_structured_method: StructuredMethod
    # DeepSeek thinking-mode models 400 if reasoning_content from prior
    # assistant turns is not echoed back on the next request.
    requires_reasoning_content_roundtrip: bool = False
    # MiniMax M2.x reasoning models need ``reasoning_split=True`` so the
    # <think> block lands in ``reasoning_details`` instead of polluting
    # ``content``. The flag is rejected by non-reasoning MiniMax models
    # (Coding Plan, MiniMax-Text-01, etc.), so we only set it where the
    # model actually consumes it. (#826)
    requires_reasoning_split: bool = False


# DeepSeek's thinking models accept the ``tools`` array but reject the
# ``tool_choice`` parameter (official Oh My Pi integration guide and the
# 400 response in issue #678). Their official tool-calling examples
# (api-docs.deepseek.com/guides/tool_calls) pass ``tools=[...]`` without
# ``tool_choice`` — we mirror that pattern by setting supports_tool_choice
# to False and letting the client suppress the kwarg.
# 【变量】DeepSeek 思维模型能力: 接受 tools 数组但拒绝 tool_choice 参数,
#         并需 reasoning_content 回传(见官方 Oh My Pi 集成指南与 #678/#400)。
_DEEPSEEK_THINKING = ModelCapabilities(
    supports_tool_choice=False,
    supports_json_mode=True,
    supports_json_schema=False,
    preferred_structured_method="function_calling",
    requires_reasoning_content_roundtrip=True,
)

# 【变量】DeepSeek 非思维(chat)模型能力: 支持 tool_choice。
_DEEPSEEK_CHAT = ModelCapabilities(
    supports_tool_choice=True,
    supports_json_mode=True,
    supports_json_schema=False,
    preferred_structured_method="function_calling",
)

# MiniMax M2.x reasoning models accept the tools array, but their
# tool_choice parameter is restricted to the enum {"none", "auto"}
# (platform.minimax.io/docs/api-reference/text-post). Langchain's
# function_calling path sends tool_choice as a function-spec dict, which
# MiniMax 400s — same shape as the DeepSeek bug. supports_tool_choice=False
# makes the dispatch in NormalizedChatOpenAI suppress the kwarg; the schema
# still ships as a tool. json_mode response_format is only for
# MiniMax-Text-01, not M2.x.
# 【变量】MiniMax M2.x 推理模型能力: tool_choice 仅接受 {"none","auto"} 枚举,
#         拒绝 langchain 发送的函数 spec dict; 需 reasoning_split=True。
_MINIMAX_THINKING = ModelCapabilities(
    supports_tool_choice=False,
    supports_json_mode=False,
    supports_json_schema=False,
    preferred_structured_method="function_calling",
    requires_reasoning_split=True,
)

# 【变量】默认能力(最宽松): 支持 tool_choice 与两种 json 结构化方式。
_DEFAULT = ModelCapabilities(
    supports_tool_choice=True,
    supports_json_mode=True,
    supports_json_schema=True,
    preferred_structured_method="function_calling",
)


# Exact-ID matches take precedence over pattern matches.
# 【变量】按精确模型 ID 查询的能力表(优先于下面的模式匹配)
_BY_ID: dict[str, ModelCapabilities] = {
    "deepseek-chat": _DEEPSEEK_CHAT,
    "deepseek-reasoner": _DEEPSEEK_THINKING,
    "deepseek-v4-flash": _DEEPSEEK_THINKING,
    "deepseek-v4-pro": _DEEPSEEK_THINKING,
    # MiniMax — full official model lineup per
    # platform.minimax.io/docs/api-reference/text-openai-api
    "MiniMax-M2.7": _MINIMAX_THINKING,
    "MiniMax-M2.7-highspeed": _MINIMAX_THINKING,
    "MiniMax-M2.5": _MINIMAX_THINKING,
    "MiniMax-M2.5-highspeed": _MINIMAX_THINKING,
    "MiniMax-M2.1": _MINIMAX_THINKING,
    "MiniMax-M2.1-highspeed": _MINIMAX_THINKING,
    "MiniMax-M2": _MINIMAX_THINKING,
}

# Forward-compat patterns. New ``deepseek-v5-*`` / ``deepseek-reasoner-*``
# or ``MiniMax-M3*`` variants inherit the thinking-mode quirks automatically.
# 【变量】前向兼容模式列表: 未来的 deepseek-v5-* / deepseek-reasoner-* /
#         MiniMax-M3* 自动继承思维模型特性, 无需逐个登记。
_BY_PATTERN: list[tuple[re.Pattern[str], ModelCapabilities]] = [
    (re.compile(r"^deepseek-v\d"), _DEEPSEEK_THINKING),
    (re.compile(r"^deepseek-reasoner"), _DEEPSEEK_THINKING),
    (re.compile(r"^MiniMax-M\d"), _MINIMAX_THINKING),
]


# 【功能】解析某模型名的能力: 先精确 ID, 再模式匹配, 最后回退默认。
# 【返回】ModelCapabilities 实例。
def get_capabilities(model_name: str) -> ModelCapabilities:
    """Resolve capabilities by exact ID, then pattern, then default."""
    if model_name in _BY_ID:
        return _BY_ID[model_name]
    for pattern, caps in _BY_PATTERN:
        if pattern.match(model_name):
            return caps
    return _DEFAULT
