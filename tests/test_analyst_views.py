"""「按数据轴分发」护栏（2026-10-19）。

【它守什么】
  用户的要求是：**先不融合**，把技术面/基本面的量化结论与技能分给不同分析师，
  到后面步骤再统一总结。本模块 `analyst_views.py` 做的就是这件事，因此本文件守的是
  「分得干净、合得受控」：

  1. ★ **不许越界**：技术视图不得出现基差/库存/利润轴的内容；基本面视图不得出现
     价格轴的内容。这比"两段都在"更难守 —— 只要有人图方便把完整 verdict 塞进去，
     两边的分工就名存实亡。
  2. ★ **不许静默丢票**：视图按 axis 过滤，若某张票的 axis 不在分配表里（如四象退回
     composite），两个视图都会收不到。必须显式报出来（与 10-19 哑票同类失败）。
  3. ★ **裁决层不写硬规则**：回测证明 18 组「谁赢」组合无一通过多重检验，
     所以冲突时只能给流程（区间/触发/失效/时间止损），不能写"某方优先"。
  4. ★ **预热必须真的等到算完**：`warm()` 若也走 8s 超时，实测会在算完前返回，
     等于没预热（首次分析拿不到任何块）。
  5. 取数失败/超时 → ""，绝不把分析拖挂。
"""

from __future__ import annotations

import time

import pytest

from tradingagents.dataflows import analyst_views as V


# ---------------------------------------------------------------------------
# 受控快照（不碰真实数据源）
# ---------------------------------------------------------------------------
def _snap(**over):
    s = {
        "code": "TA",
        "errs": {},
        "tech_snapshot": {
            "ok": True, "date": "2026-10-16", "latest": 6384.0,
            "series": {"date": ["2026-10-16"] * 162},
            "ma_state": "均线纠缠", "score": -2,
            "indicators": {
                "ma5": 6500.4, "ma10": 6523.8, "ma20": 6425.2, "ma60": 5964.88,
                "rsi14": 52.5, "macd_bar": -24.11, "mom20_pct": 2.14,
                "atr14": 197.63, "ann_vol_pct": 34.3,
                "boll_up": 6768.18, "boll_mid": 6425.2, "boll_low": 6082.22,
                "oi_latest": 1092245, "oi_chg5_pct": -2.38,
            },
        },
        "votes": [
            {"book": "鹿希武", "bias": "暂不操作", "axis": "price", "strength": 0.0,
             "confidence": 1.0, "strength_basis": "二值方向票"},
            # ★ 这一张的 strength_basis 原文含"库存"字样 —— 技术视图必须改写掉
            {"book": "多空逻辑(库存周期)", "bias": "做空", "axis": "price", "strength": 0.62,
             "confidence": 0.4,
             "strength_basis": "|价格5日变化%|/5; 库存变化>100% 记为小基数失真 → confidence 0.4"},
            {"book": "威科夫", "bias": "做空", "axis": "price", "strength": 1.0,
             "confidence": 1.0, "strength_basis": "|吸筹-派发|/2"},
            {"book": "多空逻辑(期限结构)", "bias": "偏多", "axis": "basis", "strength": 1.0,
             "confidence": 1.0, "strength_basis": "|跨月价差%|/3"},
            {"book": "多空逻辑(利润)", "bias": "做多", "axis": "profit", "strength": 0.527,
             "confidence": 1.0, "strength_basis": "|利润分位-0.5|/0.5"},
        ],
        "weighted": {
            "score": 0.35, "direction": "暂不操作", "raw_direction": "做多",
            "gated": True, "gates": ["double_break"],
            "axes": {
                "price": {"dir": -1.0, "conv": 1.0, "score": -1.0, "n": 2},
                "basis": {"dir": 1.0, "conv": 1.0, "score": 1.0, "n": 1},
                "inventory": {"dir": 1.0, "conv": 0.173, "score": 0.173, "n": 1},
                "profit": {"dir": 1.0, "conv": 0.527, "score": 0.527, "n": 1},
            },
        },
        "strategy": {"action": "单边做多", "reason": "三指标共振", "inv_grade": "低",
                     "prof_grade": "低", "basis_side": "贴水", "inv_mode": "双口径共振"},
        "vr": {"ok": True, "ratio": 27.88, "lots": 19590, "one_side_oi": 546122,
               "ck_pct": 0.33},
        "geometry": {"double_break": True},
    }
    s.update(over)
    return s


