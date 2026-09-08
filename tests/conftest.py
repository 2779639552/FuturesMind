"""Shared pytest fixtures that prevent CI hangs when API keys are absent."""

import os
from unittest.mock import MagicMock, patch

import pytest


def pytest_configure(config):
    for marker in ("unit", "integration", "smoke"):
        config.addinivalue_line("markers", f"{marker}: {marker}-level tests")


_API_KEY_ENV_VARS = (
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "ANTHROPIC_API_KEY",
    "XAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "DASHSCOPE_CN_API_KEY",
    "ZHIPU_API_KEY",
    "ZHIPU_CN_API_KEY",
    "MINIMAX_API_KEY",
    "MINIMAX_CN_API_KEY",
    "OPENROUTER_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "ALPHA_VANTAGE_API_KEY",
)


@pytest.fixture(autouse=True)
def _dummy_api_keys(monkeypatch):
    for env_var in _API_KEY_ENV_VARS:
        # `or` not a .get default: an env var present but empty (e.g. a key left
        # blank in a .env copied from .env.example) must still get the placeholder.
        monkeypatch.setenv(env_var, os.environ.get(env_var) or "placeholder")


@pytest.fixture(autouse=True)
def _disable_gtja(monkeypatch):
    """GTJA(国泰君安)是真实联网的优先数据源:默认置空 key,令离线单测稳定走原链路。

    commodity_futures 基差/库存 与 web 观点路由都以 ``gtja_api.configured()``
    (key 非空)为接入开关;置空 key 后该分支天然跳过 → 既有 cache/回退测试在无网
    环境保持确定性(不真的打国君接口)。需要测 GTJA 分支的用例自行 setenv 补 key,
    或 monkeypatch gtja_api.configured()/_request()。
    """
    monkeypatch.setenv("GTJA_ACCESS_KEY_ID", "")
    monkeypatch.setenv("GTJA_ACCESS_KEY_SECRET", "")


@pytest.fixture(autouse=True)
def _isolate_config():
    """Reset the global dataflows config before and after each test.

    ``set_config`` merges (it never clears keys absent from the override), so a
    test that sets e.g. ``tool_vendors`` would otherwise leak into later tests
    and make routing behavior order-dependent. Replace the global outright so
    every test starts from a clean DEFAULT_CONFIG.
    """
    import copy

    import tradingagents.dataflows.config as config_module
    import tradingagents.default_config as default_config

    config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)
    yield
    config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)


@pytest.fixture(autouse=True)
def _disable_rag(monkeypatch):
    """默认禁用研报 RAG 挂点(web_app 薄适配层),令离线单测零依赖确定性。

    RAG 依赖(chromadb/sentence-transformers)体积大且首跑要下载本地 embedding
    模型,不允许既有测试隐式触发。本机依赖已装时,_rag_context_for_variety 若不
    禁用会在注入点真检索;三个挂点全部 no-op 后,研报主链路测试与 RAG 无关。
    位图图表视觉重述挂点(_vision_describe_safely,本地 Ollama)同理:把
    ollama_available 桩成 False,真实钩子代码照跑但经"Ollama 不可达 → 返回空串"
    兜底路径毫秒级放行(不替换 web_app 函数本身,适配层自身可测)。
    RAG 自身测试(tests/test_rag_*.py)直接测 tradingagents.rag 包或在用例内
    monkeypatch 覆盖,不走本 fixture 的桩。
    """
    import tradingagents.dataflows.chart_vision as _chart_vision

    monkeypatch.setattr("web_app._rag_context_for_variety", lambda *a, **k: "")
    monkeypatch.setattr("web_app._rag_index_report_safely", lambda *a, **k: None)
    monkeypatch.setattr("web_app._rag_delete_vectors_safely", lambda *a, **k: None)
    monkeypatch.setattr(_chart_vision, "ollama_available", lambda timeout=2.0: False)


@pytest.fixture()
def mock_llm_client():
    client = MagicMock()
    client.get_llm.return_value = MagicMock()
    with patch(
        "tradingagents.llm_clients.factory.create_llm_client",
        return_value=client,
    ):
        yield client
