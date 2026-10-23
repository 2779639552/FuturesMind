# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
import gtja_module as g
from tradingagents.dataflows import gtja_api as G
print("MAX_PROFIT_LABELS =", getattr(g, "MAX_PROFIT_LABELS", "?"))
src = g._full_dataset_source("TA", G.EP_PROFIT, G._PROFIT_CACHE_DIR, "profitValue",
                             "利润", ["加工利润", "进口利润"], g.MAX_PROFIT_LABELS,
                             "加工/进口利润", series_cap=0)
print("ok", src.get("ok"), "available", src.get("available"), "rows", src.get("rows"), src.get("error","")[:80])
for i in src.get("indicators") or []:
    ser = i.get("series") or []
    print(" id=%-30s n=%-4d %s ~ %s" % (i.get("id"), len(ser),
          (ser[0][0] if ser else ""), (ser[-1][0] if ser else "")))