@pytest.fixture
def fake(monkeypatch):
    """把 _snapshot 打桩成受控快照，并清缓存。"""
    box = {"snap": _snap(), "calls": 0}

    def _fake_snapshot(symbol, *, blocking=False):
        box["calls"] += 1
        return box["snap"]

    monkeypatch.setattr(V, "_snapshot", _fake_snapshot)
    return box


# ---------------------------------------------------------------------------
# ① 技术视图：只含价格轴
# ---------------------------------------------------------------------------
def test_technical_view_only_price_axis(fake):
    t = V.technical_view("TA")
    assert "仅价格轴" in t
    assert "价格轴票" in t
    # 不该出现的东西（★ 必须查**票内容**：只查"基差轴"这类标题抓不到"把票全塞进来"
    #   这种回退 —— 第一版就是这么漏的，变异直接存活）
    for bad in ("三指标策略", "基差轴", "库存轴", "利润轴", "虚实盘比",
                "多空逻辑(期限结构)", "多空逻辑(利润)", "利润分位"):
        assert bad not in t, f"技术视图泄漏了 {bad}"


def test_technical_view_strips_inventory_wording(fake):
    """★ 「库存周期」票的说明原文含"库存变化>100%"，技术视图必须改写。

    否则技术分析师看到"库存"两个字就会拿库存解释 K 线 —— 正是本项目长期要避免的越界。
    """
    t = V.technical_view("TA")
    # ★ 只检查**数据区**（最后那句边界声明本来就写着"库存属于基本面分析师，勿据库存
    #   解释 K 线"—— 那是约束语，不是数据泄漏）。
    body = t.split("⚠ 本块")[0]
    assert "库存" not in body, "技术视图的数据区仍出现「库存」字样，会把技术分析师引向产业数据"
    assert "价格 5 日涨跌" in body, "改写后的说明丢了（应明确方向由价格决定）"


def test_technical_view_has_key_reading(fake):
    t = V.technical_view("TA")
    for k in ("MA20", "RSI14", "ATR14", "BOLL", "持仓量", "双线同破"):
        assert k in t, f"技术视图缺关键读数 {k}"


# ---------------------------------------------------------------------------
# ② 基本面视图：只含三轴
# ---------------------------------------------------------------------------
def test_fundamental_view_only_fund_axes(fake):
    f = V.fundamental_view("TA")
    assert "基差/库存/利润三轴" in f
    assert "三指标策略" in f and "单边做多" in f
    assert "虚实盘比" in f
    # ★ 同样要查票内容（鹿希武/威科夫/墨菲都是价格轴的票）
    for bad in ("MA5", "RSI14", "价格轴", "双线同破", "鹿希武", "威科夫", "墨菲", "均线"):
        assert bad not in f, f"基本面视图泄漏了 {bad}"


def test_fundamental_view_warns_about_weekly_frequency(fake):
    """库存是周频，必须写明不能支撑 ≤3 日判断（否则模型会拿周频数据做日线结论）。"""
    f = V.fundamental_view("TA")
    assert "周频" in f and "3 个交易日" in f


# ---------------------------------------------------------------------------
# ③ 未归属票必须外显
# ---------------------------------------------------------------------------
def test_orphan_vote_is_surfaced_in_both_views(fake):
    """★ 四象若退回 composite（两个视图都不认），必须显式报出，不能静默消失。"""
    s = _snap()
    s["votes"].append({"book": "多空逻辑(四象)", "bias": "单边做多", "axis": "composite",
                       "strength": 0.17, "confidence": 1.0, "strength_basis": "min(|估值|,|驱动|)/3"})
    fake["snap"] = s
    for fn in (V.technical_view, V.fundamental_view):
        t = fn("TA")
        assert "归不到任何轴" in t, f"{fn.__name__} 把 composite 票静默丢了"
        assert "四象" in t


