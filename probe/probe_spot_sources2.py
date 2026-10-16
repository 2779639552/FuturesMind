# -*- coding: utf-8 -*-
"""探针 2: 补测 SC/LU/CJ, 并验证生意社源的历史长度与字段口径。"""
import warnings

import akshare as ak

warnings.filterwarnings("ignore")

POOL = ["SC", "TA", "PX", "EG", "FU", "LU", "PG", "M", "CF", "CJ",
        "LC", "RU", "NR", "BR", "LH", "BU", "BZ", "EB", "SI", "PS"]
MISS = ["SC", "FU", "LU", "PX", "CJ", "BZ"]


def sec(t):
    print("\n" + "=" * 72)
    print(t)
    print("=" * 72)


sec("1. futures_spot_price_daily 全池覆盖测试(2026-01-01 ~ 2026-09-29)")
try:
    df = ak.futures_spot_price_daily(start_day="20260101", end_day="20260929")
    print("  shape:", df.shape)
    cov = df.groupby("symbol").size().to_dict()
    print(f"  {'品种':<6}{'数据点数':<10}{'状态'}")
    for c in POOL:
        n = cov.get(c, 0)
        flag = "缺失" if n == 0 else ("充足" if n >= 10 else "偏少")
        print(f"  {c:<6}{n:<10}{flag}")
    hit = [c for c in POOL if cov.get(c, 0) >= 10]
    print(f"\n  >>> 该源可补全 {len(hit)}/{len(POOL)}: {hit}")
    print(f"  >>> 仍缺: {[c for c in POOL if cov.get(c, 0) == 0]}")
    # 样本口径
    s = df[df["symbol"] == "PX"].tail(2)
    if not s.empty:
        print("\n  PX 样本行(验证字段口径):")
        print("   ", s[["date", "symbol", "spot_price", "dominant_contract",
                        "dominant_contract_price", "dom_basis", "dom_basis_rate"]].to_string(index=False))
except Exception as e:  # noqa: BLE001
    print("  [ERR]", type(e).__name__, e)

sec("2. 生意社商品现货价格(spot_price_qh / spot_price_table_qh)")
for fn_name in ("spot_price_qh", "spot_price_table_qh"):
    fn = getattr(ak, fn_name, None)
    if fn is None:
        print(f"  {fn_name}: 不存在")
        continue
    try:
        d = fn()
        print(f"  [OK] {fn_name} shape={d.shape} cols={list(d.columns)}")
        print("   ", d.head(5).to_string()[:900])
    except Exception as e:  # noqa: BLE001
        print(f"  [ERR] {fn_name}: {type(e).__name__}: {e}")

sec("3. 单日快照 futures_spot_price(20260929) — 查 SC/LU/CJ")
try:
    d = ak.futures_spot_price(date="20260929")
    print("  shape:", d.shape, "cols:", list(d.columns))
    syms = sorted(set(str(x) for x in d["symbol"].unique())) if "symbol" in d.columns else []
    print(f"  品种数 {len(syms)}")
    for c in MISS:
        print(f"    {c}: {'命中' if c in syms else '未覆盖'}")
    print(d[d["symbol"].isin(MISS)].to_string(index=False) if any(c in syms for c in MISS) else "  (目标品种均未覆盖)")
except Exception as e:  # noqa: BLE001
    print("  [ERR]", type(e).__name__, e)

sec("4. 针对 SC 的其他候选源")
CAND = ["futures_global_spot_em", "futures_spot_sys", "futures_spot_stock"]
for n in CAND:
    fn = getattr(ak, n, None)
    print(f"  {n}: {'存在' if fn else '不存在'}")

sec("5. 交易所现货-期货对比(目标品种所在所)")
for n, kw in (("futures_to_spot_czce", {"date": "20260929"}),
              ("futures_to_spot_dce", {"date": "20260929"}),
              ("futures_to_spot_shfe", {"date": "20260929"})):
    fn = getattr(ak, n, None)
    if not fn:
        continue
    try:
        d = fn(**kw)
        print(f"  [OK] {n} shape={d.shape} cols={list(d.columns)}")
        print("   ", d.head(3).to_string()[:600])
    except Exception as e:  # noqa: BLE001
        print(f"  [ERR] {n}: {type(e).__name__}: {e}")
