# -*- coding: utf-8 -*-
"""重做视觉层 · 第三步：单一设计层（v2）
插在主 <style> 的 </style> 之前 —— 同特异性下源序后者胜，因此这一层可安全重定义
前面任何规则，且回退只需删掉本段。

本层解决上一轮的六个具体问题：
  ① 卡片贴在一起       → 间距系统（容器 margin-top 统一 + 自带底距清零）
  ② 嵌套卡片同色糊一起  → .card .card 换 --bg-tile-nested
  ③ 图标按钮高度不一    → .btn:has(>svg:only-child) 定宽方形 + min-height 统一
  ④ 白底输入框边界不明  → 描边 + 悬停/聚焦态强化
  ⑤ 宽屏两侧大留白      → main 全宽（已在组件层给 max-width:none，本层补断点）
  ⑥ 正文在超宽屏难读    → 只给正文段落限宽，卡片仍铺满
"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")

LAYER = r'''
/* ══════════════════════════════════════════════════════════════════════
   APPLE 设计层  (v2 · 2026-10-14)
   ----------------------------------------------------------------------
   回退方式：删掉本段即可（前面的令牌层/组件层可独立保留）。
   间距规则写在这里而不是散落在各组件里 —— 上一轮就是因为间距分散在
   旧规则 + 内联样式两处互相打架，才出现"卡片贴一起"。
   ══════════════════════════════════════════════════════════════════════ */

/* ─────────────────────────────────────────────────────────────────
   ① 间距系统（本版最关键的一处）
   -----------------------------------------------------------------
   原实现的问题：卡片靠**内联 margin-bottom:16px** 撑间距，而 `.page-tools .grid`
   被 `margin-bottom:0!important` 清零 ⇒ `.grid` 与其后的卡片之间 **gap=0**，
   卡片改成同色块后就糊成一片（用户截图 1）。
   新实现：卡片自身底距一律清零，改由「前有兄弟元素则加上边距」统一供给 ——
   这样与父容器类型无关（.tab / .page-tools / 裸 div 都能覆盖），不会再漏。
   ───────────────────────────────────────────────────────────────── */
.card { margin-bottom: 0 !important; }
.card:first-child { margin-top: 0 !important; }

/* 纵向堆叠容器：子块之间统一 20px（显式覆盖原有的 12px 等不一致值） */
.tab > * + *,
.page-tools > * + *,
.analysis-sub > * + *,
#f-research > * + * { margin-top: var(--sp-5) !important; }

/* 通用兜底：任何"前面有兄弟"的卡片都拿到上边距 —— 覆盖非上述容器的情形 */
* + .card { margin-top: var(--sp-5); }

/* grid / 指标行 内的卡片由容器 gap 接管，避免与上边距叠加导致每行错位 */
.grid > .card,
.stat-row > .card,
.wt-excards > .card,
.wt-kpi > .card { margin-top: 0; }

/* 卡片内若还嵌卡片，内层换一个底色断开同色 */
.card .card {
  background: var(--bg-tile-nested);
  padding: var(--sp-5) var(--sp-6);
  border-radius: var(--radius);
}

/* ─────────────────────────────────────────────────────────────────
   ② 统一控件高度：图标按钮与文字按钮视觉等高
   -----------------------------------------------------------------
   原问题（用户截图 3）：图标按钮内是 14px 的 SVG、文字按钮内是文字，
   两者**内容盒高度不同**导致按钮高度不齐。组件层已用 min-height:40 钉死总高，
   这里再让"仅含一个图标的按钮"变成正方形，避免两侧内边距把图标挤偏。
   ───────────────────────────────────────────────────────────────── */
.btn:has(> svg:only-child) {
  width: 40px;
  min-width: 40px;
  padding: 0;
}
.btn-sm:has(> svg:only-child) { width: 32px; min-width: 32px; }
.btn > svg { flex: none; display: block; }

/* ─────────────────────────────────────────────────────────────────
   ③ 表单：白底 + 清晰描边 + 悬停反馈（修复"筛选器粘成一片"）
   -----------------------------------------------------------------
   输入框在白底页面上只有 1px 浅描边时，相邻框会读作一整条。这里：
     · 描边取 --border(#d2d2d7)，聚焦转品牌蓝 + 光环
     · 悬停加一层边框加深，给出"这是独立控件"的暗示
   ───────────────────────────────────────────────────────────────── */
input:hover:not(:focus), select:hover:not(:focus), textarea:hover:not(:focus) {
  border-color: var(--text-muted);
}
input[type="date"] { cursor: pointer; }
input[type="file"] { padding: var(--sp-2); min-height: 40px; }
/* 复选框 / 单选：抬到 18px（浏览器默认 13px 太小，不利于点按） */
input[type="checkbox"], input[type="radio"] {
  width: 18px;
  height: 18px;
  min-height: 18px;
  cursor: pointer;
  vertical-align: -3px;
  flex: none;
}

/* ─────────────────────────────────────────────────────────────────
   ④ 排版
   ───────────────────────────────────────────────────────────────── */
h1, h2, h3, h4 { letter-spacing: -0.022em; }
small { font-size: var(--fs-xs); }
/* 超宽屏下正文限宽保可读性；卡片/表格仍铺满 */
.report-content > p,
.report-content > ul,
.report-content > ol,
.report-content > blockquote { max-width: 1100px; }

/* 表格：表头细线、去渐变底；表头不换行（避免竖排） */
table { border-collapse: collapse; }
table th { font-size: var(--fs-xs); font-weight: 400; color: var(--text-muted); white-space: nowrap; }
table td { font-size: var(--fs-base); }
.table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }

/* ─────────────────────────────────────────────────────────────────
   ⑤ 滚动条 / 选中态
   ───────────────────────────────────────────────────────────────── */
* { scrollbar-width: thin; scrollbar-color: var(--border) transparent; }
::-webkit-scrollbar { width: 11px; height: 11px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb {
  background: var(--border);
  border-radius: 980px;
  border: 3px solid transparent;
  background-clip: content-box;
}
::-webkit-scrollbar-thumb:hover { background: var(--text-muted); background-clip: content-box; }
::-webkit-scrollbar-corner { background: transparent; }
::selection { background: var(--selection); color: var(--text); }

/* ─────────────────────────────────────────────────────────────────
   ⑥ 台账类长列表：行间细线，替代裸文字堆叠
   ───────────────────────────────────────────────────────────────── */
#fg-list > div { line-height: 1.9; }
#fg-list > div + div { border-top: 1px solid var(--border-light); }
#fg-list a { padding: 7px 6px; margin-left: 6px; white-space: nowrap; }

/* ─────────────────────────────────────────────────────────────────
   ⑦ 响应式
   ⚠️ 移动端顶栏必须去掉 backdrop-filter：按 CSS 规范它会为**后代** fixed 元素
      建立 containing block，而 nav#nav 是 header 的子元素 ⇒ 底栏的 bottom:0
      变成"相对 header 底边"，标签栏会被顶到页面顶部并与内容重叠
      （实测 navY=-6px，应 844px；控制台零报错、桌面端正常，极易漏检）。
   ───────────────────────────────────────────────────────────────── */
@media (max-width: 1500px) {
  main { padding: var(--sp-6) var(--sp-6) var(--sp-8); }
}
@media (max-width: 1200px) {
  .grid-4 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .grid-3 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .wt-excards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 900px) {
  .grid-2, .grid-3, .grid-4 { grid-template-columns: minmax(0, 1fr); }
  .wt-excards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  main { padding: var(--sp-5) var(--sp-4) var(--sp-7); }
  header { flex-wrap: wrap; height: auto; padding: var(--sp-2) var(--sp-3); gap: var(--sp-2); }
  nav { overflow-x: auto; width: 100%; -webkit-overflow-scrolling: touch; scrollbar-width: none; }
  nav::-webkit-scrollbar { display: none; }
  .chart-container { height: 240px; }
  .signal-banner .signal-rating { font-size: var(--fs-xl); }
}

@media (max-width: 768px) {
  html { font-size: 16px; }
  body { overscroll-behavior-y: none; }
  button, .btn, nav button, .expander-header { -webkit-tap-highlight-color: transparent; touch-action: manipulation; }

  /* 顶栏：去毛玻璃（见上方说明），改实色底 */
  header {
    flex-wrap: nowrap;
    height: auto;
    min-height: 52px;
    padding: calc(6px + env(safe-area-inset-top)) var(--sp-3) 6px;
    gap: var(--sp-2);
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
    background: var(--bg);
    border-bottom: 1px solid var(--border-light);
  }
  .header-left { gap: var(--sp-2); min-width: 0; flex: 1 1 auto; }
  header h1 { font-size: var(--fs-base); white-space: nowrap; }
  .header-badge { display: none; }
  .theme-toggle { padding: 0 var(--sp-2); flex-shrink: 0; }
  .theme-toggle #theme-label { display: none; }

  /* 导航下沉为底部标签栏 */
  nav#nav {
    position: fixed;
    left: 0; right: 0; bottom: 0;
    top: auto;
    z-index: 120;
    display: flex;
    gap: 2px;
    width: auto;
    align-items: stretch;
    overflow: visible;
    background: var(--bg-glass);
    backdrop-filter: saturate(180%) blur(20px);
    -webkit-backdrop-filter: saturate(180%) blur(20px);
    border-top: 1px solid var(--border-light);
    box-shadow: none;
    padding: 6px 6px calc(6px + env(safe-area-inset-bottom));
  }
  nav button {
    flex: 1 1 0;
    min-width: 0;
    min-height: 46px;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: var(--sp-1) 2px;
    font-size: var(--fs-xs);
    font-weight: 500;
    line-height: 1.2;
    white-space: normal;
    text-align: center;
    border-radius: 10px;
  }
  nav button.active {
    color: var(--brand);
    background: var(--brand-dim);
    border: none;
    text-shadow: none;
  }

  main { padding: var(--sp-4) var(--sp-3) calc(84px + env(safe-area-inset-bottom)); }
  .card { padding: var(--sp-5) var(--sp-4); border-radius: var(--radius); }
  .grid > .card, .grid.grid-2 > .card { padding: var(--sp-5) var(--sp-4); }
  .stat-row { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--sp-4); }
  .stat-card { padding: var(--sp-4); }
  .stat-card .stat-val { font-size: var(--fs-lg); }
  .signal-banner { padding: var(--sp-6) var(--sp-5); }
  input, select, textarea { font-size: 16px; min-height: 44px; }  /* 16px 防 iOS 聚焦缩放 */
  .btn { min-height: 44px; }
  .expander-header { padding: var(--sp-4) 0; font-size: var(--fs-base); }
  .expander-body { padding: 0 0 var(--sp-5); }
  .pipeline-stage { font-size: var(--fs-xs); padding: 5px var(--sp-2); }
  .chart-container { height: 250px; }
  .progress-log { max-height: 340px; font-size: var(--fs-xs); }
  pre, code { word-break: break-word; white-space: pre-wrap; }
  .table-scroll > table { font-size: var(--fs-sm); }
  .table-scroll > table th, .table-scroll > table td { padding: var(--sp-2); }
  .toast-container {
    top: auto; left: var(--sp-3); right: var(--sp-3);
    bottom: calc(84px + env(safe-area-inset-bottom));
    align-items: stretch;
  }
  .toast { max-width: none; width: 100%; }
}

@media (max-width: 420px) {
  header h1 { font-size: var(--fs-sm); }
  nav button { font-size: 0.6875rem; min-height: 44px; padding: 3px 0; }
  main { padding: var(--sp-3) 10px calc(80px + env(safe-area-inset-bottom)); }
  .card { padding: var(--sp-4) var(--sp-3); }
  .stat-row { gap: var(--sp-3); }
  .stat-card { padding: var(--sp-3); }
  .chart-container { height: 220px; }
}

@media (max-width: 900px) and (max-height: 480px) {
  .chart-container { height: 200px; }
}
'''

anchor = "</style>"
i = src.find(anchor)
if i < 0:
    raise SystemExit("FAIL: 未找到 </style>")
src = src[:i] + LAYER + "\n" + src[i:]

F.write_text(src, encoding="utf-8")
print("OK 设计层 v2 已追加")
print("行数:", src.count("\n")+1, " 字节:", len(src.encode('utf-8')))
for p in ["APPLE 设计层  (v2", "* + .card", "--bg-tile-nested", ":has(> svg:only-child)",
          "backdrop-filter: none", "max-width: none"]:
    print(f"  校验 {p!r}: {'OK' if p in src else 'MISSING'}")
print("裸 { 块（必须 0）:", len(re.findall(r'(?m)^{', src)))
