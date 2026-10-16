# -*- coding: utf-8 -*-
import re, pathlib, collections

t = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html").read_text(encoding="utf-8")
styles = re.findall(r'style="([^"]*)"', t)
fs = collections.Counter()
for s in styles:
    for m in re.finditer(r'font-size:\s*([^;"]+)', s):
        fs[m.group(1).strip()] += 1
print("内联 font-size 不同值:", len(fs), " 总出现:", sum(fs.values()))
for k, v in fs.most_common(40):
    print(f"  {k:18} {v}")

print()
col = collections.Counter()
for s in styles:
    for m in re.finditer(r'(?<!background-)color:\s*([^;"]+)', s):
        col[m.group(1).strip()] += 1
print("内联 color 不同值:", len(col), " 总出现:", sum(col.values()))
var_n = sum(v for k, v in col.items() if k.startswith("var("))
print(f"  其中走 var() 令牌: {var_n} / 硬编码: {sum(col.values())-var_n}")
