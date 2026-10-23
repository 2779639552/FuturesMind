# -*- coding: utf-8 -*-
"""核实各张"票"的底层数据轴，为去重提供依据。

重点核实一件事：**「多空逻辑(库存周期)」这一票的方向，是否只由价格决定**。
  量化判据（quant_methodology.inventory_cycle_phase）:
    (降,涨)→多 / (升,涨)→多 / (升,跌)→空 / (降,跌)→空
  四格里方向上「价格涨就偏多、跌就偏空」，库存方向只改变**标签**(被动/主动)，不改变 bias 符号。
  若按此复算与价格 5 日方向一致率接近 100%，说明它是一张**被命名为基本面的技术票**。
"""
import importlib.util, json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))
spec = importlib.util.spec_from_file_location("bt", os.path.join(HERE, "_bt_tech_fund.py"))
bt = importlib.util.module_from_spec(spec); spec.loader.exec_module(bt)

CACHE = os.path.join(HERE, "_bt_cache")
LAG = 7

def cyc_bias(inv_series, px_series, window=5):
    pos = [v for v in inv_series if v and v > 0]
    px = list(px_series)
    if len(pos) < window + 1 or len(px) < window + 1:
        return None
    ic = (pos[-1] / pos[-1 - window] - 1) * 100 if pos[-1 - window] else None
    pc = (px[-1] / px[-1 - window] - 1) * 100 if px[-1 - window] else None
    if ic is None or pc is None:
        return None
    i_d = "升" if ic > 0.5 else ("降" if ic < -0.5 else "平")
    p_d = "涨" if pc > 0.5 else ("跌" if pc < -0.5 else "平")
    return {("降","涨"):1, ("升","涨"):1, ("升","跌"):-1, ("降","跌"):-1}.get((i_d,p_d))

tot = same = diff = unr = 0
by_var = {}
for f in sorted(os.listdir(CACHE)):
    if not f.endswith(".json"): continue
    code = f[:-5]
    raw = json.load(open(os.path.join(CACHE, f), encoding="utf-8"))
    rows = bt.build_panel(raw, LAG)
    if not rows: continue
    inv_obs = [(d, v) for d, v in raw["inv"]]
    s_ok = s_tot = 0
    for r in rows:
        # 复算：库存取该日 as-of 的最近 5 个观测；价格取 5 个交易日
        idx = next((i for i, x in enumerate(inv_obs) if x[0] > r["d"]), len(inv_obs))
        seg = [v for _, v in inv_obs[:idx]]
        if len(seg) < 6: continue
        # 价格：用面板里的 close 反推 5 日前太麻烦，直接用 mom20 的比例关系不合适；
        # 这里用 close 序列（面板已含 close），从 panels 里取不到历史，故用 mom 近似不可行
        s_tot += 1
    by_var[code] = s_tot
print("（见下方精确版）")
