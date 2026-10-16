# -*- coding: utf-8 -*-
"""探针 3: 专攻 SC(原油)/LU(低硫燃料油)/CJ(红枣) 的现货源。"""
import inspect
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
            print(f"  [空] {label}: 返回 None")
            return None
        print(f"  [OK] {label}: shape={d.shape} cols={list(d.columns)[:8]}")
        print("       ", d.head(3).to_string()[:500].replace("\n", "\n        "))
        return d
    except Exception as e:  # noqa: BLE001
        print(f"  [ERR] {label}: {type(e).__name__}: {str(e)[:160]}")
        return None


sec("A. 列出 energy / crude / oil 相关函数")
for n in sorted(dir(ak)):
    if any(k in n for k in ("energy_", "crude", "oil_", "gas")):
        print("  ", n)

sec("B. futures_spot_sys 签名")
fn = getattr(ak, "futures_spot_sys", None)
if fn:
    print("  sig:", inspect.signature(fn))
    print("  doc:", (fn.__doc__ or "").strip().splitlines()[0][:120])

sec("C. futures_spot_stock 签名")
fn = getattr(ak, "futures_spot_stock", None)
if fn:
    print("  sig:", inspect.signature(fn))
    print("  doc:", (fn.__doc__ or "").strip().splitlines()[0][:120])

sec("D. 红枣现货候选")
for n, kw in (("futures_spot_sys", {"symbol": "红枣"}),
              ("futures_spot_stock", {"symbol": "红枣"}),
              ("spot_price_qh", {"symbol": "红枣"})):
    f = getattr(ak, n, None)
    if f:
        try_fn(f"{n}(红枣)", f, **kw)

sec("E. 原油 SC 现货候选")
for n, kw in (("futures_spot_sys", {"symbol": "原油"}),
              ("energy_crude_oil_hist", {}),
              ("energy_oil_hist", {}),
              ("futures_global_spot_em", {})):
    f = getattr(ak, n, None)
    if f:
        try_fn(f"{n}", f, **kw)

sec("F. 中国原油现货/期货 (spot_price_qh 系列)")
for n in sorted(dir(ak)):
    if n.startswith("spot_") and "qh" in n:
        f = getattr(ak, n)
        try:
            print(f"  {n}{inspect.signature(f)}")
        except Exception:  # noqa: BLE001
            print(f"  {n}")

sec("G. 生意社-期货品种对照表(看 CJ/SC/LU 是否在内)")
try:
    tbl = ak.futures_spot_price  # 单日快照的 vars_list 代表生意社覆盖集合
    src = inspect.getsource(tbl)
    for key in ("CJ", "'SC'", "'LU'", "'NR'"):
        print(f"   vars_list 含 {key}: {key in src}")
except Exception as e:  # noqa: BLE001
    print("  [ERR]", e)

sec("H. 交易所仓单/现货类函数(可能含现货价)")
for n in sorted(dir(ak)):
    if n.startswith("futures_") and any(k in n for k in ("warehouse", "receipt", "stock")):
        print("  ", n)
