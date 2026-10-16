# -*- coding: utf-8 -*-
"""重做视觉层 · 第四步：内联收敛 + 彩色符号清除（v2）

上一轮最大的方法论错误：只改样式表，没动内联 style。而内联 font-size 优先级
高于样式表 —— 420 处内联字号会把新字阶整体顶回去（这就是"字号还是那么多"）。
本步处理四件事：
  ① 彩色 emoji：有语义的换 1.4px 描边线性 SVG，纯装饰的删
  ② 内联字号：36 种散乱值 → 八档字阶（地板 12px）
  ③ 内联颜色：硬编码 hex → 令牌（保护 var() 兜底值）
  ④ 内联间距：卡片/栅格去掉多余的 margin-bottom（避免与设计层 gap 叠加）
"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
n0 = len(src)

# ══════════════════════════════════════════════════════════════
# ① SVG 图标（1.4px 描边，苹果风线性图标）
# ══════════════════════════════════════════════════════════════
def svg(paths, size=14):
    return (f'<svg viewBox="0 0 16 16" width="{size}" height="{size}" fill="none" '
            f'stroke="currentColor" stroke-width="1.4" aria-hidden="true" '
            f'style="vertical-align:-2px">{paths}</svg>')

ICON = {
    "sun":   svg('<circle cx="8" cy="8" r="3.1"/><path d="M8 1.1v1.7M8 13.2v1.7M14.9 8h-1.7'
                 'M2.8 8H1.1M12.9 3.1l-1.2 1.2M4.3 11.7l-1.2 1.2M12.9 12.9l-1.2-1.2'
                 'M4.3 4.3 3.1 3.1" stroke-linecap="round"/>'),
    "moon":  svg('<path d="M13.4 10.1A5.9 5.9 0 0 1 5.9 2.6a5.9 5.9 0 1 0 7.5 7.5Z" '
                 'stroke-linecap="round" stroke-linejoin="round"/>'),
    "pause": svg('<rect x="4.2" y="3.2" width="2.7" height="9.6" rx="1" fill="currentColor" '
                 'stroke="none"/><rect x="9.1" y="3.2" width="2.7" height="9.6" rx="1" '
                 'fill="currentColor" stroke="none"/>'),
    "play":  svg('<path d="M4.8 3.2 12.4 8l-7.6 4.8V3.2Z" fill="currentColor" stroke="none"/>'),
    "stop":  svg('<rect x="4" y="4" width="8" height="8" rx="1.4" fill="currentColor" stroke="none"/>'),
    "plus":  svg('<path d="M8 3.4v9.2M3.4 8h9.2" stroke-linecap="round"/>'),
    "trash": svg('<path d="M2.8 4.6h10.4M6.4 4.6V3.3a.9.9 0 0 1 .9-.9h1.4a.9.9 0 0 1 .9.9v1.3'
                 'M4.3 4.6l.7 8.2a1 1 0 0 0 1 .9h4a1 1 0 0 0 1-.9l.7-8.2" '
                 'stroke-linecap="round" stroke-linejoin="round"/>'),
    "key":   svg('<circle cx="5.6" cy="10.4" r="2.7"/><path d="M7.5 8.5 13 3M11 5l1.6 1.6M9.4 6.6l1.6 1.6" '
                 'stroke-linecap="round"/>'),
}

# ── 1a) 主题切换图标：🌙 / ☀️ -> SVG ──────────────────────
c_theme = 0
if '<span id="theme-icon">' in src:
    src, n = re.subn(r'<span id="theme-icon">[^<]*</span>',
                     f'<span id="theme-icon">{ICON["moon"]}</span>', src, count=1)
    c_theme += n
pat = re.compile(r"document\.getElementById\('theme-icon'\)\.(?:textContent|innerHTML)\s*=[^;]+;")
def _theme_js(m):
    global c_theme
    c_theme += 1
    return ("document.getElementById('theme-icon').innerHTML = theme === 'dark'\n"
            f"      ? '{ICON['moon']}'\n"
            f"      : '{ICON['sun']}';")
src, n = pat.subn(_theme_js, src, count=1)
c_theme += n
print(f"① 主题图标换 SVG: {c_theme} 处")

# ── 1b) 播放控制按钮：⏸ / ▶ / ⏹ -> SVG（按 id 精确定位）────
c_btn = 0
for bid, key in (("btn-pause", "pause"), ("btn-resume", "play"), ("btn-stop", "stop")):
    p = re.compile(r'(<button[^>]*id="' + bid + r'"[^>]*>)\s*[^<]*?\s*(</button>)')
    src, n = p.subn(lambda m: m.group(1) + ICON[key] + m.group(2), src)
    c_btn += n
    if n == 0:
        print(f"   WARN: {bid} 未命中")
# 改用 app.js 侧无 id 的同类按钮：按 title 兜底
print(f"① 播放控制按钮换 SVG: {c_btn} 处")

# ── 1c) 「新对话」的 ➕ 换 SVG ────────────────────────────
c_plus = 0
p = re.compile(r'(<button[^>]*id="res-qa-new"[^>]*>)\s*[^<]*?新对话\s*(</button>)')
src, n = p.subn(lambda m: m.group(1) + ICON["plus"] + "新对话" + m.group(2), src)
c_plus += n
# 其他 ➕（如「录入新研报」这类）统一去掉符号即可
c_plus += len(re.findall('\u2795', src))
src = src.replace('\u2795', '')
print(f"① 「新对话」➕ 换 SVG / 其余 ➕ 清除: {c_plus} 处")

# ── 1d) 🗑 / 🔑 换 SVG ────────────────────────────────────
c_misc = 0
p = re.compile(r'(<button[^>]*id="res-qa-del"[^>]*>)\s*[^<]*?\s*(</button>)')
src, n = p.subn(lambda m: m.group(1) + ICON["trash"] + m.group(2), src)
c_misc += n
p = re.compile(r'(<button[^>]*id="change-pw-btn"[^>]*>)\s*[^<]*?\s*(</button>)')
src, n = p.subn(lambda m: m.group(1) + ICON["key"] + "改密码" + m.group(2), src)
c_misc += n
print(f"① 删除/改密按钮换 SVG: {c_misc} 处")

# ── 1e) 剩余彩色 emoji 全部清除（含 U+FE0F）──────────────
COLOR_RANGES = [
    (0x1F300, 0x1FAFF), (0x1F1E6, 0x1F1FF), (0x231A, 0x231B), (0x23E9, 0x23EC),
    (0x23F0, 0x23F0), (0x23F3, 0x23F3), (0x23F8, 0x23FA), (0x25FD, 0x25FE),
    (0x2614, 0x2615), (0x2648, 0x2653), (0x267F, 0x267F), (0x2693, 0x2693),
    (0x26A1, 0x26A1), (0x26AA, 0x26AB), (0x26BD, 0x26BE), (0x26C4, 0x26C5),
    (0x26CE, 0x26CE), (0x26D4, 0x26D4), (0x26EA, 0x26EA), (0x26F2, 0x26F3),
    (0x26F5, 0x26F5), (0x26FA, 0x26FA), (0x26FD, 0x26FD), (0x2705, 0x2705),
    (0x270A, 0x270B), (0x2728, 0x2728), (0x274C, 0x274C), (0x274E, 0x274E),
    (0x2753, 0x2755), (0x2757, 0x2757), (0x2795, 0x2797), (0x27B0, 0x27B0),
    (0x27BF, 0x27BF), (0x2B1B, 0x2B1C), (0x2B50, 0x2B50), (0x2B55, 0x2B55),
]
cls = "".join(f"\\U{lo:08X}-\\U{hi:08X}" for lo, hi in COLOR_RANGES)
before = len(re.findall("[" + cls + "]", src))
src = re.sub("[ \t]?[" + cls + "][ \t]?", "", src)
src = src.replace('\uFE0F', '')
src = re.sub(r'(?<![ \t])[ \t]{2,}(?![ \t])', ' ', src)
src = re.sub(r'[ \t]+$', '', src, flags=re.M)
left = len(re.findall("[" + cls + "]", src))
print(f"① 剩余彩色 emoji 清除: {before} 处（残留 {left}）")

# ══════════════════════════════════════════════════════════════
# ② 字号归一：内联 + CSS 全走八档字阶
# ══════════════════════════════════════════════════════════════
SCALE = [0.75, 0.8125, 0.9375, 1.0625, 1.3125, 1.75, 2.5]  # 12/13/15/17/21/28/40px
def snap(px):
    return min(SCALE, key=lambda s: abs(s * 16 - px))
def fmt(rem):
    return f"{rem:.4f}".rstrip("0").rstrip(".") + "rem"

fs_report = {}
def fix_fs(m):
    num, unit = m.group(1), m.group(2)
    px = float(num) * 16 if unit == "rem" else float(num)
    px = max(px, 12.0)
    tgt = fmt(snap(px))
    fs_report[f"{num}{unit}"] = tgt
    return f"font-size:{tgt}"

# 2a) 只在内联 style 里替换
def _inline(m):
    return f'style="{re.sub(r"font-size:\s*([\d.]+)(rem|px)", fix_fs, m.group(1))}"'
src = re.sub(r'style="([^"]*)"', _inline, src)
print(f"② 内联字号归一: {len(fs_report)} 种原值 -> 档位")

# 2b) CSS 块内也归一（排除已经在令牌里的 --fs-* 定义）
a, b = src.find("<style>"), src.find("</style>")
css = src[a:b]
css_new = re.sub(r'font-size:\s*([\d.]+)(rem|px)', fix_fs, css)
src = src[:a] + css_new + src[b:]
print(f"② CSS 字号归一（合计 {len(fs_report)} 种原值）")

# ══════════════════════════════════════════════════════════════
# ③ 内联硬编码色 -> 令牌（先保护 var() 兜底值）
# ══════════════════════════════════════════════════════════════
hold = {}
def _stash(m):
    k = f"\x00H{len(hold)}\x00"
    hold[k] = m.group(0)
    return k
src = re.sub(r'var\(--[\w-]+\s*,\s*#[0-9a-fA-F]{3,8}\)', _stash, src)
CMAP = {
    "#e0e0e0": "var(--border)", "#d0d0d0": "var(--border)", "#c6cad1": "var(--border)",
    "#e5e7eb": "var(--border-light)", "#f0f0f0": "var(--border-light)", "#eee": "var(--border-light)",
    "#222": "var(--text-primary)", "#666": "var(--text-muted)", "#4b5563": "var(--text-secondary)",
    "#8a8f98": "var(--text-muted)", "#b6bac2": "var(--text-muted)", "#374151": "var(--text-secondary)",
    "#185fa5": "var(--brand)", "#1d4ed8": "var(--brand)", "#c0392b": "var(--red)",
    "#1e8e3e": "var(--green)", "#f59e0b": "var(--amber)", "#b45309": "var(--warn-ink)",
    "#f6f7f9": "var(--bg-tile)", "#eef2ff": "var(--bg-tile)", "#c084fc": "var(--brand)",
    "#a855f7": "var(--brand)", "#eef2f7": "var(--bg-tile)",
}
chit = 0
def fix_color(m):
    global chit
    v = CMAP.get(m.group(0).lower())
    if v:
        chit += 1
        return v
    return m.group(0)
src = re.sub(r'#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b', fix_color, src)
for k, v in hold.items():
    src = src.replace(k, v)
print(f"③ 内联硬编码色收敛: {chit} 处")

# ══════════════════════════════════════════════════════════════
# ④ 卡片/栅格 去掉内联 margin-bottom（避免与设计层间距叠加）
# ══════════════════════════════════════════════════════════════
c_mb = 0
def strip_mb(m):
    global c_mb
    tag, attrs = m.group(1), m.group(2)
    if not re.search(r'class="[^"]*\b(card|grid)\b', attrs):
        return m.group(0)
    sm = re.search(r'style="([^"]*)"', attrs)
    if not sm:
        return m.group(0)
    body = sm.group(1)
    new = re.sub(r'margin-bottom:\s*[\d.]+[a-z]*;?', '', body).strip(';').strip()
    if new == body:
        return m.group(0)
    c_mb += 1
    replaced = attrs[:sm.start()] + (f'style="{new}"' if new else '') + attrs[sm.end():]
    return tag + replaced
src = re.sub(r'(<(?:div|section|aside)\b)([^>]*>)', strip_mb, src)
print(f"④ 卡片/栅格 清内联 margin-bottom: {c_mb} 处")

# ⑤ 表单行 gap 统一 16px（原 10/12px 过窄，读作一整条）
c_gap = 0
def fix_gap(m):
    global c_gap
    s = m.group(1)
    if 'display:flex' not in s.replace(' ', ''):
        return m.group(0)
    new = re.sub(r'gap:\s*(?:10|12)px', 'gap:16px', s)
    new = new.replace('align-items:end', 'align-items:flex-end')
    if new != s:
        c_gap += 1
    return f'style="{new}"'
src = re.sub(r'style="([^"]*)"', fix_gap, src)
print(f"⑤ 表单行 gap 归一 16px: {c_gap} 处")

F.write_text(src, encoding="utf-8")
print()
print(f"OK 内联收敛完成: {n0} -> {len(src.encode('utf-8'))} 字节")
print("  裸 { 块（必须 0）:", len(re.findall(r'(?m)^{', src)))
print("  残留彩色 emoji:", len(re.findall("[" + cls + "]", src)))
print("  残留 U+FE0F:", src.count('\uFE0F'))
