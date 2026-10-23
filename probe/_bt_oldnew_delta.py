# -*- coding: utf-8 -*-
"""改前 vs 改后：逐笔损益差（Addendum）。

上一版只给了"日期聚簇后的平均收益"，看不出总账差多少。这里直接算：
  对每一条(品种×日)：pnl_old = fwd × sign(old)（old=0 则空仓记 0），pnl_new 同理；
  Δ = pnl_new - pnl_old。
  Δ 只来自两类行：改前空仓→改后开仓(记 +)、改前开仓→改后空仓(记 -)。
再把 Δ 拆开分别报，就能回答"多开的那批仓位到底赚不赚钱"。
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

spec = importlib.util.spec_from_file_location("on", os.path.join(HERE, "_bt_oldnew.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

HORIZONS = (5, 10, 20)


def sign(x):
    return 1 if x > 0 else (-1 if x < 0 else 0)


def wilson(k, n, z=1.96):
    if n == 0:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - r) / d * 100, (c + r) / d * 100)


def main():
    codes = sorted(f[:-5] for f in os.listdir(m.CACHE)
                   if f.endswith(".json") and not f.endswith("_profit.json"))
    rows = []
    for c in codes:
        rows.extend(m.build(c))
    print("样本 %d 条(品种×日)\n" % len(rows))

    for h in HORIZONS:
        add, rem = [], []          # 新增仓位 / 被取消的仓位
        pos_o = pos_n = 0
        for r in rows:
            so, sn = sign(r["old"]), sign(r["new"])
            f = r["fwd"][h]
            if so == 0 and sn != 0:
                add.append(f * sn)
            elif so != 0 and sn == 0:
                rem.append(f * so)
            pos_o += 1 if so else 0
            pos_n += 1 if sn else 0

        def stat(xs):
            n = len(xs)
            if not n:
                return (0, 0.0, 0.0)
            k = sum(1 for x in xs if x > 0)
            return (n, 100 * k / n, 1e4 * sum(xs) / n)

        na, ha, ma = stat(add)
        nr, hr, mr = stat(rem)
        d_bp = ma * na / len(rows) - mr * nr / len(rows)   # 每条样本的期望损益差
        lo, hi = wilson(round(ha * na / 100), na)
        print("【%d 日】" % h)
        print("  新增仓位 %5d 条：命中 %.1f%%(95%%CI %.1f~%.1f) 均值 %+.1fbp  → 小计 %+.0fbp/样本"
              % (na, ha, lo, hi, ma, ma * na / len(rows)))
        print("  取消仓位 %5d 条：命中 %.1f%%           均值 %+.1fbp  → 小计 %+.0fbp/样本"
              % (nr, hr, mr, -mr * nr / len(rows)))
        # ★ 这里不能直接乘 244：Δ 是"每条样本"的期望差，而 h 日持仓是可重叠的。
        #   换成可解释的口径：每笔差异 × 每年可周转次数(244/h) ≈ 年化差(毛、未计成本)
        per_trade = (ma * na - mr * nr) / max(1, na + nr)   # 在"发生变化的样本"上的每笔差异
        print("  Δ  = %+.1f bp/样本（全样本）| 变动样本每笔 %+.1fbp ⇒ 年化差≈%+.2f%%"
              "  |  持仓条数 改前 %d → 改后 %d (%+d)"
              % (d_bp, per_trade, per_trade / 1e4 * (244.0 / h) * 100, pos_o, pos_n, pos_n - pos_o))
        print()

    # 汇总到 JSON
    out = {}
    for h in HORIZONS:
        add = [r["fwd"][h] * sign(r["new"]) for r in rows if sign(r["old"]) == 0 and sign(r["new"]) != 0]
        rem = [r["fwd"][h] * sign(r["old"]) for r in rows if sign(r["old"]) != 0 and sign(r["new"]) == 0]
        out[f"{h}d"] = {
            "added_n": len(add), "added_hit": round(100 * sum(1 for x in add if x > 0) / max(1, len(add)), 1),
            "added_mean_bp": round(1e4 * sum(add) / max(1, len(add)), 1),
            "removed_n": len(rem), "removed_hit": round(100 * sum(1 for x in rem if x > 0) / max(1, len(rem)), 1),
            "removed_mean_bp": round(1e4 * sum(rem) / max(1, len(rem)), 1),
        }
    json.dump(out, open(os.path.join(HERE, "_bt_oldnew_delta.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
