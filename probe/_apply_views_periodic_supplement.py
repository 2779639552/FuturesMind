# -*- coding: utf-8 -*-
"""当日观点总表：在「有日报」的品种行上，补充本周（7 天内）周报机构的观点。

为什么仅靠"回退"不够（上一版改动的缺口）：
  `_prev_variety_view` 只在**该品种当天完全没有研报**时才走。但 10-19~10-21 的 BR 有华泰
  日报 ⇒ it_list 非空 ⇒ 永安（10-18 周报）根本轮不到，总表那行永远只有华泰。
  用户连续两次报障（观点总览 + 当日总表）本质是同一件事：**只发周报的机构被日内日报挤掉**。

做法（只动第一节，不动 LLM 的二~四节）：
  · 日报口径下，若某品种**今天有日报**，额外把"本周内发过周报、但不是今天"的机构并入该行；
  · 机构名后标注 `(周报10-18)`，让"当日研报数 1 / 发行方 2 家"看起来是自洽的；
  · 方向/置信度列按同样顺序追加（保持三列一一对应）；观点列追加 `机构(周报MM-DD): 首句`；
  · 周报/季报口径（r_ 非空）完全不受影响。
"""
import io
from pathlib import Path

TPL = Path(r"D:\AgentSense\AgentSense\web_app.py")

HELPER_ANCHOR = '''def _build_daily_views_table(date: str, items: list[dict], rtype: str = "",
'''
HELPER_NEW = '''def _recent_periodic_view(code: str, all_rows: list[dict], date: str, days: int = 7,
                          exclude_sources=()) -> list[tuple[str, str, dict]]:
    """取该品种"本周内、但不是 date 当天"的周报（每源只留最新一期）。

    【为什么需要】只发周报的机构（永安每周日发）在「当日观点总表」里永远缺席：
        `_collect_daily_report_items` 按 `_report_date == date` 精确过滤，
        周报的 10-18 永远不等于工作日的 10-21。用户连续两次报障就是这件事。
    【参数】exclude_sources: 当天已发日报的机构名（归一后），避免同一家出现两次。
    【返回】[(日期, 来源, DB行)]，按日期倒序、每源一条。
    """
    from datetime import date as _date, timedelta as _td
    try:
        d0 = _date.fromisoformat(date[:10])
    except Exception:
        return []
    lo = (d0 - _td(days=days)).isoformat()
    seen: set[str] = set()
    picked: list[tuple[str, str, dict]] = []
    for r in all_rows:
        if r.get("status") not in ("done", "summarized"):
            continue
        if (r.get("report_type") or "").strip() != "周报":
            continue
        rd = _report_date(r)
        if not rd or rd >= date[:10] or rd < lo:
            continue
        rcodes = [c.strip().upper() for c in str(r.get("varieties") or "").split(",") if c.strip()] \\
            or [str(r.get("variety") or "").upper()]
        if code not in rcodes:
            continue
        src = _normalize_source(r.get("source") or "")
        if not src or src in exclude_sources or src in seen:
            continue
        seen.add(src)
        picked.append((rd, src, r))
    picked.sort(key=lambda x: x[0], reverse=True)
    return picked


def _build_daily_views_table(date: str, items: list[dict], rtype: str = "",
'''

OLD_TRIPLES = '''            _seen_src.add(s)
            triples.append((s, str(x.get("direction") or "中性"), str(x.get("confidence") or "—")))
        sources = " / ".join(t[0] for t in triples)
'''
NEW_TRIPLES = '''            _seen_src.add(s)
            triples.append((s, str(x.get("direction") or "中性"), str(x.get("confidence") or "—")))
        # 【2026-10-21】本周有周报、但今天没发日报的机构 → 作为"补充发行方"并入三列。
        #   不加这段：只发周报的永安在总表里永远不出现（用户连续两次报障）。
        #   ⚠ 仅日报口径（r_ 为空）；周报/季报口径本来就只收本类型，不能混。
        extra = _recent_periodic_view(c, all_rows or [], date, 7, _seen_src) if (not r_ and all_rows) else []
        for _rd, _src, _x in extra:
            triples.append((f"{_src}(周报{_rd[5:]})", str(_x.get("direction") or "中性"),
                            str(_x.get("confidence") or "—")))
        sources = " / ".join(t[0] for t in triples)
'''

OLD_VIEWS = '''            view = " ; ".join(v for v in views_tmp if v != "—") or "—"
'''
NEW_VIEWS = '''            # 【2026-10-21】补充的本周周报观点：带机构与日期，避免被当成"当日观点"
            for _rd, _src, _x in extra:
                _seg = _daily_variety_segment(_x.get("conclusion_md") or "", c)
                _op = _extract_key_opinion(_seg, max_len=120).strip().replace("\\n", " ").replace("|", "/")
                if _op:
                    views_tmp.append(f"{_src}(周报{_rd[5:]}): {_op}")
            view = " ; ".join(v for v in views_tmp if v != "—") or "—"
'''

raw = io.open(str(TPL), "r", encoding="utf-8", newline="").read()
out = raw
for old, new in ((HELPER_ANCHOR, HELPER_NEW), (OLD_TRIPLES, NEW_TRIPLES), (OLD_VIEWS, NEW_VIEWS)):
    o = old.replace("\n", "\r\n")
    n = new.replace("\n", "\r\n")
    c = out.count(o)
    assert c == 1, "锚点命中 %d 次: %r" % (c, old[:60])
    out = out.replace(o, n, 1)

assert out.count("\n") == out.count("\r\n"), "web_app.py 应为 CRLF"
io.open(str(TPL), "w", encoding="utf-8", newline="").write(out)
print("OK")
