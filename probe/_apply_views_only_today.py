# -*- coding: utf-8 -*-
"""口径收敛（用户 2026-10-21 14:19 最终版）：

「只显示当日有研报的发行商的观点，没有的不显示，一条都没有时再显示延续的观点」。

即：
  · 该品种当天**有**研报 → 发行方列 = 当天发研报的机构（通常 1~3 家），
    其他机构哪怕昨天/上周发过，也**不混进来**；
  · 该品种当天**一条研报都没有** → 走延续分支（行首「当日无新研报，延续昨日观点；」+
    各家最近观点）。

改法：把有研报分支里的 `extra = _latest_source_views(...)` 置空即可（无研报分支不动）。
"""
import io
from pathlib import Path

P = Path(r"D:\AgentSense\AgentSense\web_app.py")
CRLF = "\r\n"
s = io.open(str(P), "r", encoding="utf-8", newline="").read()

OLD = ('        # 【2026-10-21 用户口径】当日观点 = 截至当日每家各自最新一篇（不分日报/周报/季报）。' + CRLF
       + '        extra = _latest_source_views(c, all_rows or [], date, _seen_src) if (not r_ and all_rows) else []' + CRLF)
NEW = ('        # 【2026-10-21 用户口径 · 最终版】当日观点**只显示当天发研报的发行商**：' + CRLF
       + '        #   「没有的不显示，一条都没有时再显示延续的观点」。' + CRLF
       + '        #   所以这里不再并入"各家最新"——extra 恒为空（无研报分支另走延续，见下方 _e0）。' + CRLF
       + '        extra: list[dict] = []' + CRLF)
assert s.count(OLD) == 1, "锚点 %d" % s.count(OLD)
s = s.replace(OLD, NEW, 1)

assert s.count("\n") == s.count(CRLF), "出现裸 LF"
io.open(str(P), "w", encoding="utf-8", newline="").write(s)
print("OK")
