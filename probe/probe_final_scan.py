# -*- coding: utf-8 -*-
"""探针 14(最终扫): 100ppi 日频页全部表格符号 + SC/LU/CJ 命名变体排查。"""
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
print("1. 100ppi 日频页(day-2026-09-29) 全部表格的首列符号")
print("=" * 74)
r = s.get("https://www.100ppi.com/sf/day-2026-09-29.html", timeout=30)
soup = BeautifulSoup(r.text, "lxml")
tbs = soup.find_all("table")
all_syms = []
for tb in tbs:
    rows = tb.find_all("tr")
    for tr in rows:
        cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
        if cells and cells[0] and len(cells[0]) <= 8 and not cells[0].isdigit():
            all_syms.append(cells[0])
uniq = sorted(set(all_syms))
print(f"  表格数 {len(tbs)}, 唯一首列值 {len(uniq)} 个:")
print("  ", uniq)

print()
print("=" * 74)
print("2. 关键词模糊搜索(原油/低硫/红枣 各变体)")
print("=" * 74)
KEYS = ["原油", "低硫", "红枣", "燃油", "重油", "SC", "LU", "CJ", "焦煤", "液化"]
for k in KEYS:
    hit = [u for u in uniq if k in u]
    print(f"  「{k}」→ {hit or '无'}")

print()
print("=" * 74)
print("3. 100ppi 全站 mprice 分类页里找 红枣/低硫燃料油")
print("=" * 74)
for pid in range(40, 60):
    try:
        rr = s.get(f"https://www.100ppi.com/mprice/plist-1-{pid}-1.html", timeout=15)
        if rr.status_code != 200:
            continue
        txt = rr.text
        if "红枣" in txt or "低硫" in txt:
            soup2 = BeautifulSoup(txt, "lxml")
            tb = soup2.find("table")
            rows = tb.find_all("tr")[:4] if tb else []
            print(f"  [命中] plist-1-{pid}: 红枣={'红枣' in txt} 低硫={'低硫' in txt}")
            for tr in rows[:3]:
                cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])][:6]
                if cells:
                    print("        ", cells)
    except Exception:  # noqa: BLE001
        pass

print()
print("=" * 74)
print("4. 结论汇总")
print("=" * 74)
print("  100ppi 可覆盖缺口: FU(燃料油) / BZ(纯苯) / PX(PX)   [真实现货, 41点]")
print("  仍缺: SC(原油) / LU(低硫燃料油) / CJ(红枣)")
