# -*- coding: utf-8 -*-
"""量一下 _src() 里 GTJA 控制条周边的真实字节 + 候选锚点命中数。"""
import sys
from pathlib import Path

sys.path.insert(0, r"D:\AgentSense\AgentSense")
sys.path.insert(0, r"D:\AgentSense\AgentSense\tests")

from test_page_tools_variety_state import _src, _gtja_tools_span

src = _src()
print("== 文件级统计 ==")
print("len", len(src), "| CRLF?", "\r\n" in src, "| LF count", src.count("\n"))

span = _gtja_tools_span(src)
print("span", span)
inside = src[span[0]:span[1]]
print("inside len", len(inside))
print("inside tail repr:", repr(inside[-120:]))
print("inside card count:", inside.count('class="card"'))

print("\n== 候选锚点（整文件） ==")
cands = [
    "  </div><!-- /#tab-gtja-tools :",
    "  </div><!-- /#tab-gtja-tools : 控制条到此处为止, 只含「①查询控制」一张矮卡 -->",
    "  </div><!-- /#tab-gtja-tools : 控制条到此处为止, 只含「①查询控制」一张矮卡 -->\n",
    "只含「①查询控制」一张矮卡",
    "只含「① 查询控制」一张矮卡",
    '<div id="gt-note"',
]
for c in cands:
    print("%3d  %r" % (src.count(c), c))

print("\n== 候选锚点（span 内部） ==")
for c in cands:
    print("%3d  %r" % (inside.count(c), c))

print("\n== span 内部尾部（用于插卡位置） ==")
for c in ['\n    <div id="gt-note"', '\n  </div>\n  </div>', '\n  </div>\n']:
    print("%3d  %r" % (inside.count(c), c))
