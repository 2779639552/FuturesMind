"""LLM 客户端包: 提供统一接口的厂商适配客户端。

对外暴露 BaseLLMClient(统一基类)与 create_llm_client(工厂入口)。上层业务
只调用 create_llm_client(provider, model, base_url, **kwargs) 取得客户端,
再由 .get_llm() 拿到真正 LLM, 不关心底层是哪家厂商 SDK。
"""

from .base_client import BaseLLMClient  # 【调用包】统一客户端基类
from .factory import create_llm_client  # 【调用包】多厂商 LLM 客户端工厂

__all__ = ["BaseLLMClient", "create_llm_client"]
