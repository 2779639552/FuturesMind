# -*- coding: utf-8 -*-
"""横贯整页的「长条图」宽度减半（2026-10-21 用户要求）。

判据（实测于 1600px 视口）：
  宽 ≈ 卡片内容宽（>= cardW-70）且 宽/高 >= 4  ⇒ 视觉上是一条横贯整页的"长图"，
  典型如基本面量化的期限结构 1444×240（比例 6）、数据仓库季节性图 1492×170（8.8）。

做法：
  · 新增 `.chart-half { max-width:50% }`（窄屏 <=900px 自动回退 100%，手机上半宽没法看）；
  · 只给上述几类容器挂 class，**不动高度**（高度是内容密度，宽度才是"长度"）；
  · 研报 7 类指标是动态生成的（id 为 chart-f-r-*），需改模板字符串。
"""
import io
from pathlib import Path

TPL = Path(r"D:\AgentSense\AgentSense\web_template.html")

CSS_OLD = ".chart-container { width: 100%; height: 320px; }\r\n"
CSS_NEW = (
    ".chart-container { width: 100%; height: 320px; }\r\n"
    "/* 【2026-10-21】横贯整页的「长条图」宽度减半（用户要求：长度占据整页的长图缩短一半）。\r\n"
    "   只缩宽度、不动高度 —— 这类图的问题是横向拉成一条，纵向本来就矮。\r\n"
    "   窄屏回退 100%：手机上半宽图连 x 轴标签都放不下。 */\r\n"
    ".chart-half { max-width: 50%; }\r\n"
    "@media (max-width: 900px) { .chart-half { max-width: 100%; } }\r\n"
)

PAIRS = [
    # 多品种对比（技术面量化，1492×360）
    ('<div class="chart-container" id="chart-q-cmp" style="height:360px"></div>',
     '<div class="chart-container chart-half" id="chart-q-cmp" style="height:360px"></div>'),
    # 情绪得分 vs 价格（情绪数据，1492×320）
    ('<div class="chart-container" id="chart-overlay" style="height:420px"></div>',
     '<div class="chart-container chart-half" id="chart-overlay" style="height:420px"></div>'),
    # 品种仓单走势（基本面量化 · 仓单分析，1444×320）
    ('<div class="chart-container" id="wt-h-prod" style="height:420px"></div>',
     '<div class="chart-container chart-half" id="wt-h-prod" style="height:420px"></div>'),
    # 期限结构（基本面量化，1444×240）
    ('<div class="chart-container" id="chart-f-term" style="height:240px"></div>',
     '<div class="chart-container chart-half" id="chart-f-term" style="height:240px"></div>'),
    # 数据仓库 · 季节性图（1492×170，最扁的一张，比例 8.8）
    ('<div id="gt-chart" style="height:170px"></div>',
     '<div id="gt-chart" class="chart-half" style="height:170px"></div>'),
    # 研报基本面指标 · 7 类动态卡（每张 1492×240）；用 class 而不是 inline max-width，
    # 这样窄屏的媒体查询回退才生效
    ('<div class="chart-container" id="chart-${P}-r-${k.replace(/_/g, \'-\')}"',
     '<div class="chart-container chart-half" id="chart-${P}-r-${k.replace(/_/g, \'-\')}"'),
]

raw = io.open(str(TPL), "r", encoding="utf-8", newline="").read()
out = raw
for old, new in PAIRS:
    n = out.count(old)
    assert n == 1, "锚点命中 %d 次: %r" % (n, old[:70])
    out = out.replace(old, new, 1)

assert out.count(CSS_OLD) == 1, "CSS 锚点命中 %d 次" % out.count(CSS_OLD)
out = out.replace(CSS_OLD, CSS_NEW, 1)

assert out.count("\n") == out.count("\r\n"), "出现裸 LF"
assert out.count(".chart-half { max-width: 50%; }") == 1
io.open(str(TPL), "w", encoding="utf-8", newline="").write(out)
print("OK")
