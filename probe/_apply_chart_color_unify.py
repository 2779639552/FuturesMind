# -*- coding: utf-8 -*-
"""全站图表配色收敛：解决「曲线太浅」与「图例颜色和曲线不对应」。

【审计实测的两类真问题】
1. **撞色**（图例上是两个一样的色块，分不清哪条线是哪条）
   `--candy-1` 与 `--chart-brand` 取值完全相同（dark 都 #2997ff，light 都 #0071e3），
   而多处分类色板写成 `[CHART.brand, CHART.candy1, ...]` ⇒ 第 1、2 条系列同色：
   · chart-overlay：收盘价 与 自适应因子 都是 #2997ff
   · wt-h-wh：中化兴中 与 中油广西国际 都是 #2997ff
   · wt-c3 饼图：'仓库' 与 '厂库' 都是 #2997ff
2. **太浅**：多个色板里的 #40c8e0 / #60a5fa / #ff9f0a / #30d158 亮度 0.61~0.69，
   在亮色主题的浅底上几乎不可辨；且 `CHART.brand` 的兜底值 `#8fd2f5` 更浅
   （读不到 CSS 变量时会画出"几乎看不见的线"）。

【改法】
· 收敛出一套 8 色分类板 `CHART.series`（全部亮度 0.45~0.68，双主题都够对比，彼此不撞）；
· `_initChart` 给未显式配色的图注入该色板（饼图/排名柱/散点原来吃 ECharts 默认色）；
· 修掉 candy-1 与 brand 同值这个**根**（否则别处 `[brand, candy1]` 还会撞）；
· CHART.brand 的浅色兜底改成实色。
"""
from __future__ import annotations

import io
import os

P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web_template.html")
CRLF = "\r\n"

s = io.open(P, "r", encoding="utf-8", newline="").read()
before = s


def rep(old: str, new: str, label: str, count: int = 1):
    """锚点与替换串都按 LF 书写，这里统一转成文件实际的 CRLF 再比对。"""
    global s
    old = old.replace("\n", CRLF)
    new = new.replace("\n", CRLF)
    n = s.count(old)
    assert n == count, "%s 命中 %d 次（期望 %d）:\n%s" % (label, n, count, old[:100])
    s = s.replace(old, new, count)


# ── ① 修 candy-1 与 brand 同值（撞色之根）+ 压暗两个偏浅的糖果色 ──────────
rep(" --candy-1: #2997ff;\n --candy-2: #ff9f0a;\n --candy-3: #30d158;\n --candy-4: #ff375f;",
    "  /* 【2026-10-21】candy-1 原为 #2997ff，与 --chart-brand 完全相同 ⇒\n"
    "     [brand, candy1] 这类分类色板前两条系列同色，图例上分不清谁是谁。改紫。 */\n"
    "  --candy-1: #a78bfa;\n"
    "  /* candy-2/3 原来是 #ff9f0a / #30d158，亮度 0.66/0.65，亮色主题下偏浅 */\n"
    "  --candy-2: #f97316;\n  --candy-3: #22c55e;\n  --candy-4: #ff375f;",
    "dark candy")

rep(" --candy-1: #0071e3;\n --candy-2: #f56300;\n --candy-3: #00875a;\n --candy-4: #c2185b;",
    "  --candy-1: #7c3aed;\n  --candy-2: #ea580c;\n  --candy-3: #047857;\n  --candy-4: #be185d;",
    "light candy")

# ── ② CHART 增加统一分类色板；brand 的浅色兜底改实色 ──────────────────────
rep(" get candy4() { return __cssVar('--candy-4', '#f3bbe1'); },",
    " get candy4() { return __cssVar('--candy-4', '#be185d'); },\n"
    " // 【2026-10-21】统一分类色板：多系列图（仓库走势/饼图/排名柱/散点）一律用它。\n"
    " //   选色标准：亮度 0.45~0.68 区间 —— 暗底不发黑、亮底不发白；8 色彼此可区分，\n"
    " //   且**不含** brand，避免\"第一条线和第二条线同色\"（旧 candy-1 与 brand 同值踩过）。\n"
    " get series() {\n"
    "  return ['#2997ff', '#f97316', '#22c55e', '#ff375f',\n"
    "          '#8b5cf6', '#14b8a6', '#ec4899', '#eab308'];\n"
    " },",
    "CHART.series")

# brand 兜底：原 #8fd2f5 极浅，读不到 CSS 变量时会画出几乎看不见的线
rep(" get brand() { return __cssVar('--chart-brand', '#8fd2f5'); }, // 主序列线/柱",
    " // 【2026-10-21】兜底值原为 #8fd2f5（很浅）。__cssVar 读不到变量时会静默用它，\n"
    " //   表现为\"曲线浅到看不见\"。兜底必须是可读的实色。\n"
    " get brand() { return __cssVar('--chart-brand', '#2997ff'); }, // 主序列线/柱",
    "brand fallback")