def test_no_orphan_line_when_all_axes_known(fake):
    assert "归不到任何轴" not in V.technical_view("TA")
    assert "归不到任何轴" not in V.fundamental_view("TA")


# ---------------------------------------------------------------------------
# ④ 裁决视图
# ---------------------------------------------------------------------------
def test_reconcile_detects_conflict_and_forbids_hard_rule(fake):
    r = V.reconcile_view("TA")
    assert "方向相反" in r
    assert "不得直接选边" in r
    assert "Bonferroni" in r
    # ★ 绝不能出现"某方优先"的硬规则
    assert "基本面优先" not in r or "禁止写「基本面优先」" in r
    assert "区间 + 触发条件 + 失效条件 + 时间止损" in r


def test_reconcile_gate_is_visible(fake):
    r = V.reconcile_view("TA")
    assert "硬闸门已触发" in r and "double_break" in r


def test_reconcile_agreement_branch(monkeypatch):
    s = _snap()
    s["weighted"] = {
        "axes": {
            "price": {"dir": 1.0, "conv": 1.0, "score": 1.0, "n": 2},
            "basis": {"dir": 1.0, "conv": 1.0, "score": 1.0, "n": 1},
            "inventory": {"dir": 1.0, "conv": 1.0, "score": 1.0, "n": 1},
            "profit": {"dir": 1.0, "conv": 1.0, "score": 1.0, "n": 1},
        },
        "gated": False, "gates": [],
    }
    monkeypatch.setattr(V, "_snapshot", lambda *a, **k: s)
    r = V.reconcile_view("TA")
    assert "同向" in r
    assert "失效条件" in r
    assert "不得直接选边" not in r


def test_reconcile_missing_dimension_rule(fake):
    """缺失处理必须显式：不得用「中性」顶替缺失。"""
    assert "不得用「中性」顶替缺失" in V.reconcile_view("TA")


def test_reconcile_uses_per_axis_average_not_vote_count(fake):
    """★ 两方比较必须用"每轴平均"，否则票多的一方天然占优（净票的老毛病）。

    构造：技术 1 张满空、基本面 3 张满多 → 若按总和，基本面 3 vs -1 仍偏多；
    但若基本面被拆成 3 张弱票，总和会压过技术。这里直接断言口径说明存在更实在。
    """
    r = V.reconcile_view("TA")
    assert "三轴均值" in r, "没有用每轴平均口径 —— 票多的一方会天然占优"


# ---------------------------------------------------------------------------
# ⑤ 降级与缓存
# ---------------------------------------------------------------------------
def test_all_views_empty_when_no_data(monkeypatch):
    monkeypatch.setattr(V, "_snapshot", lambda *a, **k: {})
    assert V.technical_view("TA") == ""
    assert V.fundamental_view("TA") == ""
    assert V.reconcile_view("TA") == ""


def test_snapshot_is_cached(monkeypatch):
    """同品种两次调用只算一次（否则每个分析师各算一遍，冷启动 9.6s × 3）。"""
    box = {"n": 0}

    def _compute(sym):
        box["n"] += 1
        return _snap()

    monkeypatch.setattr(V, "_compute", _compute)
    V.clear_cache()
    V._snapshot("TA")
    V._snapshot("TA")
    assert box["n"] == 1


def test_snapshot_timeout_degrades(monkeypatch):
    """算不完就必须在 WAIT_SECONDS 附近返回 ""，而不是把分析卡死。"""
    monkeypatch.setattr(V, "WAIT_SECONDS", 0.3)

    def _slow(sym):
        time.sleep(2.0)
        return _snap()

    monkeypatch.setattr(V, "_compute", _slow)
    V.clear_cache()
    t0 = time.time()
    snap = V._snapshot("TA")
    dt = time.time() - t0
    assert snap == {}, "超时后应返回空快照"
    assert dt < 1.2, f"超时保护失效：等了 {dt:.1f}s"


