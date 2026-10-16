# -*- coding: utf-8 -*-
"""Apple 化 · 第七步：CSS 字号归一 + 点击目标达标。
1) CSS 里残留的 0.65~0.88rem 微差字号 -> 统一字阶（地板 0.75rem = 12px）
2) 导航按钮 28px -> 40px（苹果导航的链接区是撑满导航高度的，44px 可点区）
3) badge / 箭头 11px -> 12px
"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
n0 = len(src)

# ══ 1) CSS 字号归一（只处理 <style> 块）════════════════════
a = src.find("<style>")
b = src.find("</style>")
css = src[a:b]
SCALE = [0.75, 0.8125, 0.875, 0.9375, 1.0, 1.0625, 1.125, 1.25, 1.375, 1.5, 1.75,
         2.0, 2.25, 2.5, 3.0, 3.5, 4.0]
def snap(px):
    return min(SCALE, key=lambda s: abs(s * 16 - px))

stats = {}
def fix(m):
    num, unit = m.group(1), m.group(2)
    v = float(num)
    px = v * 16 if unit == "rem" else v
    if px < 12.0:
        px = 12.0
    tgt = f"{snap(px):.4f}".rstrip("0").rstrip(".") + "rem"
    stats[f"{num}{unit}"] = tgt
    return f"font-size: {tgt}"

css2 = re.sub(r'font-size:\s*([\d.]+)(rem|px)', fix, css)
src = src[:a] + css2 + src[b:]
print(f"CSS 字号归一：{len(stats)} 种原值")
for k in sorted(stats, key=lambda x: float(re.sub(r'[a-z]', '', x))):
    print(f"  {k:10} -> {stats[k]}")

# ══ 2+3) 点击目标与字号细节，追加覆盖 CSS ═══════════════
FIX3 = r'''
/* ── 点击目标与字号达标（Apple 层 · 第三批） ───────────────────── */

/* 顶级导航：原 28px 高（padding 6px）不达 44px 可点区标准。
   苹果导航的链接区是撑满导航高度的 —— 这里给 40px 高、更宽的横向内边距。 */
nav button {
  padding: 10px 15px;
  min-height: 40px;
  display: inline-flex;
  align-items: center;
}
nav { align-items: center; }

/* 顶栏徽标 / 箭头 / 小徽章：抬到 12px（苹果正文下限） */
.header-badge { font-size: 0.75rem; }
.expander-header .arrow { font-size: 0.75rem; }
.card h3 .badge,
.badge { font-size: 0.75rem; }

/* 行内小链接（预览/下载/删除这类）：给足竖向可点区，不靠纯文字高度 */
td a, .dz-file a, .report-row a { display: inline-flex; align-items: center; min-height: 28px; }

/* btn-sm 也抬到 32px，满足最小点按高度 */
.btn-sm { min-height: 32px; }
'''
i = src.rfind("/* ── ≤900px 横屏/矮屏 ── */")
if i < 0:
    i = src.find("</style>")
src = src[:i] + FIX3 + "\n" + src[i:]

F.write_text(src, encoding="utf-8")
print(f"\nOK 第七步完成：{n0} -> {len(src.encode('utf-8'))} 字节")
