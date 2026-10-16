# -*- coding: utf-8 -*-
"""清理三卡合并的余党：

1) `_gtEnsureVisible` 的调用点（showGtjaIndicator 里两处）—— ④⑤ 已并入 ③，
   「查看」的结果就地渲染在同一张卡里，**没有任何需要展开的兄弟折叠块**了。
   留着这两行会让后来人以为"结果在别的卡"，属误导性子。
2) 同步 showGtjaIndicator / loadGtja 的注释（④⑤ 已不存在）。

⚠️ 方法本体 `_gtEnsureVisible` **保留** —— tests/js/gtja_module_harness.mjs 的
   ④ 段专门守护它（找方法体 → 断言折叠行为），删了 harness 直接 exit(2)。
   它是通用工具，将来跨卡交互仍可用。
⚠️ 模板是 CRLF，必须二进制读写。
"""
P = r"D:\AgentSense\AgentSense\web_template.html"

raw = open(P, "rb").read()
assert (raw.count(b"\n") - raw.count(b"\r\n")) == 0, "模板应为纯 CRLF"
txt = raw.decode("utf-8")

# ── 1) showGtjaIndicator 的注释 + 两处调用 ─────────────────────
OLD_FN = "\r\n".join([
    "  // 【功能】把某个指标画成走势图 + 明细表(点击指标行的「查看」)",
    "  // 【输入】i: 指标在 d.indicators 里的下标 【渲染】#gt-chart/#gt-table 【后端】无",
    "  showGtjaIndicator(i) {",
    "    const d = this._gtState.overview;",
    "    if (!d) return;",
    "    const ind = (d.indicators || [])[i];",
    "    if (!ind) return;",
    "    // 汇总行自身高亮(便于在长表里定位当前看的是哪一行)",
    "    const rows = document.querySelectorAll('#gt-indicators tbody tr');",
    "    rows.forEach((tr, k) => { tr.style.background = (k === i) ? 'var(--bg-hover,rgba(127,127,127,.08))' : ''; });",
    "    this._gtState.activeInd = ind;",
    "    this._renderGtjaChart(ind);",
    "    this._renderGtjaTable(ind);",
    "    // 结果分别落在卡④与卡⑤ —— 先把两块展开, 否则用户看不见任何变化(见 _gtEnsureVisible)",
    "    this._gtEnsureVisible(document.getElementById('gt-chart'));",
    "    this._gtEnsureVisible(document.getElementById('gt-table'));",
    "    setTimeout(() => this._resizeAllCharts(), 90);",
    "  },",
]) + "\r\n"

NEW_FN = "\r\n".join([
    "  // 【功能】把某个指标画成走势图 + 明细表(点击指标行的「查看」)",
    "  // 【输入】i: 指标在 d.indicators 里的下标 【渲染】#gt-chart/#gt-table 【后端】无",
    "  // 【2026-09-30 三卡合并】两块容器现与总览表同在 ③ 一张卡内, 点「查看」结果**就地**出现,",
    "  //   不再需要 _gtEnsureVisible 去展开兄弟折叠块(那样反而会滚走当前视线)。",
    "  showGtjaIndicator(i) {",
    "    const d = this._gtState.overview;",
    "    if (!d) return;",
    "    const ind = (d.indicators || [])[i];",
    "    if (!ind) return;",
    "    // 汇总行自身高亮(便于在长表里定位当前看的是哪一行)",
    "    const rows = document.querySelectorAll('#gt-indicators tbody tr');",
    "    rows.forEach((tr, k) => { tr.style.background = (k === i) ? 'var(--bg-hover,rgba(127,127,127,.08))' : ''; });",
    "    this._gtState.activeInd = ind;",
    "    this._renderGtjaChart(ind);",
    "    this._renderGtjaTable(ind);",
    "    setTimeout(() => this._resizeAllCharts(), 90);",
    "  },",
]) + "\r\n"

assert txt.count(OLD_FN) == 1, "showGtjaIndicator 未唯一命中: %d" % txt.count(OLD_FN)
txt = txt.replace(OLD_FN, NEW_FN, 1)

# ── 2) loadGtja 的渲染清单注释 ─────────────────────────────────
OLD_C1 = "  // 【输入】无 【渲染】#gt-variety/#gt-matrix/#gt-sources/#gt-indicators/#gt-chart\r\n"
NEW_C1 = "  // 【输入】无 【渲染】#gt-variety/#gt-matrix/#gt-sources/#gt-indicators(#gt-chart/#gt-table 同卡)\r\n"
assert txt.count(OLD_C1) == 1, "loadGtja 注释未唯一命中: %d" % txt.count(OLD_C1)
txt = txt.replace(OLD_C1, NEW_C1, 1)

# ── 3) loadGtjaOverview 的渲染清单注释 ─────────────────────────
OLD_C2 = "  // 【渲染】#gt-note / #gt-sources / #gt-indicators / #gt-chart\r\n"
NEW_C3 = "  // 【渲染】#gt-note / #gt-sources / #gt-indicators(走势图与明细同卡, 由「查看」驱动)\r\n"
assert txt.count(OLD_C2) == 1, "loadGtjaOverview 注释未唯一命中: %d" % txt.count(OLD_C2)
txt = txt.replace(OLD_C2, NEW_C3, 1)

# ── 4) _gtEnsureVisible 注释: 说明它现在还留着的原因 ──────────
OLD_C4 = "  // 【为什么必须有】⑨ 的小模块索引会把每页各卡片默认折叠(只展开首块)。卡⑤「查看」\r\n"
NEW_C4 = ("  // 【2026-09-30】国君页三卡合并后, 本方法**暂无调用点** —— 保留是因为上有\r\n"
          "  //   tests/js/gtja_module_harness.mjs 的 ④ 段专门守护它, 且它是通用工具(将来跨卡交互仍要用)。\r\n"
          "  // 【为什么必须有】⑨ 的小模块索引会把每页各卡片默认折叠(只展开首块)。卡⑤「查看」\r\n")
assert txt.count(OLD_C4) == 1, "_gtEnsureVisible 注释未唯一命中: %d" % txt.count(OLD_C4)
txt = txt.replace(OLD_C4, NEW_C4, 1)

out = txt.encode("utf-8")
assert (out.count(b"\n") - out.count(b"\r\n")) == 0, "落盘引入裸 LF，拒绝写入"
open(P, "wb").write(out)
print("OK 字节 %d -> %d" % (len(raw), len(out)))
