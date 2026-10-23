# -*- coding: utf-8 -*-
"""按用户口径重做：当日观点总表 = **截至当日，每家公司各自最新的一篇观点**（不分日报/周报/季报）。

用户原话：「不需要单独加周报，目前的当日观点中应该输出的是当日每个公司最新的观点，
不分日报周报等，如 10.19 发了周报就应该在当日中看到永安的观点」。

与上一版的区别：
  · 上一版：只在"本周有周报"时额外补一家，并在机构名后标 `(周报10-18)` —— 用户明确不要这种"单独加"。
  · 这一版：取数干脆**不看 report_type**，每家公司取 `publish_date < date` 的最新一篇并入当日观点；
    机构名正常显示（不标类型/日期），与当天发日报的机构平起平坐。
  · 当天已经发过的机构（it_list 里）优先，不会被更早的周报顶掉。
"""
import io
from pathlib import Path

TPL = Path(r"D:\AgentSense\AgentSense\web_app.py")

OLD_DEF_START = '''def _recent_periodic_view(code: str, all_rows: list[dict], date: str, days: int = 7,
                          exclude_sources=()) -> list[tuple[str, str, dict]]:
'''
# 用整段替换：从函数定义到下一个 def 之前
raw = io.open(str(TPL), "r", encoding="utf-8", newline="").read()
start = raw.index(OLD_DEF_START.replace("\n", "\r\n"))
end = raw.index("def _build_daily_views_table(", start)

NEW_FUNC = '''def _latest_source_views(code: str, all_rows: list[dict], date: str, exclude_sources=()) -> list[dict]:
    """该品种「截至 date、各发行方各自最新的一篇观点」（**不分日报/周报/季报**）。

    【用户口径 2026-10-21】「当日观点中应该输出的是当日每个公司最新的观点，不分日报周报等，
      如 10.19 发了周报就应该在当日中看到永安的观点」。
      所以这里**不看 report_type**：只看"这家公司到这天为止最新说了什么"。
    【参数】exclude_sources: 当天已经发过研报的机构（归一后）——它们由 it_list 承载，
            不能再用更早的周报顶掉当天日报。
    【返回】[{date, source, direction, confidence, opinion}]，按日期倒序。
    """
    cutoff = date[:10]
    best: dict[str, dict] = {}
    for r in all_rows:
        if r.get("status") not in ("done", "summarized"):
            continue
        rd = _report_date(r)
        if not rd or rd >= cutoff:
            continue
        rcodes = [c.strip().upper() for c in str(r.get("varieties") or "").split(",") if c.strip()] \\
            or [str(r.get("variety") or "").upper()]
        if code not in rcodes:
            continue
        src = _normalize_source(r.get("source") or "")
        if not src or src in exclude_sources:
            continue
        if src in best and rd <= best[src]["date"]:
            continue
        # 方向/置信度/观点一律取**该品种段落**：多品种研报的行级方向是主品种的，直接用会张冠李戴。
        seg = _daily_variety_segment(r.get("conclusion_md") or "", code)
        # 单品种研报的结论段标题往往不是「## BR 结论」（如「## 合成橡胶周报 结论」），
        # 段抽取会落空 ⇒ 单品种时退回全文；多品种时宁可留空，也不把别品种的观点张冠李戴。
        if not seg and len(rcodes) == 1:
            seg = r.get("conclusion_md") or ""
        te = _parse_trade_elements(seg)
        direction = str(te.get("方向") or r.get("direction") or "中性").strip()
        try:
            cf = float(r.get("confidence"))
            conf = "%d%%" % (round(cf * 100) if cf <= 1 else round(cf))
        except Exception:
            conf = "—"
        # 只取「综述」那一句：全文还含交易要素/附列信息，整段塞进总表会淹没其他机构。
        _m = re.search(r"综述[:：]\\s*([^\\n。；]{2,60})", seg)
        op = (_m.group(1).strip() if _m else _extract_key_opinion(seg, max_len=60).strip())
        op = op.replace("\\n", " ").replace("|", "/")
        best[src] = {"date": rd, "source": src, "direction": direction,
                     "confidence": conf, "opinion": op}
    return sorted(best.values(), key=lambda x: x["date"], reverse=True)


'''

out = raw[:start] + NEW_FUNC.replace("\n", "\r\n") + raw[end:]

# 调用点改名 + 去掉"周报MM-DD"标注
CALL_OLD = ('        extra = _recent_periodic_view(c, all_rows or [], date, 7, _seen_src) if (not r_ and all_rows) else []\r\n')
CALL_NEW = ('        # 【2026-10-21 用户口径】当日观点 = 截至当日每家各自最新一篇（不分日报/周报/季报）。\r\n'
            '        extra = _latest_source_views(c, all_rows or [], date, _seen_src) if (not r_ and all_rows) else []\r\n')
assert out.count(CALL_OLD) == 1
out = out.replace(CALL_OLD, CALL_NEW, 1)

TRI_OLD = ('        for _x in extra:\r\n'
           '            triples.append((f"{_x[\'source\']}(周报{_x[\'date\'][5:]})", _x["direction"], _x["confidence"]))\r\n')
TRI_NEW = ('        for _x in extra:\r\n'
           '            triples.append((_x["source"], _x["direction"], _x["confidence"]))\r\n')
assert out.count(TRI_OLD) == 1
out = out.replace(TRI_OLD, TRI_NEW, 1)

OP_OLD = ('            for _x in extra:\r\n'
          '                if _x["opinion"]:\r\n'
          '                    views_tmp.append(f"{_x[\'source\']}(周报{_x[\'date\'][5:]}): {_x[\'opinion\']}")\r\n')
OP_NEW = ('            for _x in extra:\r\n'
          '                if _x["opinion"]:\r\n'
          '                    views_tmp.append(f"{_x[\'source\']}: {_x[\'opinion\']}")\r\n')
assert out.count(OP_OLD) == 1, "观点拼接锚点命中 %d" % out.count(OP_OLD)
out = out.replace(OP_OLD, OP_NEW, 1)

# 注释里的旧表述
out = out.replace('# 【2026-10-21】本周有周报、但今天没发日报的机构 → 作为"补充发行方"并入三列。\r\n'
                  '        #   不加这段：只发周报的永安在总表里永远不出现（用户连续两次报障）。\r\n'
                  '        #   ⚠ 仅日报口径（r_ 为空）；周报/季报口径本来就只收本类型，不能混。\r\n',
                  '# 【2026-10-21 用户口径】截至当日、各家各自最新一篇观点并入三列（不分日报/周报/季报）。\r\n'
                  '        #   不加这段：只发周报的永安在「当日观点总表」里永远不出现（用户连续两次报障）。\r\n'
                  '        #   ⚠ 仅日报口径（r_ 为空）；周报/季报口径本来就只收本类型，不能混。\r\n', 1)

assert out.count("\n") == out.count("\r\n")
io.open(str(TPL), "w", encoding="utf-8", newline="").write(out)
print("OK")
