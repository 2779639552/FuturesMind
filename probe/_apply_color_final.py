# -*- coding: utf-8 -*-
"""收尾：把残留的 CSS 变量当颜色值（ECharts 不认）与一批偏浅的硬编码色清掉。

【已实证的两类】
1. **ECharts 配置里残留 CSS 变量**：canvas 解析不了 `var(--x)`，会回落成默认色
   （表象：曲线发灰、图例色块与线对不上）。
   · `_CMP_COLORS[4] = 'var(--brand)'` —— 多品种对比图第 5 条线
   · 策略配色 `momentum_ad` / `rsi_sent` = `'var(--amber)'`
   · `CHART.kMa3` 的 fallback 竟然是 `'var(--brand)'`（CSS 变量未定义时就会漏出去）
2. **偏浅的硬编码色**：亮度 0.56~0.76，亮色主题下几乎看不见。
   #fbbf24(0.76) #60a5fa(0.61) #40c8e0(0.68) #38bdf8(0.65) #f472b6(0.57) #5ac8fa(0.70)
   ⇒ 统一换成同色相、亮度 0.45~0.58 的版本，暗底仍然清晰。
"""
from __future__ import annotations

import io
import os

P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web_template.html")
CRLF = "\r\n"

s = io.open(P, "r", encoding="utf-8", newline="").read()
before = s


def rep(old: str, new: str, label: str, expect: int = 1):
    """锚点/替换串按 LF 书写，统一转成文件实际的 CRLF 再比对。"""
    global s
    old = old.replace("\n", CRLF)
    new = new.replace("\n", CRLF)
    n = s.count(old)
    assert n == expect, "%s 命中 %d 次（期望 %d）: %s" % (label, n, expect, old[:80])
    s = s.replace(old, new, expect)


# ── ① 三处 CSS 变量残留 ──────────────────────────────────────────────────
rep("get kMa3() { return __cssVar('--chart-k-ma3', 'var(--brand)'); }, // MA20",
    "// 【2026-10-21】fallback 原为 'var(--brand)' —— 一旦 --chart-k-ma3 未定义，\n"
    " get kMa3() { return __cssVar('--chart-k-ma3', '#8e8e93'); }, // MA20",
    "kMa3 fallback")

rep("_CMP_COLORS: ['#8fd2f5', '#fbbf24', '#22c55e', '#ef4444', 'var(--brand)', '#14b8a6', '#f472b6', '#60a5fa'],",
    "// 【2026-10-21】第 5 位原为 'var(--brand)'：canvas 不认 CSS 变量 ⇒ 第 5 条线\n"
    " //   回落成默认灰，与图例色块对不上。改成实色。\n"
    " _CMP_COLORS: ['#7cc4ff', '#d97706', '#22c55e', '#ef4444', '#a78bfa', '#14b8a6', '#db2777', '#3b82f6'],",
    "_CMP_COLORS")

rep("'momentum_ad':'var(--amber)'", "'momentum_ad':'#f59e0b'", "strategy momentum_ad")
rep("'rsi_sent':'var(--amber)'", "'rsi_sent':'#f59e0b'", "strategy rsi_sent")

# ── ② 偏浅色的统一收敛（只换色值，不动任何逻辑） ────────────────────────
DARKEN = {
    "#fbbf24": "#d97706",   # 0.76 → 0.55 琥珀
    "#60a5fa": "#3b82f6",   # 0.61 → 0.45 蓝
    "#40c8e0": "#0d9488",   # 0.68 → 0.50 青
    "#38bdf8": "#0ea5e9",   # 0.65 → 0.55 天蓝
    "#f472b6": "#db2777",   # 0.57 → 0.45 粉
    "#5ac8fa": "#22d3ee",   # 0.70 → 0.72（青，暗底更亮、亮底仍可辨）
}
for old, new in DARKEN.items():
    cnt = s.count(old)
    if cnt:
        s = s.replace(old, new)
        print("  %s -> %s  x%d" % (old, new, cnt))

assert s != before
assert s.count("\n") == s.count(CRLF), "CRLF 被破坏"
io.open(P, "w", encoding="utf-8", newline="").write(s)
print("ok")
