# -*- coding: utf-8 -*-
"""观点列逐家标注「延续 MM-DD」（用户 2026-10-21：「当日没有研报时，要标明：延续昨日观点」）。

为什么整行标注不够：10-20/10-21 每个品种当天都至少有一家研报，一直不走"整行无研报"分支，
于是用户看到的永远没有"延续"字样 —— 但那几家的观点其实是 3 天前甚至 20 天前的。
⇒ 改为**逐家**标注：某家的观点不是当日发布，就在机构名后写（延续 MM-DD）。
   整行都没有当日研报时，仍保留行首「当日无新研报，延续昨日观点；」。
"""
import io
from pathlib import Path

P = Path(r"D:\AgentSense\AgentSense\web_app.py")
CRLF = "\r\n"
s = io.open(str(P), "r", encoding="utf-8", newline="").read()

# ① 记录每家的观点日期
OLD1 = (
    "        _op_by_src: dict[str, str] = {}" + CRLF
    + "        for x in it_list:" + CRLF
    + '            _s = _normalize_source(str(x.get("source") or "未知"))' + CRLF
    + "            if _s in _op_by_src:" + CRLF
    + "                continue" + CRLF
    + "            # ⚠ 清洗必须保留：\\n 会把 markdown 表格劈成两行；| 是单元格分隔符" + CRLF
    + '            _op_by_src[_s] = str(x.get("opinion") or "").strip().replace("\\n", " ").replace("|", "/")' + CRLF
    + "        for _x in extra:" + CRLF
    + '            _op_by_src.setdefault(_x["source"], _x["opinion"])' + CRLF
)
NEW1 = (
    "        _op_by_src: dict[str, str] = {}" + CRLF
    + "        _op_date: dict[str, str] = {}   # 该家观点的**实际发布日**，用于标「延续 MM-DD」" + CRLF
    + "        for x in it_list:" + CRLF
    + '            _s = _normalize_source(str(x.get("source") or "未知"))' + CRLF
    + "            if _s in _op_by_src:" + CRLF
    + "                continue" + CRLF
    + "            # ⚠ 清洗必须保留：\\n 会把 markdown 表格劈成两行；| 是单元格分隔符" + CRLF
    + '            _op_by_src[_s] = str(x.get("opinion") or "").strip().replace("\\n", " ").replace("|", "/")' + CRLF
    + '            _op_date[_s] = str(x.get("rdate") or date)[:10]' + CRLF
    + "        for _x in extra:" + CRLF
    + '            _op_by_src.setdefault(_x["source"], _x["opinion"])' + CRLF
    + '            _op_date.setdefault(_x["source"], _x["date"])' + CRLF
)
assert s.count(OLD1) == 1, "① 锚点 %d" % s.count(OLD1)
s = s.replace(OLD1, NEW1, 1)

# ② 生成观点段时带延续标注
OLD2 = '        views_tmp = [f"{t[0]}: {_op_by_src.get(t[0]) or \'—\'}" for t in triples]' + CRLF
NEW2 = (
    "        # 【2026-10-21】不是当天发的观点，逐家标（延续 MM-DD）——" + CRLF
    + "        #   否则 10-20/21 这种「每个品种都有当日研报」的日子，用户永远看不到延续提示。" + CRLF
    + "        views_tmp = []" + CRLF
    + "        for _t in triples:" + CRLF
    + "            _d = _op_date.get(_t[0], date)[:10]" + CRLF
    + '            _tag = "" if _d >= date[:10] else f"（延续 {_d[5:]}）"' + CRLF
    + '            views_tmp.append(f"{_t[0]}{_tag}: {_op_by_src.get(_t[0]) or \'—\'}")' + CRLF
)
assert s.count(OLD2) == 1, "② 锚点 %d" % s.count(OLD2)
s = s.replace(OLD2, NEW2, 1)

assert s.count("\n") == s.count(CRLF), "出现裸 LF"
io.open(str(P), "w", encoding="utf-8", newline="").write(s)
print("OK")
