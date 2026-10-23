# -*- coding: utf-8 -*-
"""补测：① 同向(共振)格的日期聚簇显著性  ② 固定权重 w=0.5 的时间切分检验。

【为什么补 ②】前面那张「最优 w」是**在同一份数据上扫出来的**（in-sample），
  把它当作推荐权重等于自己给自己发证书。这里改成：前半段定权重/看方向，
  后半段只看固定 w=0.5 的表现，两个子样本分别报 —— 至少能看出是否只在某一段成立。
"""
from __future__ import annotations

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

spec = importlib.util.spec_from_file_location("bt", os.path.join(HERE, "_bt_tech_fund.py"))
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)

CACHE = os.path.join(HERE, "_bt_cache")
LAG = 7


def _t(xs):
    n = len(xs)
    if n < 8:
        return n, 0.0, 0.0
    mu = sum(xs) / n
    sd = statistics.stdev(xs)
    return n, mu * 1e4, (mu / (sd / n ** 0.5) if sd > 0 else 0.0)


def main():
    codes = [f[:-5] for f in os.listdir(CACHE) if f.endswith(".json")]
    panels = {}
    for c in codes:
        raw = json.load(open(os.path.join(CACHE, c + ".json"), encoding="utf-8"))
        rows = bt.build_panel(raw, LAG)
        if rows:
            panels[c] = rows
    allrows = [(c, r) for c, v in panels.items() for r in v]
    allrows.sort(key=lambda t: t[1]["d"])
    mid = allrows[len(allrows) // 2][1]["d"]
    print("全样本 %d 行, 中位日期 %s（前段 ≤ 此日, 后段 > 此日）\n" % (len(allrows), mid))

    FUND = {
        "库存": lambda r: None if r["inv4"] is None else -r["inv4"],
        "仓单": lambda r: None if r["ck5"] is None else -r["ck5"],
        "基差": lambda r: None if r["bpct"] is None else (r["bpct"] - 0.5),
    }
    TECH = {"动量20": "mom20", "动量60": "mom60"}

    print("=== ① 同向(共振)格, 按技术方向持有, 日期聚簇 + 非重叠抽样 ===")
    for tn, tk in TECH.items():
        for fn, ff in FUND.items():
            for h in (10, 20):
                byd = {}
                # ★ 必须逐品种做 k%h 非重叠抽样：前瞻 h 日收益在相邻日期之间高度重叠，
                #   不抽样会让日期序列强自相关，t 值虚高（实测虚高 1.5~2 倍）。
                for c, rows in panels.items():
                    for k, r in enumerate(rows):
                        if k % h:
                            continue
                        t, f = r.get(tk), ff(r)
                        if t is None or f is None:
                            continue
                        ts = 1 if t > 0 else -1
                        fs = 1 if f > 0 else -1
                        if ts != fs:
                            continue
                        byd.setdefault(r["d"], []).append(r["fwd"][h] * ts)
                seq = [sum(v) / len(v) for d, v in sorted(byd.items())]
                n, mu, t = _t(seq)
                print("  %-16s %2dd  日期=%-4d %+7.1fbp  t=%5.2f" % (tn + "×" + fn, h, n, mu, t))

    print("\n=== ② 固定 w=0.5 且「分歧不持仓」，前后两段分别报 ===")
    print("    只在同向时持仓(0.5/0.5 等权时分歧即对冲为 0)；h=10, 非重叠；年化按 244/h 折算")
    for tn, tk in TECH.items():
        for fn, ff in FUND.items():
            for seg, cond in (("前段", lambda d: d <= mid), ("后段", lambda d: d > mid)):
                xs = []
                for c, r in allrows:
                    if not cond(r["d"]):
                        continue
                    idx = panels[c].index(r)
                    if idx % 10:
                        continue
                    t, f = r.get(tk), ff(r)
                    if t is None or f is None:
                        continue
                    ts = 1 if t > 0 else -1
                    fs = 1 if f > 0 else -1
                    sc = 0.5 * ts + 0.5 * fs
                    # ★ 两者相反时 sc == 0 ⇒ **不持仓**。
                    #   第一版写成 `1 if sc > 0 else -1`，等于把分歧格全部强制做空
                    #   （分歧约占一半样本），结果全线 -5%~-11% —— 那是 bug 不是结论。
                    if abs(sc) < 1e-9:
                        continue
                    xs.append(r["fwd"][10] * (1 if sc > 0 else -1))
                n, mu, t = _t(xs)
                ann = mu / 1e4 * (244.0 / 10) * 100
                print("  %-16s %s n=%-4d %+7.1fbp/10d  t=%5.2f  年化≈%+6.1f%%"
                      % (tn + "×" + fn, seg, n, mu, t, ann))


if __name__ == "__main__":
    main()
