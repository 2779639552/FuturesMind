# -*- coding: utf-8 -*-
"""Apple 化 · 第九步（修正版）：用 padding 扩命中区，**不改 display**。

上一版错误：对表格内 <a>/<button> 强制 `display:inline-flex`，
使原本按内容撑开的窄链接收缩（warehouse 小目标 4 -> 54，属回归）。
正确做法：inline 元素加 padding 即可扩大 boundingRect 与命中区，
line box 不受影响，因此不会改变版面密度。
"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
n0 = len(src)

# 找到并删除上一版（第五批）整块
start = src.find("/* ── 点击目标终修（Apple 层 · 第五批） ──────────────────────── */")
if start >= 0:
    # 到下一个同级分隔注释或 Apple 层末尾
    end = src.find("/* ── ≤900px 横屏/矮屏 ── */", start)
    if end < 0:
        end = src.find("</style>", start)
    removed = src[start:end]
    src = src[:start] + src[end:]
    print(f"已移除上一版块（{len(removed)} 字符）")
else:
    print("WARN: 未找到上一版块")

FIX5 = r'''/* ── 点击目标终修（Apple 层 · 第五批，修正版） ──────────────────
   原则：**不改 display**，只用 padding 抬命中区 —— inline 元素的 padding 计入
   boundingRect，可扩大点击范围，同时不影响 line box 与版面密度。
   上一版对表格链接强制 inline-flex，导致窄链接宽度收缩、warehouse 小目标从
   4 涨到 54（回归）。此处按修正原则重写。 */

/* 外文研报历史任务：预览/下载/删除 —— 抬高命中区到 32px */
#fg-list a {
  padding: 7px 6px;
  margin-left: 6px;
  white-space: nowrap;
}

/* 表格内的操作链接 / 小按钮：上下各 5px padding，稳达 28px 命中高 */
td a, td button,
.rv-tbl a, .rv-tbl button,
.report-content a { padding: 5px 3px; }

/* 顶栏「改密码」按钮 */
#change-pw-btn { min-height: 30px; }
'''
i = src.rfind("/* ── ≤900px 横屏/矮屏 ── */")
if i < 0:
    i = src.find("</style>")
src = src[:i] + FIX5 + "\n" + src[i:]

F.write_text(src, encoding="utf-8")
print(f"OK 第九步(修正)：{n0} -> {len(src.encode('utf-8'))} 字节")
# 复查：确认没有再强制 display
bad = re.findall(r'(?:td|\.rv-tbl)[^{]*\{[^}]*display:\s*inline-flex[^}]*\}', src)
print("残留强制 inline-flex 的表格规则:", len(bad))
