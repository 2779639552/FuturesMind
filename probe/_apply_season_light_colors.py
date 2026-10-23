# -*- coding: utf-8 -*-
"""季节性图年份色：亮色主题下用同色相的深一档版本。

「季节性图绘制包」给的是 2024 #00B0F0 / 2025 #00B050 —— 暗底上很好，但白底上
WCAG 对比度只有 2.48 / 2.87（<3），实测截图里两条线淡得几乎看不出。
不换色相（仍保持"蓝=2024、绿=2025"的年份语义），只在亮色主题压深一档。
"""
from __future__ import annotations

import io
import os

P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web_template.html")
CRLF = "\r\n"

s = io.open(P, "r", encoding="utf-8", newline="").read()

OLD = (
    " _seasonColor(y) {" + CRLF
    + " const YCOL = { 2021: '#000000', 2022: '#FFC000', 2023: '#70449F'," + CRLF
    + " 2024: '#00B0F0', 2025: '#00B050', 2026: '#FF0000' };" + CRLF
    + " const EXTRA = ['#8C8C8C', '#C0504D', '#4F81BD', '#9BBB59', '#8064A2', '#4BACC6'];" + CRLF
    + " const k = String(y);" + CRLF
    + " if (Object.prototype.hasOwnProperty.call(YCOL, k)) {" + CRLF
    + " return (k === '2021') ? CHART.seasonBlack : YCOL[k];" + CRLF
    + " }" + CRLF
    + " return EXTRA[Math.abs(parseInt(k, 10) || 0) % EXTRA.length];" + CRLF
    + " },"
)
NEW = (
    " _seasonColor(y) {" + CRLF
    + " // 暗色：照搬「季节性图绘制包」season_colors.py 的 6 色映射" + CRLF
    + " const YCOL = { 2021: '#000000', 2022: '#FFC000', 2023: '#70449F'," + CRLF
    + " 2024: '#00B0F0', 2025: '#00B050', 2026: '#FF0000' };" + CRLF
    + " // 【2026-10-21】亮色主题下 #00B0F0 / #00B050 的白底对比度只有 2.48 / 2.87（<3），" + CRLF
    + " //   实测截图里两条年份线淡到几乎看不见。保持色相不变、压深一档 ——" + CRLF
    + " //   \"蓝=2024 / 绿=2025\" 的年份语义不变，只是白底可读。" + CRLF
    + " const YCOL_LIGHT = { 2021: '#595959', 2022: '#B8860B', 2023: '#5B3B85'," + CRLF
    + " 2024: '#007BA6', 2025: '#00703C', 2026: '#C00000' };" + CRLF
    + " const EXTRA = ['#8C8C8C', '#C0504D', '#4F81BD', '#9BBB59', '#8064A2', '#4BACC6'];" + CRLF
    + " const k = String(y);" + CRLF
    + " // document 在 harness(Node 桩)里可能不存在 ⇒ 与 seasonBlack 一样用 try 兜底" + CRLF
    + " let _light = false;" + CRLF
    + " try { _light = (document.documentElement.getAttribute('data-theme') === 'light'); } catch (e) {}" + CRLF
    + " const MAP = _light ? YCOL_LIGHT : YCOL;" + CRLF
    + " if (Object.prototype.hasOwnProperty.call(MAP, k)) {" + CRLF
    + " return (k === '2021' && !_light) ? CHART.seasonBlack : MAP[k];" + CRLF
    + " }" + CRLF
    + " return EXTRA[Math.abs(parseInt(k, 10) || 0) % EXTRA.length];" + CRLF
    + " },"
)
assert s.count(OLD) == 1, s.count(OLD)
s = s.replace(OLD, NEW, 1)
assert s.count("\n") == s.count(CRLF), "CRLF 被破坏"
io.open(P, "w", encoding="utf-8", newline="").write(s)
print("ok")
