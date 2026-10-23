# -*- coding: utf-8 -*-
"""A9 多维度曲线的 x 轴：日期标签原来全挤在底部（778px 里塞 119 个）。

`hideOverlap: true` + `rotate: 45` 在 ECharts 里对 category 轴并不可靠 ——
实测截图仍是一排叠字的斜日期。改成按点数**显式抽稀到约 8 个**，
标签不旋转（"MM-DD" 5 个字符，8 个 × 40px 远小于 778px），
同时把 grid.bottom 从 58 收到 44（不旋转就不需要那么多留白，绘图区更高）。
"""
from __future__ import annotations

import io
import os

P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web_template.html")
CRLF = "\r\n"

s = io.open(P, "r", encoding="utf-8", newline="").read()

OLD = (
    " grid: { left: 66, right: 62, top: 16, bottom: 58 }," + CRLF
    + " xAxis: { type: 'category', data: axis," + CRLF
    + " axisLine: { lineStyle: { color: gridColor } }," + CRLF
    + " // 日期只显示 月-日(完整 ISO 10 字符在 120+ 天时会互相压叠); hideOverlap 让" + CRLF
    + " // ECharts 自行抽稀标签, 比写死 interval 更能适应不同天数的窗口。" + CRLF
    + " axisLabel: { color: CHART.axis, fontSize: 10, rotate: axis.length > 60 ? 45 : 0," + CRLF
    + " hideOverlap: true, formatter: v => String(v).slice(5) }," + CRLF
)
NEW = (
    " // 【2026-10-21】bottom 58→44：标签不再旋转，不需要留那么多斜排空间。" + CRLF
    + " grid: { left: 66, right: 62, top: 16, bottom: 44 }," + CRLF
    + " xAxis: { type: 'category', data: axis," + CRLF
    + " axisLine: { lineStyle: { color: gridColor } }," + CRLF
    + " // 日期只显示 月-日；**显式按点数抽稀到约 8 个** —— 原来靠 hideOverlap + rotate 45，" + CRLF
    + " //   在 778px 里塞 119 个点实测仍是一排叠字（截图确认）。8 个 × 40px 远小于可用宽度，" + CRLF
    + " //   所以也不再需要旋转。" + CRLF
    + " axisLabel: { color: CHART.axis, fontSize: 10, rotate: 0," + CRLF
    + " interval: Math.max(0, Math.ceil(axis.length / 8) - 1)," + CRLF
    + " formatter: v => String(v).slice(5) }," + CRLF
)
assert s.count(OLD) == 1, s.count(OLD)
s = s.replace(OLD, NEW, 1)
assert s.count("\n") == s.count(CRLF), "CRLF 被破坏"
io.open(P, "w", encoding="utf-8", newline="").write(s)
print("ok")
