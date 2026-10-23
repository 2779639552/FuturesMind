# -*- coding: utf-8 -*-
"""图例可读性修复：重叠 / 贴边裁切 / 饼图标签与扇区颜色不对应。

【实测证据（暗色主题截图）】
· chart-overlay：5 项长图例挤在右上角，「情绪7日均线」与「简单平均情绪」文字重叠；
· wt-h-wh：图例 bottom:0 贴着容器底边，文字下半截被裁；
· wt-c2 / wt-c3：饼图 label 一律灰色，与扇区颜色无关，看不出哪块对应哪个名字。
"""
from __future__ import annotations

import io
import os

P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web_template.html")
CRLF = "\r\n"

s = io.open(P, "r", encoding="utf-8", newline="").read()
before = s


def rep(old: str, new: str, label: str):
    global s
    old = old.replace("\n", CRLF)
    new = new.replace("\n", CRLF)
    n = s.count(old)
    assert n == 1, "%s 命中 %d 次:\n%s" % (label, n, old[:120])
    s = s.replace(old, new, 1)


rep(""" legend: {
 data: ['收盘价', '作者加权情绪', '情绪7日均线', '简单平均情绪', '自适应因子'],
 top: 0,
 right: 10, // 【布局】图例移到右上角, 与标题错开, 不再挤占底部空间
 icon: 'roundRect',
 itemWidth: 14,
 itemHeight: 8,
 textStyle: { color: CHART.axis, fontSize: 11 }
 },""",
    """ legend: {
 data: ['收盘价', '作者加权情绪', '情绪7日均线', '简单平均情绪', '自适应因子'],
 top: 0,
 right: 10, // 【布局】图例移到右上角, 与标题错开, 不再挤占底部空间
 icon: 'roundRect',
 itemWidth: 12,
 itemHeight: 8,
 itemGap: 8,
 // 【2026-10-21】5 项长名字在 680px 宽里放不下 ⇒ 后两项文字重叠（截图实测）。
 //   改 scroll：放不下时出翻页箭头，不再叠字。
 type: 'scroll',
 textStyle: { color: CHART.axis, fontSize: 10 }
 },""",
    "chart-overlay legend")

rep(""" legend: { bottom: 0, textStyle: { color: c.label, fontSize: 10 }, type: 'scroll' },
 grid: { left: 8, right: 16, top: 12, bottom: 46, containLabel: true },""",
    """ // 【2026-10-21】图例原来贴着容器底边（bottom:0）⇒ 文字下半截被裁（截图实测）。
 legend: { bottom: 2, textStyle: { color: c.label, fontSize: 10 }, type: 'scroll' },
 grid: { left: 8, right: 16, top: 12, bottom: 52, containLabel: true },""",
    "wt-h-wh legend")

rep(" label: { color: c.label, fontSize: 10, formatter: '{b}\\n{d}%' },",
    " // 【2026-10-21】标签原来统一灰色，读者看不出哪块对应哪个名字 ⇒ 跟随扇区色\n"
    " label: { color: 'inherit', fontSize: 10, formatter: '{b}\\n{d}%' },",
    "pie label 1")

rep(" label: { color: c.label, fontSize: 11, formatter: '{b} {d}%' },",
    " label: { color: 'inherit', fontSize: 11, formatter: '{b} {d}%' },",
    "pie label 2")

assert s != before
assert s.count("\n") == s.count(CRLF), "CRLF 被破坏"
io.open(P, "w", encoding="utf-8", newline="").write(s)
print("ok")
