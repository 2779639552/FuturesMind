# -*- coding: utf-8 -*-
"""技术面 × 基本面「四格回测」—— 分歧时谁占优 + 权重扫描。

【要回答的三个问题】
  Q1 技术与基本面同向时，收益是否真的更好（共振溢价）？
  Q2 两者分歧时，未来 h 日收益更像技术还是更像基本面？
  Q3 最优组合权重 w（技术占比）是多少？是否显著优于 w=0 / w=1？

【口径】
  · 价格：akshare 主连日线（项目内 get_futures_price 同源），2020-01 起
  · 仓单：国君 warehouse.on_warrant（交易所注册仓单，准官方），2022-01 起
  · 库存：国君 社会库存（全量数据集），约 5 年，周频
  · 基差：国君 basis.value / spot_price，仅 2023-11 起（短板，单列辅证）

【防前视偏差】
  1) 所有基本面序列按「观测日 + 发布滞后 P 天」才允许被使用，P 做 0/7/14 三档敏感性；
     库存是周频且普遍滞后发布，P=0 必然虚高，主结论只看 P=7。
  2) 前瞻收益从信号日收盘起算（同 bar 收盘决策），不做次日开盘修正——这是乐观口径，
     真实交易会有 1 日滑点，结论只用于「哪种组合更优」而非绝对收益。

【为什么不用 fetch_indicator_seasonal】它把闰年 doy 对齐到平年（_doy），日期会被改写，
  回测会错位。这里直接调底层 _src_* / _full_dataset_source 拿原始 {date,value}。
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

import gtja_module as g  # noqa: E402
from tradingagents.dataflows import gtja_api as G  # noqa: E402
from tradingagents.dataflows.commodity_futures import get_futures_price  # noqa: E402

CACHE_DIR = os.path.join(ROOT, "probe", "_bt_cache")
OUT_JSON = os.path.join(ROOT, "probe", "_bt_result.json")
os.makedirs(CACHE_DIR, exist_ok=True)

START = "2019-06-01"
TODAY = dt.date.today().isoformat()
HORIZONS = (5, 10, 20)
LAGS = (0, 7, 14)


# ────────────────────────────── 取数 ──────────────────────────────
def _parse_price(code: str) -> list[tuple[str, float]]:
    raw = get_futures_price(code, START, TODAY)
    out: list[tuple[str, float]] = []
    for line in str(raw or "").strip().split("\n"):
        p = line.split(",")
        if len(p) >= 5 and p[0].strip()[:4].isdigit():
            try:
                out.append((p[0].strip()[:10], float(p[4])))
            except ValueError:
                continue
    return out


def _series_of(src: dict, iid: str) -> list[tuple[str, float]]:
    ind = next((i for i in (src.get("indicators") or []) if i.get("id") == iid), None)
    if not ind:
        return []
    out = []
    for r in ind.get("series") or []:
        # 两种形态都要吃: 底层原始是 {date,value}, _tail 之后是 [date, value]
        if isinstance(r, (list, tuple)):
            d, v = (r + [None, None])[:2] if len(r) >= 2 else (None, None)
        else:
            d, v = r.get("date"), r.get("value")
        if d and v is not None:
            try:
                out.append((str(d)[:10], float(v)))
            except (TypeError, ValueError):
                continue
    out.sort(key=lambda x: x[0])
    return out


def fetch_one(code: str) -> dict:
    """取一个品种的全部原始序列，落盘缓存（回测会反复调，别每次打接口）。"""
    cp = os.path.join(CACHE_DIR, f"{code}.json")
    if os.path.exists(cp):
        with open(cp, encoding="utf-8") as f:
            return json.load(f)

    res: dict = {"code": code, "price": [], "inv": [], "inv_id": "", "ck": [],
                 "basis": [], "spot": [], "err": {}}

    try:
        res["price"] = _parse_price(code)
        if not res["price"]:
            res["err"]["price"] = "empty"
    except Exception as e:  # noqa: BLE001
        res["err"]["price"] = f"{type(e).__name__}: {e}"[:80]

    # 库存：全量数据集，优先「社会库存」，退而「期货库存」
    try:
        src = g._full_dataset_source(
            code, G.EP_GTJA_INVENTORY, G._GTJA_INV_CACHE_DIR,
            "inventoryValue", "库存", ["期货库存", "社会库存", "工厂库存"], 3,
            "国君自建库存数据集", series_cap=0)
        cands = [i.get("id") for i in (src.get("indicators") or []) if str(i.get("id", "")).startswith("库存.")]
        pick = next((c for c in cands if "社会" in c), None) or (cands[0] if cands else None)
        if pick:
            res["inv_id"] = pick
            res["inv"] = _series_of(src, pick)
        else:
            res["err"]["inv"] = "no-id"
    except Exception as e:  # noqa: BLE001
        res["err"]["inv"] = f"{type(e).__name__}: {e}"[:80]

    # 仓单
    try:
        src = g._src_warehouse(code, START, TODAY, series_cap=0)
        res["ck"] = _series_of(src, "warehouse.on_warrant")
        if not res["ck"]:
            res["err"]["ck"] = src.get("error") or "empty"
    except Exception as e:  # noqa: BLE001
        res["err"]["ck"] = f"{type(e).__name__}: {e}"[:80]

    # 基差
    try:
        src = g._src_basis(code, START, TODAY, series_cap=0)
        res["basis"] = _series_of(src, "basis.value")
        res["spot"] = _series_of(src, "basis.spot_price")
        if not res["basis"]:
            res["err"]["basis"] = src.get("error") or "empty"
    except Exception as e:  # noqa: BLE001
        res["err"]["basis"] = f"{type(e).__name__}: {e}"[:80]

    with open(cp, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    return res


# ────────────────────────────── 对齐 ──────────────────────────────
def asof_map(series: list[list], dates: list[str], lag_days: int) -> list[float | None]:
    """把低频序列按「观测日 + lag」映射到每个交易日（严格 as-of，不向前偷看）。

    【为什么不用 pandas ffill】ffill 只对齐到观测日当天，等于假设当天收盘就能拿到
      周频数据——这正是回测虚高的常见来源。这里显式加发布滞后。
    """
    if not series:
        return [None] * len(dates)
    obs_d = [s[0] for s in series]
    obs_v = [s[1] for s in series]
    out: list[float | None] = []
    for d in dates:
        cutoff = (dt.date.fromisoformat(d) - dt.timedelta(days=lag_days)).isoformat()
        i = bisect.bisect_right(obs_d, cutoff) - 1
        out.append(obs_v[i] if i >= 0 else None)
    return out


def pct_rank_trailing(vals: list[float | None], window: int = 250) -> list[float | None]:
    """滚动分位（只用当日及以前的值）。"""
    out: list[float | None] = [None] * len(vals)
    for i, v in enumerate(vals):
        if v is None:
            continue
        lo = max(0, i - window + 1)
        hist = [x for x in vals[lo:i + 1] if x is not None]
        if len(hist) >= 60:
            out[i] = sum(1 for x in hist if x <= v) / len(hist)
    return out


def _chg(series: list[float | None], i: int, back: int) -> float | None:
    if i - back < 0:
        return None
    a, b = series[i], series[i - back]
    if a is None or b is None or b == 0:
        return None
    return a / b - 1.0


# ────────────────────────────── 统计 ──────────────────────────────
def _stat(xs: list[float]) -> dict:
    n = len(xs)
    if n == 0:
        return {"n": 0, "mean": None, "t": None, "win": None}
    mu = sum(xs) / n
    if n > 1:
        sd = (sum((x - mu) ** 2 for x in xs) / (n - 1)) ** 0.5
        t = mu / (sd / n ** 0.5) if sd > 0 else 0.0
    else:
        sd, t = 0.0, 0.0
    win = sum(1 for x in xs if x > 0) / n
    return {"n": n, "mean": round(mu * 100, 3), "t": round(t, 2), "win": round(win * 100, 1)}


def _asof(obs: list[tuple[str, float]], dates: list[str], lag: int) -> list[float | None]:
    """把 (观测日, 值) 序列按「观测日 + lag」映射到交易日（严格不偷看未来）。"""
    if not obs:
        return [None] * len(dates)
    ds = [o[0] for o in obs]
    vs = [o[1] for o in obs]
    out: list[float | None] = []
    for d in dates:
        cutoff = (dt.date.fromisoformat(d) - dt.timedelta(days=lag)).isoformat()
        i = bisect.bisect_right(ds, cutoff) - 1
        out.append(vs[i] if i >= 0 else None)
    return out


def _chg_over_obs(vals: list[float], back: int) -> list[float | None]:
    """按**观测个数**算变化率（周频序列的 back=4 就是 4 周，不是 4 天）。

    ⚠ 这是本脚本第一版最大的坑：先把周频 ffilled 到日频再取 back=4，等价于"4 天变化"，
      实测 daily 对齐后 90% 的日期 inv4 恒等于 0 —— 信号被抹平，四格几乎全空。
    """
    out: list[float | None] = [None] * len(vals)
    for k in range(back, len(vals)):
        a, b = vals[k], vals[k - back]
        if b not in (None, 0) and a is not None:
            out[k] = a / b - 1.0
    return out


def build_panel(raw: dict, lag: int) -> list[dict]:
    price = raw.get("price") or []
    if len(price) < 120:
        return []
    dates = [p[0] for p in price]
    close = [p[1] for p in price]
    n = len(dates)

    # ① 库存：周频，变化率按"4 个观测(≈4 周)"算，取负号 ⇒ 去库为正
    inv_obs = [(d, v) for d, v in (raw.get("inv") or [])]
    inv_sig = _asof(list(zip([o[0] for o in inv_obs],
                             _chg_over_obs([o[1] for o in inv_obs], 4))), dates, lag) if inv_obs else [None] * n

    # ② 仓单：日频，5 个交易日变化，取负 ⇒ 仓单减少(现货转紧)为正
    ck_obs = [(d, v) for d, v in (raw.get("ck") or [])]
    ck_sig = _asof(list(zip([o[0] for o in ck_obs],
                            _chg_over_obs([o[1] for o in ck_obs], 5))), dates, lag) if ck_obs else [None] * n

    # ③ 基差率 = 基差/现货，取 250 个观测的滚动分位（在原始日频上算，再映射）
    bpct_sig = [None] * n
    bmap = {d: v for d, v in (raw.get("basis") or [])}
    smap = {d: v for d, v in (raw.get("spot") or [])}
    common = sorted(set(bmap) & set(smap))
    if len(common) >= 120:
        brate = [bmap[d] / smap[d] for d in common if smap[d]]
        if len(brate) == len(common):
            pr = pct_rank_trailing(brate, 250)
            bpct_sig = _asof(list(zip(common, pr)), dates, lag)

    rows = []
    for i in range(n):
        if i < 60 or i + max(HORIZONS) >= n:
            continue
        mom20 = close[i] / close[i - 20] - 1.0
        mom60 = close[i] / close[i - 60] - 1.0
        rows.append({
            "d": dates[i], "close": close[i],
            "mom20": mom20, "mom60": mom60,
            "inv4": inv_sig[i], "ck5": ck_sig[i], "bpct": bpct_sig[i],
            "fwd": {h: close[i + h] / close[i] - 1.0 for h in HORIZONS},
        })
    return rows


def cell_table(rows: list[dict], tech_key: str, fund_of_row, h: int, step: int = 1) -> dict:
    """四格表 + 分歧格的『谁赢』。

    step>1 时按 step 抽样，降低重叠收益带来的伪显著。
    """
    buckets: dict[tuple[int, int], list[float]] = {}
    for k, r in enumerate(rows):
        if k % step:
            continue
        t = r.get(tech_key)
        f = fund_of_row(r)
        if t is None or f is None:
            continue
        ts = 1 if t > 0 else (-1 if t < 0 else 0)
        fs = 1 if f > 0 else (-1 if f < 0 else 0)
        if ts == 0 or fs == 0:
            continue
        buckets.setdefault((ts, fs), []).append(r["fwd"][h])

    out = {}
    for key, xs in buckets.items():
        out[f"{key[0]},{key[1]}"] = _stat(xs)

    # 分歧：ts != fs。看「按技术方向持有」与「按基本面方向持有」的收益差
    dis = [(t, f, x) for (t, f), xs in buckets.items() if t != f for x in xs]
    tech_side = [x * t for (t, f, x) in dis]
    fund_side = [x * f for (t, f, x) in dis]
    agree = [x * t for (t, f), xs in buckets.items() if t == f for x in xs]
    out["_agree"] = _stat(agree)
    out["_dis_tech"] = _stat(tech_side)
    out["_dis_fund"] = _stat(fund_side)
    return out


def weight_scan(rows: list[dict], tech_key: str, fund_of_row, h: int) -> list[dict]:
    """w = 技术权重；position = sign(w*s_t + (1-w)*s_f)。非重叠抽样（每 h 天取一次）。"""
    res = []
    for wi in range(0, 11):
        w = wi / 10.0
        rets = []
        for k, r in enumerate(rows):
            if k % h:
                continue
            t = r.get(tech_key)
            f = fund_of_row(r)
            if t is None or f is None:
                continue
            ts = 1 if t > 0 else -1
            fs = 1 if f > 0 else -1
            score = w * ts + (1 - w) * fs
            if abs(score) < 0.3:      # 恰好对冲掉 ⇒ 不持仓
                continue
            rets.append(r["fwd"][h] * (1 if score > 0 else -1))
        s = _stat(rets)
        # 年化：每日 1 次抽样，h 日一换；粗略折算
        mu, n, sd_t = s["mean"], s["n"], s["t"]
        ann = None
        if mu is not None and n:
            ann = mu * (244.0 / h)
        res.append({"w": round(w, 1), "n": s["n"], "mean_pct": mu, "t": sd_t,
                    "win": s["win"], "ann_pct": None if ann is None else round(ann, 2)})
    return res


def main() -> None:
    import quant_module as q
    pool = q._pool()
    print(f"[pool] {len(pool)} 品种: {', '.join(pool)}", flush=True)

    raws = {}
    for k, code in enumerate(pool, 1):
        try:
            raw = fetch_one(code)
        except Exception as e:  # noqa: BLE001
            print(f"[{k}/{len(pool)}] {code} FAIL {type(e).__name__}: {e}", flush=True)
            continue
        raws[code] = raw
        print("[%d/%d] %-4s price=%-4d inv=%-4d(%s) ck=%-4d basis=%-4d  err=%s"
              % (k, len(pool), code, len(raw["price"]), len(raw["inv"]), raw["inv_id"],
                 len(raw["ck"]), len(raw["basis"]), raw["err"] or "-"), flush=True)

    FUND_DEFS = {
        "库存(去库=多)": lambda r: (None if r["inv4"] is None else -r["inv4"]),
        "仓单(减=多)": lambda r: (None if r["ck5"] is None else -r["ck5"]),
        "基差分位(贴水=多)": lambda r: (None if r["bpct"] is None else (r["bpct"] - 0.5)),
    }
    TECH_DEFS = {"动量20日": "mom20", "动量60日": "mom60"}

    result: dict = {"pool": pool, "generated": dt.datetime.now().isoformat(timespec="seconds"),
                    "coverage": {}, "cells": {}, "weights": {}, "by_variety": {}}

    for lag in LAGS:
        panels = {c: build_panel(raws[c], lag) for c in raws if raws[c].get("price")}
        panels = {c: v for c, v in panels.items() if v}
        if lag == 7:
            result["coverage"] = {c: len(v) for c, v in panels.items()}
        print(f"\n===== lag={lag} 天, 可用品种 {len(panels)} =====", flush=True)

        for tname, tkey in TECH_DEFS.items():
            for fname, ffn in FUND_DEFS.items():
                for h in HORIZONS:
                    pooled = [r for v in panels.values() for r in v]
                    # ★ 非重叠抽样(每 h 天取一次): 重叠样本会让 t 值虚高一倍以上
                    tbl = cell_table(pooled, tkey, ffn, h, step=h)
                    dis_n = (tbl.get("_dis_tech") or {}).get("n", 0)
                    if dis_n < 30:
                        continue
                    key = f"{tname}×{fname}×{h}d×lag{lag}"
                    result["cells"][key] = tbl
                    print("  %-34s 分歧 n=%-4d 技术侧 %+.3f%%(t=%5.2f) 基本面侧 %+.3f%%(t=%5.2f) "
                          "同向 %+.3f%%(n=%d) [非重叠]"
                          % (key, dis_n,
                             tbl["_dis_tech"]["mean"], tbl["_dis_tech"]["t"] or 0,
                             tbl["_dis_fund"]["mean"], tbl["_dis_fund"]["t"] or 0,
                             tbl["_agree"]["mean"] or 0, tbl["_agree"]["n"]), flush=True)

                    if h == 10:
                        w = weight_scan(pooled, tkey, ffn, h)
                        result["weights"][key] = w
                        best = max(w, key=lambda x: (x["ann_pct"] if x["ann_pct"] is not None else -99))
                        print("      权重扫描(10日, 非重叠) 最优 w=%.1f 年化 %+.1f%% | "
                              "w=0 %+.1f%% / w=0.5 %+.1f%% / w=1 %+.1f%%"
                              % (best["w"], best["ann_pct"] or 0,
                                 w[0]["ann_pct"] or 0, w[5]["ann_pct"] or 0, w[10]["ann_pct"] or 0),
                              flush=True)

        # 分品种一致性（只看 10 日、动量20×库存）
        if lag == 7:
            per = {}
            for c, rows in panels.items():
                tbl = cell_table(rows, "mom20", FUND_DEFS["库存(去库=多)"], 10, step=1)
                if (tbl.get("_dis_tech") or {}).get("n", 0) >= 15:
                    per[c] = {"n": tbl["_dis_tech"]["n"],
                              "tech": tbl["_dis_tech"]["mean"],
                              "fund": tbl["_dis_fund"]["mean"]}
            result["by_variety"] = per

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print("\n[saved]", OUT_JSON, flush=True)


if __name__ == "__main__":
    main()
