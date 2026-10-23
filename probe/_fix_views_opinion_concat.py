# -*- coding: utf-8 -*-
"""观点列：LLM 观点存在时也要追加"其他机构最新一篇"的观点。

原逻辑只在 `view` 为空时拼 `views_tmp`（含 extra），一旦 llm_views 给了该品种一句话，
`extra` 的观点就整段丢掉 —— 结果发行方列有 6 家、观点列只有 1 家的话，三列对不上。
"""
import io
from pathlib import Path

P = Path(r"D:\AgentSense\AgentSense\web_app.py")
CRLF = "\r\n"

s = io.open(str(P), "r", encoding="utf-8", newline="").read()

OLD = (
    "        if not view or view == \"—\":" + CRLF
    + "            views_tmp = []" + CRLF
)
NEW = (
    "        # 【2026-10-21】LLM 观点只覆盖当天发研报的那几家；其他机构（含只发周报的永安）" + CRLF
    + "        #   必须**在 LLM 观点之后照样追加**，否则发行方列 6 家、观点列 1 家，三列对不上。" + CRLF
    + "        views_tmp = [view] if (view and view != \"—\") else []" + CRLF
    + "        if not view or view == \"—\":" + CRLF
)
assert s.count(OLD) == 1, "① 锚点 %d" % s.count(OLD)
s = s.replace(OLD, NEW, 1)

# 把 extra 的追加从 if 分支里提出来
OLD2 = (
    "            # 【2026-10-21】补充的本周周报观点：带机构与日期，避免被当成\"当日观点\"" + CRLF
    + "            for _x in extra:" + CRLF
    + "                if _x[\"opinion\"]:" + CRLF
    + "                    views_tmp.append(f\"{_x['source']}: {_x['opinion']}\")" + CRLF
    + "            view = \" ; \".join(v for v in views_tmp if v != \"—\") or \"—\"" + CRLF
)
NEW2 = (
    "            view = \" ; \".join(v for v in views_tmp if v and v != \"—\") or \"—\"" + CRLF
)
assert s.count(OLD2) == 1, "② 锚点 %d" % s.count(OLD2)
s = s.replace(OLD2, NEW2, 1)

# 在 if 分支结束后，统一追加 extra 并重算 view
OLD3 = (
    "            # 【2026-10-16】原先再把拼接结果截到 80 字 ⇒ 与上面的 40 字形成**两层叠加**截断，" + CRLF
    + "            #   实测把「利多：柴油上行支撑估值(逻辑…」硬断在括号中间。现不再截。" + CRLF
)
NEW3 = OLD3 + (
    "        # 其他机构「截至当日最新一篇」的观点（不分日报/周报/季报），统一追加在末尾" + CRLF
    + "        for _x in extra:" + CRLF
    + "            if _x[\"opinion\"]:" + CRLF
    + "                views_tmp.append(f\"{_x['source']}: {_x['opinion']}\")" + CRLF
    + "        view = \" ; \".join(v for v in views_tmp if v and v != \"—\") or \"—\"" + CRLF
)
assert s.count(OLD3) == 1, "③ 锚点 %d" % s.count(OLD3)
s = s.replace(OLD3, NEW3, 1)

assert s.count("\n") == s.count(CRLF), "出现裸 LF"
io.open(str(P), "w", encoding="utf-8", newline="").write(s)
print("OK")
