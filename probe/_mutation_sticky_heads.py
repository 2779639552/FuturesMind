# -*- coding: utf-8 -*-
"""变异自检：表头吸顶的 5 条护栏是否真能抓住"改回去"。

每个变异：改 web_template.html → 跑 tests/test_sticky_table_heads.py → 必须变红 → 复原。
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

SRC = Path("web_template.html")
TEST = "tests/test_sticky_table_heads.py"
ORIG = SRC.read_bytes()
ORIG_MD5 = hashlib.md5(ORIG).hexdigest()
TEXT = ORIG.decode("utf-8").replace("\r\n", "\n")
PY = sys.executable

MUTS = [
    ("M1 技术面容器去掉 table-scroll 标记（会被二次包装）",
     '<div class="table-scroll" style="overflow:auto;max-height:calc(100vh - 300px)">',
     '<div style="overflow:auto;max-height:calc(100vh - 300px)">'),
    ("M2 去掉 position:sticky（只剩 top/背景）",
     "  position: sticky;\n  top: 0;",
     "  position: static;\n  top: 0;"),
    ("M3 去掉实色背景（数据会透上来）",
     "  background: var(--bg-card);\n  /* 用 inset 阴影代替",
     "  /* 用 inset 阴影代替"),
    ("M4 恢复 <tr> 级 sticky（与 th 规则打架）",
     '<tr style="color:var(--text-muted);font-size:0.75rem">',
     '<tr style="position:sticky;top:0;color:var(--text-muted);font-size:0.75rem">'),
    ("M5 wrapTable 不再跳过 .table-scroll",
     "if (p.classList && p.classList.contains('table-scroll')) return;",
     "if (false) return;"),
]


def run() -> tuple[bool, str]:
    r = subprocess.run([PY, "-m", "pytest", TEST, "-q", "--no-header", "-p", "no:warnings", "--tb=no"],
                       capture_output=True, text=True)
    tail = (r.stdout or "").strip().splitlines()
    return r.returncode == 0, tail[-1] if tail else ""


killed = survived = skipped = 0
try:
    for title, old, new in MUTS:
        n = TEXT.count(old)
        if n != 1:
            print("  ⚠ 跳过（锚点命中 %d）：%s" % (n, title))
            skipped += 1
            continue
        SRC.write_bytes(TEXT.replace(old, new, 1).replace("\n", "\r\n").encode("utf-8"))
        ok, last = run()
        print(("  ✗ 存活：" if ok else "  ✔ 击杀：") + title + "   [" + last + "]")
        killed += 0 if ok else 1
        survived += 1 if ok else 0
finally:
    SRC.write_bytes(ORIG)
    now = hashlib.md5(SRC.read_bytes()).hexdigest()
    print("\n  md5 复原: %s" % ("✔ 一致" if now == ORIG_MD5 else "★不一致"))

print("  合计：击杀 %d / 存活 %d / 跳过 %d" % (killed, survived, skipped))
sys.exit(1 if (survived or skipped) else 0)
