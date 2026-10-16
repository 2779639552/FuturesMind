# -*- coding: utf-8 -*-
"""Apple 化 · 第四步：清除彩色 emoji + 主题图标换线性 SVG。
要点：
  · 只删 U+1F300–U+1FAFF（真正的彩色 emoji），**保留** → ⇒ ⚠ ✓ ★ ✗ ⬇ 这类单色排版符号
    —— 它们是好的排版元素，苹果自身也大量使用箭头；删掉反而损失信息。
  · 删 U+FE0F 变体选择符：它把 ⚠ 这种单色符号"彩色化"，删掉即回归单色。
  · 主题按钮的 🌙/☀️ 是图标位，纯文字会怪，换成 1.4px 描边线性 SVG。
"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
before = src

MOON = ('<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" '
        'stroke-width="1.4" aria-hidden="true" style="vertical-align:-2px">'
        '<path d="M13.4 10.1A5.9 5.9 0 0 1 5.9 2.6a5.9 5.9 0 1 0 7.5 7.5Z" '
        'stroke-linecap="round" stroke-linejoin="round"/></svg>')
SUN = ('<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" '
       'stroke-width="1.4" aria-hidden="true" style="vertical-align:-2px">'
       '<circle cx="8" cy="8" r="3.1"/><path d="M8 1.1v1.7M8 13.2v1.7M14.9 8h-1.7M2.8 8H1.1'
       'M12.9 3.1l-1.2 1.2M4.3 11.7l-1.2 1.2M12.9 12.9l-1.2-1.2M4.3 4.3 3.1 3.1" '
       'stroke-linecap="round"/></svg>')

# ── 1) 主题图标 -> SVG ──────────────────────────────
# HTML 初始态
src = src.replace('<span id="theme-icon">🌙</span>', f'<span id="theme-icon">{MOON}</span>', 1)
# JS 切换态（同时把 textContent 改为 innerHTML，否则 SVG 会被当纯文本转义）
old_js = "    document.getElementById('theme-icon').textContent = theme === 'dark' ? '🌙' : '☀️';"
new_js = ("    document.getElementById('theme-icon').innerHTML = theme === 'dark'\n"
          f"      ? '{MOON}'\n"
          f"      : '{SUN}';")
if old_js in src:
    src = src.replace(old_js, new_js, 1)
else:
    # 宽松匹配（原串可能带全角/变体选择符差异）
    pat = re.compile(r"document\.getElementById\('theme-icon'\)\.textContent\s*=[^;]+;")
    m = pat.search(src)
    if m:
        src = src[:m.start()] + new_js.strip() + src[m.end():]
        print("WARN: theme-icon JS 走宽松匹配")
    else:
        print("WARN: 未找到 theme-icon 的 JS 赋值")

# ── 2) 删除彩色 emoji（含其紧邻的一个空白）──────────
# 范围只取 1F300–1FAFF（彩色 emoji 主区），刻意排除 2600–27BF 的单色符号
EMOJI = re.compile(r'[ \t]?[\U0001F300-\U0001FAFF]\uFE0F?[ \t]?')
cnt = len(EMOJI.findall(src))
src = EMOJI.sub('', src)

# ── 3) 删残余变体选择符（让 ⚠️ -> ⚠ 回归单色）────────
fe0f = src.count('\uFE0F')
src = src.replace('\uFE0F', '')

# ── 4) 清理因删除产生的双空格 / 行尾空格 ────────────
src = re.sub(r'(?<![ \t])[ \t]{2,}(?![ \t])', ' ', src)
src = re.sub(r'[ \t]+$', '', src, flags=re.M)

F.write_text(src, encoding="utf-8")
print(f"OK emoji 清理完成：删除彩色 emoji {cnt} 处、变体选择符 {fe0f} 处")
print("行数:", src.count("\n")+1, "字节:", len(src.encode('utf-8')), f"(原 {len(before.encode('utf-8'))})")
# 残留检查
left = re.findall(r'[\U0001F300-\U0001FAFF]', src)
print("残留彩色 emoji:", len(left), set(left) if left else "无")
print("主题 SVG:", "OK" if 'M13.4 10.1' in src else "MISSING")
