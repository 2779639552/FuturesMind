# -*- coding: utf-8 -*-
"""探针 10(收敛): 剩余可能源一次测完 —— 目标 SC/LU/CJ。"""
import inspect
import warnings

import requests
from bs4 import BeautifulSoup

warnings.filterwarnings("ignore")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Referer": "https://www.100ppi.com/"}


def sec(t):
    print("\n" + "=" * 74)
    print(t)
    print("=" * 74)


sec("1. 100ppi 品种专属页 sf/NN.html —— 看是否含历史序列")
for pid, name in ((45, "原油"), (16, "低硫燃料油"), (44, "红枣"), (17, "燃料油"), (34, "棉花")):
    u = f"https://www.100ppi.com/sf/{pid}.html"
    try:
        r = requests.get(u, headers=H, timeout=25)
        s = BeautifulSoup(r.text, "lxml")
        tbs = s.find_all("table")
        print(f"\n  {name}({pid}) {u} -> {r.status_code} len={len(r.text)} tables={len(tbs)}")
        for i, tb in enumerate(tbs[:4]):
            rows = tb.find_all("tr")
            print(f"     表{i}: {len(rows)} 行")
            for rr in rows[:3]:
                cells = [c.get_text(strip=True) for c in rr.find_all(["th", "td"])][:7]
                if cells:
                    print("        ", cells)
        # 找 "历史" 或 日期链接
        dates = [a.get("href") for a in s.find_all("a", href=True)
                 if "day-" in str(a.get("href")) or "history" in str(a.get("href"))]
        print(f"     历史链接样例: {dates[:4]}")
    except Exception as e:  # noqa: BLE001
        print(f"  {name}({pid}) ERR {type(e).__name__}: {str(e)[:80]}")

sec("2. futures_spot_sys 正确品种名(从源码/表找)")
import akshare as ak
try:
    src = inspect.getsource(ak.futures_spot_sys)
    print(src[:1400])
except Exception as e:  # noqa: BLE001
    print("  ERR", e)

sec("3. 金投网期货现货对比")
for u in ("https://futures.cngold.org/",
          "https://www.cngold.org/futures/"):
    try:
        r = requests.get(u, headers={"User-Agent": UA}, timeout=20)
        print(f"  {u} -> {r.status_code} len={len(r.text)} 含原油={'原油' in r.text} 含红枣={'红枣' in r.text}")
    except Exception as e:  # noqa: BLE001
        print(f"  {u} -> ERR {type(e).__name__}: {str(e)[:70]}")
