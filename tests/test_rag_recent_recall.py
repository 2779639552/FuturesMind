# -*- coding: utf-8 -*-
"""测试:研报检索"新研报召回不到"的修复(2026-09-17)。

真机现象:用户问"PTA 近日情况",09-16 的华泰聚酯日报(id=579)完全不出现。

四处叠加缺陷,各自独立可测:
  A. 保底通道整条崩 —— _merge_recent_reports 用一个 try 罩住所有报告,
     任一报告取 embeddings 抛错(chroma "Error finding id")就 return hits,
     本该兜底的"最新研报保底召回"整个失效。
  B. 保底通道门槛误杀 —— variety 已指定时 latest 已按品种精确筛过,
     却仍卡 0.65 语义门槛,把"同品种但换了说法的新日报"挡在外面。
  C. 候选只取"全库最新 N 篇" —— 语义是"最新"而非"最近",当天日报一多,
     前一天(09-16)的研报一篇都进不了候选池,门槛设多低都没用。
  D. 时效加权被乘两遍 + 排名单位是"块" —— 一篇长报告的多块会霸占候选槽位,
     20 个槽只覆盖 7 篇,把短日报挤出结果。

另有 E:health_check —— chunks/reports 只反映 sqlite 块数,证明不了"检得出来";
只有"正文重编码 → 检索 → 命中自己"才是端到端判据。
"""

from datetime import date, timedelta

import pytest

from tradingagents.rag import service


def _fake_getter(vec, fail_rids=()):
    """构造 get_by_report 替身:返回单块报告,指定 rid 抛错。"""

    def _get(rid, include):
        if rid in fail_rids:
            raise RuntimeError("Error executing plan: Internal error: Error finding id")
        return {
            "documents": [f"doc{rid}"],
            "metadatas": [{"report_id": rid, "publish_date": "2026-09-16"}],
            "embeddings": [list(vec)],
        }

    return _get


def _stub_bulk(monkeypatch, getter, fail_bulk=False):
    """打桩 store.get_by_reports:默认逐篇拼装;fail_bulk=True 时整体抛错。"""

    def _bulk(rids, include):
        if fail_bulk:
            raise RuntimeError("Error executing plan: Internal error: Error finding id")
        out = {"documents": [], "metadatas": [], "embeddings": []}
        for rid in rids:
            one = getter(rid, include)
            for key in out:
                val = one.get(key)
                out[key].extend([] if val is None else list(val))
        return out

    monkeypatch.setattr(service.store, "get_by_reports", _bulk)


def _stub_store(monkeypatch, raw, metas=None, vec=None):
    _v = vec or [0.1, 0.2]
    monkeypatch.setattr(service.embedding, "encode", lambda texts: [_v])
    monkeypatch.setattr(service.store, "query", lambda *a, **k: list(raw))
    monkeypatch.setattr(service.store, "get_metadatas", lambda: list(metas or []))
    monkeypatch.setattr(service.store, "get_by_report", _fake_getter(_v))
    _stub_bulk(monkeypatch, _fake_getter(_v))


def _metas(*rids, variety="TA"):
    out = []
    for rid in rids:
        m = {"report_id": rid, "publish_date": "2026-09-16"}
        if variety:
            m["variety"] = variety
            m["varieties"] = f"PX,{variety}"
        out.append(m)
    return out


# ── A. 保底通道不得因局部失败整条报废 ────────────────────────────────────
def test_recent_channel_survives_single_report_failure(monkeypatch):
    """批量取向量整体失败(库残缺)→ 退化逐篇;逐篇里坏了一篇也不影响其余。"""
    monkeypatch.setattr(service.store, "get_metadatas", lambda: _metas(11, 12, 13))
    getter = _fake_getter([1.0, 0.0], fail_rids={12})
    monkeypatch.setattr(service.store, "get_by_report", getter)
    _stub_bulk(monkeypatch, getter, fail_bulk=True)

    hits = service._merge_recent_reports([], [1.0, 0.0], 5)

    got = {h["metadata"]["report_id"] for h in hits}
    assert got == {11, 13}, f"坏了一篇不应影响其余新研报入池,实际={got}"


def test_recent_channel_uses_bulk_fetch(monkeypatch):
    """批量取成功时不应再逐篇开库(窗口打开后候选可达数十篇,逐篇会拖慢 QA)。"""
    monkeypatch.setattr(service.store, "get_metadatas", lambda: _metas(11, 13))
    getter = _fake_getter([1.0, 0.0])
    per_call = []
    monkeypatch.setattr(
        service.store,
        "get_by_report",
        lambda rid, include: (per_call.append(rid), getter(rid, include))[1],
    )
    _stub_bulk(monkeypatch, getter)

    hits = service._merge_recent_reports([], [1.0, 0.0], 5)

    assert len(hits) == 2
    assert per_call == [], "批量成功后不应再逐篇调 get_by_report"


