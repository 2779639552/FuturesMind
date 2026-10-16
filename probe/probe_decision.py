# -*- coding: utf-8 -*-
"""探针 13(定案): dump 100ppi 现期图全字典 + 100ppi 日频页全符号, 确定最终可用源。"""
import re
import warnings
from io import StringIO

import pandas as pd
import requests
from bs4 import BeautifulSoup

warnings.filterwarnings("ignore")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def sec(t):
    print("\n" + "=" * 74)
    print(t)
    print("=" * 74)


s = requests.Session()
s.headers.update({"User-Agent": UA, "Referer": "https://www.100ppi.com/"})
r0 = s.get("https://www.100ppi.com/sf/792.html", timeout=25)
m = re.search(r'var _0x2 = "([0-9a-f]+)"', r0.text)
if m:
    s.cookies.set("HW_CHECK", m.group(1), domain="www.100ppi.com")
r1 = s.get("https://www.100ppi.com/sf/792.html", timeout=25)

sec("1. 100ppi 现期图 完整品种字典")
soup = BeautifulSoup(r1.text, "lxml")
d = soup.find("div", attrs={"class": "q8"})
href_map = {}
if d:
    for li in d.find_all("li"):
        a = li.find("a")
        if a and a.get("href"):
            href_map[a.get_text().strip()] = a["href"]
print(f"  共 {len(href_map)} 个:")
print("   ", list(href_map.keys()))

sec("2. 100ppi 日频页 全符号扫描 (最近交易日)")
r2 = s.get("https://www.100ppi.com/sf/day-2026-09-29.html", timeout=25)
soup2 = BeautifulSoup(r2.text, "lxml")
tbs = soup2.find_all("table")
print(f"  day-2026-09-29.html {r2.status_code} tables={len(tbs)}")
if tbs:
    rows = tbs[-1].find_all("tr")
    print(f"  末表 {len(rows)} 行")
    syms = []
    for tr in rows:
        cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
        if cells:
            syms.append(cells[0])
    print("  首列:", syms[:60])

sec("3. 全池 20 品种在两大源的最终归属")
with open("probe/_pool.txt", "w", encoding="utf-8") as f:
    f.write("")
POOL = ["SC", "TA", "PX", "EG", "FU", "LU", "PG", "M", "CF", "CJ",
        "LC", "RU", "NR", "BR", "LH", "BU", "BZ", "EB", "SI", "PS"]
try:
    import akshare as ak
    df = ak.futures_spot_price_daily(start_day="20260801", end_day="20260929")
    cov = df.groupby("symbol").size().to_dict()
    print(f"  {'品种':<6}{'100ppi日频':<12}{'现期图页'}")
    name_map = {"SC": "原油", "TA": "PTA", "PX": "对二甲苯", "EG": "乙二醇", "FU": "燃料油",
                "LU": "低硫燃料油", "PG": "液化石油气", "M": "豆粕", "CF": "棉花", "CJ": "红枣",
                "LC": "碳酸锂", "RU": "天然橡胶", "NR": "20号胶", "BR": "丁二烯橡胶",
                "LH": "生猪", "BU": "石油沥青", "BZ": "纯苯", "EB": "苯乙烯",
                "SI": "工业硅", "PS": "多晶硅"}
    final = {}
    for c in POOL:
        n = cov.get(c, 0)
        cn = name_map.get(c, "")
        page = href_map.get(cn, "-")
        final[c] = {"daily": n, "page": page}
        print(f"  {c:<6}{n:<12}{page}")
    print("\n  >>> 100ppi 日频可补:", [c for c in POOL if final[c]['daily'] >= 10])
    print("  >>> 现期图页可补:", [c for c in POOL if final[c]['page'] != '-'])
    both_miss = [c for c in POOL if final[c]['daily'] < 10 and final[c]['page'] == '-']
    print("  >>> 两源都缺:", both_miss)
except Exception as e:  # noqa: BLE001
    print("  [ERR]", type(e).__name__, str(e)[:150])
