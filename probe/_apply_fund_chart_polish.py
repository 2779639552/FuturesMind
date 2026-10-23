# -*- coding: utf-8 -*-
"""A1 图表收窄 + 三处轴标签遮挡 + A4 曲线配色（2026-10-21 用户反馈）。

用户原话：
  ·「A1 中图表过大，我之前提过把过长的图表压缩长度」
  ·「部分图表中的字体存在被遮挡」
  ·「A4 中图表的曲线颜色太浅，换成其他颜色」

根因：
1. A1 拆成独立卡后**丢了原来的 grid-2 半宽约束** ⇒ 季节性 1572×300、仓单 1572×240（宽高比 5~6.5）。
2. 遮挡是**旋转轴标签的底部留白不够**：
   · 期限结构 bottom:30 + rotate:30 ⇒ 合约名下半截被裁；
   · 加工利润 bottom:46 + rotate:45 ⇒ 日期被裁。
3. A4 配色第 1 项写成 `var(--amber)` —— **ECharts 画布不认 CSS 变量**，
   非法色值回落成浅灰（表格里的圆点是 HTML，能解析，所以"表格有颜色、曲线是灰的"）。

⚠ CRLF：写回 newline=""。
"""
import io

P = r"D:\AgentSense\AgentSense\web_template.html"
CRLF = "\r\n"

s = io.open(P, "r", encoding="utf-8", newline="").read()
before = s

# ── ① A1 两张图收成半宽（沿用全站 .chart-half = max-width:50%）──────────
OLD1 = '<div class="chart-container" id="chart-f-soc" style="height:300px"></div>'
NEW1 = '<div class="chart-container chart-half" id="chart-f-soc" style="height:300px"></div>'
assert s.count(OLD1) == 1, "① soc 锚点 %d" % s.count(OLD1)
s = s.replace(OLD1, NEW1, 1)

OLD2 = '<div class="chart-container" id="chart-f-inv" style="height:240px"></div>'
NEW2 = '<div class="chart-container chart-half" id="chart-f-inv" style="height:240px"></div>'
assert s.count(OLD2) == 1, "① inv 锚点 %d" % s.count(OLD2)
s = s.replace(OLD2, NEW2, 1)

# ── ② 期限结构：底部留白 + 右边距（"近月"标注贴在右缘）──────────────
OLD3 = (' grid: { left: 58, right: 18, top: 14, bottom: 30 },' + CRLF
        + ' xAxis: { type: \'category\', data: rows.map(r => r.contract),' + CRLF
        + ' axisLabel: { color: textColor, fontSize: 9, rotate: 30 },')
NEW3 = (' // 【2026-10-21】bottom 30→52 / rotate 30→24：合约名旋转后下半截被画布裁掉；' + CRLF
        + ' //   right 18→46：markLine 的「近月」标注贴在右缘会被切。' + CRLF
        + ' grid: { left: 58, right: 46, top: 14, bottom: 52 },' + CRLF
        + ' xAxis: { type: \'category\', data: rows.map(r => r.contract),' + CRLF
        + ' axisLabel: { color: textColor, fontSize: 9, rotate: 24 },')
assert s.count(OLD3) == 1, "② 期限结构锚点 %d" % s.count(OLD3)
s = s.replace(OLD3, NEW3, 1)

# ── ③ 加工利润：底部留白（rotate 45 需要更多空间）──────────────────
OLD4 = (" legend: { bottom: 0, textStyle: { color: CHART.axis, fontSize: 11 } }," + CRLF
        + " grid: { left: 65, right: 26, top: 16, bottom: 46 }," + CRLF)
NEW4 = (" legend: { bottom: 0, textStyle: { color: CHART.axis, fontSize: 11 } }," + CRLF
        + " // 【2026-10-21】bottom 46→64：日期轴 rotate:45，原留白不足 ⇒ 日期下半截被裁。" + CRLF
        + " grid: { left: 65, right: 26, top: 16, bottom: 64 }," + CRLF)
assert s.count(OLD4) == 1, "③ 利润 grid 锚点 %d" % s.count(OLD4)
s = s.replace(OLD4, NEW4, 1)

# ── ④ A4 配色：var() → 具体色值（画布不认 CSS 变量）────────────────
OLD5 = " const colors = ['var(--amber)', '#60a5fa', '#22c55e', 'var(--brand)', '#ef4444', '#14b8a6'];"
NEW5 = (" // 【2026-10-21】原第 1 项写 `var(--amber)`、第 4 项写 `var(--brand)` ——\n"
        " //   ECharts 画在 canvas 上，**解析不了 CSS 变量** ⇒ 非法色值回落成浅灰\n"
        " //   （表格里的圆点是 HTML 元素，能解析，于是出现「表格有色、曲线是灰的」）。\n"
        " //   这里全部用具体色值；同 _repaintCharts 的「按色值精确替换」机制兼容。\n"
        " const colors = ['#FF9F0A', '#3B82F6', '#22C55E', '#A855F7', '#F43F5E', '#14B8A6'];")
assert s.count(OLD5) == 1, "④ 配色锚点 %d" % s.count(OLD5)
s = s.replace(OLD5, NEW5, 1)

# ── ⑤ 季节性图：顶部/右侧留白（最新值标注贴边）─────────────────────
OLD6 = " grid: { left: 46, right: 14, top: 10, bottom: 44 },"
NEW6 = (" // 【2026-10-21】top 10→16 / right 14→40：Y 轴最高刻度与「最新值」标注都贴边被切。\n"
        " grid: { left: 46, right: 40, top: 16, bottom: 44 },")
assert s.count(OLD6) == 1, "⑤ 季节 grid 锚点 %d" % s.count(OLD6)
s = s.replace(OLD6, NEW6, 1)

# 注入片段用 \n 书写 ⇒ 归一为 CRLF
s = s.replace("\r\n", "\n").replace("\n", CRLF)
assert s != before
assert s.count("\n") == s.count(CRLF), "CRLF 被破坏"
io.open(P, "w", encoding="utf-8", newline="").write(s)
print("patched ok")
