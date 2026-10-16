# -*- coding: utf-8 -*-
"""重做视觉层 · 第八步（终）：排版收口 + 单元素栅格铺满。

一、字号离群的真因（实测反查得出）
   · 16px（92 处）：body **未设 font-size** ⇒ 所有无显式尺寸的文字继承 html 的 16px
   · 24px / 30px：h1/h2 **无显式规则** ⇒ 走浏览器默认 2em / 1.5em
   → 修法：body 定到 --fs-base(15px)，h1-h6 全部显式钉到字阶，.badge 钉 12px。
     这样"继承链"上任何位置都只会落到字阶上，不会再冒出游离档位。

二、单元素栅格铺满
   · run 页「分析配置」在 .grid-2 里但只有 1 个子元素 ⇒ 只占左半屏（用户截图 1 的空白）
   → 修法：:only-child 横跨所有列。

三、卡片标题行的三段式布局
   · h3 + 按钮组 + 描述文字 三个 flex 子元素时，justify-content:space-between
     会把按钮组甩到中间、描述占右侧，视觉错乱。
   → 修法：描述独占一行（flex-basis:100%），首行变成 h3 左 + 按钮右。
"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
n0 = len(src)

# ── 1) body 显式字号（补上继承链缺口）──────────────────────
old_body = " font-family: var(--sans);\n /* 【皮肤重构】星云底"
new_body = (" font-family: var(--sans);\n"
            " /* 【2026-10-14 v2】显式钉住基准字号 —— 此前 body 无 font-size，\n"
            "    所有未显式设尺寸的文字（如大量 <b>）会继承 html 的 16px，\n"
            "    在 12/13/15/17/21/28 的字阶之外多出一个 16px 档（实测 92 处）。 */\n"
            " font-size: var(--fs-base);\n"
            " /* 【皮肤重构】星云底")
if old_body in src:
    src = src.replace(old_body, new_body, 1)
    print("① body font-size -> var(--fs-base): OK")
else:
    print("① WARN body 锚点未命中")

# ── 2) 设计层追加：排版收口 + 栅格补全 ─────────────────────
POLISH = r'''
/* ══════════════════════════════════════════════════════════════════
   排版收口（v2 · 终）
   ------------------------------------------------------------------
   为什么需要这一段：字号离群的根源不在我们写的规则，而在**浏览器默认样式**与
   **继承链缺口** —— h1/h2 没有显式规则时走 UA 的 2em/1.5em（→ 30px/24px），
   body 没设 font-size 时无尺寸文字继承 html（→ 16px）。
   把这两处堵上，字号就只可能落在字阶上。
   ══════════════════════════════════════════════════════════════════ */

/* ① 标题全部显式钉到字阶（堵住 UA 默认的 2em/1.5em/1.17em） */
h1 { font-size: var(--fs-xl); font-weight: 600; line-height: 1.15; }
h2 { font-size: var(--fs-lg); font-weight: 600; line-height: 1.2; }
h3 { font-size: var(--fs-lg); font-weight: 600; line-height: 1.2; }
h4 { font-size: var(--fs-md); font-weight: 600; line-height: 1.25; }
h5, h6 { font-size: var(--fs-base); font-weight: 600; line-height: 1.3; }

/* ② 强调只改字重、不改字号（避免 <b> 把字号顶出字阶） */
b, strong { font-weight: 600; font-size: inherit; }
/* 苹果的 UI 里强调用 500/600，不用 700；700 只留给数字指标 */
.report-content b, .report-content strong { font-weight: 600; }

/* ③ 徽标统一 12px（此前只有 .card h3 内的被钉住，其他位置的漏了） */
.badge,
.badge-info, .badge-bull, .badge-bear, .badge-neutral { font-size: var(--fs-xs); }

/* ④ SVG 内文字（黑洞 loading 的 textPath）也收进字阶 */
svg text, svg textPath { font-size: var(--fs-lg); }

/* ⑤ 数字统一用 tabular-nums，且**不放大** —— 保持与正文同档 */
.stat-val, .delta-new, .num, td, th { font-variant-numeric: tabular-nums; }

/* ── 单元素栅格铺满 ─────────────────────────────────────────────
   实测：run 页「分析配置」位于 .grid.grid-2 内但只有一个子元素，
   于是只占左半屏、右侧留出 910px 空白（用户截图 1）。
   ⚠️ 只处理 :only-child —— 有 2 个以上子元素的栅格是刻意的并排布局
      （如情绪页的「平台预测力 / 品种级方向准确率」），不能动。 */
.grid > :only-child { grid-column: 1 / -1; }

/* ── 卡片标题行三段式 ───────────────────────────────────────────
   结构：h3 + 操作按钮组 + 描述文字（三个 flex 子元素）。
   只有 h3 与按钮组时，space-between 会把按钮推到最右（正确）；
   一旦多出描述文字，它会被夹在中间。让描述独占一行即可恢复正确布局。 */
.card > div:has(> h3) { display: flex; flex-wrap: wrap; align-items: center; }
.card > div:has(> h3) > h3 { margin-bottom: 0; }
.card > div:has(> h3) > div:last-child:not(:nth-child(2)) {
  flex-basis: 100%;
  margin-top: var(--sp-3);
}
/* 按钮组贴右 */
.card > div:has(> h3) > div:nth-child(2) { margin-left: auto; }

/* ── 表格表头：不换行、字号统一 ─────────────────────────────── */
th { font-size: var(--fs-xs); font-weight: 400; }
/* markdown 表格里用 <br> 拆出来的多行表头，避免看起来像"两行不同字号" */
th br { display: none; }
th br + * { display: inline; }
'''
i = src.rfind("/* ── ≤900px 横屏/矮屏 ── */")
if i < 0:
    i = src.find("</style>")
src = src[:i] + POLISH + "\n" + src[i:]

F.write_text(src, encoding="utf-8")
print(f"② 排版收口层已追加")
print()
print(f"OK: {n0} -> {len(src.encode('utf-8'))} 字节")
print("裸 { 块（必须 0）:", len(re.findall(r'(?m)^{', src)))
