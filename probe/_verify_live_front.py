"""验收前端覆盖层下拉(21 项)与实时行情 P 报价。

运行: ./venv/Scripts/python.exe probe/_verify_live_front.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = "http://127.0.0.1:5000"


def get_raw(path: str, timeout: int = 60) -> str:
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(BASE + path, timeout=timeout) as r:
        return r.read().decode("utf-8")


def get(path: str, timeout: int = 60):
    return json.loads(get_raw(path, timeout))


print("=== ① 页面里覆盖层品种来源(overlay picker) ===")
html = get_raw("/")
for key in ("ov-variety", "overlay-variety", "品种池", "ACTIVE_VARIETIES"):
    idx = html.find(key)
    print(f"  '{key}' 出现在位置 {idx}")

# 找前端如何填充 overlay 下拉
import re  # noqa: E402

for m in re.finditer(r"id=[\"']([^\"']*(?:overlay|ov-)[^\"']*)[\"']", html):
    print("  相关 id:", m.group(1))

print()
print("=== ② 实时行情(price_fetcher 生产路径) ===")
for path in ("/api/prices/realtime", "/api/realtime", "/api/price/realtime"):
    try:
        d = get(path, timeout=30)
        print(f"  {path} -> ok")
        rows = d if isinstance(d, list) else (d.get("rows") or d.get("prices") or d.get("data") or [])
        if isinstance(rows, dict):
            rows = [{"code": k, **(v if isinstance(v, dict) else {"value": v})} for k, v in rows.items()]
        print(f"    条数 = {len(rows)}")
        p = [r for r in rows if str(r.get("code") or r.get("variety") or "").upper() == "P"]
        print(f"    P = {json.dumps(p[0], ensure_ascii=False) if p else '缺失'}")
        break
    except Exception as e:  # noqa: BLE001
        print(f"  {path} -> {type(e).__name__}: {str(e)[:80]}")
