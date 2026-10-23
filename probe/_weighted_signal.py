# -*- coding: utf-8 -*-
"""加权总信号原型（probe，不改生产）。

【三个必须分开的量】
  s 方向   ∈ {-1, 0, +1}
  m 强度   ∈ [0,1]  这张票离"中性"有多远（利润 24% 分位 vs 49% 分位，不该同权）
  c 可信度 ∈ [0,1]  这张票自己的数据靠不靠得住（缺口/小基数失真/样本不足）
  w 轴权重          由历史表现定，**不能从票自己身上推** —— 本原型只用先验等权

【为什么要先归轴再加权】
  直接对 9 张票求和 = 票多的那一方赢。而"票多"往往是同源重复：
  四象(基差+库存)、期限结构(基差)、库存周期(价格) 分别落在不同轴上。
  做法：轴内先按 m·c 归一（Σ(m·c·s)/Σ(m·c)），再轴间加权 ⇒ 票数不再等于权重。

【注意】本原型从 detail 的**中文文本**里正则抠数字。那是临时手段：
  这些数在生产里都算出来了，只是没作为字段暴露 —— 正式实现必须改成结构化字段。
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)   # 让 vote_semantics 可导入（探针从 probe/ 运行）
BASE = "http://127.0.0.1:5000"

# 先验轴权重（等权）。★ 这只是占位：真实权重应由历史 IC 拟合，见报告 L1/L2
W_AXIS = {"price": 1.0, "basis": 1.0, "inventory": 1.0, "profit": 1.0}


def clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _f(pat: str, text: str, idx: int = 1):
    m = re.search(pat, text or "")
    return float(m.group(idx)) if m else None


def get(code: str) -> dict:
    with urllib.request.urlopen(BASE + "/api/quant/verdict?variety=" + code, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def build_items(d: dict) -> list[dict]:
    """L1a 之后：**直接读字段**，不再正则抠 detail 文本。

    【为什么这次改写很重要】改前是从中文句子里 parse 数字，等于把"机器算出来的数"
      再让机器读一遍自己的文案。字段化之后这条链路彻底不需要了 —— 这就是 L1a 的目的。
    """
    import vote_semantics as vs

    sd = (d.get("stages") or {}).get("direction") or {}
    items: list[dict] = []
    for v in sd.get("votes") or []:
        items.append(dict(
            name=str(v.get("book")),
            axis=str(v.get("axis") or "?"),
            s=vs.sign(v.get("bias")),
            m=float(v.get("strength") or 0.0),
            c=float(v.get("confidence") or 0.0),
            why=str(v.get("strength_basis") or ""),
            modifier="price_conf_down" if v.get("bias") == "反转预警" else None,
        ))
    return items


def score(items: list[dict]) -> dict:
    # ① 同轴可信度修正：出现"price_conf_down"（背离预警）→ 该轴 c 打折
    if any(it.get("modifier") == "price_conf_down" for it in items):
        for it in items:
            if it["axis"] == "price":
                it["c"] *= 0.6

    # ② 轴内归一：Σ(m·c·s)/Σ(m·c) —— 票数不等于权重
    axes: dict[str, dict] = {}
    for it in items:
        w = it["m"] * it["c"]
        a = axes.setdefault(it["axis"], {"num": 0.0, "den": 0.0, "n": 0})
        a["num"] += w * it["s"]
        a["den"] += w
        a["n"] += 1
    for k, a in axes.items():
        # ★ 方向与"证据量"必须拆开算：
        #   d = Σ(m·c·s)/Σ(m·c)  只给方向（±1），会把幅度抹平——单个弱票也会变成 ±1.00；
        #   conv = min(1, Σ(m·c)) 是这条轴的总证据量（1 = 一张满强度的票）。
        #   两者相乘才是"这条轴能出多强的信号"。第一版只用 d，结果四象强度 0.17 也报 +1.00。
        a["dir"] = (a["num"] / a["den"]) if a["den"] > 0 else 0.0
        a["conv"] = min(1.0, a["den"])
        a["score"] = a["dir"] * a["conv"]
        a["w"] = W_AXIS.get(k, 1.0)

    # ③ 轴间加权（先验等权）
    tot_w = sum(a["w"] for a in axes.values())
    total = sum(a["w"] * a["score"] for a in axes.values()) / tot_w if tot_w else 0.0
    return {"axes": axes, "total": total, "items": items}


def main() -> None:
    codes = sys.argv[1:] or ["TA"]
    for code in codes:
        d = get(code)
        if not d.get("ok"):
            print(code, "不可用", d.get("error"))
            continue
        items = build_items(d)
        r = score(items)
        sd = (d.get("stages") or {}).get("direction") or {}
        au = sd.get("vote_audit") or {}
        print("=" * 78)
        print("%s  当前净票 %+d（计入 %d 张）  加权总分 %+.3f"
              % (code, au.get("net") or 0, au.get("counted") or 0, r["total"]))
        print("%-18s %-10s %5s %5s %5s  %s" % ("票", "轴", "方向", "强度", "可信", "依据"))
        for it in r["items"]:
            print("%-18s %-10s %+5d %5.2f %5.2f  %s"
                  % (it["name"], it["axis"], it["s"], it["m"], it["c"], it["why"]))
        print("-- 轴得分 --")
        for k, a in sorted(r["axes"].items()):
            print("  %-10s 票数 %d  方向 %+.2f × 证据量 %.2f = 得分 %+.3f"
                  % (k, a["n"], a["dir"], a["conv"], a["score"]))


if __name__ == "__main__":
    main()
