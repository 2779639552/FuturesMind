# -*- coding: utf-8 -*-
"""语法层自检：抽出 web_template.html 里的内联 <script> 块，逐个 node --check。

（项目四层验证的第 1 层：内联 JS 抽出后 node --check）
"""
import re
import subprocess
import tempfile
from pathlib import Path

TPL = Path(r"D:\AgentSense\AgentSense\web_template.html")
NODE = r"C:\Users\lenovo\.workbuddy\binaries\node\versions\22.22.2-3\node.exe"

html = TPL.read_text(encoding="utf-8", errors="replace")

# 抓所有 <script ...>...</script>
blocks = []
for m in re.finditer(r"<script\b([^>]*)>(.*?)</script>", html, re.S):
    attrs, body = m.group(1), m.group(2)
    if re.search(r"\bsrc\s*=", attrs):
        continue  # 外链，跳过
    if not body.strip():
        continue
    # 行号（便于报错定位）
    line = html[: m.start(2)].count("\n") + 1
    blocks.append((line, body))

print("内联 <script> 块数:", len(blocks))
bad = 0
tmpdir = Path(tempfile.mkdtemp(prefix="as_jscheck_"))
for i, (line, body) in enumerate(blocks):
    f = tmpdir / ("blk_%02d.mjs" % i)
    f.write_text(body, encoding="utf-8")
    r = subprocess.run([NODE, "--check", str(f)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    tag = "OK  " if r.returncode == 0 else "FAIL"
    if r.returncode != 0:
        bad += 1
    print("  [%s] 块#%d (HTML 起始行 %d, %d 字节)" % (tag, i, line, len(body)))
    if r.returncode != 0:
        for ln in (r.stderr or "").strip().splitlines()[:6]:
            print("        ", ln)

print("\n结论: %d 块语法 OK, %d 块失败" % (len(blocks) - bad, bad))
raise SystemExit(1 if bad else 0)
