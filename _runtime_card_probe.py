"""运行时能力卡探针 —— 回答"跑一遍, 哪些卡真的出现在输出里"。

【为什么静态扫描不够】grep 只能证明 slug 出现在源码某处(哪怕是注释、死代码)。
函数写好了但没被调用 = 没接入, 静态扫描看不出来。本探针真实调 build_verdict +
三个注入块, 从**返回值**里收集 card 字段, 是唯一可信的覆盖率证据。

【口径】
  真·跑出的卡 = build_verdict(...)['stages'][*]['card'|'trading_plan']['card'] ∪
                build_verdict(...)['evidence'] 中列出的卡 ∪
                三个注入块文本里 ◆ 行署名的卡

【用法】python _runtime_card_probe.py [品种]   (默认 RB 螺纹钢)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

SLUG_PAT = re.compile(r"cap-([a-z][a-z-]*[a-z])")


def _collect_card_strings(obj, sink: set[str]) -> None:
    """递归遍历产物, 收集所有 card 字段的值(可能含合并署名)。

    【为什么不只认 'card' 键】cards.append("x.cap-y") 的卡最终落在
    evidence.cards_used / cards_by_book 里, 是**纯字符串列表**, 没有 'card' 键。
    只认 'card' 键会漏掉一半的卡(money-management / trading-plan / 方向票那一批)。
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "card" and isinstance(v, str):
                sink |= set(SLUG_PAT.findall(v))
            else:
                _collect_card_strings(v, sink)
    elif isinstance(obj, (list, tuple)):
        for it in obj:
            if isinstance(it, str):
                sink |= set(SLUG_PAT.findall(it))
            else:
                _collect_card_strings(it, sink)


def probe_symbol(symbol: str) -> set[str]:
    """跑一个品种, 返回它在输出里真用到的卡 slug 集。"""
    symbol = symbol.upper()

    import quant_module as qmod
    from quant_verdict import build_verdict
    from tradingagents.dataflows import methodology_context as mc

    # ── 取真实数据(与 ⑧ 卡片同源) ──
    bars = qmod._load_daily_bars(symbol, 250)
    if len(bars) < 30:
        print(f"[skip] {symbol} 日线不足({len(bars)} 根)")
        return set()
    b = qmod._fetch_basis(symbol, 180)
    basis_rates = [r["basis_rate"] for r in b["rows"]
                   if r.get("basis_rate") is not None] if b.get("ok") else []
    inv = qmod._fetch_inventory(symbol, 180)
    inv_series = [r["qty"] for r in inv["rows"] if r.get("qty") is not None] if inv.get("ok") else []
    pos = [v for v in inv_series if isinstance(v, (int, float)) and v > 0]
    inv_chg = round((pos[-1] / pos[-4] - 1) * 100, 2) if len(pos) >= 4 and pos[-4] else None
    term = qmod._fetch_term_structure(symbol)

    payload = {
        "variety": symbol, "name": symbol, "unit": "元/吨",
        "bars": bars, "basis_rate_series": basis_rates, "inv_series": inv_series,
        "inv_chg_pct": inv_chg, "term": term if term.get("ok") else None,
        "account": 1_000_000, "risk_pct": 0.30, "times": 10,
        "point_value": 10.0, "round_trip_fee": 6.0, "slip_ticks": 1.0,
    }
    verdict = build_verdict(payload)

    used: set[str] = set()
    if verdict.get("ok"):
        _collect_card_strings(verdict.get("stages") or {}, used)
        _collect_card_strings(verdict.get("evidence") or {}, used)

    # ── 注入块此刻在提示里署名的卡 ──
    mc.clear_cache()
    for fn in ("technical_methodology_context", "fundamental_methodology_context",
               "macro_methodology_context"):
        txt = getattr(mc, fn)(symbol)
        for line in txt.splitlines():
            if "◆" in line:
                used |= set(SLUG_PAT.findall(line))

    return used


# 覆盖样本: 尽量横跨不同产业链(黑色/农产品/能化/有色), 让"某张卡没触发"能区分
#   "品种没数据" 与 "卡坏了"。
_SAMPLE = ["RB", "M", "TA", "CU", "SC"]


def main() -> None:
    arg = sys.argv[1].upper() if len(sys.argv) > 1 else None
    symbols = [arg] if arg else _SAMPLE

    per_symbol: dict[str, list[str]] = {}
    union: set[str] = set()
    for s in symbols:
        got = probe_symbol(s)
        per_symbol[s] = sorted(got)
        union |= got

    out = ROOT / "_runtime_cards.json"
    out.write_text(json.dumps(
        {"symbols": symbols, "per_symbol": per_symbol, "union": sorted(union),
         "union_count": len(union)}, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"样本品种: {', '.join(symbols)}")
    for s in symbols:
        print(f"  {s}: {len(per_symbol[s])} 张 — {' '.join(per_symbol[s])}")
    print(f"并集({len(union)} 张): {' '.join(sorted(union))}")
    print(f"-> {out}")


if __name__ == "__main__":
    main()
