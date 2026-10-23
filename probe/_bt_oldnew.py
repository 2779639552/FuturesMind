# -*- coding: utf-8 -*-
"""改前 vs 改后：净票命中率与盈利对比。

【改了什么（只此一处）】
  改前: 期限结构票返回「偏多/偏空」，不在旧 dirmap 里 ⇒ 恒计 0。
  改后: 该票按 ±1 计入。
  其余三票（二维四象 / 库存周期 / 利润）两套完全同口径 —— 所以差异**只能**来自这一票。

【怎么保证"忠实复刻"而不是另写一套】
  · 库存周期：直接调 `quant_methodology.inventory_cycle_phase`（生产同一个纯函数）
  · 利润：阈值 0.3/0.7、(cur-lo)/(hi-lo) 与 quant_verdict 一致
  · 四象：`单边做多 = 低库存+低利润+贴水`、`单边做空 = 高+高+升水`，其余不计票（与 _strategy_verdict 一致）

【三处必须声明的近似】
  1) **期限结构**：生产用 akshare 全合约**实时**报价，没有历史 ⇒ 用 sign(基差) 代理
     （back 结构 ⟺ 期货贴水 ⟺ 基差>0，二者同号；这是个**结构性等价**，不是精确复制）。
  2) **库存档位**：生产用 `_inv_dual`（社会库存 + 仓单双口径，背离记"矛盾"），
     这里只用社会库存的扩展分位（0.35/0.65）。⇒ 我的"四象票"会比生产**更常触发单边**，
     但两套口径用的是同一个复刻，比较仍然公平。
  3) **利润分位**：生产用**全样本** min/max（含未来数据）；回测改成**扩展窗口** min/max，
     否则等于偷看未来。这会让利润票比生产更保守。

【评估】position = sign(net)；收益 = 前瞻 h 日收益 × position；命中率 = 同号占比。
  日期聚簇 + 每 h 天非重叠抽样（与上一次回测同一套纪律）。
"""
from __future__ import annotations

import bisect
import datetime as dt
import importlib.util
import json
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

import gtja_module as g  # noqa: E402
import quant_methodology as qm  # noqa: E402
from tradingagents.dataflows import gtja_api as G  # noqa: E402

spec = importlib.util.spec_from_file_location("bt", os.path.join(HERE, "_bt_tech_fund.py"))
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)

CACHE = os.path.join(HERE, "_bt_cache")
LAG = 7
HORIZONS = (5, 10, 20)


# ────────────────────────── 数据 ──────────────────────────
def load_profit(code: str) -> list[tuple[str, float]]:
    cp = os.path.join(CACHE, f"{code}_profit.json")
    if os.path.exists(cp):
        return [tuple(x) for x in json.load(open(cp, encoding="utf-8"))]
    try:
        src = g._full_dataset_source(
            code, G.EP_PROFIT, G._PROFIT_CACHE_DIR, "profitValue",
            "利润", ["加工利润", "进口利润"], g.MAX_PROFIT_LABELS,
            "加工/进口利润", series_cap=0)
    except Exception:  # noqa: BLE001
        return []
    best: list[tuple[str, float]] = []
    for ind in src.get("indicators") or []:
        ser = []
        for r in ind.get("series") or []:
            d, v = (r + [None, None])[:2] if isinstance(r, (list, tuple)) else (r.get("date"), r.get("value"))
            if d and v is not None:
                try:
                    ser.append((str(d)[:10], float(v)))
                except (TypeError, ValueError):
                    continue
        ser.sort()
        if len(ser) > len(best):      # 与 fund_module 同规则: 取样本最长的口径
            best = ser
    json.dump(best, open(cp, "w", encoding="utf-8"), ensure_ascii=False)
    return best


def expanding_pct(vals: list[float]) -> list[float]:
    """扩展窗口分位：第 k 个值在 vals[:k+1] 中的百分位（只用过去，不看未来）。"""
    out, seen = [], []
    for v in vals:
        bisect.insort(seen, v)
        out.append(bisect.bisect_right(seen, v) / len(seen))
    return out


def asof(obs: list[tuple[str, float]], dates: list[str], lag: int) -> list[float | None]:
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


def _grade(p, lo=0.35, hi=0.65) -> str:
    if p is None:
        return "不明"
    return "低" if p <= lo else ("高" if p >= hi else "中")


