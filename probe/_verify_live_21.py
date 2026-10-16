"""重启后**线上验收**: 通过 HTTP 直连 :5000 校验 21 池在各模块全线生效。

运行: ./venv/Scripts/python.exe probe/_verify_live_21.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = "http://127.0.0.1:5000"


def get(path: str, timeout: int = 60):
    # 必须绕过 http_proxy(否则回环请求被代理转发 → 502)
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(BASE + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


print("=" * 66)
print("① /api/varieties  品种池")
print("=" * 66)
d = get("/api/varieties")
vs = d if isinstance(d, list) else (d.get("varieties") or d.get("rows") or [])
codes = [v.get("code") for v in vs if isinstance(v, dict)]
print(f"品种数 = {len(codes)}")
print(f"含 P = {'P' in codes}")
print(f"codes = {codes}")


def meta(path, label):
    print()
    print("=" * 66)
    print(f"② {path}   {label}")
    print("=" * 66)
    try:
        m = get(path, timeout=120)
    except Exception as e:  # noqa: BLE001
        print("  请求失败:", type(e).__name__, e)
        return
    print("ok =", m.get("ok"), "| 品种数 =", len(m.get("varieties", [])),
          "| 含 P =", any(v.get("code") == "P" for v in m.get("varieties", [])))
    for s in m.get("sources", []):
        print(f"  {s['name']:<16} {s['coverage']:<8} {s['note']}")


meta("/api/quant/meta", "量化模块数据边界")
meta("/api/fund/meta", "基本面量化模块数据边界")

print()
print("=" * 66)
print("③ /api/gtja/coverage?range_days=30   国君四源覆盖矩阵")
print("=" * 66)
try:
    g = get("/api/gtja/coverage?range_days=30", timeout=300)
    print("ok =", g.get("ok"), "| total =", g.get("total"))
    print("summary =", json.dumps(g.get("summary"), ensure_ascii=False))
    p = next((r for r in g.get("rows", []) if r.get("code") == "P"), None)
    print("P 行 =", json.dumps(p, ensure_ascii=False) if p else "缺失!")
except Exception as e:  # noqa: BLE001
    print("  请求失败:", type(e).__name__, e)

print()
print("=" * 66)
print("④ /api/gtja/overview?variety=P   棕榈油取数")
print("=" * 66)
try:
    o = get("/api/gtja/overview?variety=P", timeout=300)
    print("ok =", o.get("ok"))
    for k, v in (o.items() if isinstance(o, dict) else []):
        if isinstance(v, dict) and "available" in v:
            print(f"  {k:<10} available={v.get('available')} rows={v.get('rows')} note={str(v.get('note'))[:60]}")
except Exception as e:  # noqa: BLE001
    print("  请求失败:", type(e).__name__, e)
