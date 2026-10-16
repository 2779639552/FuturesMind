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


def test_index_reports_batch_equals_single(rag_env):
    """批量索引 = 逐篇索引的等价语义(2026-09-22 新增)。"""
    rows = [_row(1, "RB", text="甲" * 900), _row(2, "CU", text="乙" * 700), _row(3, "SC", text="")]
    n = service.index_reports_batch(rows)
    # 单篇等价:分别算一遍 chunk 总数
    expect = 0
    for r in rows:
        expect += len(service.build_chunks(r))
    assert n == expect, f"批量写入 {n} ≠ 单篇合计 {expect}"
    status = service.index_status()
    assert status["reports"] == 2, "空文本行不应产生报告条目"
    # 可检索性:三篇里非空的两篇都能被召回
    for rid, kw in ((1, "甲"), (2, "乙")):
        hits = service.retrieve_hits(kw, top_k=3)
        assert any(int(h["metadata"]["report_id"]) == rid for h in hits), f"批量写入后 rid={rid} 不可检索"


def test_index_reports_batch_idempotent(rag_env):
    """同一批重复 upsert 幂等:chunk 数不变(2026-09-22)。"""
    rows = [_row(1, "RB", text="甲" * 600)]
    a = service.index_reports_batch(rows)
    b = service.index_reports_batch(rows)  # 覆盖写
    assert a == b == service.index_status()["chunks"]
    assert service.index_status()["reports"] == 1


def test_backfill_all_batch_skip_and_force(rag_env):
    """backfill_all 批量路径语义(2026-09-22):
    已索引跳过 / 新行写入 / force 重建。"""
    r1, r2 = _row(1, "RB"), _row(2, "CU")
    st1 = service.backfill_all([r1, r2])
    assert (st1["done"], st1["skipped"], st1["failed"]) == (2, 0, 0)
    chunks = service.index_status()["chunks"]

    st2 = service.backfill_all([r1, r2])  # 已索引 → 全跳过,不重复写
    assert (st2["done"], st2["skipped"]) == (0, 2)
    assert service.index_status()["chunks"] == chunks

    st3 = service.backfill_all([r1], force=True)  # force 删净重写
    assert (st3["done"], st3["skipped"]) == (1, 0)
    assert service.index_status()["reports"] == 2  # r1 重写 + 旧 r2 仍在
    assert service.index_status()["chunks"] == chunks  # 删净重写 → chunk 总数不变

    st4 = service.backfill_all([r1, _row(3, "SC")])  # 混合:1 跳过 + 1 新写
    assert (st4["done"], st4["skipped"]) == (1, 1)
    assert service.index_status()["reports"] == 3  # r1, r2, r3


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


