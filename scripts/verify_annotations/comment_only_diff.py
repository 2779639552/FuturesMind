#!/usr/bin/env python
"""【QA 工具】注释-only diff 核验 —— 每条新增行只可能是注释/文档字符串/行尾注释。

这是辅助启发式(启发式说明见文末);"零逻辑改动"的权威门禁是
ast_normalize_compare.py。

用法:
    venv/Scripts/python.exe scripts/verify_annotations/comment_only_diff.py [paths...]

对每个 .py 跑 `git diff -U0 HEAD -- <path>`,解析 `+` 开头的"新增行",逐条判定:
    A. 去空白后以 `#` 或 `//` 开头            -> 独立注释,通过;
    B. 位于三引号文档字符串块内,或本行含三引号   -> 文档字符串,通过;
    C. 剥掉行尾 `# ...` 注释后与 HEAD 某旧行 strip 相等 -> 旧行+行尾注释,通过;
    D. 剥掉行尾注释后为空                     -> 纯注释残行,通过;
    E. 其余                                   -> 红牌。

启发式局限: 代码字符串里含 `#` 的行(如 a["#"])会被误判为红牌;遇到这种情况
以 ast_normalize_compare 的结论为准,不视为真红牌。

返回码: 0 = 无红牌; 1 = 有红牌。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

try:  # Windows 下强制 UTF-8 输出,避免中文注释乱码
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

TRAILING_COMMENT = re.compile(r"[\t ]+#.*$")


def _head_lines(path: str) -> set[str]:
    """HEAD 旧行集合: 同时保留"strip 原样"与"剥行尾注释后"两种形态,
    以便匹配"旧行本身带 # noqa 等行尾注释"再被追加中文注释的情况。"""
    proc = subprocess.run(
        ["git", "show", f"HEAD:{path}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        return set()
    lines = {ln.strip() for ln in proc.stdout.splitlines()}
    for ln in proc.stdout.splitlines():
        lines.add(TRAILING_COMMENT.sub("", ln).strip())
    return lines


def _count_dq(line: str) -> int:
    return len(re.findall(r'"""', line))


def _count_sq(line: str) -> int:
    return len(re.findall(r"'''", line))


def check_one(path: str, old_lines: set[str]) -> tuple[int, list[str]]:
    """返回 (红牌数, 红牌行列表)。"""
    proc = subprocess.run(
        ["git", "diff", "-U0", "HEAD", "--", path],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    reds: list[str] = []
    in_docstring = False
    for raw in proc.stdout.splitlines():
        if not raw.startswith("+"):
            continue
        line = raw[1:]  # 去掉 diff 前缀 +
        if raw.startswith("+++"):
            continue
        s = line.strip()
        if not s:
            continue
        if s.startswith("#") or s.startswith("//"):
            continue
        dq, sq = _count_dq(s), _count_sq(s)
        if in_docstring or dq or sq:
            in_docstring = in_docstring ^ (dq % 2 == 1) ^ (sq % 2 == 1)
            continue
        code = TRAILING_COMMENT.sub("", line).strip()
        if code in old_lines or code == "":
            continue
        reds.append(line)
    return len(reds), reds


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="要核验的 .py;缺省=工作区改动文件")
    args = parser.parse_args()

    if args.paths:
        paths = args.paths
    else:
        proc = subprocess.run(
            ["git", "diff", "--name-only", "HEAD", "--", "*.py"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        paths = [ln for ln in proc.stdout.splitlines() if ln.strip()]

    if not paths:
        print("没有待核验的文件。")
        return 0

    total_red = 0
    for p in sorted(paths):
        if not Path(p).exists():
            print(f"SKIP  {p}: 工作区不存在")
            continue
        if not p.endswith(".py"):
            print(f"SKIP  {p}: 非 .py(JS 用 node_check_html)")
            continue
        n, reds = check_one(p, _head_lines(p))
        if n:
            total_red += n
            print(f"FAIL  {p}: {n} 条疑似非注释新增行:")
            for r in reds:
                print(f"      + {r}")
        else:
            print(f"PASS  {p}: 所有新增行均为注释/文档字符串/行尾注释")

    print(f"\n红牌合计 {total_red}。注意: 此工具为启发式,若与 AST 门禁矛盾以 AST 为准。")
    return 1 if total_red else 0


if __name__ == "__main__":
    sys.exit(main())
