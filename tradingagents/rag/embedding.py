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

# 【2026-09-11 修复】本地模型路径探测(不依赖环境变量,worker 环境诡异时兜底):
# 1) RAG_EMBED_MODEL 环境变量(优先);
# 2) 项目内 models/bge-small-zh-v1.5(本机部署位置);
# 3) 默认模型名(回退,可能走 HF 缓存)。
_LOCAL_MODEL_CANDIDATES = (
    "D:/AgentSense/models/bge-small-zh-v1.5",
    "D:/AgentSense/AgentSense/models/bge-small-zh-v1.5",
)


def _resolve_model_name() -> str:
    env = os.environ.get("RAG_EMBED_MODEL") or ""
    if env.strip():
        return env.strip()
    for cand in _LOCAL_MODEL_CANDIDATES:
        if os.path.isdir(cand):
            return cand
    return DEFAULT_MODEL

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

                model_name = _resolve_model_name()
                logger.info("RAG: 加载 embedding 模型 %s", model_name)
                # 【2026-09-11 修复】本地模型目录路径优先且不回退 HF 缓存:
                # 本机 HF 缓存 models--BAAI--bge-small-zh-v1.5 已损坏(0 字节 config),
                # 若加载本地路径失败后回退到默认模型名会命中坏缓存 → JSONDecodeError /
                # TextEncodeInput,RAG 全链路 500。本地目录时只信任本地加载,失败直接抛错
                # (由上层 context/retrieve 兜底降级,不碰缓存)。
                if os.path.isdir(model_name):
                    _embedder = SentenceTransformer(model_name, local_files_only=True)
                else:
                    try:
                        _embedder = SentenceTransformer(model_name, local_files_only=True)
                    except Exception:
                        logger.info("RAG: 本地缓存未命中,联网下载模型 %s", model_name)
                        _embedder = SentenceTransformer(model_name)
    return _embedder


def _sanitize_texts(texts: list[str]) -> list[str]:
    """清洗待编码文本:剔除孤立 surrogate 等非法码位。

    【为什么需要】2026-09-11 真机:前端中文经某环节被损坏成孤立代理项
    (\ud800-\udfff 中不成对的,如 \udc9f),传给 SentenceTransformer.encode →
    tokenizer 报 TextEncodeInput(检索全失败)。此处把非法码位替换为空格,
    保证 encode 永不因输入码位崩溃(丢失的字符本身已是乱码,剔除不损语义)。
    """
    out = []
    for t in texts:
        if isinstance(t, str):
            try:
                # 用 utf-8 往返剔除孤立 surrogate(surrogatepass 允许解码后过滤)
                t = t.encode("utf-8", "surrogatepass").decode("utf-8", "replace")
            except Exception:
                pass
        out.append(t)
    return out


def encode(texts: list[str]) -> list[list[float]]:
    """批量编码并 L2 归一化(归一化后内积=余弦,配合 Chroma cosine 空间)。"""
    model = get_embedder()
    vectors = model.encode(_sanitize_texts(texts), normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vectors]


def reset_embedder() -> None:
    """测试用:丢弃缓存的单例。"""
    global _embedder
    with _lock:
        _embedder = None
