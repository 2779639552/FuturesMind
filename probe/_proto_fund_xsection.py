"""基本面截面行的原型实测 —— 只读探针（2026-10-16）。

目的：在动手写接口之前，先量清"基本面截面表"每一列在 21 品种上的**真实覆盖率与耗时**，
避免设计出"一半行是空"的列。不改任何源文件。

候选列（对应技术面总览的 涨跌幅/日线/60分/15分/5分/状态/共振/结论）：
  基差（值/率/升贴水/分位）、仓单库存（双口径综合档位 + 分位）、月差（期限结构形态+价差）、
  利润（档位）、各 SKILL 判断（多空逻辑系 4 票）、综合结论（三指标策略）
"""

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import fund_module as fm  # noqa: E402
import quant_methodology as qmth  # noqa: E402
import quant_module as q  # noqa: E402
from tradingagents.dataflows.commodity_futures import ACTIVE_VARIETIES  # noqa: E402


def one(code: str) -> dict:
    t0 = time.time()
    row = {"variety": code, "name": q._meta(code).get("name", code), "err": []}

    # ① 基差
    try:
        b = q._fetch_basis(code, 60, batch=True)
        if b.get("ok") and b.get("rows"):
            v = [r["basis"] for r in b["rows"] if r.get("basis") is not None]
            r_ = [r["basis_rate"] for r in b["rows"] if r.get("basis_rate") is not None]
            if v:
                cur = v[-1]
                row["basis"] = cur
                row["basis_rate"] = r_[-1] if r_ else None
                row["basis_side"] = "贴水" if cur > 0 else ("升水" if cur < 0 else "平水")
                row["basis_pct"] = round(sum(1 for x in v if x <= cur) / len(v) * 100, 1)
                row["basis_src"] = b.get("source")
            else:
                row["err"].append("基差序列空")
        else:
            row["err"].append("基差:" + (b.get("error") or "无")[:24])
    except Exception as e:  # noqa: BLE001
        row["err"].append("基差异常:%s" % type(e).__name__)

    # ② 仓单 + 社会库存 → 双口径综合
    try:
        inv = q._fetch_inventory(code, 180, batch=True)
        rows = (inv.get("rows") or []) if inv.get("ok") else []
        dual = q._inv_dual(code, rows, 180, inv.get("source", "") if inv.get("ok") else "")
        row["inv_grade"] = dual.get("grade")
        row["inv_mode"] = dual.get("mode")
        row["ck_pct"] = (dual.get("ck") or {}).get("pct_rank")
        row["ck_chg5"] = (dual.get("ck") or {}).get("chg_pct")
        row["ck_zero"] = bool((dual.get("ck") or {}).get("warn"))
        soc = dual.get("social") or {}
        row["soc_ok"] = bool(soc.get("ok"))
        row["soc_pct"] = soc.get("pct_rank")
        row["soc_label"] = soc.get("label")
    except Exception as e:  # noqa: BLE001
        row["err"].append("库存异常:%s" % type(e).__name__)

    # ③ 月差（期限结构）
    try:
        t = q._fetch_term_structure(code)
        if t.get("ok"):
            rows = t.get("rows") or []
            tsr = qmth.term_structure_regime(rows)
            row["term_shape"] = t.get("shape")
            row["term_spread"] = t.get("spread")
            row["term_ok"] = True
            if tsr.get("ok"):
                row["term_bias"] = tsr.get("bias")
                row["term_spread_pct"] = tsr.get("spread_pct")
        else:
            row["term_ok"] = False
            row["err"].append("月差:" + (t.get("error") or "无")[:24])
    except Exception as e:  # noqa: BLE001
        row["err"].append("月差异常:%s" % type(e).__name__)

    # ④ 利润
    try:
        p = fm._profit_summary(code)
        row["prof_ok"] = bool(p.get("ok"))
        row["prof_grade"] = p.get("grade")
    except Exception as e:  # noqa: BLE001
        row["err"].append("利润异常:%s" % type(e).__name__)

    # ⑤ 各 SKILL 判断（多空逻辑系 4 票；纯函数，不额外取数）
    votes = []
    try:
        br = [r["basis_rate"] for r in (q._fetch_basis(code, 180, batch=True).get("rows") or [])
              if r.get("basis_rate") is not None]
        ivr = (q._fetch_inventory(code, 180, batch=True).get("rows") or [])
        ivs = [r["qty"] for r in ivr if r.get("qty") is not None]
        vd = qmth.value_drive_matrix(br, ivs, _chg(ivs))
        row["skill_vd"] = vd.get("advice") if vd.get("ok") else None
        if vd.get("ok"):
            votes.append(("二维四象", vd.get("advice")))
        # 期限结构票
        tsr = qmth.term_structure_regime(
            (q._fetch_term_structure(code).get("rows") or []))
        row["skill_term"] = tsr.get("bias") if tsr.get("ok") else None
        if tsr.get("ok"):
            votes.append(("期限结构", tsr.get("bias")))
        # 库存周期票
        bars = q._load_daily_bars(code, 80)
        px = [float(b["close"]) for b in bars if b.get("close") is not None]
        icp = qmth.inventory_cycle_phase(ivs, px, 5)
        row["skill_cyc"] = icp.get("phase") if icp.get("ok") else None
        if icp.get("ok") and icp.get("phase") in ("被动去库", "主动建库", "被动建库", "主动去库"):
            votes.append(("库存周期", icp.get("phase")))
    except Exception as e:  # noqa: BLE001
        row["err"].append("skill异常:%s" % type(e).__name__)
    row["skill_votes"] = votes

    # ⑥ 综合（三指标策略）
    try:
        inv_grade = row.get("inv_grade") or "不明"
        prof_grade = row.get("prof_grade") if row.get("prof_ok") else "不明"
        side = row.get("basis_side") or "不明"
        v = fm._strategy_verdict(inv_grade, prof_grade, side)
        row["strategy"] = v.get("action")
        row["strategy_ok"] = v.get("ok")
    except Exception as e:  # noqa: BLE001
        row["err"].append("策略异常:%s" % type(e).__name__)

    row["sec"] = round(time.time() - t0, 2)
    return row


