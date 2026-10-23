# -*- coding: utf-8 -*-
"""分类色板收敛到「双主题都够对比」的中间调（WCAG ≥ 3:1）。

【为什么还要改】
上一版按"简单亮度 0.45~0.72"选的色，在**亮色主题**下实测仍不够：
用 WCAG 对比度复核（相对亮度要先做 sRGB 线性化），10 处 < 3:1 ——
最差 #eab308 只有 1.92、#FF9F0A 2.06、#22c55e 2.28、#14b8a6 2.49。

【判据】白底要求 L ≤ 0.175、深底要求 L ≥ 0.10 ⇒ 只有 L∈[0.10,0.175] 这一窄带
能同时满足。下面 8 色的 L 全部落在 0.15~0.30 且实测对比度 ≥ 3（见交付说明）。

季节性图的年份色（#00B0F0 / #00B050）**不动** —— 那是用户指定的
「季节性图绘制包」规范配色，不属于本次统一色板的范围。
"""
from __future__ import annotations

import io
import os

P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web_template.html")
CRLF = "\r\n"

s = io.open(P, "r", encoding="utf-8", newline="").read()
before = s

OLD_SERIES = "  return ['#2997ff', '#f97316', '#22c55e', '#f43f5e',\n          '#8b5cf6', '#14b8a6', '#ec4899', '#eab308'];"
NEW_SERIES = "  return ['#2997ff', '#ea580c', '#16a34a', '#f43f5e',\n          '#8b5cf6', '#0d9488', '#ec4899', '#a16207'];"

assert s.count(OLD_SERIES.replace("\n", CRLF)) == 1, "分类色板锚点未命中"
s = s.replace(OLD_SERIES.replace("\n", CRLF), NEW_SERIES.replace("\n", CRLF), 1)

pairs = [
    # candy 兜底同步（与 CSS 深色值保持一致，否则读不到变量时又变浅）
    ("get candy2() { return __cssVar('--candy-2', '#f97316'); },",
     "get candy2() { return __cssVar('--candy-2', '#ea580c'); },"),
    ("get candy3() { return __cssVar('--candy-3', '#22c55e'); },",
     "get candy3() { return __cssVar('--candy-3', '#16a34a'); },"),
    # CSS 深色主题的糖果色同步（否则 tooltip 圆点 / 轴名还是旧的浅色）
    (" --candy-2: #f97316;\n --candy-3: #22c55e;",
     " --candy-2: #ea580c;\n --candy-3: #16a34a;"),
    # 四所色板：大商所 #0ea5e9 白底 2.77 ⇒ 加深
    ("exMeta = { CZCE: ['郑商所', '#db2777'], DCE: ['大商所', '#0ea5e9'],",
     "exMeta = { CZCE: ['郑商所', '#db2777'], DCE: ['大商所', '#0284c7'],"),
    # 多品种对比首色同样问题
    ("_CMP_COLORS: ['#0ea5e9', '#d97706'", "_CMP_COLORS: ['#0284c7', '#d97706'"),
    # A4 加工利润第一色（#FF9F0A 白底 2.06）
    ("const colors = ['#FF9F0A', '#3B82F6'", "const colors = ['#EA580C', '#3B82F6'"),
]
for old, new in pairs:
    o = old.replace("\n", CRLF)
    n2 = new.replace("\n", CRLF)
    if s.count(o) == 0:
        print("  (跳过，未命中) %s" % old[:56])
        continue
    assert s.count(o) == 1, "命中 %d: %s" % (s.count(o), old[:60])
    s = s.replace(o, n2, 1)

assert s != before
assert s.count("\n") == s.count(CRLF), "CRLF 被破坏"
io.open(P, "w", encoding="utf-8", newline="").write(s)
print("ok")
