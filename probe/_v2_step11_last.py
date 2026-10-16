# -*- coding: utf-8 -*-
"""重做视觉层 · 第十一步：最后两类符号收口。
  ⬇ (4 处) —— 下载按钮/链接的图标 → 1.4px 描边 SVG（与其它图标风格统一）
  ⚖ (3 处) —— 辩论裁决/图例的装饰符号；旁边文字已完整表达含义，直接删
  ☀ (1 处) —— 仅在 JSDoc 注释里，保留不动
"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
n0 = len(src)

DOWNLOAD = ('<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" '
            'stroke-width="1.4" aria-hidden="true" style="vertical-align:-2px">'
            '<path d="M8 2.6v7.2M4.9 6.9 8 10l3.1-3.1M2.8 12.4v.6a1 1 0 0 0 1 1h8.4a1 1 0 0 0 1-1v-.6" '
            'stroke-linecap="round" stroke-linejoin="round"/></svg>')

# ── ① ⬇ -> SVG 下载图标 ────────────────────────────────────
c1 = 0
# 形态： "⬇ 下载 PDF" / "⬇ 导出 PDF" / "⬇ 下载"
def rep_dl(m):
    global c1
    c1 += 1
    return m.group(1) + DOWNLOAD + m.group(2)
src, n = re.subn(r'(\u2B07\s*)(下载|导出|下载 PDF|导出 PDF)', rep_dl, src)
if n == 0:
    # 兜底：直接替换所有 ⬇
    c = src.count("\u2B07")
    src = src.replace("\u2B07", DOWNLOAD)
    c1 += c
print(f"① ⬇ 下载图标 -> SVG: {c1} 处")

# ── ② ⚖ 装饰符号：删除（相邻文字已表达含义）────────────────
c2 = src.count("\u2696")
src = src.replace("'\u2696'", "''").replace("'\u2696 ", "'").replace("\u2696 ", "").replace("\u2696", "")
# 清理可能产生的空引号残留
src = src.replace("icon:''}", "icon:''}").replace(",'':", ",'' :")
print(f"② ⚖ 装饰符号清除: {c2} 处")

# ── ③ 清理因删除产生的多余空格 ─────────────────────────────
src = re.sub(r'(?<![ \t])[ \t]{2,}(?![ \t])', ' ', src)
src = re.sub(r'[ \t]+$', '', src, flags=re.M)

F.write_text(src, encoding="utf-8")
print()
print(f"OK: {n0} -> {len(src.encode('utf-8'))} 字节")
print("残留 ⬇:", src.count("\u2B07"), " ⚖:", src.count("\u2696"))
print("裸 { 块（必须 0）:", len(re.findall(r'(?m)^{', src)))
print("空 button:", len(re.findall(r'<button\b[^>]*>\s*</button>', src)))
