# -*- coding: utf-8 -*-
"""Apple 化 · 第五步：内联样式收敛（把 36 种散乱字号归一为字阶）+ 细节修复。

为什么必须做这步：内联 style 的优先级高于样式表，420 处内联 font-size 若不动，
无论 CSS 字阶设计得多好，页面呈现的仍是原来的 36 种档位 —— 这正是用户说的
「换汤不换药」。本步把它收敛为 12 档苹果式字阶。
"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
n0 = len(src)

# ══ 1) 内联 font-size 归一为字阶 ══════════════════════════════
# 基准 1rem = 16px。目标档位取苹果式阶梯，最小 12px（苹果正文下限）。
SCALE = [0.75, 0.8125, 0.875, 0.9375, 1.0, 1.0625, 1.125, 1.25, 1.375, 1.5, 1.75, 2.0,
         2.25, 2.5, 3.0, 3.5]

def snap(px):
    """把任意 px 值吸附到最近的档位。"""
    best = min(SCALE, key=lambda s: abs(s * 16 - px))
    return best

def fmt(rem):
    s = f"{rem:.4f}".rstrip("0").rstrip(".")
    return f"{s}rem"

stats = {}

def snap_font_size(m):
    num, unit = m.group(1), m.group(2)
    try:
        v = float(num)
    except ValueError:
        return m.group(0)
    px = v * 16 if unit == "rem" else v
    # 低于 12px 的一律抬到 12px（可读性下限）
    px = max(px, 12.0)
    tgt = fmt(snap(px))
    stats[f"{num}{unit}"] = tgt
    return f"font-size:{tgt}"

# 只在 style="..." 属性内部替换
def fix_style_attr(m):
    inner = m.group(1)
    inner2 = re.sub(r'font-size:\s*([\d.]+)(rem|px)', snap_font_size, inner)
    return f'style="{inner2}"'

src = re.sub(r'style="([^"]*)"', fix_style_attr, src)
print(f"内联字号归一：{len(stats)} 种原值 -> 字阶")
for k in sorted(stats, key=lambda x: float(re.sub(r'[a-z]', '', x))):
    print(f"  {k:10} -> {stats[k]}")

# ══ 2) 内联硬编码色 -> 令牌（保护 var() 兜底值）════════════
# 先把 `var(--x, #hex)` 整体抽成占位符，避免把兜底值也替换掉。
holders = {}
def stash(m):
    key = f"\x00H{len(holders)}\x00"
    holders[key] = m.group(0)
    return key

src = re.sub(r'var\(--[\w-]+\s*,\s*#[0-9a-fA-F]{3,8}\)', stash, src)

CMAP = {
    "#e0e0e0": "var(--border)", "#d0d0d0": "var(--border)", "#c6cad1": "var(--border-light)",
    "#e5e7eb": "var(--border-light)", "#f0f0f0": "var(--border-light)", "#eee": "var(--border-light)",
    "#222": "var(--text-primary)", "#666": "var(--text-muted)", "#4b5563": "var(--text-secondary)",
    "#8a8f98": "var(--text-muted)", "#b6bac2": "var(--text-muted)", "#374151": "var(--text-secondary)",
    "#185fa5": "var(--brand)", "#1d4ed8": "var(--brand)", "#c0392b": "var(--red)",
    "#1e8e3e": "var(--green)", "#a855f7": "var(--purple)", "#f59e0b": "var(--amber)",
    "#b45309": "var(--warn-ink)", "#f6f7f9": "var(--bg-tile)", "#eef2ff": "var(--bg-tile)",
    "#c084fc": "var(--purple)",
}
hit = 0
def fix_color(m):
    global hit
    raw = m.group(0)
    key = raw.lower()
    if key in CMAP:
        hit += 1
        return CMAP[key]
    return raw

src = re.sub(r'#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b', fix_color, src)
for k, v in holders.items():
    src = src.replace(k, v)
print(f"\n内联硬编码色收敛：{hit} 处 -> 令牌（var 兜底值已保护）")

# ══ 3) 细节修复（追加覆盖 CSS）════════════════════════════
FIX = r'''
/* ── 细节修复（Apple 层补充） ─────────────────────────────────────── */

/* 控制条内的下拉：内联 min-width:140px 太窄，「BR - 丁二烯橡胶 (94%)」会被截断 */
.page-tools select { min-width: 200px; }
.page-tools input[type="date"] { min-width: 150px; }

/* 折叠条：把「点击展开」提示与箭头一起推到右侧，标题独占左侧
   （删掉 emoji 后，flex space-between 会把中间那个 span 甩到正中央，很怪） */
.expander-header > span:not(.arrow) { margin-left: auto; padding-right: 12px; }

/* KPI 值：文本型状态（如「就绪」）与长数字共用一个尺寸会显得笨重，收一档 */
.stat-card .stat-val { font-size: 2rem; }

/* select 文本省略号：极窄容器下不撑破 */
select { text-overflow: ellipsis; }
'''
i = src.rfind("/* ── ≤900px 横屏/矮屏 ── */")
if i < 0:
    i = src.find("</style>")
src = src[:i] + FIX + "\n" + src[i:]

F.write_text(src, encoding="utf-8")
print(f"\nOK 第五步完成：{n0} -> {len(src.encode('utf-8'))} 字节")
