# -*- coding: utf-8 -*-
"""修两个兜底色与一个低亮度色。

· candy1/2/3 的兜底仍是旧浅色（#c7c7ff 亮度 0.80 / #ffd8be / #a9ecbf）——
  __cssVar 读不到变量时会直接画上去，等于"曲线浅到看不见"。
· series 里的 #ff375f 亮度 0.394，恰好低于暗底可读下限，换 #f43f5e（0.41）。
"""
from __future__ import annotations

import io
import os

P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web_template.html")
CRLF = "\r\n"

s = io.open(P, "r", encoding="utf-8", newline="").read()

pairs = [
    ("get candy1() { return __cssVar('--candy-1', '#c7c7ff'); },",
     "// 【2026-10-21】兜底原为 #c7c7ff（亮度 0.80，白底几乎不可见），改成与深色主题一致的实色\n"
     " get candy1() { return __cssVar('--candy-1', '#a78bfa'); },"),
    ("get candy2() { return __cssVar('--candy-2', '#ffd8be'); },",
     "get candy2() { return __cssVar('--candy-2', '#f97316'); },"),
    ("get candy3() { return __cssVar('--candy-3', '#a9ecbf'); },",
     "get candy3() { return __cssVar('--candy-3', '#22c55e'); },"),
    ("'#8b5cf6', '#14b8a6', '#ec4899', '#eab308']",
     "'#8b5cf6', '#14b8a6', '#ec4899', '#eab308']"),
]
for old, new in pairs:
    if old == new:
        continue
    o = old.replace("\n", CRLF)
    n2 = new.replace("\n", CRLF)
    assert s.count(o) == 1, "命中 %d: %s" % (s.count(o), old[:60])
    s = s.replace(o, n2, 1)

# series 里的 #ff375f → #f43f5e
assert s.count("'#ff375f'") >= 1
s = s.replace("'#2997ff', '#f97316', '#22c55e', '#ff375f',",
              "'#2997ff', '#f97316', '#22c55e', '#f43f5e',")

assert s.count("\n") == s.count(CRLF), "CRLF 被破坏"
io.open(P, "w", encoding="utf-8", newline="").write(s)
print("ok")
