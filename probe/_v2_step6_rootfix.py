# -*- coding: utf-8 -*-
"""修复：根字号被字号归一脚本误伤。

事故经过：第四步的字号归一用正则扫了整个 CSS 块，把 `html { font-size: 16px }`
也吸附成了 `0.9375rem` —— 而 rem 是相对**浏览器默认 16px**，于是根字号退到 15px，
全站所有 rem 尺寸等比缩水 6.25%（0.75rem 本应 12px，实际渲染 11.25px）。
共 4 处受影响（桌面基准 + 两处移动端补偿）。

修法：所有 html 基准字号一律写成**绝对 px 16px**。
为什么不保留原来的 14.5/16.5px 移动端补偿：那些值是为了绕开"基准 15px"而存在的补丁，
现在字号阶以 16px 为基准、且所有小字号都有 px 地板，不再需要缩放基准
（缩小基准会让 0.75rem 跌破 12px 可读性地板）。
"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")

hits = []
def fix_html_fs(m):
    hits.append((m.group(0), m.group(2)))
    return "html { font-size: 16px;" + m.group(2)

src2 = re.sub(r'html \{ font-size:\s*([\d.]+)(rem|px);', fix_html_fs, src)

print(f"修正 html 基准字号: {len(hits)} 处")
for old, _ in hits:
    print(f"   {old}  ->  html {{ font-size: 16px;")

F.write_text(src2, encoding="utf-8")
print()
for m in re.finditer(r'html \{ font-size:[^;]+;', src2):
    print("  现在:", m.group(0))

# 断言：不得再有 rem 形式的 html 字号
bad = re.findall(r'html \{ font-size:\s*[\d.]+rem', src2)
print()
print("残留 rem 形式的 html 字号（必须 0）:", len(bad))
