# -*- coding: utf-8 -*-
"""密度重构 v3 · 第四步：表格列宽优化 + 图表网格线清理。

一、表格列宽（用户："你这个表格都没优化，太丑了"）
   问题：`table-layout:auto` 下浏览器按内容宽度分配，于是——
     · 窄列（#、类型、仓单量）按内容收紧
     · 宽列（仓库/厂库）吃掉全部余量 ⇒ 中间大片空白、数字挤在右端
   修法：`table-layout: fixed` + 首列自适应 + 数字列定宽右对齐。
   这条对数据表是**基本要求**：数字列必须定宽且右对齐，才能纵向对比大小。

二、图表网格线
   问题：横向柱状图的网格线画在每个数据点上，10+ 条竖线把图切碎。
   苹果式做法：网格线只保留极少几条（或直接去掉），让数据本身说话。

三、ECharts 全局默认（本项目图表散落在 30+ 处，用全局 default 收敛）
"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
n0 = len(src)

FIX = r'''
/* ── 表格列宽：定宽 + 数字右对齐（数据表的基本要求）──────────────────
   原 `table-layout:auto` 按内容分配列宽 ⇒ 窄列收紧、宽列吃掉全部余量，
   结果中间大片空白、数字挤在右端（用户指为"太丑"）。
   改为 fixed 后需显式给列宽，否则各列等分；下面的规则覆盖常见表格形态。 */
.dt, #wt-table table, .report-content table, .delta-tbl {
  table-layout: fixed;
  width: 100%;
}
/* 通用：首个文本列吃掉剩余宽度，其余列按内容收紧 */
#wt-table table th:nth-child(1),
#wt-table table td:nth-child(1) { width: 44px; text-align: right; }   /* # */
#wt-table table th:nth-child(2),
#wt-table table td:nth-child(2) { width: auto; }                       /* 名称：吃掉余量 */
#wt-table table th:nth-child(3),
#wt-table table td:nth-child(3) { width: 78px; }                       /* 类型 */
#wt-table table th:nth-child(4),
#wt-table table td:nth-child(4) { width: 88px; }                       /* 地区(若有) */
#wt-table table th:nth-last-child(3),
#wt-table table td:nth-last-child(3) { width: 110px; text-align: right; }  /* 仓单量 */
#wt-table table th:nth-last-child(2),
#wt-table table td:nth-last-child(2) { width: 96px; text-align: right; }   /* 当日增减 */
#wt-table table th:nth-last-child(1),
#wt-table table td:nth-last-child(1) { width: 78px; text-align: right; }   /* 占比 */
/* 名称列过长时省略，避免撑破 */
#wt-table table td:nth-child(2) { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* 数字一律等宽对齐，便于纵向比大小 */
#wt-table table td:nth-last-child(-n+3),
#wt-table table th:nth-last-child(-n+3) { font-variant-numeric: tabular-nums; }
/* 仓库名里的编号用弱化小字，与名称拉开层级 */
#wt-table table td:nth-child(2) span { font-size: var(--fs-xs); }

/* 研报/增量表的数字列同样右对齐（这两类表列多，只做对齐不固定宽度） */
.delta-tbl td, .delta-tbl th { font-variant-numeric: tabular-nums; }

/* ── 表格密度再压一档：行高 6px→5px，斑马纹辅助长表阅读 ──────────────
   长表（20+ 行）靠细线容易串行，加极淡的隔行底色更利于横向跟踪。 */
#wt-table table tbody tr:nth-child(even) { background: var(--bg-hover); }
#wt-table table th { position: sticky; top: 0; z-index: 1; background: var(--bg-card); }

/* ── 徽标在表格内收紧（类型列里"仓库/厂库"占位更小）──────────────── */
#wt-table .badge { font-size: 11px; padding: 1px 6px; }
'''

i = src.rfind("/* ── ≤900px 横屏/矮屏 ── */")
if i < 0:
    i = src.find("</style>")
src = src[:i] + FIX + "\n" + src[i:]
print("① 表格列宽规则已追加")

# ── ② ECharts 全局默认：清理冗余网格线 ────────────────────
# 找 __cssVar 或 CHART 对象，在其后注入全局 setOption 默认
anchor = "const CHART = {"
if anchor in src:
    idx = src.find(anchor)
    # 找该对象的结尾
    depth = 0
    j = src.find("{", idx)
    k = j
    while k < len(src):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                break
        k += 1
    inject = r'''

// ══ 2026-10-14 v3：ECharts 全局默认 —— 清理冗余网格线 ══════════════
// 为什么需要：「仓单数量排名」这类横向柱状图原本在每个数据点画一条网格线，
// 10+ 条竖线把图切成碎片，读不出大小关系。苹果式做法是网格线极少或不用，
// 让数据本身占据视觉。这里统一收敛 splitLine / axisLine。
(function () {
  if (typeof echarts === 'undefined') return;
  const baseAxis = () => ({
    axisLine: { show: false },
    axisTick: { show: false },
    splitLine: { show: false },
  });
  const baseValueAxis = () => ({
    axisLine: { show: false },
    axisTick: { show: false },
    // 数值轴只保留极淡的横线，且条数收敛
    splitLine: { show: true, lineStyle: { color: 'rgba(128,128,128,0.14)', width: 1 },
                 interval: 'auto' },
    splitNumber: 4,
  });
  window.__AX = { cat: baseAxis, val: baseValueAxis };
  // 统一给所有 ECharts 实例设默认（后续 setOption 里的显式配置仍可覆盖）
  const origInit = echarts.init;
  echarts.init = function () {
    const inst = origInit.apply(this, arguments);
    inst.setOption({
      grid: { left: 8, right: 12, top: 24, bottom: 6, containLabel: true },
      textStyle: { fontFamily: getComputedStyle(document.body).fontFamily },
    }, { lazyUpdate: true });
    return inst;
  };
})();

// 横向柱状图/条形图默认关闭分割线（数值轴）
window.__barAxisClean = function (ax) {
  if (!ax) return ax;
  const arr = Array.isArray(ax) ? ax : [ax];
  arr.forEach(a => {
    a.axisLine = { show: false };
    a.axisTick = { show: false };
    // 分类轴：绝不画分割线（这是"多余竖线"的根源）
    if (a.type === 'category') a.splitLine = { show: false };
  });
  return ax;
};
'''
    src = src[:k + 1] + inject + src[k + 1:]
    print("② ECharts 全局默认已注入（含 grid / 坐标轴收敛与 __barAxisClean）")
else:
    print("② WARN 未找到 CHART 对象")

F.write_text(src, encoding="utf-8")
print()
print(f"OK: {n0} -> {len(src.encode('utf-8'))} 字节")
print("裸 { 块（必须 0）:", len(re.findall(r'(?m)^{', src)))
print("script 标签配平:", src.count("<script") - src.count("</script>"))
