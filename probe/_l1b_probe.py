# -*- coding: utf-8 -*-
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import quant_verdict as qv
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_l1a_probe.py")).read().split("d = qv.build_verdict")[0].split("import quant_verdict as qv")[1])
d = qv.build_verdict(payload)
sd = d["stages"]["direction"]
print("net", sd["vote_audit"]["net"], "| verdict", sd["verdict"])
w = sd.get("weighted") or {}
print("weighted score %+.3f → %s" % (w.get("score",0), w.get("direction")))
for ax, a in (w.get("axes") or {}).items():
    print("  %-10s dir %+.2f × conv %.2f = %+.3f (n=%d)" % (ax, a["dir"], a["conv"], a["score"], a["n"]))
print("active", w.get("active_axes"), "weights", w.get("weights"))