def test_dedupe_backfill_keeps_global_score_order(rag_env, monkeypatch):
    """回归(2026-09-17):公平分配第二轮补进来的"同篇第 2 块"分数可能高于第一轮
    某篇的第 1 块。补块后必须重排回全局 score 降序 —— 否则返回结果形如
    [高, 低, 中],而下游 format_context 是按顺序编号 [1][2][3] 注入 prompt 的,
    顺序错乱等于把高分块降格。

    用桩喂确定的分数(不依赖 embedding 巧合):rid1 的 B 块(0.90)高于 rid2 的
    A 块(0.50) —— 正是真机失败快照里的形状。"""
    def _hit(rid: int, score: float, tag: str) -> dict:
        return {
            "id": f"{rid}-{tag}",
            "text": f"块{tag}",
            "score": score,
            "metadata": {
                "report_id": rid,
                "variety": "RB",
                "varieties": "RB",
                "publish_date": "2026-09-07",  # 同日期 → 时效加权因子相同,不动相对序
                "title": f"报告{rid}",
            },
        }

    fake = [_hit(1, 0.95, "A"), _hit(2, 0.50, "B"), _hit(1, 0.90, "C")]
    monkeypatch.setattr(service.store, "query", lambda *a, **k: [dict(h) for h in fake])
    monkeypatch.setattr(service, "_merge_recent_reports", lambda hits, *a, **k: list(hits))

    hits = service.retrieve_hits("螺纹钢 库存去化", top_k=3, variety="RB")
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True), f"去重后必须全局降序,实际 {scores}"
    # rid1 的两块(含第二轮补的那块)必须排在低分的 rid2 之前
    assert [h["metadata"]["report_id"] for h in hits] == [1, 1, 2]


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
    """批量路径下单份坏行不中断回填(2026-09-22 语义平移):
    index_reports_batch 整体失败 → 降级逐篇 index_report → 只有坏行计 failed。
    (原实现打桩 index_report;批量路径的失败缝在 build_chunks,故打桩点平移。)
    """
    rows = [_row(1, "RB"), _row(2, "CU")]

    real_chunk = service.build_chunks

    def flaky(row):
        if int(row["id"]) == 1:
            raise RuntimeError("embed boom")
        return real_chunk(row)

    monkeypatch.setattr(service, "build_chunks", flaky)
    stats = service.backfill_all(rows)
    assert stats["failed"] == 1 and stats["done"] == 1


def test_recent_provider_matches_legacy(rag_env):
    """保底通道 DB 注入路径与旧 get_metadatas 路径结果等价(2026-09-22)。

    索引 2 篇(RB 近期 + CU 老),带 variety 检索时保底通道把近期 RB 强制入池;
    provider 路径与旧路径应召回同一 report_id 集合(去掉主检索自身命中的部分)。
    """
    r1 = _row(1, "RB")
    r1["publish_date"] = "2026-09-20"
    r2 = _row(2, "CU")
    r2["publish_date"] = "2026-01-05"
    service.index_reports_batch([r1, r2])

    provider = {
        1: {"publish_date": "2026-09-20", "varieties": "RB", "report_type": "日报"},
        2: {"publish_date": "2026-01-05", "varieties": "CU", "report_type": "日报"},
    }

    hits_legacy = service.retrieve_hits("螺纹钢 库存去化", top_k=4, variety="RB")
    hits_prov = service.retrieve_hits(
        "螺纹钢 库存去化", top_k=4, variety="RB",
        recent_meta_provider=lambda: dict(provider),
    )

    def rids(hits):
        return {int(h["metadata"]["report_id"]) for h in hits}

    assert rids(hits_legacy) == rids(hits_prov), (
        f"provider 路径与旧路径召回不一致: legacy={rids(hits_legacy)} prov={rids(hits_prov)}"
    )
    assert 1 in rids(hits_prov), "近期同品种研报应被保底通道强制召回"
    assert all(h["metadata"]["report_id"] == 1 for h in hits_prov), "variety=RB 时不应混入 CU"


def test_recent_provider_empty_falls_back(rag_env, monkeypatch):
    """provider 返回空 dict → 保底通道无候选,但不报错(service 层兜底)。"""
    service.index_report(_row(1, "RB"))
    hits = service.retrieve_hits(
        "螺纹钢 库存去化", top_k=4, variety="RB",
        recent_meta_provider=lambda: {},
    )
    assert isinstance(hits, list), "provider 空结果不应抛错"


