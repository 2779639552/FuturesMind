"""RAG Chroma store 测试:RAG_CHROMA_DIR 指向 tmp_path,embedding 用确定性向量。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingagents.rag import store  # noqa: E402


def _fake_vec(text: str, dim: int = 8) -> list[float]:
    """确定性伪向量:同文本同向量,不同文本近似正交。"""
    import hashlib

    digest = hashlib.sha256(text.encode("utf-8")).digest()
    vec = [((digest[i % len(digest)] / 255.0) - 0.5) for i in range(dim)]
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


@pytest.fixture()
def chroma_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_CHROMA_DIR", str(tmp_path / "chroma"))
    store.reset_for_tests()
    yield tmp_path
    store.reset_for_tests()


def _chunk(report_id: int, idx: int, text: str) -> dict:
    return {
        "id": f"{report_id}:{idx}",
        "text": text,
        "embedding_text": text,
        "metadata": {
            "report_id": report_id,
            "chunk_index": idx,
            "chunk_total": 1,
            "variety": "RB",
            "varieties": "RB",
            "publish_date": "2026-09-07",
            "report_type": "日报",
            "title": "t",
            "source": "s",
            "direction": "",
            "text_truncated": False,
        },
    }


def test_upsert_query_roundtrip(chroma_tmp):
    c1 = _chunk(1, 0, "螺纹钢供需宽松,成本支撑下移。")
    store.upsert_chunks([c1], [_fake_vec(c1["text"])])
    hits = store.query(_fake_vec(c1["text"]), top_k=3)
    assert len(hits) == 1
    assert hits[0]["id"] == "1:0"
    assert hits[0]["metadata"]["report_id"] == 1
    assert hits[0]["score"] > 0.99


def test_upsert_idempotent_same_id(chroma_tmp):
    c1 = _chunk(1, 0, "文本甲")
    store.upsert_chunks([c1], [_fake_vec("文本甲")])
    store.upsert_chunks([c1], [_fake_vec("文本甲")])  # 同 id 二次写入
    assert store.count() == 1


def test_delete_by_report(chroma_tmp):
    chunks = [_chunk(1, 0, "甲"), _chunk(1, 1, "乙"), _chunk(2, 0, "丙")]
    store.upsert_chunks(chunks, [_fake_vec(c["text"]) for c in chunks])
    assert store.count() == 3
    store.delete_by_report(1)
    assert store.count() == 1
    remaining = store.query(_fake_vec("丙"), top_k=5)
    assert [h["id"] for h in remaining] == ["2:0"]


def test_query_exclude_report_id(chroma_tmp):
    chunks = [_chunk(1, 0, "甲"), _chunk(2, 0, "乙")]
    store.upsert_chunks(chunks, [_fake_vec(c["text"]) for c in chunks])
    hits = store.query(_fake_vec("甲"), top_k=5, exclude_report_id=1)
    assert [h["id"] for h in hits] == ["2:0"]
