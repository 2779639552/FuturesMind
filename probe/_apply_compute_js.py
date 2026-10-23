# -*- coding: utf-8 -*-
"""数据计算面板迁到基本面后，JS 侧的品种源与加载时机跟着改。

· 面板原来读 `#q-variety`（技术面下拉）；搬到基本面 A9 后必须读 `#f-variety`，
  否则在基本面页里永远算的是技术面页残留的那个品种。
· 加载时机：原来在技术面 `loadQuant` 里触发；现在由 `loadFund()` 触发（进单品种明细即加载）。
  技术面那边删掉调用 —— 否则每次切技术面还会白拉一次价格/基差/仓单三源。
"""
from __future__ import annotations

import io
import os

P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web_template.html")
CRLF = "\r\n"

s = io.open(P, "r", encoding="utf-8", newline="").read()
before = s


def rep(old: str, new: str, label: str):
    global s
    old = old.replace("\n", CRLF)
    new = new.replace("\n", CRLF)
    n = s.count(old)
    assert n == 1, "%s 命中 %d 次:\n%s" % (label, n, old[:120])
    s = s.replace(old, new, 1)


# ① 品种源：_quantSym → _computeSym
rep("async loadQuantCompute(force, reset) {\n const sym = this._quantSym();",
    "async loadQuantCompute(force, reset) {\n"
    " // 【2026-10-21】面板已迁到基本面 A9 ⇒ 品种要读 #f-variety（原来读技术面的 #q-variety，\n"
    " //   会在基本面页里算出\"技术面页残留品种\"的相关系数）\n"
    " const sym = this._computeSym();",
    "loadQuantCompute sym")

# ② 新增 _computeSym（放在 _fundSym 后面）
rep(" _fundSym() {\n const el = document.getElementById('f-variety');\n return (el?.value || '').toUpperCase();\n },",
    " _fundSym() {\n const el = document.getElementById('f-variety');\n return (el?.value || '').toUpperCase();\n },\n"
    "\n"
    " // 【功能】A9 数据计算面板的品种源：优先基本面下拉，其次技术面下拉（兜底）\n"
    " // 【为什么还要兜底】面板的容器 id 与 loadQuantCompute 都还是 q- 前缀，\n"
    " //   直接调它（旧书签/测试）时 #f-variety 可能不存在，此时沿用旧行为取 #q-variety。\n"
    " _computeSym() {\n"
    " const f = document.getElementById('f-variety');\n"
    " const q = document.getElementById('q-variety');\n"
    " return String((f && f.value) || (q && q.value) || '').toUpperCase();\n"
    " },",
    "_computeSym")

# ③ 技术面：删掉调用（面板已不在这一页）
# 这行前面那句注释是唯一的（另一处同名调用在 _cmpToggleAll 里）
rep(" // 不 await ⇒ 不阻塞其它块(它内部要拉价格/基差/仓单三源, 首拉慢)。\n this.loadQuantCompute();\n },",
    " // 【2026-10-21】数据计算面板已迁至基本面 A9，本页不再加载\n"
    " //   （原来每次进量化页都会白拉价格/基差/仓单三源）\n },", "quant call")

# ④ 基本面：进入单品种明细时加载
rep(" this.loadQuantSignals();\n this.loadFundVotes();\n },",
    " this.loadQuantSignals();\n this.loadFundVotes();\n"
    " // 【2026-10-21】A9 数据计算面板(原技术面 ⑫): 品种取 #f-variety，\n"
    " //   不 await ⇒ 不阻塞其它块（它要拉价格/基差/仓单三源，首拉慢）。\n"
    " this.loadQuantCompute();\n },",
    "fund call")

assert s != before
assert s.count("\n") == s.count(CRLF), "CRLF 被破坏"
io.open(P, "w", encoding="utf-8", newline="").write(s)
print("ok")
