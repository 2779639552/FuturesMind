# -*- coding: utf-8 -*-
"""逐笔口径的总命中率/均值（与上面的"日期聚簇"互补，两者都报才不会被口径误导）。"""
import importlib.util, os, sys, math
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
spec = importlib.util.spec_from_file_location("on", os.path.join(HERE, "_bt_oldnew.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

codes = sorted(f[:-5] for f in os.listdir(m.CACHE) if f.endswith(".json") and not f.endswith("_profit.json"))
rows = []
for c in codes: rows.extend(m.build(c))

def sign(x): return 1 if x > 0 else (-1 if x < 0 else 0)
def wilson(k, n, z=1.96):
    p=k/n; d=1+z*z/n; c=p+z*z/(2*n); r=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))
    return ((c-r)/d*100,(c+r)/d*100)

print("样本 %d 条\n" % len(rows))
for h in (5,10,20):
    print("【%d 日】逐笔口径" % h)
    for key,label in (("old","改前"),("new","改后")):
        xs=[r["fwd"][h]*sign(r[key]) for r in rows if r[key]!=0]
        n=len(xs); k=sum(1 for x in xs if x>0); lo,hi=wilson(k,n)
        print("  %s 持仓 %5d 条  命中 %5.1f%% (CI %.1f~%.1f)  均值 %+7.1fbp  年化 %+6.1f%%"
              % (label, n, 100*k/n, lo, hi, 1e4*sum(xs)/n, 1e4*sum(xs)/n/1e4*(244.0/h)*100))
    print()
