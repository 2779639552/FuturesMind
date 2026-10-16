"""国君页「默认展开」修复: 按 tab 配置默认展开集合, 替代"高度 >=200px 才展开"的几何裁决。

4 处改动:
  A) 新增 App._SEC_OPEN_BY_TAB + App._secDefaultOpen(tabId)
  B) _renderSectionIndex: 默认展开裁决改走配置 (openRows)
  C) _resetSecFilter:     清空筛选后的还原同样走配置 (否则点「共 N 项」会把刚展开的块收回去)
  D) pill.title 文案(保留"展开首个有内容的模块"会与 B/C 实际行为矛盾)

⚠️ web_template.html 是 **CRLF**: 必须二进制读写 + 校验 bareLF == 0。
⚠️ 每个锚点必须**恰好命中 1 次**(锚点唯一性自查, 防改错目标)。
"""
import sys

P = "web_template.html"
raw = open(P, "rb").read()
orig_len = len(raw)
before_barelf = raw.count(b"\n") - raw.count(b"\r\n")

# ── A) 新增配置表 + 派发函数 ────────────────────────────────────────
A_old = "  _SEC_IDX_VER: '17',\r\n"
A_new = (
    "  _SEC_IDX_VER: '17',\r\n"
    "\r\n"
    "  // 【默认展开集合】2026-09-30 新增。\r\n"
    "  //   为什么需要它：原逻辑是“只展开第一个高度 ≥ 200px 的块”，\r\n"
    "  //   而“高度”是一个**渲染期不可信**的临时量 —— 索引在 loadGtja() 之后建(0ms)时，\r\n"
    "  //   各卡还是“查询后显示。”空壳(实测覆盖矩阵 110px、四源状态 45px)，\r\n"
    "  //   真高(四源 435px / 指标总览 895px)要等接口回填才出来，于是\r\n"
    "  //   “四源取数状态**有内容却被默认收起**” —— 用户报的即此现象。\r\n"
    "  //   因此按“用户想一进页就看到哪些模块”显式配置，不再让几何量替用户做决定。\r\n"
    "  _SEC_OPEN_BY_TAB: {\r\n"
    "    // 国君数据：覆盖矩阵 + 四源取数状态(下标 0/1)默认展开。\r\n"
    "    //   两者是**挑品种决策的前提与输入**，不看它们就不知道该选哪个品种。\r\n"
    "    //   ③ 指标总览照旧默认收起(体量大，用户很少一进页就读整张总览)。\r\n"
    "    'gtja': [0, 1],\r\n"
    "  },\r\n"
    "\r\n"
    "  // 【功能】本 tab 默认要展开的模块**下标集合**（空集合 = 走旧逻辑）。\r\n"
    "  //   为什么不用单个索引值：一个 tab 常有多块该展开(如国君页 2 块)，\r\n"
    "  //   数组表达最直接；且只要配置存在(即使是空数组)就不再走几何裁决 ——\r\n"
    "  //   `in` 能区分“包含空数组”与“未配置”两种语义。\r\n"
    "  _secDefaultOpen(tabId) {\r\n"
    "    // 返回 null 表示“本 tab 未配置” → 调用方走旧逻辑。\r\n"
    "    const o = this._SEC_OPEN_BY_TAB[tabId];\r\n"
    "    if (!o) return null;\r\n"
    "    return new Set(o);\r\n"
    "  },\r\n"
)
A_old_b, A_new_b = A_old.encode("utf-8"), A_new.encode("utf-8")

# ── B) _renderSectionIndex ──────────────────────────────────────────
B_old = (
    "    const OPEN_MIN_H = 200;\r\n"
    "    let openIdx = 0;\r\n"
    "    for (let i = 0; i < rows.length; i++) {\r\n"
    "      const hs = (rows[i].blocks || []).map(b => b.scrollHeight);\r\n"
    "      if (hs.some(h => h >= OPEN_MIN_H)) { openIdx = i; break; }\r\n"
    "    }\r\n"
    "    rows.forEach((r, i) => {\r\n"
    "      (r.blocks || []).forEach(b => b.classList.add('sec-init'));\r\n"
    "      if (r.btn) r.btn.classList.toggle('is-open', i === openIdx);\r\n"
    "      (r.blocks || []).forEach(b => {\r\n"
    "        if (i === openIdx) { b.classList.remove('is-collapsed'); b.classList.add('sec-open'); b.style.maxHeight = 'none'; }\r\n"
    "        else { b.classList.add('is-collapsed'); b.classList.remove('sec-open'); b.style.maxHeight = '0px'; }\r\n"
    "      });\r\n"
    "    });\r\n"
)
B_new = (
    "    const OPEN_MIN_H = 200;\r\n"
    "    // ★【显式配置优先】看不见的模块对用户等于不存在，所以不能让\r\n"
    "    //   “该模块此刻有多高”决定它要不要被看到(数据还没回填时它是空的)。\r\n"
    "    const _cfgOpen = this._secDefaultOpen(tabId);\r\n"
    "    let openRows;\r\n"
    "    if (_cfgOpen) {\r\n"
    "      openRows = _cfgOpen;\r\n"
    "    } else {\r\n"
    "      let openIdx = 0;\r\n"
    "      for (let i = 0; i < rows.length; i++) {\r\n"
    "        const hs = (rows[i].blocks || []).map(b => b.scrollHeight);\r\n"
    "        if (hs.some(h => h >= OPEN_MIN_H)) { openIdx = i; break; }\r\n"
    "      }\r\n"
    "      openRows = new Set([openIdx]);\r\n"
    "    }\r\n"
    "    rows.forEach((r, i) => {\r\n"
    "      (r.blocks || []).forEach(b => b.classList.add('sec-init'));\r\n"
    "      if (r.btn) r.btn.classList.toggle('is-open', openRows.has(i));\r\n"
    "      (r.blocks || []).forEach(b => {\r\n"
    "        if (openRows.has(i)) { b.classList.remove('is-collapsed'); b.classList.add('sec-open'); b.style.maxHeight = 'none'; }\r\n"
    "        else { b.classList.add('is-collapsed'); b.classList.remove('sec-open'); b.style.maxHeight = '0px'; }\r\n"
    "      });\r\n"
    "    });\r\n"
)
B_old_b, B_new_b = B_old.encode("utf-8"), B_new.encode("utf-8")

