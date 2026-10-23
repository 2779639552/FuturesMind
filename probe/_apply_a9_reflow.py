# -*- coding: utf-8 -*-
"""A9 卡内重排：曲线与相关系数矩阵左右并排，明细表放下面。

【为什么改】
搬过来的原布局是「曲线 786px（chart-half）+ 下方两栏（矩阵 / 明细）」，
在基本面页的整宽卡里，曲线右边会空出 786px，跟 A1 修过的观感问题一样。

【新布局】
┌─────────────┬─────────────┐
│ 多维度曲线   │ 相关系数矩阵  │   ← 各 50%，同一视线高度
├─────────────┴─────────────┤
│ 所选维度明细（整宽，列多）  │
└───────────────────────────┘
曲线**去掉 chart-half** —— 已经在 50% 的格子里，再挂就是 1/4 宽（A1 踩过）。
"""
from __future__ import annotations

import io
import os

P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web_template.html")
CRLF = "\r\n"

s = io.open(P, "r", encoding="utf-8", newline="").read()

OLD = (
    " <div class=\"chart-container chart-half\" id=\"chart-q-cmp\" style=\"height:360px\"></div>" + CRLF
    + " <div class=\"grid grid-2\" style=\"margin-top:14px\">" + CRLF
    + " <div>" + CRLF
    + " <div style=\"font-size:0.8125rem;font-weight:600;margin-bottom:6px\">" + CRLF
    + " 相关系数矩阵 <span class=\"badge badge-info\" id=\"q-cmp-corr-badge\"></span>" + CRLF
    + " </div>" + CRLF
    + " <div id=\"q-cmp-corr\" style=\"overflow-x:auto;font-size:0.75rem\">加载中...</div>" + CRLF
    + " </div>" + CRLF
    + " <div>" + CRLF
    + " <div style=\"font-size:0.8125rem;font-weight:600;margin-bottom:6px\">" + CRLF
    + " 所选维度明细 <span class=\"badge badge-info\" id=\"q-cmp-series-badge\"></span>" + CRLF
    + " </div>" + CRLF
    + " <div id=\"q-cmp-series\" style=\"overflow-x:auto;font-size:0.75rem\">加载中...</div>" + CRLF
    + " </div>" + CRLF
    + " </div>" + CRLF
)
NEW = (
    " <!-- 【2026-10-21】曲线与矩阵左右并排：曲线原来独占半宽、右边空 786px。" + CRLF
    + "      曲线**不挂 chart-half** —— 已经在 50% 格子里，再挂会变 1/4 宽。" + CRLF
    + "      矩阵紧跟曲线同一视线高度：改口径/天数时两者要一起看。" + CRLF
    + "      明细表列多（维度 × 日期），放整宽一行更合适。 -->" + CRLF
    + " <div class=\"grid grid-2\" style=\"gap:16px;align-items:start;margin-top:4px\">" + CRLF
    + " <div>" + CRLF
    + " <div style=\"font-size:0.8125rem;font-weight:600;margin-bottom:6px\">所选维度曲线</div>" + CRLF
    + " <div class=\"chart-container\" id=\"chart-q-cmp\" style=\"height:360px\"></div>" + CRLF
    + " </div>" + CRLF
    + " <div>" + CRLF
    + " <div style=\"font-size:0.8125rem;font-weight:600;margin-bottom:6px\">" + CRLF
    + " 相关系数矩阵 <span class=\"badge badge-info\" id=\"q-cmp-corr-badge\"></span>" + CRLF
    + " </div>" + CRLF
    + " <div id=\"q-cmp-corr\" style=\"overflow-x:auto;font-size:0.75rem\">加载中...</div>" + CRLF
    + " </div>" + CRLF
    + " </div>" + CRLF
    + " <div style=\"margin-top:14px\">" + CRLF
    + " <div style=\"font-size:0.8125rem;font-weight:600;margin-bottom:6px\">" + CRLF
    + " 所选维度明细 <span class=\"badge badge-info\" id=\"q-cmp-series-badge\"></span>" + CRLF
    + " </div>" + CRLF
    + " <div id=\"q-cmp-series\" style=\"overflow-x:auto;font-size:0.75rem\">加载中...</div>" + CRLF
    + " </div>" + CRLF
)
assert s.count(OLD) == 1, s.count(OLD)
s = s.replace(OLD, NEW, 1)
assert s.count("\n") == s.count(CRLF), "CRLF 被破坏"
io.open(P, "w", encoding="utf-8", newline="").write(s)
print("ok")
