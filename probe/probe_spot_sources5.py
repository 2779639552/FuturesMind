# -*- coding: utf-8 -*-
"""探针 7: 生意社(100ppi)直连 + 其他源, 攻 SC/LU/CJ。

兼查: futures_spot_price 的 100ppi 品种映射表里到底有没有 SC/LU/CJ。
"""
import inspect
import re
import warnings

import akshare as ak

warnings.filterwarnings("ignore")


def sec(t):
    print("\n" + "=" * 74)
    print(t)
    print("=" * 74)


sec("1. 读 futures_spot_price 源码, 看 100ppi 品种映射")
try:
    src = inspect.getsource(ak.futures_spot_price)
    print(src[:3000])
except Exception as e:  # noqa: BLE001
    print("  [ERR]", e)

sec("2. akshare 里所有 spot 相关顶层函数(排除股票/债券/基金)")
names = [n for n in dir(ak) if n.startswith("spot_") or "spot_price" in n]
for n in sorted(names):
    if any(k in n for k in ("stock", "bond", "fund", "etf", "lof", "index", "forex", "crypto", "fx_")):
        continue
    f = getattr(ak, n)
    try:
        print(f"   {n}{inspect.signature(f)}")
    except Exception:  # noqa: BLE001
        print(f"   {n}")

sec("3. 卓创/其他 农产品现货(红枣候选)")
for n in sorted(dir(ak)):
    if any(k in n for k in ("soozhu", "cofeed", "zhuochuang", "agri")):
        print("   ", n)

sec("4. 上期能源/上海期货 - 原油现货相关")
for n in sorted(dir(ak)):
    if n.startswith("energy_") or "crude" in n or "ine" in n.lower():
        print("   ", n)

sec("5. 生意社官网直连测试(100ppi)")
import requests
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
for url in ("https://www.100ppi.com/",
            "https://www.100ppi.com/price/",
            "https://price.100ppi.com/"):
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        print(f"   {url} -> {r.status_code} len={len(r.text)}")
    except Exception as e:  # noqa: BLE001
        print(f"   {url} -> ERR {type(e).__name__}: {str(e)[:80]}")
