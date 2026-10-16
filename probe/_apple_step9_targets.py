# -*- coding: utf-8 -*-
"""Apple 化 · 第九步（终）：把剩余小点击目标抬到 32px 可点区 + 定位 quant/research 残余。"""
import pathlib, re

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
n0 = len(src)

FIX5 = r'''
/* ── 点击目标终修（Apple 层 · 第五批） ──────────────────────── */

/* 外文研报历史任务的 预览/下载/删除：28px 仍偏小，抬到 32px 且给足横向命中区 */
#fg-list a {
  min-height: 32px;
  min-width: 44px;
  justify-content: center;
  padding: 0 6px;
  margin-left: 8px;
}
/* 任务行的标题链接也一并给足高度 */
#fg-list a:first-child { min-width: 0; justify-content: flex-start; }

/* 表格里的操作链接 / 行内小按钮：抬高命中区，不改视觉密度 */
td a, td button, .report-content a { min-height: 28px; display: inline-flex; align-items: center; }

/* 研报库/数据集表格里的「查看/原文/删除」小按钮 */
.rv-tbl a, .rv-tbl button,
table .mini-btn, table a { min-height: 28px; display: inline-flex; align-items: center; }

/* 通配兜底：任何残留的显式矮按钮不再低于 30px（不影响视觉，只扩命中区） */
button:not(.theme-toggle):not(nav button) { min-height: 30px; }
'''
i = src.rfind("/* ── ≤900px 横屏/矮屏 ── */")
if i < 0:
    i = src.find("</style>")
src = src[:i] + FIX5 + "\n" + src[i:]
F.write_text(src, encoding="utf-8")
print(f"OK 第九步：{n0} -> {len(src.encode('utf-8'))} 字节")
