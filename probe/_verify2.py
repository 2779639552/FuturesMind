# -*- coding: utf-8 -*-
import re, pathlib
F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
t = F.read_text(encoding="utf-8")

print("=== nav 标签实际出现位置 ===")
for m in re.finditer(r'<nav\b[^>]*>', t):
    line = t[:m.start()].count("\n") + 1
    print(f"  L{line}: {m.group()[:70]}")
print("  </nav> 次数:", t.count("</nav>"))

print()
print("=== 彩色 emoji 正确统计（单反斜杠）===")
em = re.findall(r'[\U0001F300-\U0001FAFF]', t)
print("  彩色 emoji:", len(em), set(em) if em else "无")

print()
print("=== 章节同步函数实际名 ===")
for m in re.finditer(r'\b(_?sync\w*[Ss]ection\w*|_secScan|_applySecFilter|_toggleSection|_flipChips)\s*[:(]', t):
    print("  ", m.group(1))
print("  去重:", sorted(set(re.findall(r'\b(_secScan|_applySecFilter|_toggleSection|_flipChips|_syncSections|_buildSecIndex)\b', t))))

print()
print("=== 归档对照：备份里 emoji 数 ===")
bak = pathlib.Path(r"D:\AgentSense\AgentSense\backup\web_template.html.bak_apple_redesign_20261014_091523").read_text(encoding="utf-8")
print("  备份彩色 emoji:", len(re.findall(r'[\U0001F300-\U0001FAFF]', bak)))
print("  当前彩色 emoji:", len(re.findall(r'[\U0001F300-\U0001FAFF]', t)))
