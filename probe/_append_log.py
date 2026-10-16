"""追加今日工作日志（记忆：append-only）。"""
LOG = r"C:/Users/lenovo/WorkBuddy/2026-09-07-16-38-57/.workbuddy/memory/2026-09-30.md"
BLOCK = """

## 十、国君页「覆盖矩阵 + 四源状态」默认展开（v18/v19，用户报「仍然折叠」）

**现象**：用户答「覆盖矩阵和四源状态仍然折叠」——两块都要一进页就展开。

**根因（比推测更隐蔽）**：不是「只展开第一个 ≥200px 的块」这么简单。
索引在 `switchTab` 的 `setTimeout(..., 0)` 里构建（= `loadGtja()` 之后**立即**），
那一刻各卡还是「查询后显示。」**空壳**（实测覆盖矩阵 110px、四源状态 45px），
**真高（429px / 895px）要等接口回填**才出来 ⇒ **有内容的块因为"建索引那一刻它还是空的"被判默认收起**。
（先用探针取真值才看清：四源 435px 却收着 ⇒ 与"首个≥200px 才展开"的推测**不一致**，
这才暴露"几何量本身不可信"这一层。）

**修法**：新增 `App._SEC_OPEN_BY_TAB`（`'gtja': [0, 1]`）+ `App._secDefaultOpen(tabId)`
（未配置返回 `null` ⇒ 回退旧几何逻辑）；**两处同改**：`_renderSectionIndex`（首建索引）
**与** `_resetSecFilter`（点「共 N 项」清空筛选后的还原）——
只改前者 ⇒ 用户点一次清空筛选，刚看到的块**又被收回去**。

**⚠️ E2E 抓到的真缺陷（当时静态断言全绿）**：`_resetSecFilter` 末尾残留 `i === openIdx`
⇒ 运行时 `openIdx is not defined` ⇒ 块的开合**已改完**、只有"标签 is-open 同步"被跳过
⇒ 表现为「清空筛选后 ③ 指标总览仍撑着 895px」。**变量改名必须 `grep -n <旧名>` 归零**。

**验收（逐层）**
- 新 E2E `probe/_e2e_gtja_default_open.mjs`：**15/0**（含"清空筛选后默认态原样恢复"）。
- 新隔离探针 `probe/_probe_all_tabs_fold.mjs`：其他 7 个 tab（run/research/sentiment/quant/fund/warrant/variety）
  仍**只展开 1 块** ⇒ 改动**只影响 gtja**，无外溢。
- 重跑既有三层：gtja merge **14/0**、page_tools **61/0**、gtja harness **50/0**。
- 全量 pytest：**2354 / 0 failed / 0 errors / 1 skipped**（基线 2347 + 新增 7 条护栏，**丢失 0**）。

**护栏**：`tests/test_page_tools_variety_state.py` 新增 7 条 ——
「配置存在」「**下标 ↔ 卡片顺序一致性**」（防将来挪卡后配置静默指向别的模块）
「基线过」「锚点唯一」+ 3 条变异自检（删配置 / 把 `openRows.has(i)` 打回 `i === openIdx` / 让 `_secDefaultOpen` 恒 null）。

**⚠️⚠️ 本次撞上「并发写入」**：`web_template.html` 与 `tests/test_page_tools_variety_state.py`
**被另一会话同时改动**（量化页新增「数据计算面板」，新控件 `q-cmp-dims/q-cmp-method`、
新方法 `loadQuantCompute`，行数 13539 → 13982）。我写的 `_SEC_IDX_VER='18'` 被其改为 `'19'`
（它也需 bump，且晚于我）。**处置**：
① 把自家 E2E 的版本断言从 `== '18'` 改为 `>= 18`（保留"已 bump ⇒ 老索引会重建"的本意，
   不再抢占版本号归属）；② 测试断言对齐当前真值 `'19'`（该文件刻意用精确值，强度不变）。
⇒ **教训：多人/多会话共用同一工作目录时，"我改过所以现在是这样"的推断必须重新取证**；
写文件前的"锚点命中数==1 才写"脚本习惯在此立功（未命中 = 安全失败，没改错地方）。
"""
raw = open(LOG, "rb").read()
if b"## \xe5\x8d\x81\xe3\x80\x81\xe5\x9b\xbd\xe5\x90\x9b\xe9\xa1\xb5" in raw:
    print("已存在第十节，跳过")
else:
    open(LOG, "wb").write(raw.rstrip(b"\n") + b"\n" + BLOCK.encode("utf-8"))
    print("OK 追加日志; 文件", len(raw), "->", len(raw) + len(BLOCK.encode("utf-8")))
