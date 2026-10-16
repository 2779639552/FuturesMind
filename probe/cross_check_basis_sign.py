# -*- coding: utf-8 -*-
"""交叉验证: 同一品种、同一日期, 国君 vs 100ppi 的基差符号方向是否一致。

【为什么必须做】两源若符号相反, 直接替换会让估值轴方向整体反掉 —— 这是静默错误:
  接口全 200、数字看着正常、但"深贴水"被判成"高估值", 结论完全反向。
【做法】取两源**重叠品种**(CF/M/TA/EG/RU/BU/EB/SI/PS/LC/LH/PG/BR/NR 中的若干),
  对齐同一交易日, 比较 (spot-fut) 与 dom_basis 的符号关系。
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta

import quant_module as Q

import ppi_basis

OVERLAP = ["CF", "M", "RU", "BU", "EB", "LH", "PG", "BR", "SI", "PS", "LC"]

print("=" * 96)
print("同一日期两源对照 (国君 GTJA vs 生意社 100ppi)")
print("=" * 96)
print(f"{'品种':<5}{'日期':<12}{'源':<14}{'现货':>10}{'期货':>10}{'spot-fut':>11}{'dom_basis':>11}{'关系':<10}")

concl = []
for c in OVERLAP:
    g = Q._fetch_basis_gtja(c, 180, batch=True)
    p = ppi_basis.basis_rows(c, 180)
    pmap = {r["date"]: r for r in p}
    if not g.get("ok"):
        print(f"{c:<5}{'':<12}国君无数据, 跳过")
        continue
    grows = g.get("rows") or []
    # 取两源都有的最近一天
    matched = None
    for r in reversed(grows):
        d = r["date"]
        if d in pmap and r.get("spot_price") and pmap[d].get("spot_price"):
            matched = (d, r, pmap[d])
            break
    if not matched:
        print(f"{c:<5}{'':<12}无共同日期, 跳过")
        continue
    d, gr, pr = matched
    g_sf = (gr["spot_price"] or 0) - (gr["futures_price"] or 0)
    g_b = gr["basis"]
    p_sf = (pr["spot_price"] or 0) - (pr["futures_price"] or 0)
    p_b = pr["basis"]

    def rel(sf, b):
        if sf == 0 or b is None:
            return "?"
        return "basis=现货-期货" if abs(sf - b) <= max(abs(sf) * 0.02, 0.5) else \
               ("basis=期货-现货" if abs(sf + b) <= max(abs(sf) * 0.02, 0.5) else "不一致?")

    print(f"{c:<5}{d:<12}{'国君':<14}{gr['spot_price']:>10.2f}{gr['futures_price']:>10.2f}{g_sf:>11.2f}{g_b:>11.2f}{rel(g_sf, g_b):<10}")
    print(f"{c:<5}{d:<12}{'100ppi':<14}{pr['spot_price']:>10.2f}{pr['futures_price']:>10.2f}{p_sf:>11.2f}{p_b:>11.2f}{rel(p_sf, p_b):<10}")
    # 同时核对: 同日同品种两边现货价是否接近(证明确实同口径的现货)
    sp_diff = abs(gr["spot_price"] - pr["spot_price"])
    concl.append((c, d, rel(g_sf, g_b), rel(p_sf, p_b), sp_diff, gr["spot_price"], pr["spot_price"]))
    print()

print("=" * 96)
print("结论汇总")
print("=" * 96)
print(f"{'品种':<5}{'日期':<12}{'国君口径':<18}{'100ppi口径':<18}{'现货价差':>10}")
for c, d, rg, rp, sd, gs, ps in concl:
    print(f"{c:<5}{d:<12}{rg:<18}{rp:<18}{sd:>10.2f}   (国君 {gs} vs ppi {ps})")

g_rels = {x[2] for x in concl}
p_rels = {x[3] for x in concl}
print()
print(f"  国君口径集合  : {g_rels}")
print(f"  100ppi 口径集合: {p_rels}")
if g_rels == {"basis=现货-期货"} and p_rels == {"basis=期货-现货"}:
    print("  >>> ⚠️⚠️ 两源符号**相反**! 100ppi 必须取负号才能与国君/本系统口径对齐。")
elif g_rels == p_rels:
    print("  >>> ✅ 两源符号一致, 可直接替换。")
else:
    print("  >>> ⚠️ 口径不完全确定, 需人工复核上表。")
