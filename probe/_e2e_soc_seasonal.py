# -*- coding: utf-8 -*-
"""真机验收：基本面量化的「社会库存 · 季节性图」（2026-10-21）。

覆盖三种形态：
  ① M / TA（社会库存 3 年）→ #chart-f-soc 多年度叠加（366 天轴）+ #chart-f-inv 仓单日频
  ② CJ / BZ（无社会库存）  → #chart-f-soc 隐藏 + #chart-f-inv 只有仓单
  ③ 切换品种后不会残留上一品种的季节性图 / 不会把两条序列画两遍

用法：python probe/_e2e_soc_seasonal.py
"""
from __future__ import annotations

import json
import pathlib
import sys

from playwright.sync_api import sync_playwright

OUT = pathlib.Path(__file__).resolve().parent.parent / "docs" / "shots"
OUT.mkdir(parents=True, exist_ok=True)
BASE = "http://127.0.0.1:5000/"

GRAB = """() => {
  const g = id => {
    const el = document.getElementById(id);
    if (!el) return { id, missing: true };
    const inst = (App._chartInstances || {})[id];
    const o = inst && inst.getOption ? inst.getOption() : null;
    return {
      id,
      display: getComputedStyle(el).display,
      h: Math.round(el.getBoundingClientRect().height),
      series: o ? o.series.map(s => s.name) : null,
      grids: o && o.grid ? o.grid.length : null,
      xLen: o && o.xAxis && o.xAxis[0] && o.xAxis[0].data ? o.xAxis[0].data.length : null,
      widths: o ? o.series.map(s => (s.lineStyle || {}).width || null) : null,
    };
  };
  return {
    soc: g('chart-f-soc'), inv: g('chart-f-inv'),
    title: (document.getElementById('f-inv-ck-title') || {}).textContent,
    badge: (document.getElementById('f-inv-badge') || {}).textContent,
    variety: (document.getElementById('f-variety') || {}).value,
  };
}"""

pass_n = fail_n = 0


def ck(name: str, ok: bool, extra: str = "") -> None:
    global pass_n, fail_n
    if ok:
        pass_n += 1
    else:
        fail_n += 1
    print(f"--- {name} --- {'PASS' if ok else 'FAIL'}{'' if ok else '  ' + extra}")


def main() -> int:
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1680, "height": 1000})
        errs: list[str] = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(BASE, wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(1500)
        pg.evaluate("() => App.switchTab('fund')")
        pg.wait_for_timeout(2000)
        pg.evaluate("() => App.fOpenDetail('M')")

        # 等下拉被填充（明细视图才有选项）
        for _ in range(60):
            pg.wait_for_timeout(1000)
            n = pg.evaluate("() => document.getElementById('f-variety').options.length")
            if n >= 10:
                break

        # ── ① 有 3 年社会库存 ──
        def wait_ready(expect_series):
            last = None
            for _ in range(40):
                pg.wait_for_timeout(1000)
                last = pg.evaluate(GRAB)
                if last["soc"].get("series") and len(last["soc"]["series"]) >= expect_series:
                    return last
            return last

        d = wait_ready(3)
        ck("M: 季节性图可见", d["soc"].get("display") == "block", str(d["soc"].get("display")))
        ck("M: 季节性图至少 3 个年份", len(d["soc"].get("series") or []) >= 3,
           str(d["soc"].get("series")))
        ck("M: x 轴是 366 天（不是日期类目）", d["soc"].get("xLen") == 366,
           str(d["soc"].get("xLen")))
        ck("M: 仓单图仍在且只有 2 条序列（注册仓单/日变化）",
           d["inv"].get("series") == ["注册仓单", "日变化"], str(d["inv"].get("series")))
        ck("M: 仓单图 x 轴是真实日期（≠366）", d["inv"].get("xLen") not in (None, 366),
           str(d["inv"].get("xLen")))
        ck("M: 社会库存没有被画两遍（仓单图里没有「社会库存」序列）",
           "社会库存" not in (d["inv"].get("series") or []), str(d["inv"].get("series")))
        ck("M: 小标题=交易所仓单 · 日频走势", d["title"] == "交易所仓单 · 日频走势", str(d["title"]))

        el = pg.query_selector("#chart-f-soc")
        el.scroll_into_view_if_needed()
        pg.wait_for_timeout(600)
        el.screenshot(path=str(OUT / "soc_seasonal_M.png"))

        # ── ② 无社会库存（CJ）──
        pg.select_option("#f-variety", "CJ")
        d2 = None
        for _ in range(40):
            pg.wait_for_timeout(1000)
            d2 = pg.evaluate(GRAB)
            if (d2["badge"] or "").strip():
                break
        ck("CJ: 季节性图被隐藏", d2["soc"].get("display") == "none", str(d2["soc"].get("display")))
        ck("CJ: 仓单图仍有 2 条序列", d2["inv"].get("series") == ["注册仓单", "日变化"],
           str(d2["inv"].get("series")))
        ck("CJ: 小标题不谎称「双口径」", "双口径" not in (d2["title"] or ""), str(d2["title"]))

        # ── ③ 切回有季节性的品种，确认不残留 ──
        pg.select_option("#f-variety", "TA")
        # ⚠ 不能只等 series>=3：切品种瞬间 getOption() 还是上一张图（陈旧 option），
        #   要等"这套数据真的属于 TA"⇒ 以 display 恢复为 block 为准（它由本次渲染置位）。
        d3 = None
        for _ in range(40):
            pg.wait_for_timeout(1000)
            d3 = pg.evaluate(GRAB)
            if d3["soc"].get("display") == "block" and len(d3["soc"].get("series") or []) >= 3:
                break
        ck("TA: 切回后季节性图重新可见", d3["soc"].get("display") == "block",
           str(d3["soc"].get("display")))
        ck("TA: 年份序列已换成 TA 自己的（仍 ≥3 年）",
           len(d3["soc"].get("series") or []) >= 3, str(d3["soc"].get("series")))

        ck("无 JS 运行时错误", not errs, "; ".join(errs[:3]))
        b.close()

    print(f"\n{pass_n} passed, {fail_n} failed")
    return 0 if fail_n == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
