# -*- coding: utf-8 -*-
"""当日无研报的品种行：标明「延续昨日观点」（用户 2026-10-21 补充要求）。

准确性处理：只有当"最新一期恰好是前一天"时才写"延续昨日观点"；
否则写"延续最近观点（YYYY-MM-DD）" —— 10-19 的 BR 最新是永安 10-18（昨日，符合），
但若某品种最近一期是 3 天前，仍写"昨日"就是错的，会再次被当成 bug 报上来。
"""
import io
from pathlib import Path

P = Path(r"D:\AgentSense\AgentSense\web_app.py")
CRLF = "\r\n"

s = io.open(str(P), "r", encoding="utf-8", newline="").read()

OLD = (
    "                _ops = \" ; \".join(f\"{x['source']}: {x['opinion']}\" for x in _e0 if x[\"opinion\"]) or \"—\"" + CRLF
    + "                return (f\"| {c} | {name_map.get(c, '')} | {_srcs} | {_dirs} | {_confs} | \"" + CRLF
    + "                        f\"当日无新研报；{_ops} | — | 0 | — |\")" + CRLF
)
NEW = (
    "                _ops = \" ; \".join(f\"{x['source']}: {x['opinion']}\" for x in _e0 if x[\"opinion\"]) or \"—\"" + CRLF
    + "                # 【2026-10-21 用户补充】当日没研报时要明确写「延续昨日观点」。" + CRLF
    + "                #   但只有「最新一期恰好是前一天」才配这个措辞；否则写具体日期，" + CRLF
    + "                #   免得某品种最近一期是 3 天前却标「昨日」，又被当成 bug 报上来。" + CRLF
    + "                _newest = max((x[\"date\"] for x in _e0), default=\"\")" + CRLF
    + "                try:" + CRLF
    + "                    from datetime import date as _dt, timedelta as _td" + CRLF
    + "                    _yday = (_dt.fromisoformat(date[:10]) - _td(days=1)).isoformat()" + CRLF
    + "                except Exception:" + CRLF
    + "                    _yday = \"\"" + CRLF
    + "                _head = (\"当日无新研报，延续昨日观点\" if _newest == _yday" + CRLF
    + "                         else f\"当日无新研报，延续最近观点（{_newest or '—'}）\")" + CRLF
    + "                return (f\"| {c} | {name_map.get(c, '')} | {_srcs} | {_dirs} | {_confs} | \"" + CRLF
    + "                        f\"{_head}；{_ops} | — | 0 | — |\")" + CRLF
)
assert s.count(OLD) == 1, "锚点 %d" % s.count(OLD)
s = s.replace(OLD, NEW, 1)

assert s.count("\n") == s.count(CRLF), "出现裸 LF"
io.open(str(P), "w", encoding="utf-8", newline="").write(s)
print("OK")
