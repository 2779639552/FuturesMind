"""实测 21 品种池在国君四源上的真实覆盖率(用于把硬编码 "x/20" 文案改成 21 口径)。

运行(必须在项目根, 用项目 venv):
    ./venv/Scripts/python.exe probe/_probe_pool21_coverage.py
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.chdir(ROOT)

from tradingagents.dataflows.commodity_futures import ACTIVE_VARIETIES  # noqa: E402

# 与 web_app._VARIETY_SECTORS 同序(油品→能化→农产品→新能源)
ORDER = [
    "SC", "FU", "LU", "BU",
    "PX", "TA", "EG", "PG", "BR", "EB", "BZ",
    "RU", "NR", "LH", "M", "CJ", "CF", "P",
    "LC", "SI", "PS",
]

codes = [c for c in ORDER if c in ACTIVE_VARIETIES]
codes += [c for c in ACTIVE_VARIETIES if c not in codes]

print(f"ACTIVE_VARIETIES 总数 = {len(ACTIVE_VARIETIES)}")
print(f"派生的查询顺序({len(codes)}) = {codes}")
print(f"集合一致(ORDER 覆盖全部池内品种) = {set(codes) == set(ACTIVE_VARIETIES)}")

import gtja_module  # noqa: E402

payload = gtja_module.fetch_coverage(codes, 30)
if not payload.get("ok"):
    print("fetch_coverage 失败:", payload.get("error"))
    sys.exit(1)

print("\n== summary ==")
print(json.dumps(payload["summary"], ensure_ascii=False))
print("total =", payload["total"])
print("sources =", json.dumps(payload["sources"], ensure_ascii=False))

print("\n== 逐品种 ==")
for r in payload["rows"]:
    print(
        f"{r['code']:<3} {r['name']:<6} "
        f"basis={r['basis']['state']:<5} "
        f"wh={r['warehouse']['state']:<5} "
        f"inv={r['inventory']['state']:<5} "
        f"prf={r['profit']['state']:<5}"
        + (f"  basis_err={r['basis']['error'][:50]}" if r["basis"]["error"] else "")
    )

print("\n== 无数据(empty)品种 ==")
for k in ("basis", "warehouse", "inventory", "profit"):
    empties = [r["code"] for r in payload["rows"] if r[k]["state"] == "empty"]
    fails = [r["code"] for r in payload["rows"] if r[k]["state"] == "fail"]
    print(f"{k:<10} ok={payload['summary'][k]:>2}/{payload['total']}  empty={empties}  fail={fails}")
