# -*- coding: utf-8 -*-
"""密度重构 v3 · 第六步（终）：进一步压缩垂直空间。

用户诉求：「在一个页面内尽可能多的去塞下信息」。上一轮已把卡片内距/间距/字号收紧，
但仍有几处明显浪费：
  · 图表容器固定高 420/340px，横向柱状图只用了一半有效高度（数据条细）
  · KPI 卡（stat-card）内 padding 仍偏松
  · 「品种信息」这类单行文本卡占 76px 却有 40px 空白
  · 区块标题行上下留白偏大
本步统一压一档，并给图表容器更合理的默认高度。
"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
n0 = len(src)

FIX = r'''
/* ══ 密度加码（v3 · 终）══════════════════════════════════════════════
   目标：一屏塞下尽可能多的信息。所有数值都取自间距阶，不引入游离值。
   ══════════════════════════════════════════════════════════════════ */

/* ① 区块与卡片内边距再压一档 */
.card { padding: 12px 14px; }
.card h3 { margin-bottom: 8px; }
.card > div + div { margin-top: 8px; }
/* 卡片内直接子元素之间统一 8px（原各处 10~14px 不等） */
.card > *:not(:first-child) { margin-top: 8px !important; }

/* ② 卡片之间 12px → 10px；区块之间保持 12px 以区分层级 */
* + .card { margin-top: 10px; }
.tab > * + * { margin-top: 12px !important; }

/* ③ KPI 指标带：再压一档（9px/14px → 7px/12px） */
.stat-card { padding: 7px 12px; min-width: 96px; }
.stat-card .stat-val { font-size: var(--fs-base); line-height: 1.2; }
.stat-card .stat-lbl { font-size: 11px; margin-top: 0; }
/* 注：11px 仅用于"标签"这类辅助信息（非正文），是密度与可读性的折中；
   正文/表格/按钮一律 ≥12px。 */

/* ④ 图表容器：默认高度收敛，并让长条图更紧凑
   为什么：固定 420px 高在只有 15 条数据时，条与条之间会拉出大片空白。 */
.chart-container { height: 260px; }
.chart-container[style*="height:420px"],
.chart-container[style*="height: 420px"] { height: 320px !important; }
.chart-container[style*="height:340px"],
.chart-container[style*="height: 340px"] { height: 260px !important; }

/* ⑤ 表格：行高再压，字号保持 13px（可读性下限） */
table th { padding: 6px 9px; }
table td { padding: 5px 9px; }
#wt-table table td:nth-child(2) { max-width: 0; }   /* 配合 fixed 布局启用省略号 */

/* ⑥ 折叠条：11px/0 padding → 更紧 */
.expander-header { padding: 9px 0; }
/* 展开区顶部再收 */
.expander-body { padding: 0 0 10px; }

/* ⑦ 表单：控件高 34 → 32px，行距更紧 */
input, select, textarea { min-height: 32px; }
.card > div[style*="display:flex"] { gap: 10px !important; }

/* ⑧ 区块标题（"1 · 仓库仓单数量分布"这类）与卡片外元素间距收紧 */
.wt-tabs, .wt-kpi, .sent-plat-filter { margin-bottom: 8px; }
.grid { gap: 10px; }

/* ⑨ 长表斑马纹与紧凑并存时避免视觉噪音：只在高密度明细表使用 */
#wt-table table tbody tr:nth-child(even) { background: var(--bg-hover); }

/* ⑩ 移动端：密度回退（触屏可点区优先，不做极致压缩） */
@media (max-width: 768px) {
  .card { padding: 12px 12px; }
  .stat-card { padding: 8px 10px; min-width: 0; }
  .stat-card .stat-val { font-size: var(--fs-base); }
  .stat-card .stat-lbl { font-size: var(--fs-xs); }
  .chart-container { height: 220px; }
  input, select, textarea { min-height: 44px; }
  table th, table td { padding: 7px 8px; }
  table td { font-size: var(--fs-sm); }
}
'''
i = src.rfind("/* ── ≤900px 横屏/矮屏 ── */")
if i < 0:
    i = src.find("</style>")
src = src[:i] + FIX + "\n" + src[i:]

F.write_text(src, encoding="utf-8")
print("OK 密度加码层已追加")
print(f"  {n0} -> {len(src.encode('utf-8'))} 字节")
print("  裸 { 块（必须 0）:", len(re.findall(r'(?m)^{', src)))
print("  script 配平:", src.count("<script") - src.count("</script>"))
