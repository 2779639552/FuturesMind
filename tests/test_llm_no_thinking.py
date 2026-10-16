"""DeepSeek V4 关思考链(no_thinking)开关 (#2026-09-22)。

研报问答首字延迟实测: 思考链模式 4.1s → 关思考 0.7~1.0s(长输入)。
方舟端点接受 thinking:disabled, 但 openai SDK 拦截未知顶层参数,
必须走 ChatOpenAI 的 extra_body 字段(与 MiniMax reasoning_split 同款坑 #826)。
仅 DeepSeekChatOpenAI 认该开关; 其他厂商不透传, 避免构造 TypeError。
"""

from __future__ import annotations

import pytest

from tradingagents.llm_clients.factory import create_llm_client
from tradingagents.llm_clients.openai_client import DeepSeekChatOpenAI


def _mk_deepseek() -> DeepSeekChatOpenAI:
    return DeepSeekChatOpenAI(
        model="deepseek-v4-flash",
        api_key="placeholder",
        base_url="http://127.0.0.1:1/v1",  # 只测 payload 构造, 不发请求
        extra_body={"thinking": {"type": "disabled"}},
    )


@pytest.mark.unit
def test_extra_body_carries_thinking_disabled():
    llm = _mk_deepseek()
    assert llm.extra_body == {"thinking": {"type": "disabled"}}
    # payload 顶层必须带 extra_body(SDK 会 merge 到请求体顶层)
    payload = llm._get_request_payload("你好")
    assert payload.get("extra_body") == {"thinking": {"type": "disabled"}}


@pytest.mark.unit
def test_no_thinking_default_off():
    llm = DeepSeekChatOpenAI(
        model="deepseek-v4-flash",
        api_key="placeholder",
        base_url="http://127.0.0.1:1/v1",
    )
    assert llm.extra_body in (None, {})


@pytest.mark.unit
def test_no_thinking_passthrough_create_client(monkeypatch):
    """create_llm_client(no_thinking=True) 一路穿透到 DeepSeek 实例的 extra_body。"""
    monkeypatch.setenv("ARK_API_KEY", "placeholder")
    client = create_llm_client("doubao", "deepseek-v4-flash", no_thinking=True)
    llm = client.get_llm()
    assert isinstance(llm, DeepSeekChatOpenAI), type(llm)
    assert llm.extra_body == {"thinking": {"type": "disabled"}}


@pytest.mark.unit
def test_no_thinking_not_leaked_to_other_providers(monkeypatch):
    """no_thinking 不得透传给非 DeepSeek 端点(否则构造 TypeError / 服务端 400)。

    【为什么必须显式给 base_url】项目 .env 的 TRADINGAGENTS_LLM_BACKEND_URL 全局指向
    api.deepseek.com，**不显式指定地址时任何 provider 都会拿到该地址** ⇒ 本用例会被
    误判成"DeepSeek 端点"（2026-10-13 实测踩过）。显式指向 OpenAI 官方地址，
    才真正测到"非 DeepSeek"这一条件。
    """
    monkeypatch.setenv("OPENAI_API_KEY", "placeholder")
    client = create_llm_client(
        "openai", "gpt-5.4-mini", no_thinking=True,
        base_url="https://api.openai.com/v1",
    )
    llm = client.get_llm()  # 不应抛 TypeError
    assert not isinstance(llm, DeepSeekChatOpenAI)
    assert llm.extra_body in (None, {}), "非 DeepSeek 端点不应收到 thinking 开关"


@pytest.mark.unit
def test_no_thinking_works_via_generic_provider_pointing_at_deepseek(monkeypatch):
    """★ 2026-10-13 修复的回归：通用 provider 指向 DeepSeek 官方端点时也要生效。

    【缺陷经过】项目配的是 provider=openai_compatible + base_url=https://api.deepseek.com。
    原判据 `chat_cls is DeepSeekChatOpenAI` 对通用 provider 不成立 ⇒ no_thinking 被
    **静默丢弃**、思考链全开。实测（外文研报翻译，1043 字符块）：
    completion_tokens=4721 里 reasoning=4529，单块 44.9s；关掉后 1.6s（28x），
    且思考 token 挤占输出预算导致译文中途截断（6 块里 1 块未译完）。
    """
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "placeholder")
    client = create_llm_client(
        "openai_compatible", "deepseek-flash",
        no_thinking=True, base_url="https://api.deepseek.com",
    )
    llm = client.get_llm()
    assert llm.extra_body == {"thinking": {"type": "disabled"}}, (
        "通用 provider 指向 DeepSeek 端点时未注入 thinking 开关"
    )


@pytest.mark.unit
def test_no_thinking_not_injected_for_generic_non_deepseek_endpoint(monkeypatch):
    """反向保护：通用 provider 指向本地/其他端点时，**仍不得**注入 thinking 开关。

    否则 vLLM / Ollama / LM Studio 这类不认识该字段的服务会直接 400。
    """
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "placeholder")
    client = create_llm_client(
        "openai_compatible", "qwen2.5-7b",
        no_thinking=True, base_url="http://localhost:11434/v1",
    )
    llm = client.get_llm()
    assert llm.extra_body in (None, {}), "非 DeepSeek 方言端点不应收到 thinking 开关"

