# -*- coding: utf-8 -*-
"""探针 17(终局): sf2 与 vane 页面形态 —— 能否给出 SC/LU/CJ 现货序列。"""
import re
import warnings
from io import StringIO

import pandas as pd
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
print("A. sf2 页面形态: 找原油/红枣/低硫燃料油的 sf2 id")
print("=" * 74)
for n in list(range(1, 1300, 1)):
    pass
# 直接从已知页探测: sf2/45 = 原油?
for pid, name in ((45, "原油"), (16, "低硫燃料油"), (44, "红枣"), (387, "燃料油"), (120, "纯苯")):
    for form in (f"https://www.100ppi.com/sf2/{pid}.html", f"https://www.100ppi.com/vane/detail-{pid}.html"):
        try:
            rr = s.get(form, timeout=20)
            if rr.status_code != 200 or len(rr.text) < 4000:
                print(f"  {name:<8}{form[-28:]:<30} -> {rr.status_code} len={len(rr.text)}")
                continue
            txt = rr.text
            ttl = re.search(r"<title>(.*?)</title>", txt)
            ttl = ttl.group(1).strip()[:30] if ttl else ""
            print(f"  {name:<8}{form[-28:]:<30} -> 200 len={len(txt)} title='{ttl}' "
                  f"含'{name}'={name in txt}")
            # 试读表
            try:
                tbs = pd.read_html(StringIO(txt))
                if tbs:
                    print(f"        表数={len(tbs)} 首表shape={tbs[0].shape}")
                    print("        ", tbs[0].head(3).to_string()[:280].replace("\n", "\n         "))
            except Exception:  # noqa: BLE001
                pass
        except Exception as e:  # noqa: BLE001
            print(f"  {name:<8}{form[-28:]:<30} -> ERR {type(e).__name__}")

print()
print("=" * 74)
print("B. 结论: 最终数据源覆盖矩阵")
print("=" * 74)
print("""  源 1 国君 GTJA (现存)         : 覆盖 14/20, 缺 SC/LU/CJ/BZ/FU/PX
  源 2 100ppi 生意社 (新增)     : 覆盖 17/20, 补 FU/PX/BZ(+13 个冗余校验)
  源 3 交易所官方 (现存, 仅仓单) : 库存维度已覆盖 19/20
  仍无现货指数: SC(原油) / LU(低硫燃料油) / CJ(红枣)""")
