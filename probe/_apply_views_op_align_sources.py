# -*- coding: utf-8 -*-
"""观点列与发行方一一对齐（用户 2026-10-21：「核心观点部分应该与前面的发行方一一对齐」）。

问题：发行方列是 `华泰 / 中信建投 / 永安期货 / 国泰君安 / 东证 / 中信期货`（6 家），
      观点列却是「LLM 合并一句话（无机构名）+ 5 家分条（缺一家）」⇒ 段数与顺序都对不上。

改法：观点列严格按 triples（发行方）顺序生成，每家一段 `机构: 观点`；某家无观点写 `—` 占位。
      LLM 提炼的一句话只有在"该品种当天只有一家机构"时才有明确归属，才用它替换那一家；
      多机构时它是合并观点，挂给谁都是张冠李戴，弃用（各家自己的首句更准）。
"""
import io
from pathlib import Path

P = Path(r"D:\AgentSense\AgentSense\web_app.py")
CRLF = "\r\n"
s = io.open(str(P), "r", encoding="utf-8", newline="").read()

START = "        # 一句话核心观点:优先 LLM 提炼(之前模式,总表解析或二节兜底);缺失回退 items opinion 首句。" + CRLF
END = '        view = " ; ".join(v for v in views_tmp if v and v != "—") or "—"' + CRLF
i = s.index(START)
j = s.index(END, i) + len(END)

NEW = (
    "        # 【2026-10-21 用户要求】观点列必须与发行方**一一对齐**：每家一段、顺序一致、带机构名。" + CRLF
    + "        #   原实现是「LLM 合并观点在前(无机构名) + 其他机构在后」⇒ 发行方 6 家、观点 7 段且首段无归属。" + CRLF
    + "        _op_by_src: dict[str, str] = {}" + CRLF
    + "        for x in it_list:" + CRLF
    + '            _s = _normalize_source(str(x.get("source") or "未知"))' + CRLF
    + "            if _s in _op_by_src:" + CRLF
    + "                continue" + CRLF
    + "            # ⚠ 清洗必须保留：\\n 会把 markdown 表格劈成两行；| 是单元格分隔符" + CRLF
    + '            _op_by_src[_s] = str(x.get("opinion") or "").strip().replace("\\n", " ").replace("|", "/")' + CRLF
    + "        for _x in extra:" + CRLF
    + '            _op_by_src.setdefault(_x["source"], _x["opinion"])' + CRLF
    + "        # LLM 提炼的一句话只有「当天只有一家机构」时才有明确归属，才拿来替换；" + CRLF
    + "        # 多机构时它是合并观点，挂到任何一家都是张冠李戴，宁可不用。" + CRLF
    + '        _llm_v = (llm_views.get(c) or "").strip()' + CRLF
    + '        if _llm_v and _llm_v != "—" and len(triples) == 1:' + CRLF
    + "            _op_by_src[triples[0][0]] = _llm_v" + CRLF
    + '        views_tmp = [f"{t[0]}: {_op_by_src.get(t[0]) or \'—\'}" for t in triples]' + CRLF
    + '        view = " ; ".join(views_tmp) or "—"' + CRLF
)

s = s[:i] + NEW + s[j:]

# 无研报分支：同样保证每家一段（缺观点写 —）
OLD2 = '                _ops = " ; ".join(f"{x[\'source\']}: {x[\'opinion\']}" for x in _e0 if x["opinion"]) or "—"' + CRLF
NEW2 = '                _ops = " ; ".join(f"{x[\'source\']}: {x[\'opinion\'] or \'—\'}" for x in _e0) or "—"' + CRLF
assert s.count(OLD2) == 1, "② 锚点 %d" % s.count(OLD2)
s = s.replace(OLD2, NEW2, 1)

assert s.count("\n") == s.count(CRLF), "出现裸 LF"
io.open(str(P), "w", encoding="utf-8", newline="").write(s)
print("OK")
