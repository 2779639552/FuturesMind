# -*- coding: utf-8 -*-
"""取证: variety 过滤对 "PTA近日情况" 召回的影响(A/B)。

【背景】2026-09-30 用户报"研报问答问 PTA 找不到今天的 PTA 研报"。
根因指向 _populateVarietySelects 把 #res-ask-variety 静默补成池首 BR。
本脚本按 web_app._rag_call 的同款协议直连 rag_worker.py 子进程
(避免在主进程 import service —— torch/chromadb 原生栈会污染主进程),
在检索层对比 variety=BR / TA / PX / None 的召回差异。
"""
import json
import os
import subprocess
import sys

ROOT = r"D:\AgentSense\AgentSense"
WORKER = os.path.join(ROOT, "rag_worker.py")
sys.path.insert(0, ROOT)


def call(proc, seq, op, **args):
    payload = json.dumps({"id": seq, "op": op, "args": args}, ensure_ascii=False)
    proc.stdin.write(payload + "\n")
    proc.stdin.flush()
    while True:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("worker EOF")
        line = line.strip()
        if not line:
            continue
        resp = json.loads(line)
        if resp.get("id") == seq:
            return resp


def summarize(tag, hits):
    print("\n===== %s: %d hits =====" % (tag, len(hits)))
    seen, order = {}, []
    for h in hits:
        m = h["metadata"]
        rid = m.get("report_id")
        if rid not in seen:
            seen[rid] = [0, (m.get("title") or "")[:46], m.get("publish_date"), m.get("varieties")]
            order.append(rid)
        seen[rid][0] += 1
    for rid in order:
        n, t, dv, vs = seen[rid]
        print("  rid=%-5s n=%d %s [%s] %s" % (rid, n, dv, vs, t))
    return set(order)


proc = subprocess.Popen(
    [sys.executable, "-X", "utf8", WORKER],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    stderr=open(os.path.join(ROOT, "probe", "_probe_pta_recall.err.log"), "wb"),
    cwd=ROOT, text=True, encoding="utf-8",
)

seq = 0
res = {}
try:
    for v in ("BR", "TA", "PX", None):
        seq += 1
        resp = call(proc, seq, "retrieve", query="PTA近日情况", top_k=6, variety=v)
        if not resp.get("ok"):
            print("ERR variety=%r -> %s" % (v, resp.get("error")))
            res[v] = set()
            continue
        res[v] = summarize("variety=%r" % (v,), resp.get("result") or [])
finally:
    try:
        proc.stdin.close()
    except Exception:
        pass
    proc.terminate()

print("\n===== 目标研报命中情况(今天) =====")
targets = {1463: "国泰君安 PX PTA MEG 基本面数据", 1440: "华泰 聚酯日报 20260930"}
for rid, name in targets.items():
    marks = " ".join("%s=%s" % (v, rid in res.get(v, set())) for v in ("BR", "TA", "PX", None))
    print("  rid=%-5s %-30s %s" % (rid, name, marks))
