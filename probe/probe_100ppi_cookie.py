# -*- coding: utf-8 -*-
"""探针 12(破局): 解开 100ppi HW_CHECK cookie 挑战, 取现期图品种字典, 定位原油/红枣/低硫燃料油历史页。"""
import re
import warnings

import requests
from bs4 import BeautifulSoup

warnings.filterwarnings("ignore")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

TARGETS = ["原油", "低硫燃料油", "燃料油", "红枣", "纯苯", "对二甲苯", "棉花",
           "PTA", "乙二醇", "液化石油气", "豆粕", "碳酸锂", "天然橡胶", "20号胶",
           "丁二烯橡胶", "生猪", "石油沥青", "苯乙烯", "工业硅", "多晶硅"]


def sec(t):
    print("\n" + "=" * 74)
    print(t)
    print("=" * 74)


s = requests.Session()
s.headers.update({"User-Agent": UA})

sec("1. 带 HW_CHECK cookie 直取 792.html")
# 先裸取拿挑战值
r0 = s.get("https://www.100ppi.com/sf/792.html", timeout=25)
print(f"  裸取: {r0.status_code} len={len(r0.text)}")
m = re.search(r'var _0x2 = "([0-9a-f]+)"', r0.text)
val = m.group(1) if m else "0edfa72a989a8b8f207acbc49a10d752"
print(f"  挑战值: {val}")
s.cookies.set("HW_CHECK", val, domain="www.100ppi.com")
r1 = s.get("https://www.100ppi.com/sf/792.html", timeout=25)
print(f"  带 cookie 重取: {r1.status_code} len={len(r1.text)}")

sec("2. 解析 div.q8 品种字典")
soup = BeautifulSoup(r1.text, "lxml")
d = soup.find("div", attrs={"class": "q8"})
print(f"  div.q8: {'找到' if d else '未找到'}")
href_map = {}
if d:
    for li in d.find_all("li"):
        a = li.find("a")
        if a and a.get("href"):
            href_map[a.get_text().strip()] = a["href"]
print(f"  品种数: {len(href_map)}")
for t in TARGETS:
    print(f"    {t:<12} -> {href_map.get(t, '未收录')}")

sec("3. 取一个已知品种(铜)的现期图, 确认历史序列结构")
import pandas as pd
from io import StringIO
if href_map:
    first = list(href_map.items())[0]
    u = "https://www.100ppi.com" + first[1] if first[1].startswith("/") else first[1]
    rr = s.get(u, timeout=25)
    print(f"  {first[0]} {u} -> {rr.status_code} len={len(rr.text)}")
    try:
        tbs = pd.read_html(StringIO(rr.text))
        print(f"  read_html 表数: {len(tbs)}")
        for i, t in enumerate(tbs[:3]):
            print(f"    表{i} shape={t.shape}")
            print("      ", t.head(3).to_string()[:400].replace("\n", "\n       "))
    except Exception as e:  # noqa: BLE001
        print("  [ERR]", type(e).__name__, str(e)[:150])
