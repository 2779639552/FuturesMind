# -*- coding: utf-8 -*-
"""L1b 护栏：按轴聚合的加权总信号。

【守什么】
  ① 四象必须拆成 basis/inventory 两轴，**不许出现 composite 伪轴**（否则等于凭空多一根轴）
  ② 轴内 = 方向 × 证据量（只取方向会把单票轴恒置 ±1，L1a 实测踩过）
  ③ 缺失轴**不参与分母**（否则"没数据"被当成中性票稀释方向）
  ④ 与 net 并列：weighted 的存在不得改变 net / verdict
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import quant_verdict as qv  # noqa: E402
import vote_aggregate as vag  # noqa: E402


def _bars(n=90, trend=1.0):
    out, px = [], 1000.0
    for i in range(n):
        px *= (1 + trend * 0.004) * (1 + (0.004 if i % 7 in (0, 1) else -0.003))
        out.append({"date": f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                    "open": px * 0.995, "high": px * 1.006, "low": px * 0.994,
                    "close": px, "volume": 10000 + i * 10, "open_interest": 50000 + i * 20})
    return out


def _payload(trend=1.0):
    return {"variety": "TA", "name": "PTA", "unit": "元/吨", "bars": _bars(trend=trend),
            "basis_rate_series": [0.02 + 0.001 * i for i in range(40)],
            "inv_series": [2000000 - 20000 * i for i in range(40)], "inv_chg_pct": -6.0,
            "term": {"ok": True, "rows": [
                {"contract": "TA2610", "month": "2610", "price": 6800},
                {"contract": "TA2709", "month": "2709", "price": 6000}]},
            "profit_series": [100.0 + 5 * i for i in range(30)], "profit_label": "加工利润"}


def _w(**kw):
    d = qv.build_verdict(_payload(**kw))
    assert d.get("ok"), d.get("error")
    return d["stages"]["direction"]


# ══════════════ ① 拆轴 ══════════════
def test_no_composite_axis_in_output():
    """四象必须被拆开：输出里不允许出现 composite 伪轴，且不许有票归不到轴。"""
    w = _w()["weighted"]
    assert "composite" not in w["axes"], "四象没拆轴——composite 会让轴数凭空变多"
    assert set(w["axes"]) <= set(vag.AXES)
    # ★ 若四象被标 composite 却没给 axis_parts, 它会**整张消失**且总分只是"少了点分量",
    #   不报错。必须显式断言"没有未归属的票"。
    assert w.get("unattributed") == [], f"有方向票归不到任何轴：{w.get('unattributed')}"


def test_vd_split_into_two_axes():
    """四象的估值→basis、驱动→inventory；basis 轴应同时收到「四象估值」与「期限结构」两张票。"""
    w = _w()["weighted"]
    assert w["axes"]["basis"]["n"] >= 2, "basis 轴只收到一张票 —— 四象的估值分量没进来"
    assert w["axes"]["inventory"]["n"] >= 1, "inventory 轴空 —— 四象的驱动分量没进来"


def test_vd_parts_are_signed_strength():
    """axis_parts 必须是带符号的强度（±m），不是方向也不是原始分。"""
    votes = _w()["votes"]
    vd = next(v for v in votes if v.get("book") == "多空逻辑")
    parts = vd["axis_parts"]
    for ax in ("basis", "inventory"):
        assert -1.0 <= parts[ax] <= 1.0
        assert abs(parts[ax]) == pytest.approx(min(1.0, abs(parts[ax])))


# ══════════════ ② 轴内：方向 × 证据量 ══════════════
def test_single_vote_axis_keeps_magnitude():
    """★ 只有一张弱票的轴，得分必须等于它的强度，而不是被归一成 ±1。

    这是 L1a 验证时踩过的坑：只用 Σ(s·m)/Σ(m) 会把 0.17 报成 1.00。
    """
    w = vag.aggregate([{"book": "X", "bias": "做多", "axis": "profit",
                        "strength": 0.17, "confidence": 1.0}])
    assert w["axes"]["profit"]["dir"] == pytest.approx(1.0)
    assert w["axes"]["profit"]["conv"] == pytest.approx(0.17)
    assert w["axes"]["profit"]["score"] == pytest.approx(0.17)


def test_opposite_votes_cancel():
    """同轴多空抵消 → dir 归零（这正是"指标矛盾"应有的表达，而不是另开一个"无方向"桶）。"""
    w = vag.aggregate([
        {"book": "A", "bias": "做多", "axis": "price", "strength": 1.0, "confidence": 1.0},
        {"book": "B", "bias": "做空", "axis": "price", "strength": 1.0, "confidence": 1.0},
    ])
    assert w["axes"]["price"]["dir"] == pytest.approx(0.0)
    assert w["score"] == pytest.approx(0.0)
    assert w["direction"] == "观望"


def test_confidence_scales_contribution():
    """可信度打折必须真的削弱该票（库存小基数失真的意义所在）。"""
    full = vag.aggregate([{"book": "A", "bias": "做多", "axis": "inventory",
                           "strength": 1.0, "confidence": 1.0}])["score"]
    half = vag.aggregate([{"book": "A", "bias": "做多", "axis": "inventory",
                           "strength": 1.0, "confidence": 0.4}])["score"]
    assert half < full
    assert half == pytest.approx(0.4)


# ══════════════ ③ 缺失轴不稀释 ══════════════
def test_missing_axes_do_not_dilute():
    """只有一条轴有证据时，total 必须等于该轴得分，而不是被除以 4。"""
    w = vag.aggregate([{"book": "A", "bias": "做多", "axis": "price",
                        "strength": 1.0, "confidence": 1.0}])
    assert w["active_axes"] == ["price"]
    assert w["score"] == pytest.approx(1.0)
    assert w["weights"] == {"price": pytest.approx(1.0)}


def test_no_votes_gives_zero():
    w = vag.aggregate([])
    assert w["score"] == 0.0 and w["direction"] == "观望" and w["active_axes"] == []


# ══════════════ ④ 阈值与边界 ══════════════
def test_threshold_direction():
    just_below = vag.aggregate([{"book": "A", "bias": "做多", "axis": "price",
                                 "strength": vag.DIR_THRESHOLD - 0.01, "confidence": 1.0}])
    just_above = vag.aggregate([{"book": "A", "bias": "做多", "axis": "price",
                                 "strength": vag.DIR_THRESHOLD + 0.01, "confidence": 1.0}])
    assert just_below["direction"] == "观望"
    assert just_above["direction"] == "做多"


def test_weights_are_overridable():
    votes = [{"book": "A", "bias": "做多", "axis": "price", "strength": 1.0, "confidence": 1.0},
             {"book": "B", "bias": "做空", "axis": "basis", "strength": 1.0, "confidence": 1.0}]
    eq = vag.aggregate(votes)
    assert eq["score"] == pytest.approx(0.0)
    tilt = vag.aggregate(votes, weights={"price": 3.0, "basis": 1.0})
    assert tilt["score"] == pytest.approx(0.5), "权重覆盖没生效"


def test_deterministic():
    votes = _w()["votes"]
    a, b = vag.aggregate(votes), vag.aggregate(votes)
    assert a == b


# ══════════════ ⑤ 与 net 并列，不影响 verdict ══════════════
def test_weighted_does_not_change_verdict():
    sd = _w()
    assert sd["weighted"]["score"] != 0          # 确有加权分
    assert sd["vote_audit"]["net"] == 2          # 净票不变
    assert sd["verdict"] == "做多"


def test_verdict_independent_of_weighted(monkeypatch):
    """★ 把 weighted 换成任意离谱值，verdict 必须纹丝不动。

    【为什么不能用"构造一个两者不一致的品种"来测】那依赖数据，测不稳。
      直接把 aggregate 打桩成 mock 才是确定的：只要有人把 weighted 接进 verdict，
      这条立刻红。
    """
    base = _w()["verdict"]
    monkeypatch.setattr(qv.vag, "aggregate", lambda *a, **k: {
        "score": -0.99, "direction": "做空", "axes": {}, "active_axes": [],
        "weights": {}, "unattributed": [], "note": "mock"})
    assert _w()["verdict"] == base, "weighted 被接进了 verdict —— L1b 契约是并列, 不是替代"

# ══════════════ ⑥ 硬闸门（C：把闸门搬进加权分） ══════════════
def test_gate_suppresses_direction_but_keeps_score():
    """闸门是否决权：压 direction，**不动 score**（证据分布要照实呈现）。"""
    votes = [{"book": "A", "bias": "做多", "axis": "price", "strength": 1.0, "confidence": 1.0}]
    free = vag.aggregate(votes)
    gated = vag.aggregate(votes, gates=["double_break"])
    assert gated["score"] == free["score"], "闸门不该改证据分"
    assert gated["raw_direction"] == "做多", "原始方向应保留, 否则看不出被拦之前是什么"
    assert gated["direction"] == "暂不操作"
    assert gated["gated"] is True and gated["gates"] == ["double_break"]


def test_no_gate_direction_equals_raw():
    votes = [{"book": "A", "bias": "做空", "axis": "basis", "strength": 1.0, "confidence": 1.0}]
    w = vag.aggregate(votes)
    assert w["gated"] is False and w["gates"] == []
    assert w["direction"] == w["raw_direction"] == "做空"


def test_empty_gate_list_is_not_gated():
    """空列表/None 不能被当成"有闸门"——否则全线都会变成暂不操作。"""
    votes = [{"book": "A", "bias": "做多", "axis": "price", "strength": 1.0, "confidence": 1.0}]
    assert vag.aggregate(votes, gates=[])["direction"] == "做多"
    assert vag.aggregate(votes, gates=None)["direction"] == "做多"


def test_quant_verdict_feeds_gate_into_weighted(monkeypatch):
    """★ 端到端：把几何强制成"双线同破"，weighted 必须与 verdict 同时被拦。

    【这条守的是什么】上一轮实盘发现 TA 净票被拦成"暂不操作"、weighted 却仍说"做多"——
      两套信号在闸门触发时给出相反结论。现在闸门在 aggregate 之前算一次、两边共用，
      这条测试钉住"不许再退回去"。
    """
    real = qv.qm.analyze_trend_geometry

    def forced(bars, n=3):
        g = dict(real(bars, n))
        g["ok"] = True
        g["double_break"] = True
        g.setdefault("direction", "上升")
        return g

    monkeypatch.setattr(qv.qm, "analyze_trend_geometry", forced)
    sd = _w()
    assert sd["verdict"] == "暂不操作"
    assert sd["weighted"]["gated"] is True
    assert sd["weighted"]["gates"] == ["double_break"]
    assert sd["weighted"]["direction"] == "暂不操作", "闸门没传到 weighted —— 两套信号会打架"
    assert sd["weighted"]["raw_direction"] != "暂不操作", "原始方向应保留, 便于看出被拦之前的意图"


def test_quant_verdict_without_gate_keeps_weighted_direction(monkeypatch):
    """没有闸门时，weighted.direction 必须等于 raw_direction（别无条件压成暂不操作）。"""
    real = qv.qm.analyze_trend_geometry

    def forced(bars, n=3):
        g = dict(real(bars, n))
        g["ok"] = True
        g["double_break"] = False
        g.setdefault("direction", "上升")
        return g

    monkeypatch.setattr(qv.qm, "analyze_trend_geometry", forced)
    w = _w()["weighted"]
    assert w["gated"] is False
    assert w["direction"] == w["raw_direction"]
