# -*- coding: utf-8 -*-
"""从 web_template.html 抽出主 <script> 块, 交给 node --check 做语法校验。
（此前只靠浏览器控制台发现语法错, 太晚; 静态校验能在改完立刻拦住。）"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
html = (ROOT / "web_template.html").read_text(encoding="utf-8")

# 取所有 <script> 里不带 src 的内联块, 拼成一个文件校验
blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
js = "\n;\n".join(blocks)
out = ROOT / "probe" / "_inline_main.js"
out.write_text(js, encoding="utf-8")
print(f"抽取 {len(blocks)} 个内联 script 块, 共 {len(js)} 字符 -> {out.name}")

node = r"C:\Users\lenovo\.workbuddy\binaries\node\versions\22.22.2-3\node.exe"
r = subprocess.run([node, "--check", str(out)], capture_output=True, text=True, encoding="utf-8", errors="replace")
if r.returncode == 0:
    print("JS_SYNTAX_OK")
else:
    print("JS_SYNTAX_FAIL")
    print((r.stderr or "")[:3000])
sys.exit(r.returncode)
