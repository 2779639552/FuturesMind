"""Chroma 向量库封装:懒加载持久化客户端,embedding 由调用方算好传入。

持久目录默认 ~/.tradingagents/rag_chroma,环境变量 RAG_CHROMA_DIR 可覆盖。
collection 建为 cosine 距离空间(配合归一化向量)。
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

COLLECTION_NAME = "research_reports"

_client = None
_collection = None
# chroma 0.5x 的 PersistentClient 全局注册表非线程安全:多线程同时首次建连会
# 撞 _identifier_to_system KeyError,且其失败路径会 stop() 共享系统(rust bindings
# 被删),并发使用下可拖垮整个进程(2026-09-09 web_app 首采 RAG 并发索引时复现)。
# 这里用模块级锁把"首次建连+建 collection"串行化,之后 query/upsert 并发安全。
_init_lock = threading.Lock()


def chroma_dir() -> Path:
    custom = os.environ.get("RAG_CHROMA_DIR")
    if custom:
        return Path(custom)
    return Path.home() / ".tradingagents" / "rag_chroma"


def get_collection():
    """懒加载 Chroma persistent client 与 collection(进程内单例,加锁防并发首连竞态)。"""
    global _client, _collection
    if _collection is None:
        with _init_lock:
            if _collection is None:  # 双检:等锁期间可能已被他线程建好
                import chromadb
                from chromadb.config import Settings

                _client = chromadb.PersistentClient(
                    path=str(chroma_dir()),
                    settings=Settings(anonymized_telemetry=False),  # 单机自用,不上报
                )
                _collection = _client.get_or_create_collection(
                    name=COLLECTION_NAME,
                    metadata={"hnsw:space": "cosine"},
                )
    return _collection


def upsert_chunks(chunks: list[dict], embeddings: list[list[float]]) -> int:
    """写入/覆盖 chunk(id 幂等)。chunks 元素为 chunking.build_chunks 的产物。"""
    if not chunks:
        return 0
    collection = get_collection()
    collection.upsert(
        ids=[c["id"] for c in chunks],
        embeddings=embeddings,
        documents=[c["text"] for c in chunks],
        metadatas=[c["metadata"] for c in chunks],
    )
    return len(chunks)


def delete_by_report(report_id: int) -> None:
    """删除一份研报的全部向量(不存在时静默)。"""
    get_collection().delete(where={"report_id": report_id})


def query(
    query_embedding: list[float],
    top_k: int = 6,
    exclude_report_id: int | None = None,
) -> list[dict]:
    """余弦近邻检索。返回 [{id, text, metadata, score}],score=1-距离(越大越近)。

    exclude_report_id 在此处用 where 过滤(单报告排除,精确匹配够用);
    品种过滤因 varieties 是逗号串,由上层超采样+后过滤完成。
    """
    where = {"report_id": {"$ne": exclude_report_id}} if exclude_report_id else None
    res = get_collection().query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
        where=where,
    )
    hits = []
    ids = res["ids"][0]
    docs = res["documents"][0]
    metas = res["metadatas"][0]
    dists = res["distances"][0]
    for i in range(len(ids)):
        hits.append(
            {
                "id": ids[i],
                "text": docs[i],
                "metadata": metas[i],
                "score": 1.0 - float(dists[i]),
            }
        )
    return hits


def count() -> int:
    return get_collection().count()


def reset_for_tests() -> None:
    """测试用:丢弃进程内单例(配合 RAG_CHROMA_DIR 指向 tmp_path)。"""
    global _client, _collection
    _client = None
    _collection = None
