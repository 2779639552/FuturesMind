# -*- coding: utf-8 -*-
"""Apple 化 · 第十一步：移动端导航回归修复的补充 + 表头竖排修复。
（导航主修复已用 Edit 直接落到 Apple 层的 768px 断点内）
"""
import pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
n0 = len(src)

FIX7 = r'''
/* ── 表头与窄列防竖排（Apple 层 · 第七批） ────────────────────────
   实测：研报「当日观点总表」表头「品种」「当日研报数」被压成竖排（品/种 分两行）。
   苹果的表格表头永不换行 —— 列窄时让表格横向滚动，而不是把字拆开。 */
table th { white-space: nowrap; }
.report-content th { white-space: nowrap; }
/* 数字列 / 短标签列不换行；长文本列正常换行 */
td.num, td:has(> .pill) { white-space: nowrap; }
/* 表格容器允许横向滚动（窄屏宽表） */
.table-scroll, .tbl-wrap { overflow-x: auto; }

/* ≤420px：移动端导航进一步收紧，杜绝溢出 */
@media (max-width: 420px) {
  nav button { font-size: 0.6875rem; min-height: 44px; padding: 3px 0; }
}
'''
i = src.rfind("/* ── ≤900px 横屏/矮屏 ── */")
if i < 0:
    i = src.find("</style>")
src = src[:i] + FIX7 + "\n" + src[i:]

F.write_text(src, encoding="utf-8")
print(f"OK 第十一步：{n0} -> {len(src.encode('utf-8'))} 字节")
