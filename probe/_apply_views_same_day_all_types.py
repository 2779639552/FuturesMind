# -*- coding: utf-8 -*-
"""当日观点总表：「当日有研报」= 当日**任意类型**的研报（不只日报）。

用户报障：「但是我在1018的总结也没有看到」（永安 LC 锂产业链周报 2026-10-18）。

根因两条叠加：
1. `_collect_daily_report_items` 默认走日报口径，**排除周报/季报**；
   10-18 是周日，全库当天只有周报 ⇒ LC 的四份周报（华泰/国君/东证/永安）全被滤掉。
2. 滤空后走「延续」分支，而 `_latest_source_views` 取的是 `publish_date < 当日`，
   当天自己发的研报反而取不到 ⇒ 表里显示「LC（延续10-16）| 混沌天成」。

改法：给 `_collect_daily_report_items` 加 `include_all_types` 开关，
仅在「观点总表」（`_build_daily_views_table`）的日报口径下用它重取当日条目；
LLM 正文仍用调用方传入的日报口径 items，总结口吻不变。
"""
import io
import sys

P = r"D:\AgentSense\AgentSense\web_app.py"
CRLF = "\r\n"

src = io.open(P, "r", encoding="utf-8", newline="").read()
before = src

# ── ① 函数签名加开关 ────────────────────────────────────────────────
OLD1 = 'def _collect_daily_report_items(rows: list[dict], date: str, rtype: str = "") -> list[dict]:'
NEW1 = ('def _collect_daily_report_items(rows: list[dict], date: str, rtype: str = "",\r\n'
        '                                include_all_types: bool = False) -> list[dict]:')
assert src.count(OLD1) == 1, "① 签名命中 %d" % src.count(OLD1)
src = src.replace(OLD1, NEW1, 1)

# ── ② 文档补一句口径说明 ────────────────────────────────────────────
OLD2 = ('            类型口径(2026-09-07 拆分):日报口径(默认)只收非周报行(未知类型不排斥);' + CRLF
        + '            周报口径(rtype=\'周报\')只收周报行 —— 日报/周报总结互不混收。')
NEW2 = ('            类型口径(2026-09-07 拆分):日报口径(默认)只收非周报行(未知类型不排斥);' + CRLF
        + '            周报口径(rtype=\'周报\')只收周报行 —— 日报/周报总结互不混收。' + CRLF
        + '            include_all_types=True(2026-10-21):日报口径下**不再排除周报/季报**,' + CRLF
        + '            供「观点总表」按「当日有研报（不分类型）」取数;LLM 正文仍用默认口径。')
assert src.count(OLD2) == 1, "② 文档命中 %d" % src.count(OLD2)
src = src.replace(OLD2, NEW2, 1)

# ── ③ 类型过滤加开关 ───────────────────────────────────────────────
OLD3 = ('        elif rt_row in ("周报", "季报"):' + CRLF
        + '            continue' + CRLF)
NEW3 = ('        elif not include_all_types and rt_row in ("周报", "季报"):' + CRLF
        + '            # 【2026-10-21】观点总表要「当日任意类型」：include_all_types=True 时放行。' + CRLF
        + '            continue' + CRLF)
# 全文件有 2 处同样写法（另一处在预报收集链路），**只改本函数体内**那一处
_a = src.index('def _collect_daily_report_items(')
_b = src.index('\ndef _build_daily_summary_md(', _a)
_body = src[_a:_b]
assert _body.count(OLD3) == 1, "③ 函数体内命中 %d" % _body.count(OLD3)
src = src[:_a] + _body.replace(OLD3, NEW3, 1) + src[_b:]

# ── ④ 观点总表改用「当日任意类型」取数 ──────────────────────────────
OLD4 = ('    llm_trades = llm_trades or {}  # 【2026-09-14】与 llm_views 对称归一:两者都可缺省(None→空 dict)' + CRLF
        + '    by_code: dict[str, list[dict]] = {}' + CRLF)
NEW4 = ('    llm_trades = llm_trades or {}  # 【2026-09-14】与 llm_views 对称归一:两者都可缺省(None→空 dict)' + CRLF
        + '    # 【2026-10-21 报障「1018 的总结也没有看到永安 LC 周报」】' + CRLF
        + '    #   「当日有研报」必须是当日**任意类型**的研报，不是「当日有日报」：' + CRLF
        + '    #   10-18 是周日，当天只有周报 —— LC 有华泰/国君/东证/永安 四份周报，' + CRLF
        + '    #   日报口径把它们全滤掉 ⇒ items 空 ⇒ 落到「延续」拿 10-16 的混沌天成，' + CRLF
        + '    #   永安当天的锂产业链周报在**它自己发布的这一天**反而看不到。' + CRLF
        + '    #   ⚠ 只换「观点总表」的取数；LLM 正文仍用调用方传进来的日报口径 items。' + CRLF
        + '    if not _rtype_norm(rtype) and all_rows:' + CRLF
        + '        items = (_collect_daily_report_items(all_rows, date, "", include_all_types=True)' + CRLF
        + '                 or items)' + CRLF
        + '    by_code: dict[str, list[dict]] = {}' + CRLF)
assert src.count(OLD4) == 1, "④ 总表取数命中 %d" % src.count(OLD4)
src = src.replace(OLD4, NEW4, 1)

assert src != before
assert src.count("\n") == src.count(CRLF), "CRLF 被破坏"
io.open(P, "w", encoding="utf-8", newline="").write(src)
print("patched ok")
