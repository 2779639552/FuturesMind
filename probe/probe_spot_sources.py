# -*- coding: utf-8 -*-
"""探针: 为无国君现货指数覆盖的品种(SC/FU/LU/PX/CJ/BZ)寻找替代现货价源。

只读探针, 不修改任何生产数据 / 不写缓存。目标:
  1. 列出 akshare 中现货/基差相关函数
  2. 逐个候选源测试对这 6 个品种的覆盖情况(能否取到历史序列, >=10 点)
"""
import inspect
import traceback

import akshare as ak

TARGETS = {
    "SC": "原油", "FU": "燃料油", "LU": "低硫燃料油",
    "PX": "对二甲苯", "CJ": "红枣", "BZ": "纯苯",
}


def sec(t):
    print("\n" + "=" * 72)
    print(t)
    print("=" * 72)


def safe(label, fn, *a, **kw):
    try:
        out = fn(*a, **kw)
        print(f"[OK] {label}")
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[ERR] {label}: {type(e).__name__}: {e}")
        traceback.print_exc(limit=1)
        return None


sec("1. akshare 现货/基差相关函数")
names = [n for n in dir(ak) if any(k in n.lower() for k in ("spot", "basis"))]
for n in sorted(names):
    print("  ", n)

sec("2. 候选源签名")
for fn_name in ("futures_spot_price", "futures_spot_price_daily", "futures_spot_price_previous"):
    fn = getattr(ak, fn_name, None)
    if fn is None:
        print(f"  {fn_name}: 不存在")
        continue
    try:
        print(f"  {fn_name}{inspect.signature(fn)}")
        doc = (fn.__doc__ or "").strip().splitlines()
        if doc:
            print("       doc:", doc[0][:110])
    except Exception as e:  # noqa: BLE001
        print(f"  {fn_name}: {e}")

sec("3. futures_spot_price_daily 覆盖测试(区间取历史序列)")
df = safe("futures_spot_price_daily(20260901~20260929)",
          ak.futures_spot_price_daily, start_day="20260901", end_day="20260929")
if df is not None and not df.empty:
    print("  shape:", df.shape)
    print("  columns:", list(df.columns))
    print(df.head(3).to_string())
    vcol = "symbol" if "symbol" in df.columns else df.columns[0]
    uniq = sorted({str(x) for x in df[vcol].unique()})
    print(f"  品种数: {len(uniq)}")
    print("  全部:", uniq)
    hit = [t for t in TARGETS if t in uniq]
    print("  >>> 命中目标品种:", hit or "无")
else:
    print("  无数据")

sec("4. futures_spot_price 单日快照覆盖测试")
df2 = safe("futures_spot_price(20260929)", ak.futures_spot_price, date="20260929")
if df2 is not None and not df2.empty:
    print("  shape:", df2.shape)
    print("  columns:", list(df2.columns))
    print(df2.to_string()[:2400])
else:
    print("  无数据")
