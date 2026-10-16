"""Generic OpenAI-compatible provider (vLLM / LM Studio / llama.cpp / relays).

Verifies the user-supplied base_url is required and honored, the key is optional
(keyless local default), Chat Completions (not the Responses API) is used, any
model name is accepted, and the env backend URL precedence (#978).
"""

import pytest

from tradingagents.llm_clients.api_key_env import get_api_key_env
from tradingagents.llm_clients.factory import create_llm_client
from tradingagents.llm_clients.validators import validate_model

# Note: assert by class NAME, not isinstance — other tests reload the
# openai_client module, which would otherwise create a second class identity.


@pytest.mark.unit
def test_factory_routes_to_openai_client():
    client = create_llm_client(
        provider="openai_compatible", model="my-model", base_url="http://localhost:8000/v1"
    )
    assert type(client).__name__ == "OpenAIClient"


@pytest.mark.unit
def test_base_url_required(monkeypatch, tmp_path):
    """未给 base_url 时 openai_compatible 必须报错。

    【2026-09-30 自洽性修复】原用例只 delenv 掉 API key 就断言抛错,但 ``factory.
    create_llm_client`` 有个**兜底** ``_resolve_config_base_url()``:调用点没传
    base_url 时,它会从全局配置的 ``backend_url`` 取。于是只要运行环境(如本机 .env
    的 ``TRADINGAGENTS_LLM_BACKEND_URL=https://ai.ctaigw.cn/v1``)配了该值,就
    **不会抛错 → 用例假红**;而它此前在**全量会话里能过**,只是因为前面某个用例把
    配置弄脏成了 ``backend_url=None``(顺序污染),纯属巧合。

    【为什么不能删兜底】兜底是应用必需的:``web_app.py`` 里 12 个
    ``create_llm_client(provider, model)`` 调用点都不传 base_url,本机正是靠它才能
    正常起 LLM。所以这里改为**把兜底来源显式清空**(清 DEFAULT_CONFIG 与全局配置里的
    backend_url),让"没有地址 ⇒ 必须报错"这个语义在干净环境下也成立。
    """
    import tradingagents.dataflows.config as config_module
    import tradingagents.default_config as default_config

    monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
    monkeypatch.delenv("TRADINGAGENTS_LLM_BACKEND_URL", raising=False)
    # 兜底读的是全局配置(可能已由 .env 或前序用例填上),两处都清掉才是"真的没有地址"。
    monkeypatch.setitem(default_config.DEFAULT_CONFIG, "backend_url", None)
    glob = dict(config_module.get_config())
    glob["backend_url"] = None
    config_module.set_config(glob)

    with pytest.raises(ValueError, match="requires a base_url"):
        create_llm_client(provider="openai_compatible", model="m").get_llm()


@pytest.mark.unit
def test_keyless_local_uses_placeholder_and_chat_completions(monkeypatch):
    monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
    llm = create_llm_client(
        provider="openai_compatible", model="qwen2.5", base_url="http://localhost:8000/v1"
    ).get_llm()
    assert type(llm).__name__ == "LocalCompatibleChatOpenAI"
    assert str(llm.openai_api_base) == "http://localhost:8000/v1"
    # keyless local servers: a placeholder key is sent
    key = (
        llm.openai_api_key.get_secret_value()
        if hasattr(llm.openai_api_key, "get_secret_value")
        else llm.openai_api_key
    )
    assert key == "EMPTY"
    # must use Chat Completions, not OpenAI's Responses API
    assert getattr(llm, "use_responses_api", False) in (False, None)


@pytest.mark.unit
def test_optional_key_from_env(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "sk-relay-123")
    llm = create_llm_client(
        provider="openai_compatible", model="m", base_url="https://relay.example/v1"
    ).get_llm()
    key = (
        llm.openai_api_key.get_secret_value()
        if hasattr(llm.openai_api_key, "get_secret_value")
        else llm.openai_api_key
    )
    assert key == "sk-relay-123"


@pytest.mark.unit
def test_any_model_accepted_no_forced_key():
    assert validate_model("openai_compatible", "literally-anything") is True
    # The key env exists (read for keyed relays) but the provider is marked
    # key-optional, so the CLI never forces a prompt and keyless servers work.
    assert get_api_key_env("openai_compatible") == "OPENAI_COMPATIBLE_API_KEY"
    from tradingagents.llm_clients.openai_client import OPENAI_COMPATIBLE_PROVIDERS

    assert OPENAI_COMPATIBLE_PROVIDERS["openai_compatible"].key_optional is True


@pytest.mark.unit
def test_env_backend_url_precedence():
    # #978: explicit env URL wins over the menu/default regardless of provider source.
    from cli.utils import resolve_backend_url

    assert (
        resolve_backend_url("openai", "https://api.openai.com/v1", env_url="http://proxy/v1")
        == "http://proxy/v1"
    )
    assert (
        resolve_backend_url("openai", "https://api.openai.com/v1", env_url=None)
        == "https://api.openai.com/v1"
    )
    assert resolve_backend_url("deepseek", None, None) == "https://api.deepseek.com"


@pytest.mark.unit
def test_structured_output_suppresses_object_tool_choice(monkeypatch):
    # LM Studio / vLLM reject the object-form tool_choice langchain sends for
    # function-calling structured output (#1057). The generic provider binds the
    # schema as a tool but must not force tool_choice.
    from langchain_openai import ChatOpenAI
    from pydantic import BaseModel

    class Schema(BaseModel):
        x: int

    captured = {}
    monkeypatch.setattr(
        ChatOpenAI,
        "with_structured_output",
        lambda self, schema, method=None, **kw: (
            captured.update({"method": method, **kw}) or "BOUND"
        ),
    )
    llm = create_llm_client(
        provider="openai_compatible", model="local-llm-30b", base_url="http://localhost:1234/v1"
    ).get_llm()
    out = llm.with_structured_output(Schema)
    assert out == "BOUND"
    assert captured["method"] == "function_calling"
    assert captured["tool_choice"] is None  # not the object form