# ── C) _resetSecFilter ─────────────────────────────────────────────
C_old = (
    "    // 直接调 _setBlockOpen 会连动画一起跑, 这里一次性重置更稳: 首块展开、其余收起\r\n"
    "    let openIdx = 0;\r\n"
    "    for (let i = 0; i < rows.length; i++) {\r\n"
    "      const hs = (rows[i].blocks || []).map(b => b.scrollHeight);\r\n"
    "      if (hs.some(h => h >= 200)) { openIdx = i; break; }\r\n"
    "    }\r\n"
    "    rows.forEach((r, i) => {\r\n"
    "      (r.blocks || []).forEach(b => {\r\n"
    "        b.classList.remove('sec-hidden');\r\n"
    "        if (i === openIdx) {\r\n"
)
C_new = (
    "    // 直接调 _setBlockOpen 会连动画一起跑, 这里一次性重置更稳: 按默认集合重排\r\n"
    "    // ★ “恢复到哪些块展开”必须与首次生成索引时**同一套配置**，否则点一次“共 N 项”\r\n"
    "    //   就会把刚刚看到的四源状态又收回去(用户会以为“清空筛选”把页面弄坏了)。\r\n"
    "    const _cfgOpen = this._secDefaultOpen(tabId);\r\n"
    "    let openRows;\r\n"
    "    if (_cfgOpen) {\r\n"
    "      openRows = _cfgOpen;\r\n"
    "    } else {\r\n"
    "      let openIdx = 0;\r\n"
    "      for (let i = 0; i < rows.length; i++) {\r\n"
    "        const hs = (rows[i].blocks || []).map(b => b.scrollHeight);\r\n"
    "        if (hs.some(h => h >= 200)) { openIdx = i; break; }\r\n"
    "      }\r\n"
    "      openRows = new Set([openIdx]);\r\n"
    "    }\r\n"
    "    rows.forEach((r, i) => {\r\n"
    "      (r.blocks || []).forEach(b => {\r\n"
    "        b.classList.remove('sec-hidden');\r\n"
    "        if (openRows.has(i)) {\r\n"
)
C_old_b, C_new_b = C_old.encode("utf-8"), C_new.encode("utf-8")

# ── D) pill.title 文案 ─────────────────────────────────────────────
D_old = "    pill.title = '清空筛选 · 恢复全部模块并展开首个有内容的模块';\r\n"
D_new = (
    "    // v18 起默认展开集合可能多于一块(如国君页 2 块)，\r\n"
    "    //   故 title 不再写死“展开首个有内容的模块”(与实际不符)。\r\n"
    "    pill.title = '清空筛选 · 恢复全部模块并恢复默认展开态';\r\n"
)
D_old_b, D_new_b = D_old.encode("utf-8"), D_new.encode("utf-8")

SUBSTITUTIONS = [
    ("A: _SEC_OPEN_BY_TAB + _secDefaultOpen", A_old_b, A_new_b),
    ("B: _renderSectionIndex openRows", B_old_b, B_new_b),
    ("C: _resetSecFilter openRows", C_old_b, C_new_b),
    ("D: pill.title 文案", D_old_b, D_new_b),
]

errors = []
for name, a, _ in SUBSTITUTIONS:
    n = raw.count(a)
    if n != 1:
        errors.append(f"{name}: 锚点命中 {n} 次 (期望 1)")

if errors:
    print("ABORT: 未写入任何改动")
    for e in errors:
        print("  x", e)
    sys.exit(2)

for name, a, b in SUBSTITUTIONS:
    raw = raw.replace(a, b, 1)

after_barelf = raw.count(b"\n") - raw.count(b"\r\n")
print(f"CRLF 校验: 改前 bareLF={before_barelf} -> 改后 bareLF={after_barelf} (均须为 0)")
if after_barelf != 0:
    print("ABORT: 行尾符被破坏")
    sys.exit(2)

open(P, "wb").write(raw)
print(f"OK 写入 {len(SUBSTITUTIONS)} 处; {orig_len} -> {len(raw)} 字节")
for name, _, _ in SUBSTITUTIONS:
    print("  +", name)
