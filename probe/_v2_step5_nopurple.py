# -*- coding: utf-8 -*-
"""重做视觉层 · 第五步：清除紫色系 + 补回被误清空的图标按钮。

用户反馈"那个紫色+是什么，给我删掉，一股AI味道"。
上一步已把 ➕ 这个**彩色 emoji** 清掉，但紫色还留在两处：
  ① 图表分类色板里的 #a78bfa 等紫色系（8 处，JS 内）
  ② --candy-4 / --purple / --chart-k-ma3 三个令牌本身的值
本步把它们全部换成非紫色系，并处理副产物（1 个空按钮、黑洞遮罩的紫调底）。

⚠️ **涨跌语义色（红涨绿跌）刻意不动** —— 只换装饰性紫色。
"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
n0 = len(src)

# ══ 1) 补回被误清空的「删除术语」图标按钮 ═══════════════════
TRASH = ('<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" '
         'stroke-width="1.4" aria-hidden="true" style="vertical-align:-2px">'
         '<path d="M2.8 4.6h10.4M6.4 4.6V3.3a.9.9 0 0 1 .9-.9h1.4a.9.9 0 0 1 .9.9v1.3'
         'M4.3 4.6l.7 8.2a1 1 0 0 0 1 .9h4a1 1 0 0 0 1-.9l.7-8.2" '
         'stroke-linecap="round" stroke-linejoin="round"/></svg>')
p = re.compile(r'(<button[^>]*onclick="App\.deleteQaTerm\(\$\{t\.id\}\)"[^>]*>)\s*(</button>)')
src, n = p.subn(lambda m: m.group(1) + TRASH + m.group(2), src)
print(f"① 补回空图标按钮: {n} 处")
left_empty = len(re.findall(r'<button\b[^>]*>\s*</button>', src))
print(f"   剩余空按钮: {left_empty}")

# ══ 2) 令牌：紫色 -> 非紫 ═══════════════════════════════════
# --candy-4 / --purple 走「粉」（分类色板用，与红涨 #ff453a 色相差 10° 以上）
# --chart-k-ma3（MA20 均线）走「中性灰」—— 彻底避开红/绿/紫任何语义歧义
TOKEN_FIX = [
    # 暗色主题
    ("  --candy-4: #bf5af2;", "  --candy-4: #ff375f;"),
    ("  --purple: #bf5af2;", "  --purple: #ff375f;"),
    ("  --chart-k-ma3: #bf5af2;", "  --chart-k-ma3: #98989d;"),
    # 亮色主题
    ("  --candy-4: #8944ab;", "  --candy-4: #c2185b;"),
    ("  --chart-k-ma3: #8944ab;", "  --chart-k-ma3: #6e6e73;"),
]
tfix = 0
for old, new in TOKEN_FIX:
    if old in src:
        src = src.replace(old, new, 1)
        tfix += 1
    else:
        print(f"   WARN 未命中: {old.strip()}")
print(f"② 令牌去紫: {tfix}/{len(TOKEN_FIX)}")

# 亮色主题的 --purple 继承自暗色块？确认它也有定义
if '--purple:' not in src[src.find('[data-theme="light"]'):src.find('[data-theme="light"]')+3000]:
    print("   注: 亮色块未单独定义 --purple，继承暗色值 #ff375f（可接受）")

# ══ 3) JS 图表色板：紫色系 -> 非紫（保持各色板内部可区分）══
JS_MAP = {
    "#a78bfa": "#40c8e0",   # 紫 -> 青（分类色板）
    "#818cf8": "#5ac8fa",   # 靛 -> 天蓝
    "#8b5cf6": "#5ac8fa",   # 紫 -> 天蓝
    "#e879f9": "#ff9f0a",   # 品红 -> 橙
    "#d946ef": "#ff375f",   # 品红 -> 粉
    "#e8c9f5": "#c9e8f5",   # 淡紫 -> 淡青
    "#c0b8f0": "#f5e0c9",   # 淡紫 -> 淡棕
}
jfix = {}
for old, new in JS_MAP.items():
    pat = re.compile(re.escape(old), re.I)
    c = len(pat.findall(src))
    if c:
        src = pat.sub(new, src)
        jfix[old] = c
print(f"③ JS 色板去紫: {sum(jfix.values())} 处 {jfix}")

# ══ 4) 黑洞 loading 遮罩：去掉紫调，改中性深灰 ═══════════════
old_bh = "radial-gradient(circle at 50% 48%, #1b1720 0%, #0b0910 55%, var(--bg-deep) 100%)"
new_bh = "radial-gradient(circle at 50% 48%, #1c1c1e 0%, #0a0a0a 55%, var(--bg-deep) 100%)"
if old_bh in src:
    src = src.replace(old_bh, new_bh, 1)
    print("④ 黑洞遮罩去紫调: OK")
else:
    print("④ WARN 黑洞遮罩未命中")

# ══ 5) 兜底：清掉任何残留的紫色系 hex（排除 var() 兜底与语义色）══
PURPLE_LEFTOVER = {
    "#bf5af2": "#ff375f", "#8944ab": "#c2185b", "#a855f7": "#40c8e0",
    "#c084fc": "#40c8e0", "#9333ea": "#40c8e0", "#7c3aed": "#0a84ff",
}
lfix = 0
for old, new in PURPLE_LEFTOVER.items():
    pat = re.compile(re.escape(old), re.I)
    c = len(pat.findall(src))
    if c:
        src = pat.sub(new, src)
        lfix += c
print(f"⑤ 兜底去紫: {lfix} 处")

F.write_text(src, encoding="utf-8")

# ── 复检 ──────────────────────────────────────────────
import colorsys
def hue(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i+2], 16) / 255 for i in (0, 2, 4))
    hh, ll, ss = colorsys.rgb_to_hls(r, g, b)
    return hh * 360, ss
rest = []
for m in re.finditer(r'#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b', src):
    try:
        hh, ss = hue(m.group(0))
    except Exception:
        continue
    if ss > 0.15 and 248 <= hh <= 315:
        rest.append(m.group(0).lower())
print()
print(f"OK 第五步完成: {n0} -> {len(src.encode('utf-8'))} 字节")
print("  残留紫色系:", sorted(set(rest)) if rest else "无")
print("  空按钮:", len(re.findall(r'<button\b[^>]*>\s*</button>', src)))
