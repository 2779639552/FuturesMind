# -*- coding: utf-8 -*-
"""探针 9: 解析 100ppi mprice 页面, 定位「原油」「低硫燃料油」「红枣」价格页并试取历史序列。"""
import re
import warnings

import requests
from bs4 import BeautifulSoup

warnings.filterwarnings("ignore")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Referer": "https://www.100ppi.com/"}

TARGETS = ["原油", "低硫燃料油", "燃料油", "红枣", "纯苯", "对二甲苯", "棉花"]

print("=" * 74)
print("1. mprice 页中目标品种的链接")
print("=" * 74)
r = requests.get("https://www.100ppi.com/mprice/", headers=H, timeout=30)
soup = BeautifulSoup(r.text, "lxml")
found = {}
for a in soup.find_all("a", href=True):
    txt = a.get_text(strip=True)
    if txt in TARGETS:
        href = a["href"]
        if href not in found:
            found.setdefault(txt, []).append(href)
for t in TARGETS:
    print(f"  {t}: {found.get(t, '未找到')[:3]}")

print()
print("=" * 74)
print("2. 尝试这些链接取历史序列")
print("=" * 74)


def probe(url, tag):
    try:
        rr = requests.get(url, headers=H, timeout=25)
        if rr.status_code != 200:
            print(f"  [{tag}] {rr.status_code}")
            return None
        s = BeautifulSoup(rr.text, "lxml")
        tabs = s.find_all("table")
        print(f"  [{tag}] 200 len={len(rr.text)} tables={len(tabs)}")
        for i, tb in enumerate(tabs[:3]):
            rows = tb.find_all("tr")
            print(f"      表{i}: {len(rows)} 行; 首行: {[c.get_text(strip=True) for c in rows[0].find_all(['th','td'])][:8] if rows else []}")
            if len(rows) > 2:
                print(f"             次行: {[c.get_text(strip=True) for c in rows[1].find_all(['th','td'])][:8]}")
        return rr
    except Exception as e:  # noqa: BLE001
        print(f"  [{tag}] ERR {type(e).__name__}: {str(e)[:90]}")
        return None


for t, hrefs in found.items():
    for h in hrefs[:2]:
        u = h if h.startswith("http") else "https://www.100ppi.com" + ("/" + h.lstrip("/"))
        probe(u, f"{t} {h[:60]}")

print()
print("=" * 74)
print("3. 100ppi 站内搜索 红枣/低硫燃料油")
print("=" * 74)
for kw in ("红枣", "低硫燃料油", "原油"):
    u = f"https://www.100ppi.com/search/?k={requests.utils.quote(kw)}"
    try:
        rr = requests.get(u, headers=H, timeout=25)
        s = BeautifulSoup(rr.text, "lxml")
        links = [a.get("href") for a in s.find_all("a", href=True) if kw in a.get_text()]
        print(f"  {kw}: {rr.status_code} 命中链接 {len(links)} 例: {links[:5]}")
    except Exception as e:  # noqa: BLE001
        print(f"  {kw}: ERR {type(e).__name__}: {str(e)[:80]}")
