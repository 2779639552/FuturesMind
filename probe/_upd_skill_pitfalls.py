# -*- coding: utf-8 -*-
"""给 agentsense-add-tab 技能补两条今天实测的坑：

A) `.page-tools` 是吸顶容器, **只许放一张矮控制卡**；大表格/大卡片放进去会
   "吸顶遮挡下方模块" + "_secScan 整块跳过 ⇒ 进不了小模块索引、收不起来"。
B) 前端字段类型契约：生成端点的 `reports` 是**数字**, `report_rows` 是**数组**,
   互赋会让 `reps.reduce` 抛 TypeError 从而**整个渲染中断**。

⚠️ 该文件是 LF；必须二进制读写。
"""
P = r"C:\Users\lenovo\.workbuddy\skills\agentsense-add-tab\SKILL.md"

raw = open(P, "rb").read()
src = raw.decode("utf-8")
assert "\r\n" not in src, "该文件应为 LF, 实际含 CRLF"

# ── A) 在坑 7（Playwright/PDF）之后追加 ────────────────────────
tail_anchor = (
    "7. Playwright `page.pdf(outline=True)` **不生成书签**，必须 pymupdf `set_toc()+saveIncr()` 补；\n"
    "   needle 用完整标题片段（太短误匹配别页），封面含字距匹配不到直接写死 p1。\n"
)
assert src.count(tail_anchor) == 1, "尾部锚点不唯一: %d" % src.count(tail_anchor)

ADD = '''
8. ⚠️⚠️ **`.page-tools` 是吸顶容器，只许放「一张矮控制卡」**（2026-09-30 用户报障实测）。
   两个后果**同时**发生，且都静默：
   - **吸顶遮挡**：`.page-tools { position:sticky; top:56px; z-index:45; background:var(--bg-card) }`
     是给"矮控制条"设计的。把 21 行大表格塞进去 ⇒ 整块被钉在视口顶部，**实色底盖住下方
     模块内容**（用户原话："遮挡到了其他小模块的内容"）。
   - **进不了小模块**：`_secScan` 对 `.page-tools` 是**整块跳过**（控制条永不参与折叠）
     ⇒ 容器里的内容既不出现在小模块索引、也收不起来（用户原话："不在小模块中"）。
   ✅ 判据/修法：一个 tab 的 `.page-tools` 内 `:scope > .card` 应**恰好 1 个**；
   大卡片/大表格一律放 **tab 顶层**（会走 `_secScan` 的"直接是卡片"分支 ⇒ 自动进索引 + 可折叠 +
   不吸顶）。改完记得 `_SEC_IDX_VER` **+1**。
   ⚠️ 验证别只测"DOM 里有这张卡"：必须测 ①它不在 `.page-tools` 内 ②它 `.closest('.sec-block')`
   非空 ③**滚动后它的 `getBoundingClientRect().top` 明显上移（没被钉住）**且控制条仍 `top≈56px`。
   用 `_secScan` 的"整块跳过"特性做静态护栏时，配对的 `</div>` 必须**按 div 深度配平**，
   不能 `find('</div>')` —— 容器内外都有大量 `</div>`，会得到错误区间（实测：只改注释也能"通过"）。
9. ⚠️⚠️ **前端字段类型契约：`reports`(数字) vs `report_rows`(数组)**（2026-09-30 实测）。
   `/api/research/variety-summary/generate` 返回的 `reports` 是**标量**（`len(items)` 篇数），
   而列表端点 `/api/research/variety-summary/<date>` 返回的 `report_rows` 是**逐份行数组**。
   前端曾写 `v.report_rows = d.reports || 0;` ⇒ 数字塞进数组字段 ⇒
   `_renderVarietySummaries` 里 `reps.reduce(...)` 抛 `TypeError: reps.reduce is not a function`
   ⇒ **整个品种总结网格渲染中断**（不是局部错，是整块空白）。
   ✅ 消费端一律 `const reps = v.report_rows || [];`；改这两个字段名时，
   必须同步复核**赋值点与消费点两处**。
   ⚠️ 这类"同名字段两种类型"的坑，抽象成通则：**任何时候把接口返回值赋给前端状态字段前，
   先确认两边容器类型（scalar / array / object）一致**。
'''

src = src.replace(tail_anchor, tail_anchor + ADD, 1)
out = src.encode("utf-8")
assert b"\r\n" not in out, "落盘引入 CRLF，拒绝写入"
open(P, "wb").write(out)
print("OK 字节 %d -> %d" % (len(raw), len(out)))
