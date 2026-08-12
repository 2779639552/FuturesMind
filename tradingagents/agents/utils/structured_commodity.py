"""Structured output binding for commodity futures agents.

Adapts the original TradingAgents' structured.py pattern for DeepSeek V4 Pro,
which supports OpenAI-compatible json_schema response_format.

Usage:
    from tradingagents.agents.utils.structured_commodity import bind_structured_commodity
    structured_llm = bind_structured_commodity(llm, AnalystBiasReport)
    result = structured_llm.invoke(messages)
"""

from __future__ import annotations  # 【调用包】延迟求值注解

import json  # 【调用包】JSON 序列化/解析:构造 schema 指令文本与解析模型输出
import logging  # 【调用包】日志:记录结构化绑定/解析失败
from typing import Any  # 【调用包】动态类型注解

from pydantic import BaseModel  # 【调用包】Pydantic 基类:商品期货结构化输出 schema 的父类

logger = logging.getLogger(__name__)  # 【变量】模块级日志器


# 【功能】把 LLM 包装为"OpenAI 兼容 json_schema 结构化输出"(DeepSeek V4 Pro 支持)。
# 【参数】llm: 底层 LLM 客户端;schema: 目标 Pydantic schema。
# 【返回】LLM 包装器:.invoke() 成功返回解析后的 Pydantic 实例;失败回退自由文本或 None。
# 【关键】优先用 with_structured_output(method="json_schema");不支持时手写包装器,
#         把 JSON Schema 注入系统消息并解析/校验模型响应。
def bind_structured_commodity(llm: Any, schema: type[BaseModel]) -> Any:
    """Bind a Pydantic schema to an LLM for structured output.

    Uses OpenAI-compatible json_schema response_format which DeepSeek V4 Pro
    supports. Returns an LLM wrapper whose .invoke() returns the parsed Pydantic
    instance (on success) or falls back to free-text with a warning.
    """
    schema_json = schema.model_json_schema()  # 【变量】Pydantic 生成的 JSON Schema(含字段描述/约束),注入提示词约束输出结构
    schema_name = schema.__name__  # 【变量】schema 类名,用于日志与提示词中的类型名

    # DeepSeek / OpenAI-compatible: use with_structured_output if available
    if hasattr(llm, "with_structured_output"):
        try:
            return llm.with_structured_output(schema, method="json_schema")  # 【调用函数】用 OpenAI 兼容 json_schema 模式绑定结构化输出
        except Exception as e:
            logger.warning(
                "with_structured_output failed for %s: %s. Falling back to manual mode.",
                schema_name,
                e,
            )

    # Manual fallback: inject json_schema into the request
    # 【功能】手写结构化输出包装器:把 JSON Schema 注入系统消息,请求模型输出 JSON 并解析为 Pydantic 实例。
    class StructuredLLMWrapper:
        """Wraps an LLM to request json_schema output and parse the result."""

        # 【功能】构造包装器,保存底层 LLM 与 schema 元信息。
        # 【参数】_llm: 底层 LLM;_schema: 目标 Pydantic 类;_schema_name: 类名;_schema_json: JSON Schema。
        def __init__(self, _llm, _schema, _schema_name, _schema_json):
            self._llm = _llm
            self._schema = _schema
            self._schema_name = _schema_name
            self._schema_json = _schema_json

        # 【功能】执行一次带 schema 约束的 LLM 调用并解析响应。
        # 【参数】messages: 消息列表;**kwargs: 透传给底层 LLM 的额外参数。
        # 【返回】解析校验后的 Pydantic 实例;解析失败返回最小实例(narrative=原文)或 None。
        # 【关键】响应可能被 ```json 围栏包裹,先抽取再 model_validate_json 校验。
        def invoke(self, messages, **kwargs):
            # Build the schema instruction for the system message
            schema_instruction = (  # 【变量】追加到系统消息末尾的 JSON Schema 指令文本
                f"\n\nYou MUST respond with a JSON object matching this schema:\n"
                f"```json\n{json.dumps(self._schema_json, ensure_ascii=False, indent=2)}\n```\n"
                f"Your entire response must be valid JSON parseable as {self._schema_name}. "
                f"Do NOT include any text outside the JSON object."
            )

            # Append schema instruction to the last system message or create one
            modified_messages = []  # 【变量】原消息副本,用于把 schema 指令附加到最后一条系统消息
            for msg in messages:
                modified_messages.append(msg)

            # Try adding schema instruction
            if hasattr(modified_messages[-1], "content"):
                last = modified_messages[-1]
                if hasattr(last, "content") and isinstance(last.content, str):
                    modified_messages[-1].content += schema_instruction

            try:
                raw = self._llm.invoke(modified_messages, **kwargs)  # 【调用函数】底层 LLM 调用(带 schema 指令的消息)
                content = raw.content if hasattr(raw, "content") else str(raw)

                # Extract JSON from response (may be wrapped in ```json blocks)
                json_str = content  # 【变量】从响应中抽取的 JSON 文本(去掉 ```json 围栏后)
                if "```json" in content:
                    json_str = content.split("```json")[1].split("```")[0]
                elif "```" in content:
                    json_str = content.split("```")[1].split("```")[0]

                parsed = self._schema.model_validate_json(json_str.strip())  # 【调用函数】Pydantic 校验并解析响应 JSON 为模型实例
                return parsed

            except Exception as e:
                logger.warning(
                    "Structured output parse failed for %s: %s. Returning raw content.",
                    self._schema_name,
                    e,
                )
                # Return a minimal valid instance with the raw content as narrative
                try:
                    return self._schema(
                        narrative=content if isinstance(content, str) else str(content),
                    )
                except Exception:
                    return None

    return StructuredLLMWrapper(llm, schema, schema_name, schema_json)
