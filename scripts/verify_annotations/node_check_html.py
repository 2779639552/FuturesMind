#!/usr/bin/env python
"""【QA 工具】抽取 HTML 内联 <script> 用 node --check 验证 JS 语法。

一次性 QA 工具,不参与任何运行路径。

用法:
    node_check_html.py <html 文件> [...]

对每个 HTML: 用正则 `<script[^>]*>(.*?)</script>`(DOTALL)提取内联脚本,
逐个写入临时 .js 文件后执行 `node --check`。语法错误会给出脚本序号与 node 报错。

返回码: 0 = 全部脚本语法通过; 1 = 有语法错误或 node 不可用。
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:  # Windows 下强制 UTF-8 输出,避免中文注释乱码
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

SCRIPT_RE = re.compile(r"<script[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE)


def check_html(path: str) -> tuple[bool, list[str]]:
    content = Path(path).read_text(encoding="utf-8", errors="replace")
    scripts = SCRIPT_RE.findall(content)
    problems: list[str] = []
    for i, js in enumerate(scripts, 1):
        stripped = js.strip()
        if not stripped:
            continue
        with tempfile.NamedTemporaryFile(
            "w", suffix=".js", encoding="utf-8", delete=False
        ) as f:
            f.write(stripped)
            tmp = f.name
        try:
            proc = subprocess.run(
                ["node", "--check", tmp],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            if proc.returncode != 0:
                problems.append(
                    f"  script #{i}: node --check 失败\n{proc.stdout}{proc.stderr}"
                )
        finally:
            Path(tmp).unlink(missing_ok=True)
    return (len(problems) == 0), problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="要检查的 HTML 文件")
    args = parser.parse_args()

    if shutil.which("node") is None:
        print("FAIL  node 不在 PATH 中,无法执行 JS 语法检查")
        return 1

    rc = 0
    for p in args.paths:
        if not Path(p).exists():
            print(f"FAIL  {p}: 文件不存在")
            rc = 1
            continue
        ok, problems = check_html(p)
        if ok:
            print(f"PASS  {p}: 全部内联 JS 语法通过")
        else:
            print(f"FAIL  {p}: 发现内联 JS 语法问题")
            for prob in problems:
                print(prob)
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
