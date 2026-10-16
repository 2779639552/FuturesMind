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
    # 【2026-09-24 局域网认证】测试默认关闭守卫(生产由 web 进程 .env 未设该键 → 开启)
    monkeypatch.setenv("AGENTSENSE_AUTH_DISABLE", "1")


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


# 【2026-09-30 根因修复 · reload 污染外溢】
# ``default_config.DEFAULT_CONFIG`` 是**模块级可变 dict**;有若干测试文件用
# ``importlib.reload(default_config)`` 重造它(如 test_env_overrides / test_llm_max_retries)。
# 注意 ``monkeypatch`` 只回滚**环境变量**,管不到这个 dict —— reload 之后它会一直停在
# "最后一次 reload 的产物"(典型脏值: env 被清空 ⇒ ``backend_url`` 变 None)。
#
# 而下面 ``_isolate_config`` 是拿 ``DEFAULT_CONFIG`` 当"干净基准"去灌全局配置的,
# 于是脏值被**复制进全局配置并外溢到全部后续测试**。后果实测:
#   web_app 里 ``create_llm_client(provider, model)``(不传 base_url)经工厂兜底
#   ``_resolve_config_base_url()`` 取到 None ⇒ 抛
#   ``ValueError: Provider 'openai_compatible' requires a base_url``
# ⇒ 表现为"某用例(如 tests/test_rag_backfill.py)全量会话必红、单跑全绿"的假象,
#   极易被误判为随机抖动或跟本次改动有关。最小复现:
#       pytest tests/test_env_overrides.py tests/test_rag_backfill.py   # 1 failed
#
# 修法: 在**任何用例跑之前**抓一份 pristine 快照,每用例前后把 ``DEFAULT_CONFIG``
# 就地还原(clear+update 保持对象身份,避免 reload 换对象后旧引用失效)。这与本
# fixture 文档里"every test starts from a clean DEFAULT_CONFIG"的本意一致 ——
# 原实现只是"消费"干净基准,现在把基准本身也复位。
_PRISTINE_DEFAULT_CONFIG: dict | None = None


@pytest.fixture(autouse=True)
def _isolate_config():
    """Reset the global dataflows config before and after each test.

    ``set_config`` merges (it never clears keys absent from the override), so a
    test that sets e.g. ``tool_vendors`` would otherwise leak into later tests
    and make routing behavior order-dependent. Replace the global outright so
    every test starts from a clean DEFAULT_CONFIG.

    【同时复位 DEFAULT_CONFIG 本身】见上方"reload 污染外溢"说明:快照在首个用例
    执行**前**建立(此时不可能已被污染),之后每个用例前后都把 DEFAULT_CONFIG 还原,
    这样 ``importlib.reload`` 留下的脏值不会经本 fixture 外溢到后续文件。
    """
    import copy

    import tradingagents.dataflows.config as config_module
    import tradingagents.default_config as default_config

    global _PRISTINE_DEFAULT_CONFIG
    if _PRISTINE_DEFAULT_CONFIG is None:
        # 首次调用发生在任何用例体之前 ⇒ 此刻的 DEFAULT_CONFIG 必为初始干净值。
        _PRISTINE_DEFAULT_CONFIG = copy.deepcopy(default_config.DEFAULT_CONFIG)

    default_config.DEFAULT_CONFIG.clear()
    default_config.DEFAULT_CONFIG.update(copy.deepcopy(_PRISTINE_DEFAULT_CONFIG))
    config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)
    yield
    default_config.DEFAULT_CONFIG.clear()
    default_config.DEFAULT_CONFIG.update(copy.deepcopy(_PRISTINE_DEFAULT_CONFIG))
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
