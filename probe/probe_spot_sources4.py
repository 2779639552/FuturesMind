# -*- coding: utf-8 -*-
"""探针 4: SC/LU/CJ 最后一轮 —— 东财现货与股票、生意社现期图可用品种、外部网页源。"""
import warnings

import akshare as ak

warnings.filterwarnings("ignore")


def sec(t):
    print("\n" + "=" * 74)
    print(t)
    print("=" * 74)


def try_fn(label, fn, **kw):
    try:
        d = fn(**kw)
        if d is None:
            print(f"  [空] {label}")
            return None
        print(f"  [OK] {label}: shape={d.shape} cols={list(d.columns)[:9]}")
        print("       ", d.head(4).to_string()[:600].replace("\n", "\n        "))
        return d
    except Exception as e:  # noqa: BLE001
        print(f"  [ERR] {label}: {type(e).__name__}: {str(e)[:130]}")
        return None


sec("A. futures_spot_stock 各品类(东财 现货与股票)")
for cat in ("能源", "化工", "农产品", "有色"):
    try_fn(f"futures_spot_stock({cat})", ak.futures_spot_stock, symbol=cat)

sec("B. 生意社现期图 可用 symbol 试探")
for sym in ("原油", "燃料油", "液化气", "红枣", "WTI原油", "布伦特原油", "沥青"):
    try_fn(f"futures_spot_sys({sym})", ak.futures_spot_sys, symbol=sym)

sec("C. 生意社 spot_price_qh 支持的品种名(读源码表)")
try:
    import inspect
    src = inspect.getsource(ak.spot_price_qh)
    import re
    names = re.findall(r"['\"]([\u4e00-\u9fa5]{2,8})['\"]", src)
    print("  源码中出现的中文名:", sorted(set(names))[:60] or "(无)")
    print("  是否含 原油:", "原油" in names, "| 红枣:", "红枣" in names, "| 燃料油:", "燃料油" in names)
except Exception as e:  # noqa: BLE001
    print("  [ERR]", e)

sec("D. 新浪外盘/内盘现货相关")
for n in sorted(dir(ak)):
    if n.startswith("futures_") and any(k in n for k in ("sina", "js", "em")) and "receipt" not in n and "warehouse" not in n:
        print("  ", n)

sec("E. 红枣专项: 郑商所红枣现货/交割相关")
for n in sorted(dir(ak)):
    if "cj" in n.lower() or "hongzao" in n.lower() or "jujube" in n.lower():
        print("  ", n)
print("  (以上为空表示 akshare 无红枣专用接口)")

sec("F. 通用: 商品现货价格 - 生意社全量 spot_price_table_qh")
try:
    tbl = ak.spot_price_table_qh()
    print("  shape:", tbl.shape)
    print(tbl.to_string())
except Exception as e:  # noqa: BLE001
    print("  [ERR]", e)
