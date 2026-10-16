# -*- coding: utf-8 -*-
"""密度重构 v3 · 第一步：修复表格被推到右侧的布局 bug。

根因（实测确认）：
  我此前写下这条"卡片标题行三段式"规则 ——
      .card > div:has(> h3) { display: flex; flex-wrap: wrap; align-items: center; }
      .card > div:has(> h3) > div:nth-child(2) { margin-left: auto; }
  意图是让「标题 + 按钮组 + 描述」横向排布。但选择器用 `div` 限定，
  而**嵌套的子卡片本身就是一个含 h3 的 div**，于是被当成"标题行容器"命中：
    · 子卡片被设成 display:flex  → align-items:center 让标题垂直居中
    · 子卡片内第 2 个 div（表格容器）拿到 margin-left:auto → 被推到最右
  实测（warrant 页「仓库级明细」）：表格容器宽仅 307px、x=1053，
  而卡片宽 1328px ⇒ 左侧约 1000px 全空，正是用户截图的"90% 空白"。

修法：排除卡片/栅格自身，只命中真正的"标题行 div"。
  同时改用更稳的判据：真正的标题行 div 一定**不含 .card/.grid**，
  且通常是内联 flex（本规则的目的是兜住没写 flex 的那些）。
"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
n0 = len(src)

OLD = """.card > div:has(> h3) { display: flex; flex-wrap: wrap; align-items: center; }
.card > div:has(> h3) > h3 { margin-bottom: 0; }
.card > div:has(> h3) > div:last-child:not(:nth-child(2)) {
  flex-basis: 100%;
  margin-top: var(--sp-3);
}
/* 按钮组贴右 */
.card > div:has(> h3) > div:nth-child(2) { margin-left: auto; }"""

NEW = """/* ★【2026-10-14 修正·重要】此处原为 `.card > div:has(> h3)`，用 div 限定
   "含标题的标题行容器"。但**嵌套的子卡片本身就是含 h3 的 div**，会被一起命中：
     · 子卡片被设成 display:flex ⇒ 其内第 2 个 div 拿到 margin-left:auto 被推到最右
   实测后果（warrant「仓库级明细」）：表格容器宽仅 307px / 卡片宽 1328px，
   左侧整片空白 —— 即用户截图里的"90% 是空白"。
   修正：用 :not(.card):not(.grid) 把卡片与栅格自身排除掉。 */
.card > div:has(> h3):not(.card):not(.grid):not(.sec-block) {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
}
.card > div:has(> h3):not(.card):not(.grid):not(.sec-block) > h3 { margin-bottom: 0; }
.card > div:has(> h3):not(.card):not(.grid):not(.sec-block) > div:last-child:not(:nth-child(2)) {
  flex-basis: 100%;
  margin-top: var(--sp-2);
}
.card > div:has(> h3):not(.card):not(.grid):not(.sec-block) > div:nth-child(2) { margin-left: auto; }"""

if OLD in src:
    src = src.replace(OLD, NEW, 1)
    print("① 已修正标题行规则（排除嵌套卡片）")
else:
    print("① WARN 未精确命中，逐行修正")
    # 逐条替换
    reps = [
        (".card > div:has(> h3) {", ".card > div:has(> h3):not(.card):not(.grid):not(.sec-block) {"),
        (".card > div:has(> h3) > h3 {", ".card > div:has(> h3):not(.card):not(.grid):not(.sec-block) > h3 {"),
        (".card > div:has(> h3) > div:nth-child(2) {", ".card > div:has(> h3):not(.card):not(.grid):not(.sec-block) > div:nth-child(2) {"),
        (".card > div:has(> h3) > div:last-child:not(:nth-child(2)) {",
         ".card > div:has(> h3):not(.card):not(.grid):not(.sec-block) > div:last-child:not(:nth-child(2)) {"),
    ]
    for o, n in reps:
        c = src.count(o)
        if c:
            src = src.replace(o, n)
            print(f"   {o[:52]!r} x{c}")

# ── 兜底：给表格容器正名（无论规则如何都保证表格铺满）──
GUARD = r'''
/* ── 表格容器：一律铺满父容器（兜底）──────────────────────────────
   为什么需要兜底：表格容器常带内联 style（如 max-height/overflow），
   一旦它被当成 flex item 就会按内容宽度收缩并可能被 margin-left:auto 推右
   （实测：宽 307px vs 卡片 1328px，页面大面积空白）。
   这里用 !important 钉死宽度，避免任何祖先布局变化再次触发。 */
#wt-table,
.wt-table-body,
[data-table-box] {
  width: 100% !important;
  margin-left: 0 !important;
  flex: 1 1 100% !important;
}
/* 表格本身铺满容器（浏览器默认 table 宽 auto = 内容宽度） */
table { width: 100%; }
'''
i = src.rfind("/* ── ≤900px 横屏/矮屏 ── */")
if i < 0:
    i = src.find("</style>")
src = src[:i] + GUARD + "\n" + src[i:]
print("② 已加表格容器铺满兜底")

F.write_text(src, encoding="utf-8")
print()
print(f"OK: {n0} -> {len(src.encode('utf-8'))} 字节")
print("裸 { 块（必须 0）:", len(re.findall(r'(?m)^{', src)))
print("残留未排除的 .card > div:has(> h3) {:", len(re.findall(r'\.card > div:has\(> h3\) \{', src)))
