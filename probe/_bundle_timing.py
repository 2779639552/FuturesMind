# -*- coding: utf-8 -*-
"""测「按轴拆分」所需的各段取数耗时（决定注入块能否进同步链路）。"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import quant_module as q
import fund_module as f

def t(name, fn):
    t0 = time.time()
    try:
        r = fn()
        ok = r.get("ok") if isinstance(r, dict) else bool(r)
        print("%-34s %6.2fs ok=%s" % (name, time.time() - t0, ok))
        return r
    except Exception as e:
        print("%-34s %6.2fs FAIL %s: %s" % (name, time.time() - t0, type(e).__name__, e))
        return None

code, fn = "TA"
if len(sys.argv) > 1: code = sys.argv[1]
if len(sys.argv) > 2: fn = sys.argv[2]

bars = t("_load_daily_bars(250)", lambda: {"ok": bool(q._load_daily_bars(code, 250)),
                                           "n": len(q._load_daily_bars(code, 250))})
b = t("_fetch_basis(180)", lambda: q._fetch_basis(code, 180))
inv = t("_fetch_inventory(180)", lambda: q._fetch_inventory(code, 180))
term = t("_fetch_term_structure", lambda: q._fetch_term_structure(code))
pr = t("_fetch_profit", lambda: q._fetch_profit(code))
vi = t("_fund_vote_inputs", lambda: {"ok": True, **{k: type(v).__name__ for k, v in f._fund_vote_inputs(code)[0].items()}})
vr = t("_vr_ratio", lambda: f._vr_ratio(code))
print("\n--- 缓存后二次 ---")
for nm, fnn in (("basis", lambda: q._fetch_basis(code, 180)),
                ("inv", lambda: q._fetch_inventory(code, 180)),
                ("term", lambda: q._fetch_term_structure(code)),
                ("profit", lambda: q._fetch_profit(code))):
    t(nm + "(cached)", fnn)
