# -*- coding: utf-8 -*-
"""扫描：文件里所有非 ASCII 符号，按「是否会渲染成彩色 emoji」分类。
用于精确决定哪些必须删/换、哪些是安全排版符号应当保留。
"""
import re, pathlib, collections, unicodedata

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
t = F.read_text(encoding="utf-8")

# Emoji_Presentation=Yes 的区间（这些在浏览器里默认渲染为**彩色**）
COLOR_RANGES = [
    (0x1F300, 0x1FAFF), (0x1F1E6, 0x1F1FF),
    (0x231A, 0x231B), (0x23E9, 0x23EC), (0x23F0, 0x23F0), (0x23F3, 0x23F3),
    (0x23F8, 0x23FA), (0x25FD, 0x25FE), (0x2614, 0x2615), (0x2648, 0x2653),
    (0x267F, 0x267F), (0x2693, 0x2693), (0x26A1, 0x26A1), (0x26AA, 0x26AB),
    (0x26BD, 0x26BE), (0x26C4, 0x26C5), (0x26CE, 0x26CE), (0x26D4, 0x26D4),
    (0x26EA, 0x26EA), (0x26F2, 0x26F3), (0x26F5, 0x26F5), (0x26FA, 0x26FA),
    (0x26FD, 0x26FD), (0x2705, 0x2705), (0x270A, 0x270B), (0x2728, 0x2728),
    (0x274C, 0x274C), (0x274E, 0x274E), (0x2753, 0x2755), (0x2757, 0x2757),
    (0x2795, 0x2797), (0x27B0, 0x27B0), (0x27BF, 0x27BF), (0x2B1B, 0x2B1C),
    (0x2B50, 0x2B50), (0x2B55, 0x2B55),
]
# Emoji=Yes 但 Presentation=No（默认单色；**只有后接 U+FE0F 才变彩色**）
VARIATION_ONLY = set("⬅⬆⬇➡✔✖❤✂✈✉✏✒⚠✳✴❄❇☀☁☂☎☑☝☠☢☣☮☯☸☹☺♠♣♥♦♨♻⚒⚔⚖⚗⚙⚛⚜")

def is_color(ch):
    o = ord(ch)
    return any(a <= o <= b for a, b in COLOR_RANGES)

color, mono, other = collections.Counter(), collections.Counter(), collections.Counter()
for ch in t:
    o = ord(ch)
    if o < 0x80:
        continue
    if 0x4E00 <= o <= 0x9FFF or 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF:
        continue  # 汉字 / 中文标点 / 全角
    if ch == '\uFE0F':
        other[ch] += 1
        continue
    (color if is_color(ch) else (mono if ch in VARIATION_ONLY else other))[ch] += 1

def show(title, cnt, limit=40):
    print(f"\n=== {title} —— {len(cnt)} 种 / {sum(cnt.values())} 处 ===")
    for k, v in cnt.most_common(limit):
        try:
            nm = unicodedata.name(k)
        except ValueError:
            nm = "?"
        print(f"   U+{ord(k):04X} {k!r:9} x{v:<4} {nm[:44]}")

show("★ 彩色 emoji（必须删或换 SVG）", color)
show("△ 默认单色符号（安全，可保留）", mono)
show("· 其他非 ASCII（引号/破折号等）", other)

print("\n=== 变体选择符 U+FE0F ===", other.get('\uFE0F', 0), "处（会把前面的单色符号变成彩色）")
