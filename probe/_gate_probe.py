# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import quant_verdict as qv

def _bars(n=90, trend=1.0):
    out, px = [], 1000.0
    for i in range(n):
        px *= (1 + trend*0.004) * (1 + (0.004 if i%7 in (0,1) else -0.003))
        out.append({"date": f"2026-{(i//28)+1:02d}-{(i%28)+1:02d}", "open": px*0.995,
                    "high": px*1.006, "low": px*0.994, "close": px,
                    "volume": 10000+i*10, "open_interest": 50000+i*20})
    return out

payload = {"variety":"TA","name":"PTA","unit":"元/吨","bars":_bars(),
           "basis_rate_series":[0.02+0.001*i for i in range(40)],
           "inv_series":[2000000-20000*i for i in range(40)], "inv_chg_pct":-6.0,
           "term":{"ok":True,"rows":[{"contract":"TA2610","month":"2610","price":6800},
                                      {"contract":"TA2709","month":"2709","price":6000}]},
           "profit_series":[100.0+5*i for i in range(30)], "profit_label":"加工利润"}

orig = qv.qm.analyze_trend_geometry
def patched(bars, n=3):
    g = dict(orig(bars, n))
    g["ok"] = True
    g["double_break"] = True
    g.setdefault("direction", "上升")
    return g
qv.qm.analyze_trend_geometry = patched
try:
    d = qv.build_verdict(payload)
    sd = d["stages"]["direction"]
    w = sd["weighted"]
    print("ok", d.get("ok"))
    print("verdict:", sd["verdict"], "| desc:", sd.get("desc"))
    print("weighted: score %+.3f direction %s raw %s gated %s gates %s"
          % (w["score"], w["direction"], w["raw_direction"], w["gated"], w["gates"]))
finally:
    qv.qm.analyze_trend_geometry = orig
