# -*- coding: utf-8 -*-
"""探针 19: mprice 报价页是否含 SC/LU/CJ 的多日序列(而非单日快照)。"""
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
print("A. mprice 原油列表页(45): 抽取 日期 + 价格 配对")
print("=" * 74)
r = s.get("https://www.100ppi.com/mprice/plist-1-45-1.html", timeout=30)
soup = BeautifulSoup(r.text, "lxml")
tb = soup.find("table")
rows = tb.find_all("tr") if tb else []
print(f"  行数 {len(rows)}")
series = []
for tr in rows:
    cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
    if len(cells) >= 7 and ("原油" in cells[0]):
        series.append((cells[0], cells[6], cells[3]))
for x in series[:25]:
    print("   ", x)
print(f"  含日期报价 {len(series)} 条")

print()
print("=" * 74)
print("B. 是否有「历史报价」入口 (mprice 页面链接中含 hist/trend/day)")
print("=" * 74)
links = {a.get("href") for a in soup.find_all("a", href=True)}
cand = [l for l in links if any(k in str(l) for k in ("hist", "trend", "day", "chart", "jpg", "kline"))]
print("  候选:", list(cand)[:20])

print()
print("=" * 74)
print("C. 100ppi 站内搜索接口(找 红枣/低硫燃料油 专门页)")
print("=" * 74)
for kw in ("红枣", "低硫燃料油", "原油"):
    for u in (f"https://www.100ppi.com/search/?kw={requests.utils.quote(kw)}",
              f"https://www.100ppi.com/sf/search.html?kw={requests.utils.quote(kw)}"):
        try:
            rr = s.get(u, timeout=15)
            print(f"  {kw} {u[-40:]} -> {rr.status_code} len={len(rr.text)} 含={kw in rr.text}")
        except Exception as e:  # noqa: BLE001
            print(f"  {kw} -> ERR {type(e).__name__}")