# ── B. variety 指定时不应再卡语义门槛 ───────────────────────────────────
def test_no_sim_gate_when_variety_specified(monkeypatch):
    """同品种新日报即使与问句余弦为 0,也应入池(品种已精确筛过)。"""
    monkeypatch.setattr(service.store, "get_metadatas", lambda: _metas(21))
    _stub_bulk(monkeypatch, _fake_getter([0.0, 1.0]))  # 与 qvec 正交

    hits = service._merge_recent_reports([], [1.0, 0.0], 5, variety="TA")
    assert [h["metadata"]["report_id"] for h in hits] == [21]


def test_sim_gate_still_applies_without_variety(monkeypatch):
    """未指定品种时门槛仍在:窗口内与问题无关的报告必须被挡掉。"""
    monkeypatch.setattr(service.store, "get_metadatas", lambda: _metas(21, variety=None))
    _stub_bulk(monkeypatch, _fake_getter([0.0, 1.0]))

    hits = service._merge_recent_reports([], [1.0, 0.0], 5)
    assert hits == [], "无品种时正交向量不该入池"


# ── C. 候选要覆盖"最近 N 天",而不是只有"全库最新几篇" ──────────────────
def test_recent_window_includes_previous_days(monkeypatch):
    """前一天发布的研报必须在候选内 —— 否则问"近日情况"永远差一天。"""
    today = date.today()
    metas = [
        {"report_id": i, "publish_date": today.isoformat()} for i in range(1, 11)
    ]
    metas.append({"report_id": 99, "publish_date": (today - timedelta(days=1)).isoformat()})
    metas.append({"report_id": 50, "publish_date": (today - timedelta(days=30)).isoformat()})
    monkeypatch.setattr(service.store, "get_metadatas", lambda: metas)

    seen: list[int] = []

    def _bulk(rids, include):
        seen.extend(rids)
        return {"documents": [], "metadatas": [], "embeddings": []}

    monkeypatch.setattr(service.store, "get_by_reports", _bulk)

    service._merge_recent_reports([], [1.0, 0.0], 5)

    assert 99 in seen, "前一天(09-16 对应物)的研报必须进候选"
    assert 50 not in seen, "一个月前的研报不该只因'窗口'入选"
    assert len(seen) <= 8 * 8, "窗口必须封顶,不能退化成全库扫描"


def test_recent_window_can_be_disabled(monkeypatch):
    """RAG_RECENT_DAYS=0 时退回旧行为(只取最新 n_reports 篇),便于回滚。"""
    today = date.today()
    metas = [{"report_id": i, "publish_date": today.isoformat()} for i in range(1, 11)]
    metas.append({"report_id": 99, "publish_date": (today - timedelta(days=1)).isoformat()})
    monkeypatch.setattr(service.store, "get_metadatas", lambda: metas)
    monkeypatch.setenv("RAG_RECENT_DAYS", "0")
    seen: list[int] = []

    def _bulk(rids, include):
        seen.extend(rids)
        return {"documents": [], "metadatas": [], "embeddings": []}

    monkeypatch.setattr(service.store, "get_by_reports", _bulk)

    service._merge_recent_reports([], [1.0, 0.0], 5)

    assert 99 not in seen, "关掉窗口后应只取最新 N 篇"


# ── D. 时效加权只做一次 ────────────────────────────────────────────────
def test_recency_boost_applied_exactly_once(monkeypatch):
    calls = []
    orig = service._apply_recency_boost

    def spy(hits):
        calls.append(len(hits))
        return orig(hits)

    monkeypatch.setattr(service, "_apply_recency_boost", spy)
    _stub_store(monkeypatch, [])

    service.retrieve_hits("PTA近日情况", top_k=3)
    assert len(calls) == 1, f"时效加权应全池只乘一次,实际调用 {len(calls)} 次"


def test_oversample_factor_raised():
    """候选池倍数 3→5:目标篇原始余弦排名 21,而 3 倍只到 18,差一点就被切掉。"""
    assert service._OVERSAMPLE_FACTOR >= 5


