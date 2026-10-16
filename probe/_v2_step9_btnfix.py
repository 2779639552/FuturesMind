# -*- coding: utf-8 -*-
"""修复：图标按钮选择器误伤「新对话」按钮。

事故经过：设计层写了
    .btn:has(> svg:only-child) { width: 40px; min-width: 40px; padding: 0; }
本意是让「只有图标的按钮」变成正方形。
但 `svg:only-child` 的判定是「svg 是该父元素下唯一的子元素」——
「新对话」按钮的 DOM 是 `<button><svg/>新对话</button>`，svg 确实是 only-child
（文本节点不算 element），于是这个**带文字**的按钮也被判成纯图标按钮，
被强制 width:40px ⇒ 文字溢出、按钮被压成 20px 高、与相邻按钮重叠。

正确判据：纯图标按钮 = 按钮内**没有任何文本**。
CSS 选择器表达不了"无文本"，所以改为用元素自身的**属性**作为判据 ——
真实需求只针对「删除会话」「删除术语」这两个按钮，
它们都由 JS 通过 innerHTML 注入图标，特征明确（onclick 唯一）。
"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
n0 = len(src)

# ── 1) 删掉误伤的选择器规则 ─────────────────────────────
BAD = """.btn:has(> svg:only-child) {
  width: 40px;
  min-width: 40px;
  padding: 0;
}
.btn-sm:has(> svg:only-child) { width: 32px; min-width: 32px; }
"""
NEW = """/* ★【2026-10-14 修正】此处原来写的是 `.btn:has(> svg:only-child)`，
   意图是让"纯图标按钮"变正方形。但 `svg:only-child` 的判定是
   「svg 是父元素下唯一的**元素**子节点」—— 文本节点不参与判定，
   于是 `<button><svg/>新对话</button>` 这种**带文字**的按钮也被命中，
   被强制 width:40px ⇒ 文字溢出、按钮被压成 20px 高、与相邻按钮重叠。
   ⚠️ CSS 表达不了"无文本"，故改为按**按钮 id** 精确指定（见下）。 */
.btn > svg { flex: none; display: block; }
"""
if BAD in src:
    src = src.replace(BAD, NEW, 1)
    print("① 已移除误伤的 :has(> svg:only-child) 规则")
else:
    print("① WARN 误伤规则未精确命中，尝试正则")
    src, n = re.subn(r'\.btn:has\(> svg:only-child\) \{[^}]*\}\s*\.btn-sm:has\(> svg:only-child\) \{[^}]*\}',
                     NEW.rstrip(), src, count=1)
    print(f"   正则替换 {n} 处")

# ── 2) 用 id 精确指定真正的纯图标按钮 ───────────────────
BYID = r'''
/* ── 纯图标按钮：按 id 精确指定（正方形，与文字按钮等高）────────────
   为什么用 id 而不是 :has()：见上方说明 —— CSS 无法表达"按钮内无文本"，
   而这两个按钮是**唯一**真正的纯图标按钮，id 稳定且语义明确。 */
#res-qa-del {
  width: 40px;
  min-width: 40px;
  padding: 0;
}
/* 卡片标题行里的操作按钮组：保证彼此对齐、不被压扁 */
.card .btn { flex: none; }
'''
i = src.rfind("/* ── ≤900px 横屏/矮屏 ── */")
if i < 0:
    i = src.find("</style>")
src = src[:i] + BYID + "\n" + src[i:]
print("② 已改为按 id 精确指定")

F.write_text(src, encoding="utf-8")
print()
print(f"OK: {n0} -> {len(src.encode('utf-8'))} 字节")
print("残留 :has(> svg:only-child):", src.count(':has(> svg:only-child)'))
print("裸 { 块（必须 0）:", len(re.findall(r'(?m)^{', src)))
