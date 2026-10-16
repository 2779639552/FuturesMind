# -*- coding: utf-8 -*-
"""修复：折叠条标题被推到中间。

根因：此前写的是
    .expander-header > span:not(.arrow) { margin-left: auto; }
`margin-left:auto` 命中了**所有**非箭头 span —— 包括标题本身。
实测标题 span 的 marginLeft = 812.92px（即 auto 吃掉全部剩余空间），
于是标题被推到水平中间（用户看到「油品」居中）。

正确做法：让**标题**吃掉右侧剩余空间（margin-right:auto），
其余元素按正常流紧跟其后靠右 —— 这样 2 个子元素（标题+箭头）
与 3 个子元素（标题+提示+箭头）两种结构都正确。
"""
import re, pathlib

F = pathlib.Path(r"D:\AgentSense\AgentSense\web_template.html")
src = F.read_text(encoding="utf-8")
n0 = len(src)

# 移除旧的错误规则
OLD = """.expander-header { justify-content: flex-start; }
.expander-header > span:not(.arrow) { margin-left: auto; padding: 0 var(--sp-3) 0 0; }"""
OLD2 = """.expander-header { justify-content: flex-start; }
.expander-header > span:not(.arrow) { margin-left: auto; padding: 0 10px 0 0; }"""

NEW = """/* ★【2026-10-14 修正】此处原写 `> span:not(.arrow) { margin-left: auto }`，
   而 `margin-left:auto` 会命中**所有**非箭头 span —— 含标题本身，
   实测标题 span 的 marginLeft = 812.9px（auto 吃掉全部余量），
   标题被推到水平中间（表现为「油品」居中）。
   改为让**标题**吃掉右侧余量，其余元素按正常流靠右；
   这样「标题+箭头」与「标题+提示+箭头」两种结构都正确。 */
.expander-header { justify-content: flex-start; }
.expander-header > span:first-child { margin-right: auto; }
.expander-header > span:not(.arrow):not(:first-child) { padding: 0 var(--sp-3) 0 0; }"""

hit = False
for o in (OLD, OLD2):
    if o in src:
        src = src.replace(o, NEW, 1)
        hit = True
        print("① 已替换旧规则")
        break
if not hit:
    print("① WARN 未精确命中，走正则")
    src2, n = re.subn(
        r'\.expander-header \{ justify-content: flex-start; \}\s*\n'
        r'\.expander-header > span:not\(\.arrow\) \{[^}]*\}',
        NEW, src, count=1)
    print(f"   正则替换 {n} 处")
    src = src2 if n else src

# 兜底：确保任何残留的 margin-left:auto 不再命中标题
bad = re.findall(r'\.expander-header[^{]*\{[^}]*margin-left:\s*auto[^}]*\}', src)
print(f"② 残留 margin-left:auto 的 expander 规则: {len(bad)}")
for b in bad:
    print("   ", b[:100])

# 同时清掉内联的过期 padding 写法（若有）
src = src.replace("font-size:0.75rem;color:var(--text-muted)\">点击展开</span>",
                  "font-size:0.75rem;color:var(--text-muted)\">点击展开</span>")

F.write_text(src, encoding="utf-8")
print()
print(f"OK: {n0} -> {len(src.encode('utf-8'))} 字节")
print("裸 { 块（必须 0）:", len(re.findall(r'(?m)^{', src)))