# ── E. 按篇归并:块多者不得通吃 ──────────────────────────────────────────
def test_many_chunks_report_does_not_hog_slots(monkeypatch):
    """一篇 5 块的长报告只应占 1 个引用位(第一轮),不得把其他研报挤出去。

    真机对照:问"PTA近日情况"时 raw top20 被 rid=485/44/22 三篇用掉 13 个槽,
    20 个槽只覆盖 7 篇;09-16 的短日报(579)因此排不进结果。
    """
    raw = [
        {"id": f"1:{i}", "text": "long", "metadata": {"report_id": 1}, "score": 0.9 - i * 0.01}
        for i in range(5)
    ] + [
        {"id": f"{rid}:0", "text": "short", "metadata": {"report_id": rid}, "score": 0.85}
        for rid in (2, 3, 4)
    ]
    _stub_store(monkeypatch, raw)

    hits = service.retrieve_hits("PTA近日情况", top_k=4)

    rids = [h["metadata"]["report_id"] for h in hits]
    assert rids == [1, 2, 3, 4], f"每篇应先占 1 位,实际 {rids}"
    assert len(set(rids)) == 4, "top_k=4 应覆盖 4 篇,而不是 2 篇各 2 块"


def test_second_pass_fills_budget_when_reports_run_out(monkeypatch):
    """候选篇数不足 top_k 时,回头用同篇第 2 块补满预算(长报告的其他方面不丢)。"""
    raw = [
        {"id": "1:0", "text": "a", "metadata": {"report_id": 1}, "score": 0.9},
        {"id": "1:1", "text": "b", "metadata": {"report_id": 1}, "score": 0.8},
        {"id": "2:0", "text": "c", "metadata": {"report_id": 2}, "score": 0.7},
    ]
    _stub_store(monkeypatch, raw)

    hits = service.retrieve_hits("PTA近日情况", top_k=4)

    # 预算确实填满:3 个候选全部入选,同篇第 2 块(1:1)补了进来
    assert sorted(h["id"] for h in hits) == ["1:0", "1:1", "2:0"]
    # 【2026-09-17 修正断言】顺序必须是**全局 score 降序**,而不是"第一轮先、
    # 第二轮后"的分配序。原断言期待 [0.9, 0.7, 0.8](分配序),但那与同日
    # test_retrieve_dedupes_same_report 的降序契约矛盾,且是用户可见缺陷:
    # 前端按 score 展示百分比、format_context 按位置编号注入 prompt,
    # [90%, 70%, 80%] 会让高分块被排到低分块之后。分配轮次只决定"选哪几块"。
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True), f"应为全局降序,实际 {scores}"
    assert [h["id"] for h in hits] == ["1:0", "1:1", "2:0"]


def test_max_per_report_still_caps_second_pass(monkeypatch):
    """第二轮不得突破 max_per_report 上限。"""
    raw = [
        {"id": f"1:{i}", "text": "a", "metadata": {"report_id": 1}, "score": 0.9 - i * 0.01}
        for i in range(5)
    ]
    _stub_store(monkeypatch, raw)

    hits = service.retrieve_hits("x", top_k=5, max_per_report=2)
    assert len(hits) == 2, f"候选只有一篇时最多给 max_per_report 块,实际 {len(hits)}"


# ── F. 自召回体检 ──────────────────────────────────────────────────────
def test_health_check_counts_only_self_recall(monkeypatch):
    monkeypatch.setattr(service.store, "get_ids", lambda: ["1:0", "2:0", "3:0"])
    monkeypatch.setattr(
        service.store, "get_by_report", lambda rid, include: {"documents": [f"doc{rid}"]}
    )
    monkeypatch.setattr(service.embedding, "encode", lambda texts: [[0.5, 0.5]])
    # 只有 report_id=1 的向量真在索引里,2/3 检索时被 1 顶掉
    monkeypatch.setattr(
        service.store,
        "query",
        lambda vec, top_k=1: [{"id": "1:0", "text": "", "metadata": {}, "score": 0.99}],
    )

    r = service.health_check(sample=3)

    assert r["sampled"] == 3
    assert r["hit"] == 1
    assert r["recall"] == pytest.approx(1 / 3, abs=1e-3)
    assert set(r["failed"]) == {2, 3}


def test_health_check_survives_store_errors(monkeypatch):
    """体检本身不许把状态接口带崩(单篇异常计未命中,异常类型记进 error 字段)。"""

    def boom(_rid, include):
        raise RuntimeError("Error finding id")

    monkeypatch.setattr(service.store, "get_ids", lambda: ["1:0", "2:0"])
    monkeypatch.setattr(service.store, "get_by_report", boom)
    monkeypatch.setattr(service.embedding, "encode", lambda texts: [[0.5, 0.5]])

    r = service.health_check(sample=2)
    assert r["hit"] == 0 and r["recall"] == 0.0
    assert r["error"], "应把异常类型记进 error 字段,便于定位"


def test_health_check_empty_index(monkeypatch):
    monkeypatch.setattr(service.store, "get_ids", lambda: [])
    r = service.health_check(sample=3)
    assert r["sampled"] == 0 and r["recall"] is None