def test_recent_merge_window_aware(rag_env, monkeypatch):
    """保底通道窗口感知(2026-09-22):指定日期范围时,候选只从窗口内选,
    旧区间研报不被"全局最新∪近3天"逻辑饿死。

    RAG_RECENT_REPORTS=2 制造"候选槽满":旧语义下 8 月研报(非近3天)进不了保底;
    带 date_from/date_to 后它成为窗口内候选,应被选入。
    """
    monkeypatch.setenv("RAG_RECENT_REPORTS", "2")
    monkeypatch.setenv("RAG_RECENT_DAYS", "3")

    r1 = _row(1, "TA", text="PTA 供需 库存 加工费 聚酯" * 30)
    r1["publish_date"] = "2026-08-05"
    r2 = _row(2, "TA", text="PTA 供需 库存 加工费 聚酯" * 30)
    r2["publish_date"] = "2026-09-10"
    r3 = _row(3, "TA", text="PTA 供需 库存 加工费 聚酯" * 30)
    r3["publish_date"] = "2026-09-21"
    service.index_reports_batch([r1, r2, r3])

    provider = lambda: {
        1: {"publish_date": "2026-08-05", "varieties": "TA", "report_type": "日报"},
        2: {"publish_date": "2026-09-10", "varieties": "TA", "report_type": "日报"},
        3: {"publish_date": "2026-09-21", "varieties": "TA", "report_type": "日报"},
    }
    qvec = embedding.encode(["PTA 供需"])[0]

    with_w = service._merge_recent_reports(
        [], qvec, 6, variety="TA", recent_meta_provider=provider,
        date_from="2026-08-01", date_to="2026-09-15",
    )
    without_w = service._merge_recent_reports(
        [], qvec, 6, variety="TA", recent_meta_provider=provider,
    )
    ids_w = {int(h["metadata"]["report_id"]) for h in with_w}
    ids_wo = {int(h["metadata"]["report_id"]) for h in without_w}

    assert 1 in ids_w, f"窗口感知后 8 月研报应进保底候选,实际 {ids_w}"
    assert 1 not in ids_wo, f"旧语义 8 月研报(非近3天)不该进保底,实际 {ids_wo}"
    assert 3 not in ids_w, f"窗口外 9.21 不应进入保底候选,实际 {ids_w}"

    # 端到端:指定 8.01-9.15 检索应能带回 8.05 研报
    hits = service.retrieve_hits(
        "PTA 供需", top_k=6, variety="TA",
        date_from="2026-08-01", date_to="2026-09-15",
        recent_meta_provider=provider,
    )
    final = {int(h["metadata"]["report_id"]) for h in hits}
    assert 1 in final, f"端到端指定旧区间应召回 8 月研报,实际 {final}"
    assert 3 not in final, "窗口外 9.21 不应出现在最终结果"


def test_recent_merge_window_aware(rag_env, monkeypatch):
    """保底通道窗口感知(2026-09-22):指定日期范围时,候选只从窗口内选,
    旧区间研报不被"全局最新∪近3天"逻辑饿死。

    RAG_RECENT_REPORTS=2 制造"候选槽满":旧语义下 8 月研报(非近3天)进不了保底;
    带 date_from/date_to 后它成为窗口内候选,应被选入。
    """
    monkeypatch.setenv("RAG_RECENT_REPORTS", "2")
    monkeypatch.setenv("RAG_RECENT_DAYS", "3")

    r1 = _row(1, "TA", text="PTA 供需 库存 加工费 聚酯" * 30)
    r1["publish_date"] = "2026-08-05"
    r2 = _row(2, "TA", text="PTA 供需 库存 加工费 聚酯" * 30)
    r2["publish_date"] = "2026-09-10"
    r3 = _row(3, "TA", text="PTA 供需 库存 加工费 聚酯" * 30)
    r3["publish_date"] = "2026-09-21"
    service.index_reports_batch([r1, r2, r3])

    provider = lambda: {
        1: {"publish_date": "2026-08-05", "varieties": "TA", "report_type": "日报"},
        2: {"publish_date": "2026-09-10", "varieties": "TA", "report_type": "日报"},
        3: {"publish_date": "2026-09-21", "varieties": "TA", "report_type": "日报"},
    }
    qvec = embedding.encode(["PTA 供需"])[0]

    with_w = service._merge_recent_reports(
        [], qvec, 6, variety="TA", recent_meta_provider=provider,
        date_from="2026-08-01", date_to="2026-09-15",
    )
    without_w = service._merge_recent_reports(
        [], qvec, 6, variety="TA", recent_meta_provider=provider,
    )
    ids_w = {int(h["metadata"]["report_id"]) for h in with_w}
    ids_wo = {int(h["metadata"]["report_id"]) for h in without_w}

    assert 1 in ids_w, f"窗口感知后 8 月研报应进保底候选,实际 {ids_w}"
    assert 1 not in ids_wo, f"旧语义 8 月研报(非近3天)不该进保底,实际 {ids_wo}"
    assert 3 not in ids_w, f"窗口外 9.21 不应进入保底候选,实际 {ids_w}"

    # 端到端:指定 8.01-9.15 检索应能带回 8.05 研报
    hits = service.retrieve_hits(
        "PTA 供需", top_k=6, variety="TA",
        date_from="2026-08-01", date_to="2026-09-15",
        recent_meta_provider=provider,
    )
    final = {int(h["metadata"]["report_id"]) for h in hits}
    assert 1 in final, f"端到端指定旧区间应召回 8 月研报,实际 {final}"
    assert 3 not in final, "窗口外 9.21 不应出现在最终结果"


