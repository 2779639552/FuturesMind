import re
P = r"D:\AgentSense\AgentSense\web_app.py"
src = open(P, encoding="utf-8").read()

src, n1 = re.subn(r"for c in ACTIVE_VARIETIES\n    \]", "for c in _active_variety_list()\n    ]", src)
src, n2 = re.subn(r"for c in ACTIVE_VARIETIES(\s*#.*)?", lambda m: "for c in _active_variety_list()" + (" " + m.group(1).strip() if m.group(1) else ""), src)

open(P, "w", encoding="utf-8").write(src)
print(f"替换: {n1}+{n2}")
for i, l in enumerate(src.split("\n"), 1):
    if "for c in ACTIVE_VARIETIES" in l:
        print(f"残留 L{i}: {l.strip()[:80]}")
print("检查 _active_variety_list 使用:", src.count("_active_variety_list()"))