def test_warm_blocks_until_ready(monkeypatch):
    """★ 预热必须真的等到算完 —— 第一版 warm 也走 8s 超时，实测返回时缓存为空。"""
    monkeypatch.setattr(V, "WAIT_SECONDS", 0.2)

    def _slow(sym):
        time.sleep(0.6)
        return _snap()

    monkeypatch.setattr(V, "_compute", _slow)
    V.clear_cache()
    V.warm("TA")
    assert V._SNAP.get("TA"), "warm 返回后缓存仍为空 —— 等于没预热"
    assert V.technical_view("TA") != ""


# ---------------------------------------------------------------------------
# ⑥ 接线：三个节点各拿到自己那份
# ---------------------------------------------------------------------------
def _capture_analyst_prompt(monkeypatch, factory_name, state):
    from tradingagents.agents.analysts import commodity_analysts as mod

    captured = {}

    def _fake_loop(llm, tools, initial_messages, **kw):
        captured["prompt"] = initial_messages[0].content
        return "REPORT"

    monkeypatch.setattr(mod, "_run_tool_loop", _fake_loop)
    monkeypatch.setattr(mod, "variety_db_context", lambda s: "")
    monkeypatch.setattr(mod, "fundamental_methodology_context", lambda s: "")
    monkeypatch.setattr(mod, "technical_methodology_context", lambda s: "")
    monkeypatch.setattr(mod, "macro_methodology_context", lambda s: "")
    monkeypatch.setattr(mod, "gtja_context", lambda s: "")
    getattr(mod, factory_name)(llm=object())(state)
    return captured["prompt"]


_STATE = {"trade_date": "2026-10-19", "company_of_interest": "TA",
          "messages": [], "past_context": ""}


def test_technical_analyst_gets_tech_view_only(monkeypatch):
    from tradingagents.agents.analysts import commodity_analysts as mod

    monkeypatch.setattr(mod, "technical_view", lambda s: "【技术视图·TEST】价格轴")
    monkeypatch.setattr(mod, "fundamental_view", lambda s: "【基本面视图·TEST】三轴")
    p = _capture_analyst_prompt(monkeypatch, "create_commodity_technical_analyst", dict(_STATE))
    assert "【技术视图·TEST】" in p
    assert "基本面视图" not in p, "技术分析师拿到了基本面视图 —— 分工失效"


def test_fundamental_analyst_gets_fund_view_only(monkeypatch):
    from tradingagents.agents.analysts import commodity_analysts as mod

    monkeypatch.setattr(mod, "technical_view", lambda s: "【技术视图·TEST】价格轴")
    monkeypatch.setattr(mod, "fundamental_view", lambda s: "【基本面视图·TEST】三轴")
    p = _capture_analyst_prompt(monkeypatch, "create_commodity_fundamental_analyst", dict(_STATE))
    assert "【基本面视图·TEST】" in p
    assert "技术视图" not in p, "基本面分析师拿到了技术视图 —— 分工失效"


def test_macro_analyst_gets_neither(monkeypatch):
    """宏观/情绪节点不参与"技术 vs 基本面"的分工，两块都不给。"""
    from tradingagents.agents.analysts import commodity_analysts as mod

    monkeypatch.setattr(mod, "technical_view", lambda s: "【技术视图·TEST】")
    monkeypatch.setattr(mod, "fundamental_view", lambda s: "【基本面视图·TEST】")
    p = _capture_analyst_prompt(monkeypatch, "create_commodity_macro_analyst", dict(_STATE))
    assert "技术视图" not in p and "基本面视图" not in p


