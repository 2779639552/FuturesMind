# -*- coding: utf-8 -*-
"""取数可行性探针: 价格与国君四源各自能回多远。"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

t = time.time()
try:
    from tradingagents.dataflows.commodity_futures import get_futures_price
    csv = get_futures_price("TA", "2019-01-01", "2026-10-19")
    lines = [l for l in str(csv).strip().split("\n") if l[:4].isdigit()]
    print("price  rows=%d %s ~ %s  %.1fs" % (len(lines), lines[0].split(",")[0] if lines else "-",
                                             lines[-1].split(",")[0] if lines else "-", time.time()-t))
except Exception as e:
    print("price FAIL", type(e).__name__, str(e)[:120])

import gtja_module as g
for iid in ["basis.value", "basis.spot_price", "warehouse.on_warrant"]:
    t = time.time()
    try:
        r = g.fetch_indicator_seasonal("TA", iid, 8)
        ys = r.get("years") or {}
        n = sum(len(v) for v in ys.values())
        print("%-22s ok=%s yrs=%s n=%d %s~%s %.1fs" % (iid, r.get("ok"), sorted(ys), n,
              r.get("first"), r.get("last"), time.time()-t))
    except Exception as e:
        print(iid, "FAIL", type(e).__name__, str(e)[:100])
