"""本地 embedding:懒加载单例。

默认模型 BAAI/bge-small-zh-v1.5(~95MB,512 维);RAG_EMBED_MODEL 环境变量
可换模型名或直接传本地模型目录路径。离线下载走 HF 镜像:
    set HF_ENDPOINT=https://hf-mirror.com
"""

from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "BAAI/bge-small-zh-v1.5"

_lock = threading.Lock()
_embedder = None


def get_embedder():
    """首次调用加载 SentenceTransformer 并缓存(web_app 启动不触发加载)。

    优先 local_files_only=True 走本地 HF 缓存 —— 已缓存时零联网、秒级加载;
    未缓存才回退联网下载(首次部署需可访问 HF 或镜像 HF_ENDPOINT=hf-mirror.com)。
    直接联网校验会被防火墙长时间挂住(2026-09-08 真机踩坑),不可作为首选路径。
    """
    global _embedder
    if _embedder is None:
        with _lock:
            if _embedder is None:
                from sentence_transformers import SentenceTransformer

                model_name = os.environ.get("RAG_EMBED_MODEL") or DEFAULT_MODEL
                logger.info("RAG: 加载 embedding 模型 %s", model_name)
                try:
                    _embedder = SentenceTransformer(model_name, local_files_only=True)
                except Exception:
                    logger.info("RAG: 本地缓存未命中,联网下载模型 %s", model_name)
                    _embedder = SentenceTransformer(model_name)
    return _embedder


def encode(texts: list[str]) -> list[list[float]]:
    """批量编码并 L2 归一化(归一化后内积=余弦,配合 Chroma cosine 空间)。"""
    model = get_embedder()
    vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vectors]


def reset_embedder() -> None:
    """测试用:丢弃缓存的单例。"""
    global _embedder
    with _lock:
        _embedder = None
