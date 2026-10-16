# -*- coding: utf-8 -*-
"""探针: 生意社 100ppi 完整品种字典 —— 确认是否真有 SC/LU/CJ 的别名页
只读取证。
"""
import re
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
S = requests.Session()
S.headers.update({"User-Agent": UA, "Referer": "https://www.100ppi.com/"})

URL = "https://www.100ppi.com/sf/792.html"
print("=" * 74)
print("STEP 1  首次裸取 (预期被 HW_CHECK 挑战挡住)")
print("=" * 74)
r = S.get(URL, timeout=25)
print("  status=%s  len=%s" % (r.status_code, len(r.content)))
if len(r.content) < 2000:
    m = re.search(r'var\s+_0x2\s*=\s*"([0-9a-fA-F]+)"', r.text)
    val = m.group(1) if m else None
    print("  挑战值 _0x2 =", (val[:48] + "...") if val else "【未匹配到】")
    if val:
        S.cookies.set("HW_CHECK", val, domain="www.100ppi.com", path="/")
        r = S.get(URL, timeout=25)
        print("  写入 HW_CHECK 后 len=%s" % len(r.content))

html = r.text
print()
print("=" * 74)
print("STEP 2  解析品种字典")
print("=" * 74)
# 找 div.q8 里的链接
pairs = re.findall(r'href="(/sf/(\d+)\.html)"[^>]*>([^<]{1,20})<', html)
seen, items = set(), []
for href, sid, name in pairs:
    key = (sid, name.strip())
    if key in seen:
        continue
    seen.add(key)
    items.append((sid, name.strip()))
print("  解析到品种条目: %d" % len(items))
print()
names = [n for _, n in items]
print("  全部品种名:")
for i in range(0, len(names), 8):
    print("    " + "  ".join("%-8s" % x for x in names[i:i + 8]))
print()
print("=" * 74)
print("STEP 3  缺口品种关键词命中检查")
print("=" * 74)
KW = {
    "SC(原油)": ["原油", "石油", "WTI", "布伦特", "阿曼"],
    "LU(低硫燃料油)": ["低硫", "燃料油", "船燃", "保税"],
    "CJ(红枣)": ["红枣", "枣"],
}
for code, kws in KW.items():
    print("  %s:" % code)
    anyhit = False
    for kw in kws:
        hit = [(s, n) for s, n in items if kw in n]
        if hit:
            anyhit = True
        print("     %-6s -> %s" % (kw, hit if hit else "无"))
    if not anyhit:
        print("     ❌ 该品种在 100ppi 字典中无任何别名页")
print()
print("完成")
