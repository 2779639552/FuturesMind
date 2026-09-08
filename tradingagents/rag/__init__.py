"""研报 RAG:检索增强模块。

公共纯函数(切块)在此层;embedding / Chroma 存取 / 检索编排分别在
embedding.py / store.py / service.py,均为懒加载依赖 —— chromadb 或
sentence-transformers 未安装时,is_available() 返回 False,上层挂点静默降级。
"""

from __future__ import annotations

import importlib.util

from .chunking import CHUNK_SIZE, OVERLAP, build_chunks, split_text

__all__ = [
    "CHUNK_SIZE",
    "OVERLAP",
    "build_chunks",
    "split_text",
    "is_available",
]


def is_available() -> bool:
    """chromadb 与 sentence-transformers 是否都已安装(不真正加载)。"""
    return importlib.util.find_spec("chromadb") is not None and (
        importlib.util.find_spec("sentence_transformers") is not None
    )
