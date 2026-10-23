# -*- coding: utf-8 -*-
"""前端外显「这一所这次没采到，用的是上次数据」。

后端已经把 `stale / fetch_error / date` 放进 overview.exchanges，
但卡片只判 `product_count == 0`（旧逻辑），有快照兜底时 product_count 正常，
于是「沿用了昨天的数据」这件事对用户完全不可见 —— 正是这次报障的形态。
"""
from __future__ import annotations

import io
import os

P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web_template.html")
CRLF = "\r\n"

s = io.open(P, "r", encoding="utf-8", newline="").read()

OLD = (
    " const noData = !e.product_count;" + CRLF
    + " const noPool = !mine.length && e.product_count > 0;" + CRLF
    + " return `<div class=\"wt-excard\"${noData ? ' style=\"border-color:var(--red)\"' : ''}>"
)
NEW = (
    " const noData = !e.product_count;" + CRLF
    + " const noPool = !mine.length && e.product_count > 0;" + CRLF
    + " // 【2026-10-21】采集失败但有上次快照时 product_count 仍 > 0 ⇒ 旧判据看不出问题。" + CRLF
    + " //   用户只会看到日期不动的旧数据，以为是最新的（本次报障就是这个形态）。" + CRLF
    + " const stale = !!e.stale;" + CRLF
    + " const _warn = noData || stale;" + CRLF
    + " return `<div class=\"wt-excard\"${_warn ? ' style=\"border-color:var(--red)\"' : ''}>"
)
assert s.count(OLD) == 1, s.count(OLD)
s = s.replace(OLD, NEW, 1)

OLD2 = ' ${noData ? \'<div class="s" style="color:var(--red)">⚠ 当日无数据（日报未发布或采集为空壳）</div>\' : \'\'}'
NEW2 = (
    ' ${noData ? \'<div class="s" style="color:var(--red)">⚠ 当日无数据（日报未发布或采集为空壳）</div>\' : \'\'}\n'
    ' ${stale ? `<div class="s" style="color:var(--red)">⚠ 本次采集失败，沿用 ${e.date || \'上次\'} 的数据</div>` : \'\'}'
)
assert s.count(OLD2) == 1, s.count(OLD2)
s = s.replace(OLD2.replace("\n", CRLF), NEW2.replace("\n", CRLF), 1)

assert s.count("\n") == s.count(CRLF), "CRLF 被破坏"
io.open(P, "w", encoding="utf-8", newline="").write(s)
print("ok")
