"""Structured output binding for commodity futures agents.

Adapts the original TradingAgents' structured.py pattern for DeepSeek V4 Pro,
which supports OpenAI-compatible json_schema response_format.

Usage:
    from tradingagents.agents.utils.structured_commodity import bind_structured_commodity
    structured_llm = bind_structured_commodity(llm, AnalystBiasReport)
    result = structured_llm.invoke(messages)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)


def bind_structured_commodity(llm: Any, schema: type[BaseModel]) -> Any:
    """Bind a Pydantic schema to an LLM for structured output.

    Uses OpenAI-compatible json_schema response_format which DeepSeek V4 Pro
    supports. Returns an LLM wrapper whose .invoke() returns the parsed Pydantic
    instance (on success) or falls back to free-text with a warning.
    """
    schema_json = schema.model_json_schema()
    schema_name = schema.__name__

    # DeepSeek / OpenAI-compatible: use with_structured_output if available
    if hasattr(llm, "with_structured_output"):
        try:
            return llm.with_structured_output(schema, method="json_schema")
        except Exception as e:
            logger.warning(
                "with_structured_output failed for %s: %s. Falling back to manual mode.",
                schema_name,
                e,
            )

    # Manual fallback: inject json_schema into the request
    class StructuredLLMWrapper:
        """Wraps an LLM to request json_schema output and parse the result."""

        def __init__(self, _llm, _schema, _schema_name, _schema_json):
            self._llm = _llm
            self._schema = _schema
            self._schema_name = _schema_name
            self._schema_json = _schema_json

        def invoke(self, messages, **kwargs):
            # Build the schema instruction for the system message
            schema_instruction = (
                f"\n\nYou MUST respond with a JSON object matching this schema:\n"
                f"```json\n{json.dumps(self._schema_json, ensure_ascii=False, indent=2)}\n```\n"
                f"Your entire response must be valid JSON parseable as {self._schema_name}. "
                f"Do NOT include any text outside the JSON object."
            )

            # Append schema instruction to the last system message or create one
            modified_messages = []
            for msg in messages:
                modified_messages.append(msg)

            # Try adding schema instruction
            if hasattr(modified_messages[-1], "content"):
                last = modified_messages[-1]
                if hasattr(last, "content") and isinstance(last.content, str):
                    modified_messages[-1].content += schema_instruction

            try:
                raw = self._llm.invoke(modified_messages, **kwargs)
                content = raw.content if hasattr(raw, "content") else str(raw)

                # Extract JSON from response (may be wrapped in ```json blocks)
                json_str = content
                if "```json" in content:
                    json_str = content.split("```json")[1].split("```")[0]
                elif "```" in content:
                    json_str = content.split("```")[1].split("```")[0]

                parsed = self._schema.model_validate_json(json_str.strip())
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
