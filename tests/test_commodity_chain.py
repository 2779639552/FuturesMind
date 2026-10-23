"""产业链传导链护栏（2026-10-22）。

【这份数据的第一价值不是"多给信息"，是"划清哪里我们没有数"】
实测报告里出现过「石脑油裂解价差走阔、聚酯开工率回升至 82%」——这两个环节本系统
**根本没有数据源**。最危险的不是缺数据，是模型不知道缺数据。

所以本文件守的不是"链条写得对不对"（那是人工复核的事），而是**结构完整性**：
  1. 21 个活跃品种一个都不能漏（漏了等于该品种"没有链"，查漏规则会误报）；
  2. `external` / `none` 必须写清「缺什么数据 + 可去哪取」——这是留档清单能变成
     "数据源接入 TODO" 的前提，缺了这两项清单就只是抱怨；
  3. `active` / `library` 不许带 pending（有数还写"待接入"会误导排期）；
  4. gaps() 只收 external/none，且按影响程度排序；
  5. 生成报告必须覆盖全部待改进环节（漏一条就少一条 TODO）。
"""
from __future__ import annotations

import re

from tradingagents.dataflows import commodity_chain as C


# ---------------------------------------------------------------------------
# 1. 覆盖完整性
# ---------------------------------------------------------------------------
def test_all_active_varieties_have_chain():
    missing = [c for c in C._ACTIVE_POOL if not C.chain_of(c)]
    assert not missing, f"活跃品种缺链条: {missing}"


def test_chain_has_both_directions_or_explicit_note():
    """原油这种一次能源可以没有商品上游，但必须有一条显式的供给侧条目。"""
    for code in C._ACTIVE_POOL:
        ch = C.chain_of(code)
        assert ch.get("upstream") or ch.get("downstream"), f"{code} 上下游都空"


def test_coverage_values_are_legal():
    for code in C._ACTIVE_POOL:
        for n in C.nodes(code):
            assert n.get("coverage") in C._COVERAGE, f"{code}:{n['node']} coverage={n.get('coverage')} 非法"


# ---------------------------------------------------------------------------
# 2. 留档的前提：缺数据的环节必须写清「缺什么」「去哪取」
# ---------------------------------------------------------------------------
def test_missing_nodes_must_declare_need_and_hint():
    bad = []
    for code in C._ACTIVE_POOL:
        for n in C.missing_nodes(code):
            p = n.get("pending") or {}
            if not p.get("need") or not p.get("hint"):
                bad.append(f"{code}:{n['node']}")
    assert not bad, f"缺数据环节没写清 need/hint（清单无法变成 TODO）: {bad}"


def test_available_nodes_must_not_carry_pending():
    bad = []
    for code in C._ACTIVE_POOL:
        for n in C.nodes(code):
            if n.get("coverage") in (C.ACTIVE, C.LIBRARY) and n.get("pending"):
                bad.append(f"{code}:{n['node']}")
    assert not bad, f"已有数据的环节不该标「待接入」（会误导排期）: {bad}"


def test_pending_impact_is_legal():
    for code in C._ACTIVE_POOL:
        for n in C.missing_nodes(code):
            assert (n.get("pending") or {}).get("impact") in ("high", "medium", "low"), \
                f"{code}:{n['node']} impact 非法"


# ---------------------------------------------------------------------------
# 3. 已知样例：TA 的链必须断在石脑油与聚酯
# ---------------------------------------------------------------------------
def test_ta_chain_breaks_where_expected():
    nodes = {n["node"]: n for n in C.nodes("TA")}
    assert nodes["PX"]["coverage"] == C.ACTIVE, "PX 是池内品种，应标 active"
    assert nodes["石脑油"]["coverage"] == C.NONE, "石脑油无数据源，必须标 none"
    assert nodes["聚酯（切片/瓶片/长丝）"]["coverage"] == C.NONE
    assert "石脑油裂解价差" in nodes["石脑油"]["pending"]["need"]


def test_m_chain_flags_external_market():
    """豆粕上游是 CBOT 大豆 —— 外盘要单独一类，不能混进"无源"。"""
    nodes = {n["node"]: n for n in C.nodes("M")}
    assert nodes["CBOT 大豆"]["coverage"] == C.EXTERNAL
    assert "CME" in nodes["CBOT 大豆"]["pending"]["hint"]


# ---------------------------------------------------------------------------
# 4. gaps(): 只收缺数据的、按影响排序
# ---------------------------------------------------------------------------
def test_gaps_only_missing_and_sorted():
    rows = C.gaps()
    assert rows, "待改进清单为空，说明 coverage 标注有问题"
    assert all(r["coverage"] in (C.EXTERNAL, C.NONE) for r in rows)
    order = {"high": 0, "medium": 1, "low": 2}
    keys = [order[r["impact"]] for r in rows]
    assert keys == sorted(keys), "没按影响程度排序，清单不好排期"


def test_gaps_filter_by_impact():
    high = C.gaps(impact="high")
    assert high and all(r["impact"] == "high" for r in high)
    assert len(high) <= len(C.gaps())


def test_stats_are_self_consistent():
    stat = C.coverage_stats()
    assert stat["total"] == sum(stat[k] for k in C._COVERAGE)
    assert stat["total"] == sum(len(C.nodes(c)) for c in C._ACTIVE_POOL)
    # 68% 左右没数据源是当前现实，把下限写死，避免以后误删条目导致"看起来变好了"
    missing = stat[C.EXTERNAL] + stat[C.NONE]
    assert missing >= 40, f"无数据源环节只剩 {missing} 个，明显被误删"


# ---------------------------------------------------------------------------
# 5. 留档报告
# ---------------------------------------------------------------------------
def test_report_covers_every_gap():
    md = C.report_markdown()
    assert "# 产业链待改进环节清单" in md
    assert "draft" in md, "报告必须标明草稿状态"
    for r in C.gaps():
        assert f"{r['variety']} {r['variety_name']}" in md
        assert r["node"] in md
    # 每个环节一行，行数不少于条目数
    rows = [ln for ln in md.splitlines() if ln.startswith("| high |") or ln.startswith("| medium |")
            or ln.startswith("| low |")]
    assert len(rows) >= len(C.gaps())


def test_report_groups_by_source():
    """按数据源归类 —— 同一家供应商的缺口放一起，谈接入时才好一次谈完。"""
    md = C.report_markdown()
    assert "按数据源归类" in md
    assert "隆众" in md or "SMM" in md


# ---------------------------------------------------------------------------
# 6. 边界
# ---------------------------------------------------------------------------
def test_unknown_variety_returns_none():
    assert C.chain_of("ZZ") is None
    assert C.nodes("ZZ") == []
    assert C.missing_nodes("ZZ") == []


def test_reviewed_flag_is_false_for_all():
    """全量都是 draft。复核过一条就改一条，这个断言会提醒我们别忘。"""
    not_draft = [c for c, ch in C.CHAINS.items() if ch.get("reviewed") is not False]
    assert not not_draft, f"以下品种被标为已复核，请确认真的逐条核过: {not_draft}"
