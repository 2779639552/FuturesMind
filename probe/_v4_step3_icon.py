# -*- coding: utf-8 -*-
"""v4 收尾：统一按钮图标（把残留的 ↻ 文本符号换成 1.4px 描边 SVG）。
与既有风格一致 —— 上轮已把 🌙☀️⏸▶⏹➕🗑⬇ 全换成同规格线性 SVG，↻ 是漏网的一处。"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
n0 = len(src)

REFRESH = ('<svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" '
           'stroke-width="1.4" aria-hidden="true" style="vertical-align:-2px">'
           '<path d="M13.2 8a5.2 5.2 0 1 1-1.5-3.7" stroke-linecap="round"/>'
           '<path d="M13.4 2.6v3.1h-3.1" stroke-linecap="round" stroke-linejoin="round"/></svg>')

c = 0
# ① 「↻ 重新生成」/「↻ 重新生成全部」/「↻ 重试」：符号 + 空格 + 文字
src, n = re.subn(r'\u21bb\s+(?=重新生成|重试)', REFRESH, src)
c += n
# ② 「>↻</button>」纯图标按钮
src, n2 = re.subn(r'>\s*\u21bb\s*</button>', '>' + REFRESH + '</button>', src)
c += n2
# ③ 兜底：任何残留
rest = src.count('\u21bb')
src = src.replace('\u21bb', REFRESH)
c += rest

print(f"↻ → SVG 循环图标: {c} 处（其中兜底 {rest} 处）")
print(f"残留 ↻: {src.count(chr(0x21bb))}")

F.write_text(src, encoding="utf-8")
print(f"{n0} -> {len(src.encode('utf-8'))} 字节")
print("裸 { 块:", len(re.findall(r'(?m)^{', src)))
