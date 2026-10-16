# -*- coding: utf-8 -*-
"""探针 11(收尾): SC/LU/CJ 最终确认 + 东财现货品类全扫。"""
import warnings

import requests

warnings.filterwarnings("ignore")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def sec(t):
    print("\n" + "=" * 74)
    print(t)
    print("=" * 74)


sec("1. sf/792.html 636字节内容是什么")
r = requests.get("https://www.100ppi.com/sf/792.html", headers={"User-Agent": UA}, timeout=25)
print("  status", r.status_code, "len", len(r.text))
print("  text:", r.text[:500].replace("\n", " "))

sec("2. 东财现货(期货_现货_股票) 全品类扫 目标品种")
import akshare as ak
for cat in ("能源", "化工", "农产品", "有色", "钢铁", "建材", "橡塑", "纺织"):
    try:
        d = ak.futures_spot_stock(symbol=cat)
        names = list(d["商品名称"]) if "商品名称" in d.columns else []
        hit = [n for n in names if any(k in str(n) for k in ("原油", "燃料油", "红枣", "纯苯", "二甲苯", "棉花"))]
        print(f"  {cat}: {len(names)} 项 | 目标命中: {hit or '无'}")
        if cat in ("能源", "化工"):
            print(f"     全部: {names}")
    except Exception as e:  # noqa: BLE001
        print(f"  {cat}: ERR {type(e).__name__}: {str(e)[:70]}")

sec("3. 新浪 内盘各品种现货代码试探")
for sym in ("nf_SC0", "nf_FU0", "nf_CJ0", "nf_LU0", "nf_PX0", "nf_BZ0"):
    try:
        rr = requests.get(f"https://hq.sinajs.cn/list={sym}",
                          headers={"User-Agent": UA, "Referer": "https://finance.sina.com.cn"}, timeout=15)
        print(f"  {sym}: {rr.text[:130]}")
    except Exception as e:  # noqa: BLE001
        print(f"  {sym}: ERR {type(e).__name__}")

sec("4. 100ppi 快照页里目标品种(单日, 可日更)")
import re
from bs4 import BeautifulSoup
for pid, name in ((45, "原油类"), (16, "能源类")):
    try:
        rr = requests.get(f"https://www.100ppi.com/mprice/plist-1-{pid}-1.html",
                          headers={"User-Agent": UA, "Referer": "https://www.100ppi.com/"}, timeout=25)
        s = BeautifulSoup(rr.text, "lxml")
        tb = s.find("table")
        rows = tb.find_all("tr") if tb else []
        print(f"\n  {name}(plist-1-{pid}-1) {rr.status_code} rows={len(rows)}")
        for tr in rows[:12]:
            cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])][:7]
            if cells:
                print("     ", cells)
    except Exception as e:  # noqa: BLE001
        print(f"  {name}: ERR {type(e).__name__}: {str(e)[:70]}")
