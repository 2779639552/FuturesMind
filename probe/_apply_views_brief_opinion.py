# -*- coding: utf-8 -*-
"""统一精简「核心观点」：当日机构的完整综述也压成一句，才能和延续机构并排看全。

现象：华泰（当日）那格是"综述：…；利多：…；利空：…；交易要素…"整段，
被前端折叠成 3 行 + 「展开全文」，后面 5 家的观点全被藏在展开里 ⇒ 虽然"一一对齐"了，
但视觉上还是只看得到第一家。
"""
import io
from pathlib import Path

P = Path(r"D:\AgentSense\AgentSense\web_app.py")
CRLF = "\r\n"
s = io.open(str(P), "r", encoding="utf-8", newline="").read()

HELPER_ANCHOR = "def _latest_source_views(code: str, all_rows: list[dict], date: str, exclude_sources=()) -> list[dict]:" + CRLF
HELPER = (
    "def _brief_opinion(text) -> str:" + CRLF
    + '    """把一段研报观点压成「一句话核心观点」：优先取「综述：」后首句，否则取首句（≤60 字）。' + CRLF
    + "" + CRLF
    + "    总表每行要并排 N 家机构的观点，长文会被前端折叠成 3 行 + 「展开全文」，" + CRLF
    + "    后面的机构就全看不见了 —— 所以当日机构的完整综述也必须压短。" + CRLF
    + '    """' + CRLF
    + '    t = str(text or "").strip().replace("\\n", " ").replace("|", "/")' + CRLF
    + '    m = re.search(r"综述[:：]\\s*([^\\n。；]{2,60})", t)' + CRLF
    + "    if m:" + CRLF
    + "        return m.group(1).strip()" + CRLF
    + '    first = re.split(r"[。；;]", t)[0].strip()' + CRLF
    + "    return (first or t)[:60]" + CRLF
    + "" + CRLF
    + "" + CRLF
)
assert s.count(HELPER_ANCHOR) == 1, "helper 锚点 %d" % s.count(HELPER_ANCHOR)
s = s.replace(HELPER_ANCHOR, HELPER + HELPER_ANCHOR, 1)

OLD = '            _op_by_src[_s] = str(x.get("opinion") or "").strip().replace("\\n", " ").replace("|", "/")' + CRLF
NEW = '            _op_by_src[_s] = _brief_opinion(x.get("opinion"))' + CRLF
assert s.count(OLD) == 1, "用法锚点 %d" % s.count(OLD)
s = s.replace(OLD, NEW, 1)

assert s.count("\n") == s.count(CRLF)
io.open(str(P), "w", encoding="utf-8", newline="").write(s)
print("OK")
