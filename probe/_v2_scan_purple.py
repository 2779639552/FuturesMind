# -*- coding: utf-8 -*-
"""扫描全站 hex 颜色，按色相找出紫色系（AI 味的典型来源）。"""
import re, pathlib, colorsys, collections

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
t = F.read_text(encoding="utf-8")
a, b = t.find("<style>"), t.find("</style>")

def hue(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, bl = (int(h[i:i+2], 16) / 255 for i in (0, 2, 4))
    hh, ll, ss = colorsys.rgb_to_hls(r, g, bl)
    return hh * 360, ll, ss

cnt = collections.Counter(m.group(0).lower() for m in re.finditer(r'#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b', t))
purple, other = [], []
for hexv, n in cnt.items():
    try:
        hh, ll, ss = hue(hexv)
    except Exception:
        continue
    if ss < 0.15:
        continue          # 灰阶，不算紫
    (purple if 248 <= hh <= 315 else other).append((hexv, n, round(hh), round(ss, 2)))

print(f"=== 紫色系（色相 248-315°，饱和 >0.15）：{len(purple)} 种 ===")
for hexv, n, hh, ss in sorted(purple, key=lambda x: -x[1]):
    pos = [m.start() for m in re.finditer(re.escape(hexv), t, re.I)]
    incss = sum(1 for p in pos if a <= p < b)
    print(f"   {hexv:9} x{n:<3} 色相{hh:3}° 饱和{ss}  CSS内{incss} / 块外{len(pos)-incss}")

print()
print("=== 全部含饱和色的 hex（按色相排序，供判断）===")
for hexv, n, hh, ss in sorted(other, key=lambda x: x[2]):
    if n >= 1 and ss > 0.3:
        print(f"   {hexv:9} x{n:<3} 色相{hh:3}° 饱和{ss}")
