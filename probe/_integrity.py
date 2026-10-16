# -*- coding: utf-8 -*-
"""静态完整性检查：确认大量正则替换没有破坏 HTML/JS 结构。"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
t = F.read_text(encoding="utf-8")

print("=== 结构完整性 ===")
checks = [
    ("<style> 与 </style> 配平", t.count("<style") == t.count("</style>")),
    ("<script> 与 </script> 配平", t.count("<script") == t.count("</script>")),
    ("<header> 配平", t.count("<header>") == t.count("</header>")),
    ("<main> 配平", t.count("<main>") == t.count("</main>")),
    ("<nav> 配平", t.count("<nav") == t.count("</nav>")),
    ("<!DOCTYPE html> 存在", t.lstrip().startswith("<!DOCTYPE html>") or "<!DOCTYPE html>" in t[:400]),
    ("</html> 收尾", t.rstrip().endswith("</html>")),
    ("CSS 大括号配平", t[t.find("<style>"):t.find("</style>")].count("{") == t[t.find("<style>"):t.find("</style>")].count("}")),
    ("无裸 { 块", len(re.findall(r'(?m)^{', t)) == 0),
    ("无模板占位符泄漏 \\x00", "\x00" not in t),
]
for name, ok in checks:
    print(f"  [{'OK' if ok else 'FAIL'}] {name}")

print()
print("=== 残留问题 ===")
print(f"  彩色 emoji: {len(re.findall(r'[\\U0001F300-\\U0001FAFF]', t))}")
print(f"  空 button: {len(re.findall(r'<button\\b[^>]*>\\s*</button>', t))}")
print(f"  旧紫 #c7c7ff: {t.lower().count('#c7c7ff')}")
print(f"  星云/发光令牌仍有引用但值为 none: {'--nebula: none' in t}")

# 关键面板是否都在（防误删）
print()
print("=== 关键 DOM 存活 ===")
for el in ['id="tab-run"', 'id="tab-research"', 'id="tab-quant"', 'id="tab-fund"',
           'id="tab-warrant"', 'id="tab-foreign"', 'id="tab-warehouse"', 'id="tab-gtja"',
           'id="nav"', 'id="toasts"', 'id="login-overlay"', 'id="theme-icon"']:
    print(f"  [{'OK' if el in t else 'MISSING'}] {el}")

# JS 关键函数
print()
print("=== 关键 JS 函数 ===")
for fn in ["App.switchTab", "App.toggleTheme", "App.runAnalysis", "App._bindNav",
           "App.deleteQaSession", "App.regenerateVariety", "App._syncSections"]:
    print(f"  [{'OK' if fn in t else 'MISSING'}] {fn}")
