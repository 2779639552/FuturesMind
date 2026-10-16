# -*- coding: utf-8 -*-
"""把本轮两条结果追加进今日项目记忆（幂等）。"""
P = r"C:/Users/lenovo/WorkBuddy/2026-09-07-16-38-57/.workbuddy/memory/2026-09-30.md"

ADD = """
## 六、国君数据页「覆盖矩阵」移出控制条（用户报障：不在小模块中 + 遮挡其他小模块）

**症状（两条同时出现，根因同一个）**
- 「不在小模块中」：`_secScan` 对 `.page-tools` 是**整块跳过**（控制条永不参与折叠）
  ⇒ 矩阵卡既进不了小模块索引、也收不起来。
- 「遮挡其他小模块」：`.page-tools{position:sticky;top:56px;z-index:45;background:var(--bg-card)}`
  只适合“矮控制条”，塞进 21 行大表 ⇒ 整块钉在视口顶部，**实色底盖住下方模块**。

**修法**：把覆盖矩阵卡从 `#tab-gtja-tools`（`.page-tools`）内**整块移出**，落成 tab 顶层
普通 `.card` ⇒ 走 `_secScan` 的“直接是卡片”分支（自动进索引 + 可折叠 + 不吸顶）。
`_SEC_IDX_VER: '15' -> '16'`（不 +1 则已建过索引的页面不重建、矩阵仍不在索引里）；
`#gt-matrix-badge` 随卡搬到「覆盖矩阵」h3 下（它渲染矩阵数据，留在查询控制里语义错位）。

**⚠️ 本轮最大的教训：改注释 ≠ 改结构**
第一版 `_fix_gtja_matrix.py` **只改了注释文字**、结构没动，静态断言（只查注释/文本）**全绿**，
是 **E2E 抓出来 5 个 FAIL**（`inPageTools=true` / `ptCards=2` / `h=880px`）。
⇒ **结构类改动必须用“几何/深度”判据验收，不能只对源码文本做断言。**

**验收（全部实测）**
- E2E `probe/_e2e_page_tools.mjs` [8] 段：`inPageTools=false`、`inBlock=true`、
  索引含「覆盖矩阵」、`ptCards=1`、`h=163px`、`card position=static`、
  滚动 `before=367px -> after=-190px`（滚走没吸顶）、控制条仍 `top=56px`（正常吸顶）。
  总 **51 PASS / 0 FAIL**。
- 静态护栏 `tests/test_page_tools_variety_state.py` +7 例（62 passed）；配对的闭合 div
  必须**按 div 深度配平**（`_gtja_tools_span`），直接找下一个闭合 div 会取到错误区间。

## 七、顺手修掉一个崩溃级既有缺陷：`reports`(数字) 被赋给 `report_rows`(数组)

E2E 顺带报 `TypeError: reps.reduce is not a function`。追下去：
- `/api/research/variety-summary/generate` 返回的 `reports` 是**标量**（`web_app.py` 里 `"reports": len(items)`）；
- 列表端点 `/api/research/variety-summary/<date>` 返回的 `report_rows` 才是**逐份行数组**；
- 前端 `web_template.html` 曾写 `v.report_rows = d.reports || 0;` ⇒ 数字进数组字段 ⇒
  `_renderVarietySummaries` 的 `reps.reduce` 抛错 ⇒ **整个品种总结网格渲染中断**（整块空白，非局部错）。
- **既有缺陷**：`backup/palm_*/web_template.html` 同款两行，非本轮引入。

修法：删掉错误赋值，只保留 `generated_at` 更新（列表端点已给正确数组）+ “勿回退”注释。
护栏：`tests/test_research_variety_summary.py` +4 例（标量契约 / 禁互赋 / 消费端 `|| []` 兜底 /
变异自检），并用**真实文件级变异**验证——把错误赋值注入 `web_template.html`，护栏变红、
还原后字节一致（`bareLF=0`）。修后 E2E **JS 报错「(无)」**。

**通则**：把接口返回值赋给前端状态字段前，先确认两边**容器类型**（scalar/array/object）一致。

**回归**：全量 **2337 tests / 0 failed / 0 errors / 1 skipped**
（2326 基线 +7 矩阵护栏 +4 契约护栏 = 2337，**丢失 0**）。

**沉淀**：`agentsense-add-tab` 技能补坑 8（`.page-tools` 只许一张矮卡 + 验收判据含“滚动不吸顶”）、
坑 9（字段类型契约 scalar vs array）。
"""

data = open(P, "rb").read().decode("utf-8")
if "## 六、国君数据页" in data:
    print("ALREADY PRESENT, skipped")
else:
    out = (data.rstrip("\n") + "\n" + ADD).encode("utf-8")
    open(P, "wb").write(out)
    print("APPENDED %d -> %d bytes" % (len(data.encode("utf-8")), len(out)))
