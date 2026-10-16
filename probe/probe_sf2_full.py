# -*- coding: utf-8 -*-
"""探针 18(收口): 扫 sf2 商品主力基差表全部 57 张表, 看是否含 SC/LU/CJ。"""
import re
import warnings
from io import StringIO

import pandas as pd
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

r = s.get("https://www.100ppi.com/sf2/45.html", timeout=30)
tbs = pd.read_html(StringIO(r.text))
print(f"sf2 表数: {len(tbs)}")
allnames = set()
for i, t in enumerate(tbs):
    try:
        # 第一列可能是品种名
        col0 = [str(x) for x in t.iloc[:, 0].tolist()]
        allnames.update(col0)
    except Exception:  # noqa: BLE001
        pass
names = sorted({n for n in allnames if n and n != 'nan' and len(n) <= 12})
print(f"候选名称 {len(names)}:")
print("  ", names)
print()
for k in ("原油", "低硫", "红枣", "燃料油", "纯苯", "对二甲苯"):
    print(f"  「{k}」→ {[n for n in names if k in n] or '无'}")

# 打印每张表首行，找结构
print("\n表格结构抽样:")
for i in [0, 1, 2, 10, 30, len(tbs) - 1]:
    if 0 <= i < len(tbs):
        t = tbs[i]
        print(f"  表{i} shape={t.shape}")
        print("   ", t.head(4).to_string()[:420].replace("\n", "\n    "))
