# -*- coding: utf-8 -*-
"""清理两处恒假的分支：nodes3 至少含 h3、nodes4 至少含 h4，length 判据永远不会成立。
   假护栏比没护栏更危险——看着"有降级"，实际到不了。"""
import io
from pathlib import Path

TPL = Path(r"D:\AgentSense\AgentSense\web_template.html")
PAIRS = [
    ("if (!nodes3.length || n !== h4) return;\r\n", "if (n !== h4) return;\r\n"),
    ("if (!nodes4.length) return;\r\n", ""),
]

raw = io.open(str(TPL), "r", encoding="utf-8", newline="").read()
out = raw
for old, new in PAIRS:
    assert out.count(old) == 1, "锚点命中 %d: %r" % (out.count(old), old)
    out = out.replace(old, new, 1)

assert out.count("\n") == out.count("\r\n"), "出现裸 LF"
io.open(str(TPL), "w", encoding="utf-8", newline="").write(out)
print("OK, bare_lf=0")
