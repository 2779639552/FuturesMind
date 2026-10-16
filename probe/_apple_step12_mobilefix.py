# -*- coding: utf-8 -*-
"""Apple 化 · 第十二步：移动端底栏定位修复（真根因）。

根因（已实测证实）：header 带 backdrop-filter，按 CSS 规范它会为**后代** fixed 元素
建立 containing block。而 nav#nav 是 header 的子元素，移动端 `position:fixed; bottom:0`
于是变成"相对 header 底边"，实测 navY=-6 / navBottom=51（应贴视口底部 844）——
底部标签栏整个被顶到页面顶部、与内容重叠。
实测去掉 header 的 backdrop-filter 后 navY 由 -6 → 787（正确）。

修法：移动端 header 改实色底、去毛玻璃（毛玻璃的视觉收益远低于底部标签栏错位的代价）；
底栏 nav 自身保留毛玻璃（它是独立 fixed 层，无 fixed 后代，不受影响）。
"""
import pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
n0 = len(src)

FIX8 = r'''
/* ── 移动端底栏定位修复（Apple 层 · 第八批） ──────────────────────
   ★ 根因：header 的 backdrop-filter 会为**后代** fixed 元素建立 containing block
     （CSS 规范行为）。nav#nav 是 header 子元素，移动端 position:fixed;bottom:0
     因此变成"相对 header 底边"—— 实测 navY=-6px、navBottom=51px（应 844px），
     底部标签栏被顶到页面顶部并与内容重叠。
   ★ 实测证据：移除 header 的 backdrop-filter 后 navY 由 -6px → 787px（恢复正确）。
   ★ 取舍：移动端放弃顶栏毛玻璃，换底部标签栏正确定位。底栏自身的毛玻璃保留
     （它是独立 fixed 层，无 fixed 后代，不受影响）。 */
@media (max-width: 768px) {
  header {
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
    /* 用实色底替代半透明，否则去掉 blur 后会与内容糊在一起 */
    background: var(--bg);
  }
  /* 底栏保持毛玻璃（此时它已正确定位到视口底部） */
  nav#nav {
    backdrop-filter: saturate(180%) blur(20px);
    -webkit-backdrop-filter: saturate(180%) blur(20px);
  }
}
'''
i = src.rfind("/* ── ≤900px 横屏/矮屏 ── */")
if i < 0:
    i = src.find("</style>")
src = src[:i] + FIX8 + "\n" + src[i:]

F.write_text(src, encoding="utf-8")
print(f"OK 第十二步：{n0} -> {len(src.encode('utf-8'))} 字节")