# ── 2026-09-22 保底通道"今日提权"修复: 问"近日情况"时今天的日报要进 top_k ──
# 直接 mock store.get_by_reports 返回固定向量(OLD sim 高 0.6 / TODAY sim 低 0.4),
# 不依赖真实 embedding/切块变换, 精确验证提权系数。

def _mk_recent_records():
    from datetime import date, timedelta

    today = date.today().isoformat()
    old = (date.today() - timedelta(days=7)).isoformat()
    records = {
        "documents": ["老周报正文", "今日日报正文"],
        "metadatas": [
            {"report_id": 1, "publish_date": old, "varieties": "RB", "report_type": "周报", "chunk_index": 0},
            {"report_id": 2, "publish_date": today, "varieties": "RB", "report_type": "日报", "chunk_index": 0},
        ],
        "embeddings": [[0.6, 0, 0, 0, 0, 0, 0, 0], [0.4, 0, 0, 0, 0, 0, 0, 0]],
    }
    return records, today, old


def _mk_provider(today, old):
    return lambda: {
        1: {"publish_date": old, "varieties": "RB", "report_type": "周报"},
        2: {"publish_date": today, "varieties": "RB", "report_type": "日报"},
    }


def test_today_recent_boost_promotes_today_report(rag_env, monkeypatch):
    """默认 RAG_RECENT_BOOST=0.8: 今天 sim 低的日报被提权, score 反超老研报。"""
    records, today, old = _mk_recent_records()
    monkeypatch.setattr(store, "get_by_reports", lambda rids, include: records)
    monkeypatch.setenv("RAG_RECENT_BOOST", "0.8")
    qvec = [1.0, 0, 0, 0, 0, 0, 0, 0]
    hits = service._merge_recent_reports(
        [], qvec, 6, variety="RB", recent_meta_provider=_mk_provider(today, old)
    )
    scores = {int(h["metadata"]["report_id"]): round(h["score"], 3) for h in hits}
    assert scores[2] > scores[1], f"今天日报应提权反超, 实际 {scores}"


def test_today_recent_boost_off_keeps_old_behavior(rag_env, monkeypatch):
    """RAG_RECENT_BOOST=0 关闭提权: 保持旧行为, 老研报 sim 高居前。"""
    records, today, old = _mk_recent_records()
    monkeypatch.setattr(store, "get_by_reports", lambda rids, include: records)
    monkeypatch.setenv("RAG_RECENT_BOOST", "0")
    qvec = [1.0, 0, 0, 0, 0, 0, 0, 0]
    hits = service._merge_recent_reports(
        [], qvec, 6, variety="RB", recent_meta_provider=_mk_provider(today, old)
    )
    scores = {int(h["metadata"]["report_id"]): round(h["score"], 3) for h in hits}
    assert scores[1] > scores[2], f"关闭提权后老研报应居前, 实际 {scores}"


