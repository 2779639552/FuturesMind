# -*- coding: utf-8 -*-
"""抽出 web_template.html 的内联 <script> 交给 node --check 做语法校验。"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
html = (ROOT / "web_template.html").read_text(encoding="utf-8")
blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, flags=re.S | re.I)
out = Path(__file__).resolve().parent / "_inline.js"
out.write_text("\n;\n".join(blocks), encoding="utf-8")
print("script blocks:", len(blocks), "chars:", sum(len(b) for b in blocks))

node = None
for cand in (r"C:\Users\lenovo\.workbuddy\binaries\node\versions\22.22.2-3\node.exe",
             r"C:\Program Files\nodejs\node.exe"):
    if Path(cand).exists():
        node = cand
        break
if not node:
    print("NO NODE"); sys.exit(1)
r = subprocess.run([node, "--check", str(out)], capture_output=True, text=True)
print("node --check exit:", r.returncode)
if r.stdout.strip():
    print("STDOUT:", r.stdout[:3000])
if r.stderr.strip():
    print("STDERR:", r.stderr[:3000])
