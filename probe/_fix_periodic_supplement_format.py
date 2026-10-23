# -*- coding: utf-8 -*-
"""修正周报补充项的两处口径：
  1. 置信度必须与当日项同格式（当日是 "58%"，补充项原来直接吐 DB 的 0.68）；
  2. 方向必须取**该品种段落**的「方向」（多品种周报的行级方向是主品种的，直接用会张冠李戴）。
"""
import io
from pathlib import Path

TPL = Path(r"D:\AgentSense\AgentSense\web_app.py")

OLD_HELPER = '''        src = _normalize_source(r.get("source") or "")
        if not src or src in exclude_sources or src in seen:
            continue
        seen.add(src)
        picked.append((rd, src, r))
    picked.sort(key=lambda x: x[0], reverse=True)
    return picked
'''

NEW_HELPER = '''        src = _normalize_source(r.get("source") or "")
        if not src or src in exclude_sources or src in seen:
            continue
        seen.add(src)
        # 【口径】方向取该品种段落（多品种周报的行级方向是主品种的，直接用会张冠李戴）；
        #         置信度与当日项统一成百分比（当日由 _collect 格式化成 "58%"，这里原来吐 0.68）。
        seg = _daily_variety_segment(r.get("conclusion_md") or "", code)
        te = _parse_trade_elements(seg)
        direction = str(te.get("方向") or r.get("direction") or "中性").strip()
        try:
            cf = float(r.get("confidence"))
            conf = "%d%%" % (round(cf * 100) if cf <= 1 else round(cf))
        except Exception:
            conf = "—"
        op = _extract_key_opinion(seg, max_len=120).strip().replace("\\n", " ").replace("|", "/")
        picked.append({"date": rd, "source": src, "direction": direction, "confidence": conf, "opinion": op})
    picked.sort(key=lambda x: x["date"], reverse=True)
    return picked
'''

OLD_TRI = '''        for _rd, _src, _x in extra:
            triples.append((f"{_src}(周报{_rd[5:]})", str(_x.get("direction") or "中性"),
                            str(_x.get("confidence") or "—")))
'''
NEW_TRI = '''        for _x in extra:
            triples.append((f"{_x['source']}(周报{_x['date'][5:]})", _x["direction"], _x["confidence"]))
'''

OLD_OP = '''            for _rd, _src, _x in extra:
                _seg = _daily_variety_segment(_x.get("conclusion_md") or "", c)
                _op = _extract_key_opinion(_seg, max_len=120).strip().replace("\\n", " ").replace("|", "/")
                if _op:
                    views_tmp.append(f"{_src}(周报{_rd[5:]}): {_op}")
'''
NEW_OP = '''            for _x in extra:
                if _x["opinion"]:
                    views_tmp.append(f"{_x['source']}(周报{_x['date'][5:]}): {_x['opinion']}")
'''

raw = io.open(str(TPL), "r", encoding="utf-8", newline="").read()
out = raw
for old, new in ((OLD_HELPER, NEW_HELPER), (OLD_TRI, NEW_TRI), (OLD_OP, NEW_OP)):
    o = old.replace("\n", "\r\n")
    n = new.replace("\n", "\r\n")
    c = out.count(o)
    assert c == 1, "锚点命中 %d 次: %r" % (c, old[:60])
    out = out.replace(o, n, 1)

assert out.count("\n") == out.count("\r\n")
io.open(str(TPL), "w", encoding="utf-8", newline="").write(out)
print("OK")
