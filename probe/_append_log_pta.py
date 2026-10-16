# -*- coding: utf-8 -*-
"""追加 2026-09-30 日志 §十一：PTA 召回失败根因与修复。"""
P = r"C:\Users\lenovo\WorkBuddy\2026-09-07-16-38-57\.workbuddy\memory\2026-09-30.md"

BLOCK = """

---

## 十一、PTA 召回失败：研报问答品种下拉被静默补成池首（2026-09-30 下午）

**用户报障**：研报问答问「PTA的情况」，只回：
「PTA近日情况：现有片段不足以判断 … 当前提供的研报片段全部围绕橡胶板块（RU/NR/BR）…」
—— 当日的国泰君安「PX PTA MEG 基本面数据」与华泰「聚酯日报」一条都没搜到。

### 根因（**前端回归**，不是 RAG/索引问题）

`web_template.html` → `App._populateVarietySelects(ids)` 填充下拉后无条件补默认选中：

```js
// 【2026-09-30】填充后补默认选中 …（放在无条件路径上 = bug）
if (!sel.value) sel.value = this._curVariety(id);
```

而 `_curVariety(id)` 的最终兜底是 `_defaultCode()` = **池内第一项 = `BR`**。

`#res-ask-variety` 的 HTML 首项本是 `<option value="">全部品种</option>` —— **空串是它的合法语义**。
被补成 `BR` 后，前端 `askResearch()` 取到 `variety="BR"` 发给 `/api/research/qa/ask`，
后端 `retrieve_hits(..., variety="BR")` 做**硬过滤** `_match_variety` ⇒ 召回池只剩橡胶板块。
（代码注释区 L11161-11163 其实**早就写明**这条语义例外：「#event-variety / #res-ask-variety /
#res-filter-variety 不接入本层 —— 空串是它们的正常语义，回退成池首会把『全部』悄悄变成『某一个品种』」，
**但实现违反了自己的注释**。注释与代码不一致时，以代码行为为准。）

### 四路取证（判定顺序即"先排除后端、再排除索引"）

1. **DB `qa_messages`（冒烟枪）**：09-30 那次问句 `filters={"variety":"BR",...}`；
   而 09-29 / 09-28 **同一问句**为 `filters.variety="TA"` ⇒ 前后唯一变化就是传参品种。
2. **浏览器探针** `probe/_probe_qa_variety.mjs`：修复前 `res-ask-variety.value === "BR"`（`opt0.value === ""`）；
   修复后 `value === ""`、`opt0 === ""`，且 `res-views-variety`（无空项）仍正确默认成 `BR`。
3. **检索层 A/B** `probe/_probe_pta_recall.py`（直连 `rag_worker.py` 子进程，避免主进程 import torch）：
   - `variety='BR'` → 6 篇全是 RU/NR/BR（橡树日报、橡胶周报…）
   - `variety='TA'` → 含 **rid=1463**（国泰君安 PX PTA MEG 基本面数据）与 **rid=1440**（华泰聚酯日报 20260930）
   - ⇒ `rid=1463/1440` 的向量与 `varieties` 元数据**都在、都健康**，问题在过滤参数。
4. **端到端 API A/B** `probe/_e2e_pta_qa.py`（真实 `/api/research/qa/ask`）：
   - A) `variety=""`（修复后）→ `ok=True`，10 篇引用含 **rid=1463 ★ / rid=1440 ★**，答案正常给出 PTA 行情与开工。
   - B) `variety="BR"`（修复前行为）→ 答案原文**几乎一字不差复现用户看到的报障文本**
     （「结论先行 … 本次提供的 10 条研报片段均未涉及 PTA … 橡胶品种」）。
   ⇒ 因果链闭合：**BR 过滤 ⇒ 橡胶-only ⇒ "现有片段不足以判断"**。

### 修复

```js
// ⚠️⚠️ 但「空串本身有语义」的下拉绝不能被补选: 首项 value=""(全部品种/全部/自动识别/选择品种)
//   空串是**合法取值**。若一并补成 _curVariety(id) 的池首兜底(_defaultCode), 就会把「全部」静默变成某一个品种。
//   判据: 该 select 内存在 option[value=""] ⇒ 空串有语义 ⇒ 跳过补选。
const _allowsEmpty = !!sel.querySelector('option[value=""]');
if (!sel.value && !_allowsEmpty) sel.value = this._curVariety(id);
```

**豁免名单**（首项空串，实测枚举确认）：
`res-ask-variety` / `res-filter-variety` / `event-variety`（全部品种）、
`res-upload-variety` / `ud-upload-variety`（自动识别）、`dwh-ds-variety`（选择品种）。
**仍走默认值**（无空项）：`res-views-variety` / `comp-variety` / `gt-variety`。

### 护栏与验收

- `tests/test_page_tools_variety_state.py` 新增 12 条：
  「豁免存在」「判据活代码恰好 1 处」「6 个下拉首项仍含 `value=""`」（参数化）
  「池首非全部」+ 锚点唯一性 + 3 条变异自检（删豁免 / 删补选 / 把 res-ask-variety 首项改成非空）。
- 本文件单独跑：**90 passed**（原 77 + 13）。
- 模板主 JS `node --check` 通过；`bareLF==0`（CRLF 保持）。
- ⚠️ **本次副产品**：补丁改写了 `_populateVarietySelects` 的那一行 ⇒ **3 处旧变异锚点全部失效**
  （`_watch_populate` / `test_mutation_anchors_are_safe` / `MUTATIONS` 里都引用了旧行文本）。
  **教训：改任何被护栏锚定的代码后，必须回头 grep 旧锚点归零**，否则要么测试报"未找到方法"、
  要么更糟——变异静默不生效（`assert mutated != orig` 会兜住，但前提是有人去看）。

### 方法论沉淀（已可复用）

1. **「空串有语义」的控件绝不能走"补默认值"的统一路径** —— 判据写法
   `!!sel.querySelector('option[value=""]')` 比维护豁免名单更稳（名单会漏、会随 HTML 漂移）。
2. **注释里的"语义例外"必须落成护栏**，否则迟早被统一化重构吃掉（本次就是）。
3. **诊断 RAG 缺召回：先验传参品种，再查索引**（`variety` 是硬过滤，传错 = 该板块以外全空）。
4. **复现要拿到"与用户看到的一字不差"**：B 组 argv 复现出原文，才算因果链闭合，否则可能只是"另一条路也坏了"。
"""

s = open(P, "r", encoding="utf-8").read()
if "## 十一、PTA 召回失败" in s:
    print("已存在，跳过")
else:
    open(P, "a", encoding="utf-8").write(BLOCK)
    print("appended", len(BLOCK), "chars")
