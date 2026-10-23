# -*- coding: utf-8 -*-
"""按「数据轴」聚合方向票 → 加权总信号（L1b）。

【与 net 的关系】**并列，不替代**。`net` 仍是票数净额（L0 口径）；本模块输出的
  `weighted` 是加权分。两者同时返回，用一段时间的实盘对照再决定要不要切换。

【为什么必须按轴聚合，而不是按票】
  票是"书的观点"，轴是"信息的来源"。同一根轴上的多张票（四象估值 + 期限结构都来自基差）
  是同一份证据被读了两遍。直接对票求和 = 票多的那一方赢。

【三层口径】
  1) 轴内：dir = Σ(s·m·c)/Σ(m·c)   只给方向（−1~+1），多空抵消后归零
           conv = min(1, Σ(m·c))   这条轴攒了多少证据（1 = 一张满强度票）
           score = dir × conv      ★ 必须相乘：只取 dir 会把幅度抹平，
                                   单票轴无论强度 0.17 还是 1.0 都会算成 ±1.00（L1a 实测踩过）
  2) 轴间：total = Σ(w·score) / Σ(w)，**分母只算有证据的轴**
           （缺失轴不参与，避免"没数据"被隐式当成中性票来稀释方向）
  3) 方向：|total| ≥ DIR_THRESHOLD 才给方向，否则"观望"

【权重的诚实边界】`DEFAULT_WEIGHTS` 一律 1.0，是**先验**不是结论。
  真实权重应由历史 IC 拟合（L2），且不能在同一样本上既选权重又报绩效。
"""
from __future__ import annotations

from typing import Any, Iterable

import vote_semantics as vs

AXES = ("price", "basis", "inventory", "profit")

# 先验等权。★ 不是拟合结果，改动必须留痕（见 L2 计划）
DEFAULT_WEIGHTS = {"price": 1.0, "basis": 1.0, "inventory": 1.0, "profit": 1.0}

# 有方向的最低加权分。0.15 ≈ "不足一张弱票的一半"，先验值，L2 再校准
DIR_THRESHOLD = 0.15


def _clip01(x: Any) -> float:
    try:
        return max(0.0, min(1.0, float(x)))
    except (TypeError, ValueError):
        return 0.0


def _signed_parts(v: dict) -> list[tuple[str, float, float]]:
    """把一张票展开成 [(轴, 带符号强度, 可信度)]。

    【为什么要支持 axis_parts】二维四象横跨基差与库存两根轴。若把它当成单票塞进某一根轴，
      另一根轴的信息就丢了；若塞进 "composite" 伪轴，又等于凭空多出一根轴。
      故由投票方在源头给出**两个分轴分量**（带符号的强度），聚合层只消费、不解释。
    """
    c = _clip01(v.get("confidence", 0.0))
    parts = v.get("axis_parts")
    if isinstance(parts, dict) and parts:
        return [(ax, float(val), c) for ax, val in parts.items() if ax in AXES]
    ax = v.get("axis")
    if ax not in AXES:
        return []
    m = _clip01(v.get("strength", 0.0))
    if m <= 0:
        return []
    return [(ax, vs.sign(v.get("bias")) * m, c)]


def aggregate(votes: Iterable[dict], weights: dict[str, float] | None = None,
              gates: Any = ()) -> dict:
    """方向票 → 加权总信号。

    Args:
        votes: 票列表，每项含 bias/axis/strength/confidence，可选 axis_parts。
        weights: 轴权重；缺省用 DEFAULT_WEIGHTS（等权先验）。
        gates: 已触发的**硬闸门**名称序列（如 ["double_break"]）。闸门是否决权、不是方向，
               所以只压制 `direction`，不改 `score`——证据分布照实呈现。

    Returns:
        {score, direction, raw_direction, gated, gates, axes, active_axes, weights,
         unattributed（归不到轴的方向票，应为空）, note}

    【为什么 direction 是"已过闸门"的那个、而另给 raw_direction】
      默认字段必须是**安全**的那个。若把裸方向放在 direction、闸门版另起一个名字，
      将来谁用了 direction 就会静默绕过闸门——那是丢风控，不是丢样式。
    """
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update({k: float(v) for k, v in weights.items() if k in AXES})

    acc = {a: {"num": 0.0, "den": 0.0, "n": 0} for a in AXES}
    unattributed: list[dict] = []   # 【变量】有票但归不到任何轴 —— 必须外显, 不许静默丢
    for v in votes or ():
        if not isinstance(v, dict):
            continue
        parts = _signed_parts(v)
        if not parts:
            # 【为什么必须报】若四象被标成 composite 却没给 axis_parts, 它会**整张消失**,
            #   而总分看上去只是"少了点分量", 不会报错 —— 和 10-19 的哑票是同一类失败。
            if vs.sign(v.get("bias")) != 0:
                unattributed.append({"book": str(v.get("book")), "axis": str(v.get("axis"))})
            continue
        for ax, signed_m, c in parts:
            acc[ax]["num"] += signed_m * c
            acc[ax]["den"] += abs(signed_m) * c
            acc[ax]["n"] += 1

    axes: dict[str, dict] = {}
    for ax, a in acc.items():
        if a["den"] <= 0:
            continue
        d = a["num"] / a["den"]                       # 方向：多空抵消后归零
        conv = min(1.0, a["den"])                     # 证据量
        axes[ax] = {"dir": round(d, 4), "conv": round(conv, 4),
                    "score": round(d * conv, 4), "n": a["n"]}

    active = [ax for ax in AXES if ax in axes]
    if active:
        wsum = sum(w[ax] for ax in active)            # ★ 只对"有证据的轴"归一
        total = sum(w[ax] * axes[ax]["score"] for ax in active) / wsum
    else:
        total = 0.0

    if total >= DIR_THRESHOLD:
        raw_direction = "做多"
    elif total <= -DIR_THRESHOLD:
        raw_direction = "做空"
    else:
        raw_direction = "观望"

    gate_list = [str(g) for g in (gates or ()) if g]
    direction = "暂不操作" if gate_list else raw_direction

    return {
        "score": round(total, 4),
        "direction": direction,
        "raw_direction": raw_direction,
        "gated": bool(gate_list),
        "gates": gate_list,
        "axes": axes,
        "active_axes": active,
        "weights": {ax: w[ax] for ax in active},
        "unattributed": unattributed,
        "note": "轴内按 Σ(s·m·c)/Σ(m·c) 定方向、min(1,Σ(m·c)) 定证据量, 两者相乘; "
                "轴间等权先验(未拟合)。与 net 并列输出, 不替代。"
                "⚠ 本分只描述「票的证据分布」, **不含双线同破等硬闸门** —— 闸门只作用于 verdict; "
                "若将来让 verdict 改看本分, 闸门必须一并搬过来。",
    }
