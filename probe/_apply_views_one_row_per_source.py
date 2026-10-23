# -*- coding: utf-8 -*-
"""总表改成「每家机构单独一行」，让核心观点与发行方严格横向对齐。

用户第三次追问「核心观点的对齐怎么还没做」后才看清：发行方/方向/置信度在窄列里
天然是"每家一行"，而观点列是 `A: … ; B: … ; C: …` 一整段 → 视觉上永远对不齐。
真正能对齐的只有**每家占一个表格行**：
  · 品种/名称/头寸/研报数/是否有新研报 只在第一行写，其余行留空；
  · 观点列只放该家自己的一句（不再重复机构名，发行方列已经有了）。
"""
import io
from pathlib import Path

P = Path(r"D:\AgentSense\AgentSense\web_app.py")
CRLF = "\r\n"
s = io.open(str(P), "r", encoding="utf-8", newline="").read()

# ① 无当日研报（延续）分支
a = s.index("            if _e0:" + CRLF)
b = s.index('                        f"{_head}；{_ops} | — | 0 | — |")' + CRLF, a) + len('                        f"{_head}；{_ops} | — | 0 | — |")' + CRLF)
NEW0 = (
    "            if _e0:" + CRLF
    + "                # 【2026-10-21】每家一行：品种列第一家带「（延续昨日观点）」，观点只放该家一句。" + CRLF
    + "                _newest = max((x[\"date\"] for x in _e0), default=\"\")" + CRLF
    + "                try:" + CRLF
    + "                    from datetime import date as _dt, timedelta as _td" + CRLF
    + "                    _yday = (_dt.fromisoformat(date[:10]) - _td(days=1)).isoformat()" + CRLF
    + "                except Exception:" + CRLF
    + "                    _yday = \"\"" + CRLF
    + "                _mark = (\"（延续昨日观点）\" if _newest == _yday" + CRLF
    + "                         else f\"（延续{(_newest or '—')[5:]}）\")" + CRLF
    + "                _rows0 = []" + CRLF
    + "                for _i, _x in enumerate(_e0):" + CRLF
    + "                    _rows0.append(\"| %s | %s | %s | %s | %s | %s | %s | %s | %s |\" % (" + CRLF
    + "                        (c + _mark) if _i == 0 else \"\"," + CRLF
    + "                        name_map.get(c, \"\") if _i == 0 else \"\"," + CRLF
    + "                        _x[\"source\"], _x[\"direction\"], _x[\"confidence\"]," + CRLF
    + "                        _x[\"opinion\"] or \"—\"," + CRLF
    + "                        \"—\" if _i == 0 else \"\"," + CRLF
    + "                        \"0\" if _i == 0 else \"\"," + CRLF
    + "                        \"—\" if _i == 0 else \"\"," + CRLF
    + "                    ))" + CRLF
    + "                return \"\\n\".join(_rows0)" + CRLF
)
s = s[:a] + NEW0 + s[b:]

# ② 有当日研报分支
OLD2 = '        return f"| {c} | {name_map.get(c, \'\')} | {sources} | {dirs} | {confs} | {view} | {trade} | {n} | {\'✓\' if n > 0 else \'—\'} |"' + CRLF
NEW2 = (
    "        # 【2026-10-21 用户要求】每家机构**单独一行**：品种/名称/头寸/研报数只写第一行，" + CRLF
    + "        #   其余行留空 ⇒ 发行方、方向、置信度、核心观点横向严格对齐。" + CRLF
    + "        _rows_out = []" + CRLF
    + "        for _i, _t in enumerate(triples):" + CRLF
    + "            _rows_out.append(\"| %s | %s | %s | %s | %s | %s | %s | %s | %s |\" % (" + CRLF
    + "                c if _i == 0 else \"\"," + CRLF
    + "                name_map.get(c, \"\") if _i == 0 else \"\"," + CRLF
    + "                _t[0], _t[1], _t[2]," + CRLF
    + "                _op_by_src.get(_t[0]) or \"—\"," + CRLF
    + "                trade if _i == 0 else \"\"," + CRLF
    + "                str(n) if _i == 0 else \"\"," + CRLF
    + "                (\"✓\" if n > 0 else \"—\") if _i == 0 else \"\"," + CRLF
    + "            ))" + CRLF
    + "        return \"\\n\".join(_rows_out)" + CRLF
)
assert s.count(OLD2) == 1, "② 锚点 %d" % s.count(OLD2)
s = s.replace(OLD2, NEW2, 1)

assert s.count("\n") == s.count(CRLF), "出现裸 LF"
io.open(str(P), "w", encoding="utf-8", newline="").write(s)
print("OK")