rep(" get brand2() { return __cssVar('--chart-brand-2', '#d8eefc'); }, // 渐变亮端",
    " get brand2() { return __cssVar('--chart-brand-2', '#7cc4ff'); }, // 渐变亮端",
    "brand2 fallback")

# ── ③ _initChart 给未显式配色的图注入统一色板 ───────────────────────────
rep(" const rawSetOption = chart.setOption.bind(chart);\n"
    " chart.setOption = function(option, notMerge, lazyUpdate) {\n"
    " rec.option = option; rec.notMerge = !!notMerge;",
    " const rawSetOption = chart.setOption.bind(chart);\n"
    " chart.setOption = function(option, notMerge, lazyUpdate) {\n"
    " // 【2026-10-21】未显式写 color 的图（饼图/排名柱/散点）原来吃 ECharts 默认色板\n"
    " //   （#5470c6 等，暗底偏暗），与别处的分类色板也不一致。统一注入。\n"
    " if (option && !Array.isArray(option) && option.color === undefined) option.color = CHART.series;\n"
    " rec.option = option; rec.notMerge = !!notMerge;",
    "_initChart color")

# ── ④ 三处分类色板换成 CHART.series（消除撞色 + 偏浅色） ─────────────────
rep(" data: rs.map((r, i) => ({ name: r.name, value: r.qty || 0, itemStyle: { color: [CHART.candy1,CHART.candy2,CHART.candy3,CHART.candy4,'#60a5fa','#2dd4bf','#40c8e0','#5ac8fa'][i % 8] } })),",
    " data: rs.map((r, i) => ({ name: r.name, value: r.qty || 0, itemStyle: { color: CHART.series[i % CHART.series.length] } })),",
    "palette@16492")

rep(" const palette = [CHART.brand, CHART.candy1, CHART.candy2, CHART.candy3, CHART.candy4, '#2dd4bf', '#60a5fa', '#40c8e0', '#94a3b8'];",
    " const palette = CHART.series;",
    "palette@16526")

rep(" const pal = { '仓库': CHART.candy1, '厂库': CHART.brand, '集团': CHART.candy4 };",
    " // 【2026-10-21】原来 '仓库' 用 candy1、'厂库' 用 brand，而这两个变量同值 ⇒\n"
    " //   饼图两类同色。改用统一色板的前三个位置，保证互不相同。\n"
    " const pal = { '仓库': CHART.series[0], '厂库': CHART.series[1], '集团': CHART.series[4] };",
    "pie pal@16557")

rep(" const PAL = [CHART.brand, CHART.candy1, CHART.candy2, CHART.candy3, CHART.candy4, '#60a5fa', '#2dd4bf', '#40c8e0'];",
    " const PAL = CHART.series;",
    "PAL@16907")

# ── ⑤ 按涨跌变色的柱/点：series 层给中性色，只影响图例色块 ──────────────
#   data 项各自带 itemStyle，优先级高于 series ⇒ 柱子仍是红涨绿跌，但图例不再
#   显示一个与柱子无关的默认色。
# 「按值变色」的柱/点：图例只能显示一种颜色，与红绿交替的柱子永远对不上。
# 做法是让这三个系列的图例项**不画色块**（icon:'none'），只留文字 + 在名字里标注
# 「红涨绿跌」，读者就不会去找"哪个色块对应柱子"。
rep("legend: { top: 0, textStyle: { color: c.label, fontSize: 11 },\n"
    " data: ['仓单量', '每日增减'] },",
    "legend: { top: 0, textStyle: { color: c.label, fontSize: 11 },\n"
    " // 「每日增减」逐点按涨跌取色，图例只能显示一个色块 ⇒ 干脆不画色块，避免误导\n"
    " data: ['仓单量', { name: '每日增减（红涨绿跌）', icon: 'none' }] },",
    "wt-h-prod legend")

rep(" data: align(p.change).map(v => ({",
    " name: '每日增减（红涨绿跌）', type: 'bar', xAxisIndex: 1, yAxisIndex: 1,\n"
    " data: align(p.change).map(v => ({",
    "wt-h-prod series name")

rep("legend: { data: ['注册仓单', '日变化'], bottom: 0, textStyle: { color: textColor, fontSize: 10 } },",
    "legend: { data: ['注册仓单', { name: '日变化（红涨绿跌）', icon: 'none' }],\n"
    "  bottom: 0, textStyle: { color: textColor, fontSize: 10 } },",
    "chart-f-inv legend")

rep("legend: { data: ['基差', '基差率%'], bottom: 0, textStyle: { color: textColor, fontSize: 10 } },",
    "legend: { data: [{ name: '基差（红涨绿跌）', icon: 'none' }, '基差率%'],\n"
    "  bottom: 0, textStyle: { color: textColor, fontSize: 10 } },",
    "chart-f-basis legend")

assert s != before
assert s.count("\n") == s.count(CRLF), "CRLF 被破坏: LF=%d CRLF=%d" % (s.count("\n"), s.count(CRLF))
io.open(P, "w", encoding="utf-8", newline="").write(s)
print("ok, len", len(s))