def test_research_manager_gets_reconcile_block(monkeypatch):
    from tradingagents.agents.managers import research_manager as rm

    captured = {}

    def _fake_invoke(*args, **kw):
        captured["prompt"] = args[2] if len(args) > 2 else kw.get("prompt", "")
        return "PLAN"

    monkeypatch.setattr(rm, "invoke_structured_or_freetext", _fake_invoke)
    monkeypatch.setattr(rm, "reconcile_view", lambda s: "【两方对照·TEST】技术 -1.00 / 基本面 +0.57")
    monkeypatch.setattr(rm, "bind_structured", lambda llm, model, who: object())

    state = {
        "company_of_interest": "TA",
        "instrument_context": "TA PTA",
        "investment_debate_state": {"history": "bull/bear...", "bull_history": "",
                                    "bear_history": "", "count": 1},
    }
    rm.create_research_manager(llm=object())(state)
    p = captured["prompt"]
    assert "【两方对照·TEST】" in p
    # 对照块必须在辩论历史**之前**（先看到结构化对照，再读辩论原文）
    assert p.index("【两方对照·TEST】") < p.index("Debate History")
    assert p.count("\n---\n") == 3, "对照块前缺分隔线（会和 instrument_context 粘在一起）"


def test_research_manager_falls_back_without_reconcile(monkeypatch):
    """数据缺失时不能给空块占位（否则模型会以为"没有分歧"）。"""
    from tradingagents.agents.managers import research_manager as rm

    captured = {}

    def _fake_invoke(*args, **kw):
        captured["prompt"] = args[2] if len(args) > 2 else kw.get("prompt", "")
        return "PLAN"

    monkeypatch.setattr(rm, "invoke_structured_or_freetext", _fake_invoke)
    monkeypatch.setattr(rm, "reconcile_view", lambda s: "")
    monkeypatch.setattr(rm, "bind_structured", lambda llm, model, who: object())

    state = {
        "company_of_interest": "TA",
        "instrument_context": "TA PTA",
        "investment_debate_state": {"history": "h", "bull_history": "",
                                    "bear_history": "", "count": 1},
    }
    rm.create_research_manager(llm=object())(state)
    p = captured["prompt"]
    assert "两类结论对照" not in p
    # ★ 判据用"分隔线计数"而不是"有没有 ---"：模板本身就有两处分隔线，
    #   写成 `"---" not in ...` 会在空块时也失败（第一版就是这么错的）。
    assert p.count("\n---\n") == 2, f"空块仍多插了分隔线：{p.count(chr(10) + '---' + chr(10))}"

def test_spread_verdict_is_not_flagged_as_orphan(fake):
    """★ 四象给「套利/对冲(适合反套)」时不是数据缺口，而是**结构性结论**。

    【为什么要专门守这条】RB 实测时第一版把这张票误报成"归不到任何轴"：
      它 axis=composite、bias=套利/对冲（非方向票），但 `axis_parts` 已把两个分量
      分别送进基差轴与库存轴。误报会让分析师以为"有数据缺失"，其实是"别做单边"。
    """
    s = _snap()
    s["votes"].append({"book": "多空逻辑", "bias": "套利/对冲(适合反套, 结合仓单有效期)",
                       "axis": "composite", "strength": 0.3, "confidence": 1.0,
                       "strength_basis": "min(|估值|,|驱动|)/3",
                       "axis_parts": {"basis": 0.6, "inventory": -0.4}})
    fake["snap"] = s
    for fn in (V.technical_view, V.fundamental_view):
        assert "归不到任何轴" not in fn("TA"), f"{fn.__name__} 把非方向票误报成数据缺口"
    f = V.fundamental_view("TA")
    assert "结构性结论" in f and "套利/对冲" in f


def test_direction_vote_with_composite_axis_is_still_flagged(fake):
    """但**方向票**若轴归不上，仍必须报（那才是真缺口）。"""
    s = _snap()
    s["votes"].append({"book": "多空逻辑(四象)", "bias": "单边做多", "axis": "composite",
                       "strength": 0.3, "confidence": 1.0})
    fake["snap"] = s
    assert "归不到任何轴" in V.technical_view("TA")


def test_reconcile_counts_are_labelled_not_fraction(fake):
    """★ 计数不能写成"计入 4/2 张"——分子是轴条目、分母是票数，两个量纲。

    四象一张票拆成两个轴条目，会天然让分子 > 分母；写成 x/y 会被读成"只有一半票计入"。
    """
    r = V.reconcile_view("TA")
    assert "张票 / " in r and "个轴条目" in r
    assert "/2 张" not in r and "/4 张" not in r
