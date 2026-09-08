"""RAG service 编排测试:embedding 打桩为确定性向量,Chroma 用 tmp_path。"""

import collections
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingagents.rag import embedding, service, store  # noqa: E402


def _fake_encode(texts: list[str]) -> list[list[float]]:
    import hashlib

    out = []
    for text in texts:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        vec = [((digest[i % len(digest)] / 255.0) - 0.5) for i in range(8)]
        norm = sum(v * v for v in vec) ** 0.5 or 1.0
        out.append([v / norm for v in vec])
    return out


@pytest.fixture()
def rag_env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_CHROMA_DIR", str(tmp_path / "chroma"))
    monkeypatch.setattr(embedding, "encode", _fake_encode)
    store.reset_for_tests()
    yield tmp_path
    store.reset_for_tests()


def _row(report_id: int, variety: str, varieties: str = "", text: str | None = None) -> dict:
    return {
        "id": report_id,
        "title": f"报告{report_id}",
        "variety": variety,
        "varieties": varieties or variety,
        "publish_date": "2026-09-07",
        "report_type": "日报",
        "source": "测试",
        "direction": "",
        "extracted_text": (
            f"{variety}供需平衡表收紧,库存去化。库存去化支撑价格。库存去化。" * 3
            if text is None
            else text
        ),
    }


def test_index_report_and_status(rag_env):
    n = service.index_report(_row(1, "RB", text="甲" * 900))
    assert n > 1
    status = service.index_status()
    assert status["chunks"] == n
    assert status["reports"] == 1


def test_index_empty_text_noop(rag_env):
    assert service.index_report(_row(1, "RB", text="")) == 0
    assert service.index_status()["chunks"] == 0


def test_retrieve_variety_filter(rag_env):
    service.index_report(_row(1, "RB"))
    service.index_report(_row(2, "CU"))
    hits = service.retrieve_hits("螺纹钢 库存去化", top_k=5, variety="RB")
    assert hits, "超采样+后过滤后应仍有命中"
    assert all(
        h["metadata"]["variety"] == "RB" or "RB" in (h["metadata"]["varieties"] or "").split(",")
        for h in hits
    )


def test_retrieve_report_type_filter(rag_env):
    service.index_report(_row(1, "RB"))
    row = _row(2, "RB")
    row["report_type"] = "周报"
    service.index_report(row)
    hits = service.retrieve_hits("螺纹钢 库存", top_k=5, report_type="周报")
    assert hits
    assert all(h["metadata"]["report_type"] == "周报" for h in hits)


def test_exclude_report_id(rag_env):
    service.index_report(_row(1, "RB"))
    service.index_report(_row(2, "RB"))
    hits = service.retrieve_hits("库存去化", top_k=5, exclude_report_id=1)
    assert hits
    assert all(h["metadata"]["report_id"] != 1 for h in hits)


def test_retrieve_dedupes_same_report(rag_env):
    """回归(2026-09-08 用户反馈):同一篇研报多块同时进前 k 时应去重,
    每篇最多保留 max_per_report(默认 2)个最高分片段 —— 只留 1 块会把长报告
    的其他方面(如宏观段)全挡在外面。"""
    long_text = f"{chr(0x7532)*400}\n\n{chr(0x7532)*400}\n\n{chr(0x7532)*400}"  # 3 块
    service.index_report(_row(1, "RB", text=long_text))
    service.index_report(_row(2, "RB"))
    service.index_report(_row(3, "CU"))
    hits = service.retrieve_hits("螺纹钢 库存去化", top_k=3, variety="RB")
    report_ids = [h["metadata"]["report_id"] for h in hits]
    counts = collections.Counter(report_ids)
    assert all(c <= 2 for c in counts.values()), "每篇研报最多保留 2 条引用"
    assert len(hits) >= 2
    # 去重后按 score 降序
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)
    # max_per_report=1 恢复"每篇一条"语义
    hits_one = service.retrieve_hits(
        "螺纹钢 库存去化", top_k=5, variety="RB", max_per_report=1
    )
    one_ids = [h["metadata"]["report_id"] for h in hits_one]
    assert len(one_ids) == len(set(one_ids))
    # 关闭去重则保持旧行为(可取到同篇全部块)
    hits_raw = service.retrieve_hits("螺纹钢 库存去化", top_k=9, variety="RB", dedupe_reports=False)
    assert len(hits_raw) >= len(hits)


def test_empty_question(rag_env):
    service.index_report(_row(1, "RB"))
    assert service.retrieve_hits("   ") == []


def test_format_context_and_empty(rag_env):
    service.index_report(_row(1, "RB"))
    hits = service.retrieve_hits("库存去化", top_k=2)
    text = service.format_context(hits)
    assert text.startswith("【历史研报参考片段】")
    assert "[1]" in text and "报告1" in text
    assert service.format_context([]) == ""


def test_context_for_variety_returns_block(rag_env):
    service.index_report(_row(1, "RB"))
    text = service.context_for_variety("螺纹钢 库存去化 观点", variety="RB")
    assert text.startswith("【历史研报参考片段】")


def test_context_for_variety_swallows_errors(monkeypatch, tmp_path):
    # 检索层炸掉时不允许影响主流程:返回空串
    monkeypatch.setenv("RAG_CHROMA_DIR", str(tmp_path / "chroma"))
    store.reset_for_tests()

    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(service, "retrieve_hits", _boom)
    assert service.context_for_variety("任意问题") == ""


def test_backfill_idempotent(rag_env):
    rows = [_row(1, "RB"), _row(2, "CU"), _row(3, "RB")]
    stats1 = service.backfill_all(rows)
    assert stats1["done"] == 3 and stats1["failed"] == 0
    stats2 = service.backfill_all(rows)
    assert stats2["done"] == 0 and stats2["skipped"] == 3
    # force 全量覆盖,向量数不翻倍
    stats3 = service.backfill_all(rows, force=True)
    assert stats3["done"] == 3
    status = service.index_status()
    assert status["reports"] == 3
    assert status["chunks"] == sum(service.index_report(r) for r in rows)


def test_backfill_force_removes_stale_chunks(rag_env):
    """force 重建须先删净旧 chunk:过滤规则变化后旧 id 集合可能消失,只 upsert 会残留。"""
    service.index_report(_row(1, "RB", text="甲" * 900))  # 3 块
    assert service.index_status()["chunks"] == 3
    # 新行只有 1 块:force 重建后旧的两块必须被清掉
    stats = service.backfill_all([_row(1, "RB", text="乙" * 300)], force=True)
    assert stats["done"] == 1
    assert service.index_status()["chunks"] == 1


def test_backfill_single_failure_continues(rag_env, monkeypatch):
    rows = [_row(1, "RB"), _row(2, "CU")]

    real_index = service.index_report
    calls = []

    def flaky(row):
        calls.append(row["id"])
        if row["id"] == 1:
            raise RuntimeError("embed boom")
        return real_index(row)

    monkeypatch.setattr(service, "index_report", flaky)
    stats = service.backfill_all(rows)
    assert stats["failed"] == 1 and stats["done"] == 1
