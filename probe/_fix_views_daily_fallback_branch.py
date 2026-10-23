# -*- coding: utf-8 -*-
"""当日观点总表：无研报分支也输出「各家最新」，并跳过"上传"这类非机构来源。

配套 _apply_views_latest_per_source_daily.py（已把有研报分支改成各家最新）：
  · 10-19 的 BR 当天没有任何研报 ⇒ 走的是 `if not it_list` 的"观点延续"老分支，
    只挑最近一份（而且多半是别家的日报），永安依旧缺席。
  · 该分支同样改为 `_latest_source_views` 的多家并列。
  · 来源为「上传/本地导入/未知」的是手工导入占位，不是机构名，不进"发行方"列。
"""
import io
from pathlib import Path

P = Path(r"D:\AgentSense\AgentSense\web_app.py")
CRLF = "\r\n"

s = io.open(str(P), "r", encoding="utf-8", newline="").read()

# ① 跳过非机构来源（只在 _latest_source_views 内替换，避免误伤别处同名判断）
a = s.index("def _latest_source_views")
b = s.index("def _build_daily_views_table(", a)
body = s[a:b]
old1 = "if not src or src in exclude_sources:"
assert body.count(old1) == 1, "① 锚点 %d" % body.count(old1)
body = body.replace(
    old1,
    'if not src or src in exclude_sources or src in ("上传", "本地导入", "未知"):',
    1,
)
s = s[:a] + body + s[b:]

# ② 无研报分支：插入到 `if not it_list:` 之后
old2 = "        if not it_list:" + CRLF
assert s.count(old2) == 1, "② 锚点 %d" % s.count(old2)
ins = (
    "            # 【2026-10-21 用户口径】当日无新研报时同样输出「截至当日各家最新一篇」，" + CRLF
    + "            #   而不是只挑最近一份做「观点延续」（那样永安这类周报机构会被别家的日报顶掉）。" + CRLF
    + "            _e0 = _latest_source_views(c, all_rows or [], date, ()) if (not r_ and all_rows) else []" + CRLF
    + "            if _e0:" + CRLF
    + '                _srcs = " / ".join(x["source"] for x in _e0)' + CRLF
    + '                _dirs = " / ".join(x["direction"] for x in _e0)' + CRLF
    + '                _confs = " / ".join(x["confidence"] for x in _e0)' + CRLF
    + '                _ops = " ; ".join(f"{x[\'source\']}: {x[\'opinion\']}" for x in _e0 if x["opinion"]) or "—"' + CRLF
    + "                return (f\"| {c} | {name_map.get(c, '')} | {_srcs} | {_dirs} | {_confs} | \"" + CRLF
    + "                        f\"当日无新研报；{_ops} | — | 0 | — |\")" + CRLF
)
s = s.replace(old2, old2 + ins, 1)

assert s.count("\n") == s.count(CRLF), "出现裸 LF"
io.open(str(P), "w", encoding="utf-8", newline="").write(s)
print("OK")
