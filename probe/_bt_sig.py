# -*- coding: utf-8 -*-
"""对四格结果做**日期聚簇**显著性检验 + 分品种集中度。

【为什么必须重算】第一版把所有 (品种×日) 观察当独立样本：n=1112、t=2.17。
  但 18 个品种在**同一天**的收益高度相关（共同的宏观/板块冲击），
  有效样本远小于 n —— 这会把 t 抬高 2~4 倍，是截面研究最常见的假显著来源。
  正确做法：先在**每个日期内**对品种取均值，得到一条「日期序列」，再对这条序列做检验。

【检验量】分歧样本里，按基本面方向持有的收益 x = fwd * sign(fund)。
  x > 0 ⇒ 基本面赢；< 0 ⇒ 技术赢（两者在分歧格互为相反数）。
  再给移动块 bootstrap（块长=h，保留收益的时序自相关）的 p 值。
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import random
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

spec = importlib.util.spec_from_file_location("bt", os.path.join(HERE, "_bt_tech_fund.py"))
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)

CACHE = os.path.join(HERE, "_bt_cache")
LAG = 7
random.seed(20261019)


def date_t(xs: list[float]) -> tuple[float, float, float]:
    """返回 (日期数, 日均值bps, t)。"""
    n = len(xs)
    if n < 8:
        return n, 0.0, 0.0
    mu = sum(xs) / n
    sd = statistics.stdev(xs)
    t = mu / (sd / n ** 0.5) if sd > 0 else 0.0
    return n, mu * 1e4, t


def block_boot(xs: list[float], block: int, iters: int = 3000) -> float:
    """移动块 bootstrap: 原假设「日均值为 0」下的双侧 p。"""
    n = len(xs)
    if n < 20:
        return 1.0
    obs = sum(xs) / n
    cnt = 0
    for _ in range(iters):
        samp: list[float] = []
        while len(samp) < n:
            s = random.randrange(0, max(1, n - block + 1))
            samp.extend(xs[s:s + block])
        samp = samp[:n]
        # 去均值后再重抽 ⇒ 构造 H0 分布
        m = sum(samp) / len(samp)
        if abs(m - obs) >= abs(obs):
            cnt += 1
    return cnt / iters


def main() -> None:
    codes = [f[:-5] for f in os.listdir(CACHE) if f.endswith(".json")]
    panels = {}
    for c in codes:
        raw = json.load(open(os.path.join(CACHE, c + ".json"), encoding="utf-8"))
        rows = bt.build_panel(raw, LAG)
        if rows:
            panels[c] = rows
    print("品种 %d, 样本行 %d" % (len(panels), sum(len(v) for v in panels.values())))

    FUND = {
        "库存": lambda r: None if r["inv4"] is None else -r["inv4"],
        "仓单": lambda r: None if r["ck5"] is None else -r["ck5"],
        "基差": lambda r: None if r["bpct"] is None else (r["bpct"] - 0.5),
    }
    TECH = {"动量20": "mom20", "动量60": "mom60"}

    out = {}
    for tn, tk in TECH.items():
        for fn, ff in FUND.items():
            for h in (5, 10, 20):
                # 日期 → 当日分歧格的「按基本面方向」收益列表
                bydate: dict[str, list[float]] = {}
                for code, rows in panels.items():
                    for k, r in enumerate(rows):
                        if k % h:          # 非重叠
                            continue
                        t, f = r.get(tk), ff(r)
                        if t is None or f is None:
                            continue
                        ts = 1 if t > 0 else (-1 if t < 0 else 0)
                        fs = 1 if f > 0 else (-1 if f < 0 else 0)
                        if ts == 0 or fs == 0 or ts == fs:
                            continue
                        bydate.setdefault(r["d"], []).append(r["fwd"][h] * fs)
                dates = sorted(bydate)
                seq = [sum(bydate[d]) / len(bydate[d]) for d in dates]
                nd, mu_bps, t = date_t(seq)
                p = block_boot(seq, block=h)
                # 分品种：基本面侧为正的品种占比（集中度）
                per = []
                for code, rows in panels.items():
                    xs = []
                    for k, r in enumerate(rows):
                        if k % h:
                            continue
                        tv, fv = r.get(tk), ff(r)
                        if tv is None or fv is None:
                            continue
                        ts = 1 if tv > 0 else (-1 if tv < 0 else 0)
                        fs = 1 if fv > 0 else (-1 if fv < 0 else 0)
                        if ts == 0 or fs == 0 or ts == fs:
                            continue
                        xs.append(r["fwd"][h] * fs)
                    if len(xs) >= 10:
                        per.append(sum(xs) / len(xs))
                share = sum(1 for x in per if x > 0) / max(1, len(per))
                key = "%s×%s×%dd" % (tn, fn, h)
                out[key] = {"dates": nd, "fund_side_bps": round(mu_bps, 1),
                            "t_date": round(t, 2), "p_boot": round(p, 3),
                            "varieties": len(per), "fund_win_share": round(share * 100, 1)}
                print("  %-18s 日期数=%-4d 基本面侧 %+7.1fbp  日均t=%5.2f  p=%4.2f  "
                      "品种一致=%d/%d(%.0f%%)"
                      % (key, nd, mu_bps, t, p, round(share * len(per)), len(per), share * 100))

    json.dump(out, open(os.path.join(HERE, "_bt_sig.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
