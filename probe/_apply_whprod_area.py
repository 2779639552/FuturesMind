# -*- coding: utf-8 -*-
"""仓单量面积填充与线同色。

原来 `areaStyle` 硬编码橙色 rgba(249,115,22,0.25)，而这条线是品牌蓝 CHART.brand
⇒ 截图里线是蓝的、底下填的却是橙色，读者会以为填充属于另一条序列（图例只有"仓单量"一项）。
"""
from __future__ import annotations

import io
import os

P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web_template.html")
CRLF = "\r\n"

s = io.open(P, "r", encoding="utf-8", newline="").read()

OLD = (
    " areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [" + CRLF
    + " { offset: 0, color: 'rgba(249,115,22,0.25)' }, { offset: 1, color: 'rgba(249,115,22,0)' }]) }"
)
NEW = (
    " // 【2026-10-21】面积填充原来是硬编码橙色，而这条线是品牌蓝 ⇒ 线蓝、面积橙，" + CRLF
    + " //   读者会以为填充属于另一条序列。改成与线同色（品牌蓝）的渐隐。" + CRLF
    + " areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [" + CRLF
    + " { offset: 0, color: CHART.brandA25 }, { offset: 1, color: CHART.brandA02 }]) }"
)
assert s.count(OLD) == 1, s.count(OLD)
s = s.replace(OLD, NEW, 1)
assert s.count("\n") == s.count(CRLF), "CRLF 被破坏"
io.open(P, "w", encoding="utf-8", newline="").write(s)
print("ok")
