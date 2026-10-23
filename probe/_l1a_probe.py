# -*- coding: utf-8 -*-
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import quant_verdict as qv

def mk_bars(n=90, trend=1.0):
    bars=[]; px=1000.0
    for i in range(n):
        px *= (1 + trend*0.004) * (1 + (0.004 if i%7 in (0,1) else -0.003))
        bars.append({"date": f"2026-{(i//28)+1:02d}-{(i%28)+1:02d}", "open":px*0.995, "high":px*1.006,
                     "low":px*0.994, "close":px, "volume":10000+i*10, "open_interest":50000+i*20})
    return bars

payload = {
 "variety":"TA","name":"PTA","unit":"元/吨",
 "bars": mk_bars(),
 "basis_rate_series":[0.02+0.001*i for i in range(40)],      # 基差率走高 → 低估值 → 偏多
 "inv_series":[2000000-20000*i for i in range(40)],           # 去库 → 驱动向上
 "inv_chg_pct":-6.0,
 "term":{"ok":True,"rows":[{"contract":"TA2610","month":"2610","price":6800},
                            {"contract":"TA2709","month":"2709","price":6000}]},  # back → 偏多
 "profit_series":[100.0+5*i for i in range(30)],              # 单调升 → 高位 → 做空
 "profit_label":"加工利润",
}
d = qv.build_verdict(payload)
print("ok", d.get("ok"), "verdict", (d.get("verdict") or {}).get("direction"))
sd = d["stages"]["direction"]
print("net", (sd.get("vote_audit") or {}).get("net"))
for v in sd["votes"]:
    print("  %-22s bias=%-12s axis=%-10s s=%.2f c=%.2f | %s" %
          (v.get("book"), v.get("bias"), v.get("axis"), v.get("strength"), v.get("confidence"),
           (v.get("strength_basis") or "")[:50]))
print("votes n =", len(sd["votes"]))
