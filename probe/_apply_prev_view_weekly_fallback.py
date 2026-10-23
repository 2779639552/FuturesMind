# -*- coding: utf-8 -*-
"""当日观点总表：当天没有日报时，观点延续允许回退到最近一期周报/季报。

病根（用户报障：「近几日的当日观点总表中也没有永安的」）：
  `_prev_variety_view` 在日报口径下**显式排除周报/季报**（`elif rt in ("周报","季报"): continue`）。
  永安每周日发周报、工作日不发日报 ⇒ 一周里有六天它连"观点延续"都拿不到，
  整行落到全 —（发行方 —、方向 —、观点 —），用户看到的就是"总表里没有永安"。

改法：
  · 日报口径**优先**找日报（口径不混，原来的隔离仍在）；
  · 只有"当日及历史都没有日报"时才回退到最近一期周报/季报，并打 `carried=True`；
  · 总表文案在 carried 时带上**来源+类型**（"观点延续2026-10-18 永安期货周报（…）"），
    让人一眼看出这是谁的观点、哪天发的、什么类型 —— 不改变"当日研报数 0 / 无新研报"的事实列。
  · 周报/季报口径（rtype 非空）完全不受影响。
"""
import io
from pathlib import Path

TPL = Path(r"D:\AgentSense\AgentSense\web_app.py")

OLD = '''    r_norm = _rtype_norm(rtype)
    cands: list[dict] = []
    for r in all_rows:
        if r.get("status") not in ("done", "summarized"):  # 【2026-09-24】含 summarized(延续昨日回溯历史观点)
            continue
        rt = (r.get("report_type") or "").strip()
        # 口径隔离(与总结链路同口径):周期口径只收本类型;日报口径排除周报/季报、保留未知。
        if r_norm:
            if rt != r_norm:
                continue
        elif rt in ("周报", "季报"):
            continue
        rd = _report_date(r)
        if not rd or rd >= before_date:
            continue
        rcodes = [c.strip().upper() for c in str(r.get("varieties") or "").split(",") if c.strip()] \\
            or [str(r.get("variety") or "").upper()]
        if code not in rcodes:
            continue
        cands.append(r)
    if not cands:
        return None
'''

NEW = '''    r_norm = _rtype_norm(rtype)

    def _collect(allow_periodic: bool) -> list[dict]:
        out: list[dict] = []
        for r in all_rows:
            if r.get("status") not in ("done", "summarized"):  # 【2026-09-24】含 summarized(延续昨日回溯历史观点)
                continue
            rt = (r.get("report_type") or "").strip()
            # 口径隔离(与总结链路同口径):周期口径只收本类型;日报口径默认排除周报/季报、保留未知。
            if r_norm:
                if rt != r_norm:
                    continue
            elif not allow_periodic and rt in ("周报", "季报"):
                continue
            rd = _report_date(r)
            if not rd or rd >= before_date:
                continue
            rcodes = [c.strip().upper() for c in str(r.get("varieties") or "").split(",") if c.strip()] \\
                or [str(r.get("variety") or "").upper()]
            if code not in rcodes:
                continue
            out.append(r)
        return out

    cands = _collect(allow_periodic=False)
    carried = False
    if not cands and not r_norm:
        # 【2026-10-21 用户报障】"近几日的当日观点总表中也没有永安的"：
        #   永安只发周报(每周日)，而日报口径此前**显式排除周报** ⇒ 一周六天它连
        #   "观点延续"都拿不到，整家从总表消失。改为当日无日报时回退到最近一期
        #   周报/季报（文案会标明来源+类型+日期，口径不混，见 _build_daily_views_table）。
        cands = _collect(allow_periodic=True)
        carried = True
    if not cands:
        return None
'''

RET_OLD = '''    return {"date": _report_date(r), "direction": direction, "opinion": opinion, "trade": trade}
'''
RET_NEW = '''    return {"date": _report_date(r), "direction": direction, "opinion": opinion, "trade": trade,
            # 【2026-10-21】回退展示需要来源与类型：总表要写出"观点延续 10-18 永安期货周报"
            "source": _normalize_source(r.get("source") or ""),
            "report_type": (r.get("report_type") or "").strip(),
            "carried": carried}
'''

ROW_OLD = '''                view = f"{no_new}，观点延续{prev['date']}（{body_txt}）"
'''
ROW_NEW = '''                # 【2026-10-21】回退到周报/季报时，把来源与类型写进文案 —— 否则用户只看到
                #   "观点延续"，仍不知道这是谁的观点（报障原话"总表中也没有永安的"）。
                _carry_tag = ""
                if prev.get("carried"):
                    _cs = str(prev.get("source") or "").strip()
                    _ct = str(prev.get("report_type") or "").strip()
                    if _cs or _ct:
                        _carry_tag = " " + (_cs + _ct)
                view = f"{no_new}，观点延续{prev['date']}{_carry_tag}（{body_txt}）"
'''

raw = io.open(str(TPL), "r", encoding="utf-8", newline="").read()
out = raw
for old, new in ((OLD, NEW), (RET_OLD, RET_NEW), (ROW_OLD, ROW_NEW)):
    o = old.replace("\n", "\r\n")
    n = new.replace("\n", "\r\n")
    c = out.count(o)
    assert c == 1, "锚点命中 %d 次: %r" % (c, old[:60])
    out = out.replace(o, n, 1)

assert out.count("\n") == out.count("\r\n"), "web_app.py 应为 CRLF"
io.open(str(TPL), "w", encoding="utf-8", newline="").write(out)
print("OK")
