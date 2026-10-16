# -*- coding: utf-8 -*-
"""探针 16(冲刺): 100ppi 大宗榜/报价详情页 找 SC/LU/CJ 的历史走势。"""
import re
import warnings

import requests
from bs4 import BeautifulSoup

warnings.filterwarnings("ignore")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

s = requests.Session()
s.headers.update({"User-Agent": UA, "Referer": "https://www.100ppi.com/"})
r0 = s.get("https://www.100ppi.com/sf/792.html", timeout=25)
m = re.search(r'var _0x2 = "([0-9a-f]+)"', r0.text)
if m:
    s.cookies.set("HW_CHECK", m.group(1), domain="www.100ppi.com")

print("=" * 74)
print("A. zdb 大宗榜(day-1) 里「原油」的链接")
print("=" * 74)
r = s.get("https://top.100ppi.com/zdb/detail-day---1.html", timeout=25)
soup = BeautifulSoup(r.text, "lxml")
for a in soup.find_all("a", href=True):
    t = a.get_text(strip=True)
    if t in ("原油", "燃料油", "纯苯", "低硫燃料油", "红枣"):
        print(f"  {t} -> {a['href']}")

print()
print("=" * 74)
print("B. mprice 原油报价详情页(45) 找历史走势链接")
print("=" * 74)
for u in ("https://www.100ppi.com/mprice/detail-45.html",
          "https://www.100ppi.com/mprice/plist-1-45-1.html"):
    try:
        rr = s.get(u, timeout=45)
        print(f"  {u} -> {rr.status_code} len={len(rr.text)}")
        if rr.status_code == 200:
            sp = BeautifulSoup(rr.text, "lxml")
            links = [a["href"] for a in sp.find_all("a", href=True)
                     if any(k in str(a.get("href")) for k in ("trend", "chart", "hist", "detail", "price"))]
            print(f"     候选链接({len(links)}): {links[:12]}")
            for kw in ("价格走势", "历史", "现货价格"):
                print(f"     含「{kw}」: {kw in rr.text}")
    except Exception as e:  # noqa: BLE001
        print(f"  {u} -> ERR {type(e).__name__}: {str(e)[:90]}")

print()
print("=" * 74)
print("C. 生意社产品页(百度/站内已知形态) 试探 原油/红枣 走势图接口")
print("=" * 74)
CAND = [
    "https://www.100ppi.com/price/detail-45.html",
    "https://www.100ppi.com/sf2/45.html",
    "https://www.100ppi.com/mprice/mlist-45-1.html",
    "https://top.100ppi.com/zdb/detail-day-45-1.html",
    "https://top.100ppi.com/zdb/detail-day-45.html",
]
for u in CAND:
    try:
        rr = s.get(u, timeout=20)
        ok = rr.status_code == 200
        hit = "原油" in rr.text if ok else False
        print(f"  {u} -> {rr.status_code} len={len(rr.text)} 含原油={hit}")
    except Exception as e:  # noqa: BLE001
        print(f"  {u} -> ERR {type(e).__name__}: {str(e)[:70]}")
