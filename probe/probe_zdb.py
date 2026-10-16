# -*- coding: utf-8 -*-
"""探针 15(最后一搏): top.100ppi.com/zdb 历史页系列, 找 SC/LU/CJ。"""
import re
import warnings

import requests

warnings.filterwarnings("ignore")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

s = requests.Session()
s.headers.update({"User-Agent": UA, "Referer": "https://www.100ppi.com/"})
r0 = s.get("https://www.100ppi.com/sf/792.html", timeout=25)
m = re.search(r'var _0x2 = "([0-9a-f]+)"', r0.text)
if m:
    s.cookies.set("HW_CHECK", m.group(1), domain="www.100ppi.com")

KEYS = ["原油", "低硫燃料油", "红枣", "燃料油", "纯苯", "对二甲苯"]
found = {}
print("=" * 74)
print("扫描 top.100ppi.com/zdb/detail-day---N.html")
print("=" * 74)
for n in range(1, 71):
    u = f"https://top.100ppi.com/zdb/detail-day---{n}.html"
    try:
        r = s.get(u, timeout=12)
        if r.status_code != 200 or len(r.text) < 3000:
            continue
        txt = r.text
        title = re.search(r"<title>(.*?)</title>", txt)
        title = title.group(1).strip() if title else ""
        hit = [k for k in KEYS if k in txt]
        if hit:
            # 判断是否含历史序列表
            has_hist = "日期" in txt and ("现货" in txt or "基差" in txt)
            print(f"  N={n:<4} {title[:34]:<36} 命中={hit} 历史表={has_hist}")
            found[n] = (title, hit)
    except Exception:  # noqa: BLE001
        pass

print()
print("=" * 74)
print("汇总命中")
print("=" * 74)
for n, (t, h) in found.items():
    print(f"  N={n}: {t} -> {h}")
if not any("原油" in h or "红枣" in h or "低硫" in h for _, h in found.values()):
    print("  >>> SC/LU/CJ 在 zdb 系列中亦未检出")
