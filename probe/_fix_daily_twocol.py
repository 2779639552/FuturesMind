# -*- coding: utf-8 -*-
"""修正 _layoutDailyTwoCol 第四节收集：起点 h4 自身就是 H2，不能被自己的停止条件挡掉。"""
import io
from pathlib import Path

TPL = Path(r"D:\AgentSense\AgentSense\web_template.html")
OLD = (
    "const nodes4 = [];\r\n"
    "n = h4;\r\n"
    "while (n && n.tagName !== 'H2') { nodes4.push(n); n = n.nextElementSibling; }\r\n"
    "if (!nodes4.length) return;\r\n"
)
NEW = (
    "const nodes4 = [];\r\n"
    "n = h4;\r\n"
    "// ★ 起点 h4 自己就是 H2，停止条件必须放行「第一项」（`n !== h4`），否则一进来就收空 ⇒ 静默降级。\r\n"
    "while (n) {\r\n"
    "if (n !== h4 && n.tagName === 'H2') break;\r\n"
    "nodes4.push(n);\r\n"
    "n = n.nextElementSibling;\r\n"
    "}\r\n"
    "if (!nodes4.length) return;\r\n"
)

raw = io.open(str(TPL), "r", encoding="utf-8", newline="").read()
assert raw.count(OLD) == 1, "锚点命中 %d" % raw.count(OLD)
out = raw.replace(OLD, NEW, 1)
assert out.count("\n") == out.count("\r\n"), "出现裸 LF"
assert out.count("n !== h4 && n.tagName === 'H2'") == 1
io.open(str(TPL), "w", encoding="utf-8", newline="").write(out)
print("OK")
