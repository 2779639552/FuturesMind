# -*- coding: utf-8 -*-
"""后端补丁：让 `_inv_social_block` 带上**序列**（2026-10-16）。

【为什么】用户看到 ④ 卡的图标题写着「仓单库存 · 社会库存双口径」，但**只有仓单的图**。
根因不是没取到数 —— `_fetch_gtja_inventory` 早就返回了完整 `qty`+`dates`（实测 TA 430 点、
EG 893 点），是 `_inv_social_block` 只挑汇总值、**把序列丢了**（前端图表自然画不出来）。

【改动 1 处，纯增量】`_inv_social_block` 的返回体加 `rows: [{date, qty, change}]`。
既有字段一个不动 ⇒ ① 卡 / ⑨ 表 / 既有护栏零影响。

【不幂等】重跑会重复插入 ⇒ 开头检测标记并拒绝。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QM = ROOT / "quant_module.py"
MARK = "_SOC_ROWS_YEARS"

OLD = '''def _inv_social_block(code: str) -> dict:
    """国君社会(工厂)库存 → {分位/档位/方向} 块(① 卡与 ④ 卡**共用**, 口径唯一)。"""
    s = _fetch_gtja_inventory(code)
    if not s.get("ok"):
        return {"ok": False, "error": s.get("error") or "无国君库存数据"}
    rank = _inv_pct(s["qty"], s["latest"])
    return {"ok": True, "label": s["label"], "latest": s["latest"],
            "latest_date": s["latest_date"], "pct_rank": rank, "grade": _inv_grade(rank),
            "count": s["count"], "source": s["source"], "span": s["span"],
            "chg_pct": s.get("chg_pct"), "dir": s.get("dir")}'''

NEW = '''# 【变量】社会库存序列只回传"最近这么多年"。图上那根轴是 180 天窗口, 3 年远够覆盖
#   "窗口起点之前的最后一个读数"(阶梯对齐需要它), 又不至于白传上千个点。
_SOC_ROWS_YEARS = 3


def _social_keep(dates: list[str]) -> int:
    """返回序列应保留的**尾部点数**（最近 `_SOC_ROWS_YEARS` 年内的点数）。

    【为什么按日期切而不是固定条数】社会(工厂)库存的频率**因品种而异**（实测周频 ≈52 点/年，
      也有月频 ≈11 点/年）⇒ 固定"最近 N 条"在月频品种上会跨十几年、在日频品种上又不够长。
      按日期切既省流量、又不随频率漂。
    【取不到日期就全传】解析失败时**宁可多传也不丢数据**（与本项目"降级不静默丢东西"同一纪律）。
    """
    if not dates:
        return 0
    last = str(dates[-1])
    try:
        import datetime as _dt

        y, m, d = (int(x) for x in last.split("-")[:3])
        cut = str(_dt.date(y - _SOC_ROWS_YEARS, m, d))
    except Exception:  # noqa: BLE001
        return len(dates)
    n = 0
    for dt in reversed(dates):
        if str(dt) < cut:
            break
        n += 1
    return min(len(dates), n)


def _inv_social_block(code: str) -> dict:
    """国君社会(工厂)库存 → {分位/档位/方向/**序列**} 块(① 卡 / ④ 卡 / ⑨ 表**共用**, 口径唯一)。

    【2026-10-16 新增 `rows`】此前本块只给汇总值(latest/pct_rank/grade/dir) ⇒ ④ 卡的
      「仓单库存 · 社会库存双口径」图**画不出社会库存那条线**(标题写着双口径、图上却只有仓单)。
      用户直接看出来了。现补上序列 `rows: [{date, qty, change}]`：
        · `change` = 与**上一期**之差（社会库存多为周频 ⇒ 即"周度变化"）；首期无前值记 None；
        · 只回传最近 `_SOC_ROWS_YEARS` 年（见 `_social_keep`）；
        · ⚠ 这是**原始读数点**（周频/月频），前端按"最后已知值**阶梯对齐**"到仓单日期轴上，
          **绝不插值** —— 插值会造出根本不存在的读数（量纲不可比 + 频率不同，见前端注释）。
    """
    s = _fetch_gtja_inventory(code)
    if not s.get("ok"):
        return {"ok": False, "error": s.get("error") or "无国君库存数据"}
    rank = _inv_pct(s["qty"], s["latest"])
    qty, dates = list(s.get("qty") or []), list(s.get("dates") or [])
    keep = _social_keep(dates)
    rows = []
    for i in range(max(0, len(qty) - keep), len(qty)):
        prev = qty[i - 1] if i > 0 else None
        rows.append({"date": dates[i], "qty": qty[i],
                     "change": (None if prev is None else qty[i] - prev)})
    return {"ok": True, "label": s["label"], "latest": s["latest"],
            "latest_date": s["latest_date"], "pct_rank": rank, "grade": _inv_grade(rank),
            "count": s["count"], "source": s["source"], "span": s["span"],
            "chg_pct": s.get("chg_pct"), "dir": s.get("dir"), "rows": rows}'''


def main(write: bool) -> int:
    raw = QM.read_bytes()
    txt = raw.decode("utf-8")
    if MARK in txt:
        print("★ 改动已应用过 —— 本脚本不幂等, 勿重跑。要从 backup 还原后再跑。")
        return 2
    got = txt.count(OLD)
    print("  %s %-40s 命中 %d(期望 1)" % ("OK " if got == 1 else "★  ", "_inv_social_block 整段替换", got))
    if got != 1:
        print("  ⇒ 锚点未命中, **未写盘**")
        return 1
    new = txt.replace(OLD, NEW, 1)
    # 写盘前自检：关键函数计数不变
    for probe in ("def _inv_social_block(", "def _inv_dual(", "def _inv_cross(",
                  "def api_quant_inventory(", "def api_fund_strategy("):
        a, b = txt.count(probe), new.count(probe)
        if a != b:
            print("  ★ 自检失败: %r %d → %d" % (probe, a, b))
            return 1
    dl = len(new) - len(txt)
    if not (500 <= dl <= 6000):
        print("  ★ 自检失败: 长度变化 %+d" % dl)
        return 1
    print("  ✔ 写盘前自检通过（长度 %+d）" % dl)
    if not write:
        print("  ⇒ 空跑通过。加 --write 落盘。")
        return 0
    QM.write_bytes(new.encode("utf-8"))
    b = QM.read_bytes()
    assert b.count(b"\r\n") == 0, "quant_module 应保持 LF"
    print("  落盘 md5=%s (LF 保持)" % hashlib.md5(b).hexdigest()[:12])
    return 0


if __name__ == "__main__":
    sys.exit(main("--write" in sys.argv))
