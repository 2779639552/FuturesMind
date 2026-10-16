# -*- coding: utf-8 -*-
"""诊断 emoji 清理副作用：找出变成空壳的图标按钮，并从备份还原其原 emoji。"""
import re, pathlib

CUR = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
BAK = pathlib.Path(r"D:\AgentSense\AgentSense\backup\web_template.html.bak_apple_redesign_20261014_091523")
cur = CUR.read_text(encoding="utf-8")
bak = BAK.read_text(encoding="utf-8")

# 备份中「纯 emoji 按钮」：标签内去掉空白与 emoji 后为空
EM = r'[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u2190-\u21FF\u2700-\u27BF]'
pat_bak = re.compile(r'(<button\b[^>]*>)([^<]*?)(</button>)', re.S)
pure = []
for m in pat_bak.finditer(bak):
    inner = m.group(2).strip()
    if inner and not re.sub(EM, '', inner).strip():
        attrs = m.group(1)
        ids = re.search(r'id="([^"]+)"', attrs)
        oc = re.search(r'onclick="([^"]{0,70})', attrs)
        ti = re.search(r'title="([^"]*)"', attrs)
        al = re.search(r'aria-label="([^"]*)"', attrs)
        pure.append({
            "emoji": inner, "id": ids.group(1) if ids else "",
            "onclick": oc.group(1) if oc else "", "title": ti.group(1) if ti else "",
            "aria": al.group(1) if al else "",
        })

print(f"备份中「纯 emoji 按钮」共 {len(pure)} 个：")
for p in pure:
    print(f"  {p['emoji']!r:8} id={p['id']!r:22} title={p['title']!r:12} onclick={p['onclick'][:44]!r}")

# 当前文件里的空按钮
print()
empty = []
for m in re.finditer(r'(<button\b[^>]*>)(\s*)(</button>)', cur):
    attrs = m.group(1)
    ids = re.search(r'id="([^"]+)"', attrs)
    oc = re.search(r'onclick="([^"]{0,70})', attrs)
    empty.append({"pos": m.start(), "id": ids.group(1) if ids else "",
                  "onclick": oc.group(1) if oc else ""})
print(f"当前文件「空按钮」共 {len(empty)} 个：")
for e in empty:
    print(f"  id={e['id']!r:24} onclick={e['onclick'][:50]!r}")

# 空 span / a
print()
for tag in ("span", "a"):
    pat = re.compile(r'(<' + tag + r'\b[^>]*>)(\s*)(</' + tag + r'>)')
    n = 0
    for m in pat.finditer(cur):
        attrs = m.group(1)
        if 'class="arrow"' in attrs or 'aria-hidden' in attrs or 'svg' in attrs.lower():
            continue
        ids = re.search(r'id="([^"]+)"', attrs)
        cls = re.search(r'class="([^"]+)"', attrs)
        n += 1
        if n <= 12:
            print(f"  空<{tag}> id={ids.group(1) if ids else '':22} class={cls.group(1) if cls else ''}")
    print(f"  --> 空 <{tag}> 合计 {n} 个")
