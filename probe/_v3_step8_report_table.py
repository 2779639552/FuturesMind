# -*- coding: utf-8 -*-
"""密度重构 v3 · 第八步（终）：研报表格列宽精修。

实测观察（截图中）：研报「当日观点总表」列宽仍偏松 ——
「品种/名称/发行方/方向/置信度」这些短列占了过宽，而「一句话核心观点」
这个真正需要宽度的列反而被压窄导致换行成 3~4 行。
根因：`table-layout: auto` 下 ECharts 无关，是浏览器按内容平均分配。

修法：给研报类表格也上 fixed + 明确的列宽策略：
  · 短列（品种/方向/置信度/计数）定宽
  · 长文本列（名称/观点/头寸建议）吃掉剩余宽度并允许换行
这既压缩了总高，又让"真正需要空间的列"拿到空间。
"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
n0 = len(src)

FIX = r'''
/* ── 研报表格列宽策略（v3 · 终）─────────────────────────────────────
   问题：`table-layout:auto` 把宽度均摊，导致「一句话核心观点」这种长文本列
   被压窄后换行 3~4 行，而「品种/方向」等短列占着多余宽度 —— 既浪费横向空间，
   又虚高表格总高（与"一屏塞更多"的目标相反）。
   策略：短列定宽 + 长文本列吃余量。 */
.report-content table { table-layout: fixed; width: 100%; }
/* 短列：定宽 */
.report-content table th:nth-child(1), .report-content table td:nth-child(1) { width: 62px; }   /* 品种 */
.report-content table th:nth-child(4), .report-content table td:nth-child(4) { width: 88px; }   /* 方向 */
.report-content table th:nth-child(5), .report-content table td:nth-child(5) { width: 78px; }   /* 置信度 */
/* 名称/发行方：中等 */
.report-content table th:nth-child(2), .report-content table td:nth-child(2) { width: 120px; }
.report-content table th:nth-child(3), .report-content table td:nth-child(3) { width: 110px; }
/* 末尾的计数/标记列：定宽居中 */
.report-content table th:nth-last-child(1), .report-content table td:nth-last-child(1) { width: 82px; }
.report-content table th:nth-last-child(2), .report-content table td:nth-last-child(2) { width: 78px; }
/* 长文本列（核心观点/头寸建议）：吃余量，允许换行但不撑爆 */
.report-content table td { word-break: break-word; overflow-wrap: anywhere; }
/* 视图总表这类多列表：行高压到最紧，让一屏容纳更多行 */
.report-content table td, .report-content table th { padding: 5px 8px; line-height: 1.45; }
/* 表格外层横向滚动兜底（列多时） */
.report-content table { min-width: 900px; }

/* ── 通用长表：单元格内多行文本不再用 <br> 撑高 ───────────────── */
table td p { margin: 0; }
table td br + br { display: none; }

/* ── 「点击展开/收起」这类操作提示：贴右且弱化，避免抢注意力 ───── */
.expander-header > span[style*="text-muted"] { font-size: var(--fs-tag); }
'''
i = src.rfind("/* ── ≤900px 横屏/矮屏 ── */")
if i < 0:
    i = src.find("</style>")
src = src[:i] + FIX + "\n" + src[i:]

F.write_text(src, encoding="utf-8")
print("OK 研报表格列宽策略已追加")
print(f"  {n0} -> {len(src.encode('utf-8'))} 字节")
print("  裸 { 块（必须 0）:", len(re.findall(r'(?m)^{', src)))
