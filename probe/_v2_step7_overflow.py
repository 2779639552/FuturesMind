# -*- coding: utf-8 -*-
"""修复：warehouse 页横向溢出。

根因：拖拽区里那个"视觉隐藏但保活"的 file input 上写了**内联 `style="width:100%"`**，
内联优先级高于样式表里的 `.dz > input[type=file] { width:1px }`，
于是这个本该 1px 的隐藏控件真的撑满了一行 —— 在 1920 视口下右侧超界 91px。
（只在 ≥1920 才暴露：窄屏时它被 flex 压回容器内，所以此前一直没被发现。）

为什么不能改成 display:none：项目注释已记录 —— display:none 会让部分浏览器
拒绝 label/click() 触发文件选择框，所以必须用「1px + clip」的视觉隐藏保活模式。

修法：① 去掉 file input 的内联宽度；② 在设计层用 !important 钉死隐藏规则，
即使将来有人再加内联样式也不会复发。
"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
n0 = len(src)

# ── ① 去掉 file input 上的内联 width ───────────────────────
c1 = 0
def strip_inline(m):
    global c1
    tag = m.group(0)
    if 'type="file"' not in tag:
        return tag
    new = re.sub(r'\s*style="[^"]*"', '', tag)
    if new != tag:
        c1 += 1
    return new
src, _ = re.subn(r'<input\b[^>]*>', strip_inline, src)
print(f"① 去掉 file input 内联样式: {c1} 处")

# ── ② 设计层加 !important 兜底 ─────────────────────────────
GUARD = r'''
/* ── 拖拽区 file input 的隐藏保活（!important 兜底）─────────────────
   为什么必须 !important：该 input 上曾被写入内联 `style="width:100%"`，
   内联优先级高于普通规则 ⇒ 本该 1px 的隐藏控件撑满整行，在 1920 视口下
   右侧超界 91px（窄屏被 flex 压回容器内，故长期未暴露）。
   为什么不用 display:none：项目既有结论 —— display:none 会让部分浏览器
   拒绝 label/click() 触发文件选择框，必须保留「1px + clip」的保活模式。 */
.dz > input[type="file"],
.dz input[type="file"] {
  position: absolute !important;
  width: 1px !important;
  height: 1px !important;
  min-height: 0 !important;
  padding: 0 !important;
  margin: -1px !important;
  overflow: hidden !important;
  clip: rect(0 0 0 0) !important;
  clip-path: inset(50%) !important;
  white-space: nowrap !important;
  border: 0 !important;
  opacity: 0 !important;
}
/* 拖拽区需要 relative 作为其定位包含块 */
.dz { position: relative; }
'''
i = src.rfind("/* ── ≤900px 横屏/矮屏 ── */")
if i < 0:
    i = src.find("</style>")
src = src[:i] + GUARD + "\n" + src[i:]

F.write_text(src, encoding="utf-8")
print(f"② 已加 !important 隐藏兜底规则")
print()
print(f"OK: {n0} -> {len(src.encode('utf-8'))} 字节")
print("裸 { 块（必须 0）:", len(re.findall(r'(?m)^{', src)))
print("残留带 style 的 file input:", len(re.findall(r'<input[^>]*type="file"[^>]*style=', src)))
