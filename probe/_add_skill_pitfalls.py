"""向 agentsense-add-tab 技能追加坑 13/14。文件是 LF。"""
import sys

P = r"C:\Users\lenovo\.workbuddy\skills\agentsense-add-tab\SKILL.md"
raw = open(P, "rb").read()
if raw.count(b"\r\n"):
    print("ABORT: 该技能文件本应是 LF, 实为", raw.count(b"\r\n"))
    sys.exit(2)

BLOCK = """
13. ⚠️⚠️ **"默认展开/默认选中"这类初始形态，必须由显式配置决定，不能用"此刻的渲染几何量"**
    （2026-09-30 用户报「覆盖矩阵和四源状态仍然折叠」）。
    - **原逻辑**：`_renderSectionIndex` 里 `if (hs.some(h => h >= 200)) { openIdx = i; break; }`
      —— 「只展开第一个高度 ≥200px 的块」。看着合理，实则**高度是渲染期不可信的临时量**：
      索引在 `switchTab` 里 `setTimeout(..., 0)` 构建（即 `loadGtja()` 之后立刻），此时各卡
      还是「查询后显示。」**空壳**（实测覆盖矩阵 110px、四源状态 45px），真高（429px / 895px）
      要等接口回填才有 ⇒ **有内容的块因为"建索引那一刻它还是空的"被默认收起**。
    - **修法**：新增 `App._SEC_OPEN_BY_TAB`（tab → 默认展开下标数组，如 `'gtja': [0, 1]`）
      + `App._secDefaultOpen(tabId)`；未配置的 tab 返回 `null` ⇒ 回退旧几何逻辑。
    - ⚠️ **两处必须同改**：`_renderSectionIndex`（首次生成索引）**和** `_resetSecFilter`
      （点「共 N 项」清空筛选后的还原）。只改前者 ⇒ 用户点一次清空筛选，刚看到的块**又被收回去**。
    - ⚠️ 配置写**下标**时，务必配一条断言校验"下标 ↔ 卡片顺序"（本库
      `test_gtja_default_open_indices_match_card_order` 按三张卡标题的源码位置断言相对次序）。
      将来有人把某张卡前移，配置会**静默指向别的模块**，页面照常渲染、只是展开的不是想看的。
    - 通则：**任何"默认态"都不该依赖布局测量结果**。要测就等数据回填后再测，或干脆显式配置。
14. ⚠️⚠️ **变量改名必须 `grep -n <旧名>` 归零**，否则留一处就是运行时 ReferenceError。
    （2026-09-30 实测：把 `openIdx` 换成 `openRows.has(i)` 时漏改 `_resetSecFilter` 末尾一行
    `if (r.btn) ...toggle('is-open', i === openIdx);`）
    - **症状很隐蔽**：块的开合**已经改完**（那行之前在 forEach 里），只有"标签 is-open 同步"
      被跳过 ⇒ 表现为「清空筛选后某个块仍撑着不收起」+ 控制台 `openIdx is not defined`。
    - ⚠️ **静态文本断言当时全绿** —— 因为断言里没有运行时求值。**只有真实交互 E2E 才抓到**
      （`probe/_e2e_gtja_default_open.mjs`：勾选一块 → 点 pill 清空 → 读每块实际像素高度）。
    - 教训：**"改完文案/变量名"类的重构，必须跑一条"点一下看看"的 E2E**；纯 grep 不够。
    - 配套静态护栏可这样写（本库 `test_default_open_guard_kills_reset_filter_regression`）：
      把 `_resetSecFilter` 的 `openRows.has(i)` 变异回 `i === openIdx`，断言护栏变红 ——
      即**把这次真实回归固化成变异用例**。
15. ⚠️ **同一批改动里 CRLF / LF 是混装的，锚点行尾别写错**
    （本库：`web_template.html` 是 **CRLF**；`tests/*.py`、`tests/js/*.mjs`、`probe/*.mjs` 是 **LF**）。
    2026-09-30 实测：一个脚本同时改 `web_template.html`(\r\n 锚点) 和
    `tests/test_page_tools_variety_state.py`(\n 锚点)，后者用 `\r\n` 命中 **0 次**。
    - **改文件前先量一次行尾**：`raw.count(b"\\r\\n")` 为 0 即 LF。
    - 脚本务必**先校验命中次数==1 再写**（本库所有 `probe/_fix_*.py` 都这么写）——
      这样"锚点写错"是**安全失败**（文件没被改），而不是改错地方。
"""

tail = raw.rstrip(b"\n") + b"\n" + BLOCK.encode("utf-8")
open(P, "wb").write(tail)
print("OK 追加", len(BLOCK.encode("utf-8")), "字节; bareLF =",
      tail.count(b"\n") - tail.count(b"\r\n"))