def test_today_recent_boost_only_today_not_recent3d(rag_env, monkeypatch):
    """提权只作用于"今天"; 近 3 天但非今天(昨天)不加成。"""
    from datetime import date, timedelta

    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    records = {
        "documents": ["昨日日报", "今日日报"],
        "metadatas": [
            {"report_id": 3, "publish_date": yesterday, "varieties": "RB", "report_type": "日报", "chunk_index": 0},
            {"report_id": 2, "publish_date": today, "varieties": "RB", "report_type": "日报", "chunk_index": 0},
        ],
        "embeddings": [[0.45, 0, 0, 0, 0, 0, 0, 0], [0.4, 0, 0, 0, 0, 0, 0, 0]],
    }
    monkeypatch.setattr(store, "get_by_reports", lambda rids, include: records)
    monkeypatch.setenv("RAG_RECENT_BOOST", "0.8")
    qvec = [1.0, 0, 0, 0, 0, 0, 0, 0]
    provider = lambda: {
        3: {"publish_date": yesterday, "varieties": "RB", "report_type": "日报"},
        2: {"publish_date": today, "varieties": "RB", "report_type": "日报"},
    }
    hits = service._merge_recent_reports([], qvec, 6, variety="RB", recent_meta_provider=provider)
    scores = {int(h["metadata"]["report_id"]): round(h["score"], 3) for h in hits}
    # 今天提权: 0.4*1.8=0.72 > 昨天原分 0.45(不加成)
    assert scores[2] == round(0.4 * 1.8, 3), f"今天应提权到 0.72, 实际 {scores}"
    assert scores[3] == 0.45, f"昨天不应加成, 实际 {scores}"


# ── 2026-09-22 保底通道"今日提权"修复: 问"近日情况"时今天的日报要进 top_k ──
# 直接 mock store.get_by_reports 返回固定向量(OLD sim 高 0.6 / TODAY sim 低 0.4),
# 不依赖真实 embedding/切块变换, 精确验证提权系数。

def _mk_recent_records():
    from datetime import date, timedelta

    today = date.today().isoformat()
    old = (date.today() - timedelta(days=7)).isoformat()
    records = {
        "documents": ["老周报正文", "今日日报正文"],
        "metadatas": [
            {"report_id": 1, "publish_date": old, "varieties": "RB", "report_type": "周报", "chunk_index": 0},
            {"report_id": 2, "publish_date": today, "varieties": "RB", "report_type": "日报", "chunk_index": 0},
        ],
        "embeddings": [[0.6, 0, 0, 0, 0, 0, 0, 0], [0.4, 0, 0, 0, 0, 0, 0, 0]],
    }
    return records, today, old


def _mk_provider(today, old):
    return lambda: {
        1: {"publish_date": old, "varieties": "RB", "report_type": "周报"},
        2: {"publish_date": today, "varieties": "RB", "report_type": "日报"},
    }


def test_today_recent_boost_promotes_today_report(rag_env, monkeypatch):
    """默认 RAG_RECENT_BOOST=0.8: 今天 sim 低的日报被提权, score 反超老研报。"""
    records, today, old = _mk_recent_records()
    monkeypatch.setattr(store, "get_by_reports", lambda rids, include: records)
    monkeypatch.setenv("RAG_RECENT_BOOST", "0.8")
    qvec = [1.0, 0, 0, 0, 0, 0, 0, 0]
    hits = service._merge_recent_reports(
        [], qvec, 6, variety="RB", recent_meta_provider=_mk_provider(today, old)
    )
    scores = {int(h["metadata"]["report_id"]): round(h["score"], 3) for h in hits}
    assert scores[2] > scores[1], f"今天日报应提权反超, 实际 {scores}"


