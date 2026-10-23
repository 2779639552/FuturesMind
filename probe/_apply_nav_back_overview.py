# -*- coding: utf-8 -*-
"""点击模块名回总览（2026-10-21 用户要求）。

背景：
  · 技术面量化 #tab-quant = #q-overview-view（全品种总览）+ #q-detail-view（单品种明细）
  · 基本面量化 #tab-fund  = #f-overview-view + #f-detail-view
  · 两页原先**只有**明细视图顶部那个小「← 返回」按钮能回去；点顶部模块名只会 switchTab，
    而 switchTab 的既定语义是"切走再切回不丢上下文"（明细态还会 loadQuant/loadFund），
    所以点了模块名反而又重拉一遍明细 —— 用户要的是"点模块名 = 回模块入口"。

改法（只在 nav 点击时生效，Ctrl+1~7 保持原语义）：
  1. _bindNav 传 { fromNav: true }；
  2. switchTab 收 opts，命中时调 _navBackToOverview(t) 把视图切回总览，
     随后原有的 lazy-load 分支自然走 loadQuantOverview / loadFundRanking；
  3. 逻辑抽成独立方法，便于用桩 DOM 单测（switchTab 太重，没法直接跑）。
"""
import io
from pathlib import Path

TPL = Path(r"D:\AgentSense\AgentSense\web_template.html")

PAIRS = [
    # ① nav 点击带上来源
    (
        " btn.addEventListener('click', () => this.switchTab(btn.dataset.tab));\r\n",
        " // 【2026-10-21】点「技术面量化」/「基本面量化」= 回该模块入口：明细视图下直接退回全品种总览。\r\n"
        " //   fromNav 只在这里传；Ctrl+1~7 不传 ⇒ 仍保留「切走再切回不丢上下文」。\r\n"
        " btn.addEventListener('click', () => this.switchTab(btn.dataset.tab, { fromNav: true }));\r\n",
    ),
    # ② switchTab 签名 + 回退调用
    (
        " switchTab(t) {\r\n"
        " this._currentTab = t;\r\n",
        " switchTab(t, opts) {\r\n"
        " this._currentTab = t;\r\n"
        " // 【2026-10-21】点击模块名 = 回模块入口（用户要求）：两页都是「总览 ⇄ 单品种明细」两视图，\r\n"
        " //   明细态点顶部模块名直接退回总览；总览态点则无副作用（照常刷新）。\r\n"
        " //   ⚠ 只认 fromNav：Ctrl+1~7 与程序化切页不传，否则从别的页跳回某品种会被强制弹回总览。\r\n"
        " if (opts && opts.fromNav) this._navBackToOverview(t);\r\n",
    ),
    # ③ 插独立方法（放在 qBackOverview 之前，技术面那一段）
    (
        " // 【功能】详情页「← 返回总览」\r\n"
        " qBackOverview() {\r\n",
        " // 【功能】2026-10-21 点击顶部模块名时，把「单品种明细」切回「全品种总览」。\r\n"
        " // 【返回】true = 确实从明细退回了；false = 本来就在总览 / 不是这两个模块（调用方无需关心）。\r\n"
        " // 【为什么只切 display 不调 loadXxx】switchTab 紧接着就会按视图状态走\r\n"
        " //   loadQuantOverview / loadFundRanking（后者顺带重排定期刷新定时器）⇒\r\n"
        " //   这里再拉一次会重复请求一轮后端（总览首算 ~105s，全池 ~40s）。\r\n"
        " _navBackToOverview(t) {\r\n"
        " const pair = { quant: ['q-detail-view', 'q-overview-view'],\r\n"
        " fund: ['f-detail-view', 'f-overview-view'] }[t];\r\n"
        " if (!pair) return false;\r\n"
        " const dv = document.getElementById(pair[0]);\r\n"
        " const ov = document.getElementById(pair[1]);\r\n"
        " // 已在总览（或两视图元素缺失）⇒ 什么都不做，保持原行为\r\n"
        " if (!dv || !ov || dv.style.display === 'none') return false;\r\n"
        " dv.style.display = 'none';\r\n"
        " ov.style.display = '';\r\n"
        " if (typeof window !== 'undefined' && window.scrollTo) window.scrollTo({ top: 0, behavior: 'smooth' });\r\n"
        " return true;\r\n"
        " },\r\n"
        "\r\n"
        " // 【功能】详情页「← 返回总览」\r\n"
        " qBackOverview() {\r\n",
    ),
    # ④ 基本面明细页的模块大字标题也可点（用户看到的"大模块名"就是它）
    (
        " <span>基本面量化</span>\r\n",
        " <span onclick=\"App.fBackOverview()\" title=\"点击返回全品种总览\" style=\"cursor:pointer\">基本面量化</span>\r\n",
    ),
]

raw = io.open(str(TPL), "r", encoding="utf-8", newline="").read()
out = raw
for old, new in PAIRS:
    n = out.count(old)
    assert n == 1, "锚点命中 %d 次: %r" % (n, old[:70])
    out = out.replace(old, new, 1)

assert out.count("\n") == out.count("\r\n"), "出现裸 LF"
assert out.count("_navBackToOverview(t) {\r\n") == 1
assert out.count("this._navBackToOverview(t);") == 1
io.open(str(TPL), "w", encoding="utf-8", newline="").write(out)
print("OK written")
