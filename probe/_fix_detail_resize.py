# -*- coding: utf-8 -*-
"""进入单品种明细后补一次全局图表 resize。

问题（既有，非本次宽度改动引入）：`#wt-h-prod` 等图住在 #f-detail-view 里，而
loadWarrant() 在**总览态**就被 switchTab 触发了 —— 那时 detail 视图 display:none，
容器宽 0 ⇒ ECharts init 落到默认 100px，画布从此停在 100px；进明细后视图变可见，
但没有任何地方 resize ⇒ 一张 722px 宽的容器里只有 100px 的图（贴左，右侧空白）。

本次把长图改半宽后，这个既有问题会让"半宽"看起来像"图缩成一条"，必须一并修掉。
"""
import io
from pathlib import Path

TPL = Path(r"D:\AgentSense\AgentSense\web_template.html")

PAIRS = [
    (
        "  this.loadQuant();\r\n"
        "  window.scrollTo({ top: 0, behavior: 'smooth' });\r\n",
        "  this.loadQuant();\r\n"
        "  // 【2026-10-21】视图由 display:none 变可见后补一次 resize：隐藏期间 init 的图表\r\n"
        "  //   拿到的是 0 宽（ECharts 回落 100px），不 resize 会一直画在 100px 画布上。\r\n"
        "  setTimeout(() => this._resizeAllCharts(), 0);\r\n"
        "  window.scrollTo({ top: 0, behavior: 'smooth' });\r\n",
    ),
    (
        "  this.loadFund();\r\n"
        "  window.scrollTo({ top: 0, behavior: 'smooth' });\r\n",
        "  this.loadFund();\r\n"
        "  // 【2026-10-21】同 qOpenDetail：仓单/利润等图在总览态（detail 隐藏）就已 init，\r\n"
        "  //   进明细时必须补 resize，否则画布停在 100px。\r\n"
        "  setTimeout(() => this._resizeAllCharts(), 0);\r\n"
        "  window.scrollTo({ top: 0, behavior: 'smooth' });\r\n",
    ),
]

raw = io.open(str(TPL), "r", encoding="utf-8", newline="").read()
out = raw
for old, new in PAIRS:
    n = out.count(old)
    assert n == 1, "锚点命中 %d 次: %r" % (n, old[:50])
    out = out.replace(old, new, 1)

assert out.count("\n") == out.count("\r\n"), "出现裸 LF"
io.open(str(TPL), "w", encoding="utf-8", newline="").write(out)
print("OK")