# ────────────────────────── 组装 ──────────────────────────
def build(code: str) -> list[dict]:
    rp = os.path.join(CACHE, f"{code}.json")
    if not os.path.exists(rp):
        return []
    raw = json.load(open(rp, encoding="utf-8"))
    price = raw.get("price") or []
    if len(price) < 160:
        return []
    dates = [p[0] for p in price]
    close = [p[1] for p in price]
    n = len(dates)

    # ① 库存：扩展分位（档位用）+ 5 观测变化（周期用）
    inv_obs = [(d, v) for d, v in (raw.get("inv") or [])]
    inv_pct_daily = [None] * n
    cyc_daily = [None] * n
    if len(inv_obs) >= 30:
        ipct = expanding_pct([v for _, v in inv_obs])
        inv_pct_daily = asof(list(zip([d for d, _ in inv_obs], ipct)), dates, LAG)
        chg = [None] * len(inv_obs)
        for k in range(5, len(inv_obs)):
            a, b = inv_obs[k][1], inv_obs[k - 5][1]
            if b:
                chg[k] = a / b - 1
        cyc_daily = asof(list(zip([d for d, _ in inv_obs], chg)), dates, LAG)

    # ② 利润：扩展 min/max 分位
    prof_obs = load_profit(code)
    prof_pct_daily = [None] * n
    if len(prof_obs) >= 20:
        vals = [v for _, v in prof_obs]
        lo_hi, lo, hi = [], vals[0], vals[0]
        for v in vals:
            lo, hi = min(lo, v), max(hi, v)
            lo_hi.append(((v - lo) / (hi - lo)) if hi > lo else 0.5)
        prof_pct_daily = asof(list(zip([d for d, _ in prof_obs], lo_hi)), dates, LAG)

    # ③ 基差
    bmap = {d: v for d, v in (raw.get("basis") or [])}
    smap = {d: v for d, v in (raw.get("spot") or [])}
    common = sorted(set(bmap) & set(smap))
    bval_daily, brate_daily = [None] * n, [None] * n
    if common:
        bv = [(d, bmap[d]) for d in common]
        br = [(d, bmap[d] / smap[d]) for d in common if smap[d]]
        bval_daily = asof(bv, dates, LAG)
        brate_daily = asof(br, dates, LAG)

    rows = []
    for i in range(n):
        if i < 70 or i + max(HORIZONS) >= n:
            continue
        px_chg5 = close[i] / close[i - 5] - 1 if close[i - 5] else None

        # ── 库存周期（生产同一纯函数）──
        cyc = 0
        if cyc_daily[i] is not None and px_chg5 is not None:
            icp = qm.inventory_cycle_phase([1.0, 1.0 + cyc_daily[i]], [1.0, 1.0 + px_chg5], 1)
            if icp.get("ok"):
                cyc = {"被动去库": 1, "主动建库": 1, "被动建库": -1, "主动去库": -1}.get(icp["phase"], 0)

        # ── 利润票 ──
        prof = 0
        pp = prof_pct_daily[i]
        if pp is not None:
            prof = 1 if pp <= 0.3 else (-1 if pp >= 0.7 else 0)

        # ── 二维四象（单边才算票）──
        vd = 0
        ip = inv_pct_daily[i]
        bv = bval_daily[i]
        if ip is not None and pp is not None and bv is not None:
            inv_pos = _grade(ip)
            prof_pos = "低" if pp <= 0.3 else ("高" if pp >= 0.7 else "中")
            basis_side = "贴水" if bv > 0 else ("升水" if bv < 0 else "平水")
            if inv_pos == "低" and prof_pos == "低" and basis_side == "贴水":
                vd = 1
            elif inv_pos == "高" and prof_pos == "高" and basis_side == "升水":
                vd = -1

        # ── 期限结构（代理：基差符号；back ⟺ 贴水 ⟺ 偏多）──
        term = 0
        r = brate_daily[i]
        if r is not None:
            term = 1 if r > 0.002 else (-1 if r < -0.002 else 0)

        old = vd + cyc + prof          # 改前：期限结构票恒为 0
        new = old + term               # 改后：按 ±1 计入
        rows.append({
            "code": code, "d": dates[i], "old": old, "new": new, "term": term,
            "parts": (vd, cyc, prof),
            "fwd": {h: close[i + h] / close[i] - 1.0 for h in HORIZONS},
        })
    return rows