def _chg(seq, back=4):
    pos = [v for v in seq if v is not None and v > 0]
    if len(pos) < back + 1 or not pos[-(back + 1)]:
        return None
    return round((pos[-1] / pos[-(back + 1)] - 1) * 100, 2)


def main():
    codes = sorted(ACTIVE_VARIETIES)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=6) as ex:
        rows = list(ex.map(one, codes))
    wall = time.time() - t0

    print("全池 %d 品种，墙钟 %.1fs（并发 6）" % (len(rows), wall))
    print()
    hdr = ("code", "基差", "升贴水", "基差分位", "库存综合", "口径关系", "仓单分位", "社库存",
           "月差形态", "月差价差", "利润", "四象", "期限票", "库存周期", "综合策略")
    print("%-5s %-9s %-7s %-8s %-8s %-14s %-8s %-7s %-10s %-9s %-5s %-9s %-7s %-9s %s" % hdr)
    print("-" * 156)
    for r in rows:
        print("%-5s %-9s %-7s %-8s %-8s %-14s %-8s %-7s %-10s %-9s %-5s %-9s %-7s %-9s %s" % (
            r["variety"],
            _n(r.get("basis"), 0), r.get("basis_side") or "—", _n(r.get("basis_pct"), 0),
            r.get("inv_grade") or "—", (r.get("inv_mode") or "—")[:12],
            _n(r.get("ck_pct"), 2), _n(r.get("soc_pct"), 2),
            (r.get("term_shape") or "—")[:8], _n(r.get("term_spread"), 1),
            r.get("prof_grade") or "—",
            (r.get("skill_vd") or "—")[:7], (r.get("skill_term") or "—")[:5],
            (r.get("skill_cyc") or "—")[:7], r.get("strategy") or "—"))

    print()
    print("=== 覆盖率 ===")
    n = len(rows)
    def cov(k, pred=lambda v: v is not None):
        c = sum(1 for r in rows if pred(r.get(k)))
        print("  %-14s %2d/%d" % (k, c, n))
    cov("basis"); cov("basis_pct"); cov("soc_pct")
    cov("term_spread")
    cov("skill_vd"); cov("skill_term"); cov("skill_cyc")
    cov("prof_grade", lambda v: v and v != "不明")
    print("  库存综合档位(非不明) %2d/%d" % (sum(1 for r in rows if r.get("inv_grade") not in (None, "不明")), n))
    print("  口径关系带数据     %2d/%d" % (sum(1 for r in rows if r.get("inv_mode") not in (None, "无口径")), n))
    print()
    print("=== 耗时分布（单品种秒） ===")
    secs = sorted(r["sec"] for r in rows)
    print("  min %.2f / 中位 %.2f / max %.2f" % (secs[0], secs[len(secs) // 2], secs[-1]))
    print()
    print("=== 有报错的品种 ===")
    for r in rows:
        if r["err"]:
            print("  %-5s %s" % (r["variety"], "; ".join(r["err"])))


def _n(v, dp):
    return "—" if v is None else ("%%.%df" % dp) % v


if __name__ == "__main__":
    main()
