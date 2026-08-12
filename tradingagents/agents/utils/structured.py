"""Shared helpers for invoking an agent with structured output and a graceful fallback.

The Portfolio Manager, Trader, and Research Manager all follow the same
canonical pattern:

1. At agent creation, wrap the LLM with ``with_structured_output(Schema)``
   so the model returns a typed Pydantic instance. If the provider does
   not support structured output (rare; mostly older Ollama models), the
   wrap is skipped and the agent uses free-text generation instead.
2. At invocation, run the structured call and render the result back to
   markdown. If the structured call itself fails for any reason
   (malformed JSON from a weak model, transient provider issue), fall
   back to a plain ``llm.invoke`` so the pipeline never blocks.

Centralising the pattern here keeps the agent factories small and ensures
all three agents log the same warnings when fallback fires.
"""

from __future__ import annotations  # 【调用包】延迟求值注解

import logging  # 【调用包】日志:记录结构化输出回退警告
from collections.abc import Callable  # 【调用包】回调类型:render 函数签名
from typing import Any, TypeVar  # 【调用包】TypeVar 泛型:约束 schema 必须是 BaseModel 子类

from pydantic import BaseModel  # 【调用包】Pydantic 基类:结构化输出 schema 的父类

logger = logging.getLogger(__name__)  # 【变量】模块级日志器

T = TypeVar("T", bound=BaseModel)  # 【变量】泛型变量:限定 schema 类型为 BaseModel 子类,保证 render 返回 str


# 【功能】把 LLM 包装为"结构化输出模式"(返回 Pydantic 实例);provider 不支持时返回 None。
# 【参数】llm: 底层 LLM 客户端;schema: 目标 Pydantic schema;agent_name: 用于日志的代理名。
# 【返回】包装后的 LLM(可用 .invoke() 拿 Pydantic 实例),不支持时 None(走自由文本)。
# 【关键】捕获 NotImplementedError/AttributeError——老 Ollama 等模型不支持 with_structured_output。
def bind_structured(llm: Any, schema: type[T], agent_name: str) -> Any | None:
    """Return ``llm.with_structured_output(schema)`` or ``None`` if unsupported.

    Logs a warning when the binding fails so the user understands the agent
    will use free-text generation for every call instead of one-shot fallback.
    """
    try:
        return llm.with_structured_output(schema)  # 【调用函数】绑定结构化输出模式;不支持的 provider 抛异常
    except (NotImplementedError, AttributeError) as exc:
        logger.warning(
            "%s: provider does not support with_structured_output (%s); "
            "falling back to free-text generation",
            agent_name,
            exc,
        )
        return None


# 【功能】执行结构化调用并把结果渲染为 markdown;任何失败都回退为普通自由文本。
# 【参数】structured_llm: bind_structured 的包装器(可能为 None);plain_llm: 普通 LLM;
#        prompt: 底层 LLM 接受的提示(字符串或消息列表);render: Pydantic 实例→markdown;
#        agent_name: 日志用代理名。
# 【返回】markdown 字符串(结构化或回退路径的产物)。
# 【关键】思考型模型可能不调工具而直接答文本,导致解析结果 None,也按 miss 回退。
def invoke_structured_or_freetext(
    structured_llm: Any | None,
    plain_llm: Any,
    prompt: Any,
    render: Callable[[T], str],
    agent_name: str,
) -> str:
    """Run the structured call and render to markdown; fall back to free-text on any failure.

    ``prompt`` is whatever the underlying LLM accepts (a string for chat
    invocations, a list of message dicts for chat models that take that
    shape). The same value is forwarded to the free-text path so the
    fallback sees the same input the structured call did.
    """
    if structured_llm is not None:
        try:
            result = structured_llm.invoke(prompt)  # 【调用函数】结构化调用:LLM 返回 Pydantic 实例
            if result is None:
                # A thinking model can answer in plain text instead of calling
                # the tool, leaving the parser with nothing to return. Treat it
                # as a structured miss and fall back, with a clear reason.
                raise ValueError("structured output returned no parsed result")
            return render(result)
        except Exception as exc:
            logger.warning(
                "%s: structured-output invocation failed (%s); retrying once as free text",
                agent_name,
                exc,
            )

    response = plain_llm.invoke(prompt)  # 【调用函数】回退路径:普通自由文本调用,拿到原始内容
    return response.content