def test_today_recent_boost_off_keeps_old_behavior(rag_env, monkeypatch):
    """RAG_RECENT_BOOST=0 关闭提权: 保持旧行为, 老研报 sim 高居前。"""
    records, today, old = _mk_recent_records()
    monkeypatch.setattr(store, "get_by_reports", lambda rids, include: records)
    monkeypatch.setenv("RAG_RECENT_BOOST", "0")
    qvec = [1.0, 0, 0, 0, 0, 0, 0, 0]
    hits = service._merge_recent_reports(
        [], qvec, 6, variety="RB", recent_meta_provider=_mk_provider(today, old)
    )
    scores = {int(h["metadata"]["report_id"]): round(h["score"], 3) for h in hits}
    assert scores[1] > scores[2], f"关闭提权后老研报应居前, 实际 {scores}"


def test_today_recent_boost_only_today_not_recent3d(rag_env, monkeypatch):
    """提权只作用于"今天"; 近 3 天但非今天(昨天)不加成。"""
    from datetime import date, timedelta

    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    records = {
        "documents": ["昨日日报", "今日日报"],
        "metadatas": [
            {"report_id": 3, "publish_date": yesterday, "varieties": "RB", "report_type": "日报", "chunk_index": 0},
            {"report_id": 2, "publish_date": today, "varieties": "RB", "report_type": "日报", "chunk_index": 0},
        ],
        "embeddings": [[0.45, 0, 0, 0, 0, 0, 0, 0], [0.4, 0, 0, 0, 0, 0, 0, 0]],
    }
    monkeypatch.setattr(store, "get_by_reports", lambda rids, include: records)
    monkeypatch.setenv("RAG_RECENT_BOOST", "0.8")
    qvec = [1.0, 0, 0, 0, 0, 0, 0, 0]
    provider = lambda: {
        3: {"publish_date": yesterday, "varieties": "RB", "report_type": "日报"},
        2: {"publish_date": today, "varieties": "RB", "report_type": "日报"},
    }
    hits = service._merge_recent_reports([], qvec, 6, variety="RB", recent_meta_provider=provider)
    scores = {int(h["metadata"]["report_id"]): round(h["score"], 3) for h in hits}
    # 今天提权: 0.4*1.8=0.72 > 昨天原分 0.45(不加成)
    assert scores[2] == round(0.4 * 1.8, 3), f"今天应提权到 0.72, 实际 {scores}"
    assert scores[3] == 0.45, f"昨天不应加成, 实际 {scores}"


# ── 2026-09-22 时间分层采样: 宽日期窗口保证全时段覆盖(整月复盘) ──

def _mk_dated_hits():
    """7 个不同日期的候选, score 降序。"""
    out = []
    dates = ["2026-09-05", "2026-09-06", "2026-09-08", "2026-09-10",
             "2026-09-15", "2026-09-20", "2026-09-28"]
    for i, d in enumerate(dates):
        out.append({
            "id": f"{100 + i}:x", "text": f"块{i}",
            "metadata": {"report_id": 100 + i, "publish_date": d, "varieties": "TA"},
            "score": 1.0 - i * 0.05,
        })
    return out


def test_temporal_sampling_spreads_across_window(rag_env):
    """宽窗口(9 月整月)分层: 覆盖多个时点, 而非只取最像的月底几天。"""
    hits = _mk_dated_hits()
    picked = service._temporal_sampling(hits, "2026-09-01", "2026-09-30", top_k=4)
    dates = sorted((h["metadata"]["publish_date"]) for h in picked)
    assert len(picked) == 4, f"应取 4 段各 1 块, 实际 {len(picked)}"
    # 覆盖要散布全月(首段应含 9 月上旬, 末段 9 月下旬)
    assert any(d < "2026-09-10" for d in dates), f"早段应被覆盖, 实际 {dates}"
    assert any(d > "2026-09-25" for d in dates), f"晚段应被覆盖, 实际 {dates}"


