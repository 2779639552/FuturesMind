# -*- coding: utf-8 -*-
"""L1a 行为等价性：同一 payload 分别喂给「改前」与「改后」两份 quant_verdict，
比对 verdict / net / 每张票的 (book, bias) 序列必须逐项相同（新增字段不算差异）。"""
import importlib.util, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "probe"))

def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m

new = load(os.path.join(ROOT, "quant_verdict.py"), "qv_new")
old = load(os.path.join(ROOT, "backup", "quant_verdict_pre_l1a_20261019.py"), "qv_old")

def mk_bars(n=90, trend=1.0):
    bars=[]; px=1000.0
    for i in range(n):
        px *= (1 + trend*0.004) * (1 + (0.004 if i%7 in (0,1) else -0.003))
        bars.append({"date": f"2026-{(i//28)+1:02d}-{(i%28)+1:02d}", "open":px*0.995, "high":px*1.006,
                     "low":px*0.994, "close":px, "volume":10000+i*10, "open_interest":50000+i*20})
    return bars

def payload(v):
    return {"variety":"TA","name":"PTA","unit":"元/吨","bars":mk_bars(trend=v),
            "basis_rate_series":[0.02+0.001*i for i in range(40)],
            "inv_series":[2000000-20000*i for i in range(40)], "inv_chg_pct":-6.0,
            "term":{"ok":True,"rows":[{"contract":"TA2610","month":"2610","price":6800},
                                       {"contract":"TA2709","month":"2709","price":6000}]},
            "profit_series":[100.0+5*i for i in range(30)], "profit_label":"加工利润"}

bad = 0
for v in (1.0, -1.0, 0.0):
    a = old.build_verdict(payload(v)); b = new.build_verdict(payload(v))
    sa, sb = a["stages"]["direction"], b["stages"]["direction"]
    va, vb = sa.get("verdict"), sb.get("verdict")
    na, nb = (sa.get("vote_audit") or {}).get("net"), (sb.get("vote_audit") or {}).get("net")
    pa = [(x["book"], x["bias"]) for x in sa["votes"]]
    pb = [(x["book"], x["bias"]) for x in sb["votes"]]
    ok = (va == vb and na == nb and pa == pb and a.get("verdict_text") == b.get("verdict_text"))
    print("trend=%+.0f  verdict %s/%s  net %s/%s  票序列一致=%s  整体一致=%s"
          % (v, va, vb, na, nb, pa == pb, ok))
    if not ok:
        bad += 1
        print("   OLD:", pa); print("   NEW:", pb)
    # 新字段检查
    miss = [x["book"] for x in sb["votes"] if not {"axis","strength","confidence","strength_basis"} <= set(x)]
    oob = [x["book"] for x in sb["votes"]
           if not (0 <= x.get("strength",-1) <= 1 and 0 <= x.get("confidence",-1) <= 1)]
    print("   缺字段 %s | 越界 %s" % (miss or "无", oob or "无"))
print("\n结论：%s" % ("行为完全一致（只多出字段）" if bad == 0 else "有 %d 组不一致" % bad))