# ────────────────────────── 评估 ──────────────────────────
def _m(xs: list[float]) -> dict:
    n = len(xs)
    if n == 0:
        return {"n": 0, "hit": None, "mean_bp": None}
    return {"n": n, "hit": round(100 * sum(1 for x in xs if x > 0) / n, 1),
            "mean_bp": round(10000 * sum(xs) / n, 1)}


def eval_side(rows: list[dict], key: str, h: int, subset=None) -> dict:
    """日期聚簇 + 非重叠：先按日期内取均值，再汇总。"""
    byd: dict[str, list[float]] = {}
    for r in rows:
        net = r[key]
        if net == 0:
            continue
        if subset and not subset(r):
            continue
        byd.setdefault(r["d"], []).append(r["fwd"][h] * (1 if net > 0 else -1))
    seq = [sum(v) / len(v) for _, v in sorted(byd.items())]
    n = len(seq)
    if n == 0:
        return {"dates": 0, "hit": None, "mean_bp": None}
    mu = sum(seq) / n
    return {"dates": n, "hit": round(100 * sum(1 for x in seq if x > 0) / n, 1),
            "mean_bp": round(mu * 1e4, 1)}


def main() -> None:
    codes = sorted(f[:-5] for f in os.listdir(CACHE)
                   if f.endswith(".json") and not f.endswith("_profit.json"))
    rows: list[dict] = []
    for k, c in enumerate(codes, 1):
        try:
            rs = build(c)
        except Exception as e:  # noqa: BLE001
            print("[%d/%d] %s FAIL %s: %s" % (k, len(codes), c, type(e).__name__, e), flush=True)
            continue
        rows.extend(rs)
        print("[%d/%d] %-4s rows=%-5d" % (k, len(codes), c, len(rs)), flush=True)

    # 两套净票的分布差异
    same = sum(1 for r in rows if r["old"] == r["new"])
    o0n1 = sum(1 for r in rows if r["old"] == 0 and r["new"] != 0)
    on0 = sum(1 for r in rows if r["old"] != 0 and r["new"] == 0)
    flip = sum(1 for r in rows if r["old"] * r["new"] < 0)
    print("\n总行 %d；两套相同 %d (%.0f%%)；0→有向 %d；有向→0 %d；反向 %d"
          % (len(rows), same, 100 * same / max(1, len(rows)), o0n1, on0, flip))

    out = {"rows": len(rows), "same": same, "zero_to_dir": o0n1,
           "dir_to_zero": on0, "flip": flip, "metrics": {}}

    print("\n%-6s %-28s %-28s %s" % ("持有期", "改前(期限结构不计)", "改后(按±1计入)", "差异"))
    for h in HORIZONS:
        a = eval_side(rows, "old", h)
        b = eval_side(rows, "new", h)
        out["metrics"][f"{h}d"] = {"old": a, "new": b}
        print("%-6s 命中 %5.1f%% / %+6.1fbp (n=%d)  命中 %5.1f%% / %+6.1fbp (n=%d)"
              "   %+5.1fpp / %+6.1fbp"
              % (f"{h}日", a["hit"] or 0, a["mean_bp"] or 0, a["dates"],
                 b["hit"] or 0, b["mean_bp"] or 0, b["dates"],
                 (b["hit"] or 0) - (a["hit"] or 0), (b["mean_bp"] or 0) - (a["mean_bp"] or 0)))

    print("\n=== 只在两套方向不同时比较（这才是有信息量的子集）===")
    for h in HORIZONS:
        dis = lambda r: r["old"] * r["new"] < 0        # noqa: E731 反向
        zero = lambda r: r["old"] == 0 and r["new"] != 0  # noqa: E731 改前空仓、改后开仓
        dd = eval_side(rows, "new", h, dis)
        d0 = eval_side(rows, "new", h, zero)
        print("  %-4s 反向: 改后命中 %s%% / %s bp (n=%d)  |  0→开仓: 命中 %s%% / %s bp (n=%d)"
              % (f"{h}日", dd["hit"], dd["mean_bp"], dd["dates"],
                 d0["hit"], d0["mean_bp"], d0["dates"]))
        out["metrics"][f"{h}d"]["disagree_new"] = dd
        out["metrics"][f"{h}d"]["zero_to_dir_new"] = d0

    json.dump(out, open(os.path.join(HERE, "_bt_oldnew.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