def test_temporal_sampling_skips_narrow_window(rag_env):
    """窄窗口(跨度 <4 天)不触发分层, 原样返回。"""
    hits = _mk_dated_hits()
    picked = service._temporal_sampling(hits, "2026-09-05", "2026-09-06", top_k=4)
    assert picked == hits, "窄窗口不应分层采样"


def test_temporal_sampling_bucket_takes_top_score(rag_env):
    """每段取该段最高分块(score 降序输入, 首个命中即最高)。"""
    hits = _mk_dated_hits()
    picked = service._temporal_sampling(hits, "2026-09-01", "2026-09-30", top_k=2)
    # top_k=2 → 2 段: 09-01~09-15 与 09-16~09-30
    first_half = [h for h in picked if h["metadata"]["publish_date"] <= "2026-09-15"]
    second_half = [h for h in picked if h["metadata"]["publish_date"] > "2026-09-15"]
    assert len(first_half) == 1 and len(second_half) == 1
    # 前段最高分块是 09-05(score 1.0); 后段最高分是 09-20(score 0.7)
    assert first_half[0]["metadata"]["publish_date"] == "2026-09-05"
    assert second_half[0]["metadata"]["publish_date"] == "2026-09-20"


# ── 2026-09-22 时间分层采样: 宽日期窗口保证全时段覆盖(整月复盘) ──

def _mk_dated_hits():
    """7 个不同日期的候选, score 降序。"""
    out = []
    dates = ["2026-09-05", "2026-09-06", "2026-09-08", "2026-09-10",
             "2026-09-15", "2026-09-20", "2026-09-28"]
    for i, d in enumerate(dates):
        out.append({
            "id": f"{100 + i}:x", "text": f"块{i}",
            "metadata": {"report_id": 100 + i, "publish_date": d, "varieties": "TA"},
            "score": 1.0 - i * 0.05,
        })
    return out


def test_temporal_sampling_spreads_across_window(rag_env):
    """宽窗口(9 月整月)分层: 覆盖多个时点, 而非只取最像的月底几天。"""
    hits = _mk_dated_hits()
    picked = service._temporal_sampling(hits, "2026-09-01", "2026-09-30", top_k=4)
    dates = sorted((h["metadata"]["publish_date"]) for h in picked)
    assert len(picked) == 4, f"应取 4 段各 1 块, 实际 {len(picked)}"
    # 覆盖要散布全月(首段应含 9 月上旬, 末段 9 月下旬)
    assert any(d < "2026-09-10" for d in dates), f"早段应被覆盖, 实际 {dates}"
    assert any(d > "2026-09-25" for d in dates), f"晚段应被覆盖, 实际 {dates}"


def test_temporal_sampling_skips_narrow_window(rag_env):
    """窄窗口(跨度 <4 天)不触发分层, 原样返回。"""
    hits = _mk_dated_hits()
    picked = service._temporal_sampling(hits, "2026-09-05", "2026-09-06", top_k=4)
    assert picked == hits, "窄窗口不应分层采样"


def test_temporal_sampling_bucket_takes_top_score(rag_env):
    """每段取该段最高分块(score 降序输入, 首个命中即最高)。"""
    hits = _mk_dated_hits()
    picked = service._temporal_sampling(hits, "2026-09-01", "2026-09-30", top_k=2)
    # top_k=2 → 2 段: 09-01~09-15 与 09-16~09-30
    first_half = [h for h in picked if h["metadata"]["publish_date"] <= "2026-09-15"]
    second_half = [h for h in picked if h["metadata"]["publish_date"] > "2026-09-15"]
    assert len(first_half) == 1 and len(second_half) == 1
    # 前段最高分块是 09-05(score 1.0); 后段最高分是 09-20(score 0.7)
    assert first_half[0]["metadata"]["publish_date"] == "2026-09-05"
    assert second_half[0]["metadata"]["publish_date"] == "2026-09-20"
