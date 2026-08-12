#!/usr/bin/env python
"""【QA 工具】归一化 AST 对比 HEAD —— 核验"只加注释/文档字符串、零逻辑改动"。

这是本次细粒度注释批次的主门禁脚本,一次性 QA 工具,不参与任何运行路径。

用法:
    venv/Scripts/python.exe scripts/verify_annotations/ast_normalize_compare.py [paths...]

未指定路径时,对 git 工作区相对 HEAD 有改动的所有 .py 做对比。

归一化规则(两端一致):
    ① 每个 Module/FunctionDef/AsyncFunctionDef/ClassDef 的 body 首语句若是
       docstring(Expr(Constant(str))),整体删除该节点 —— 这使"已标注文件修改
       docstring 内容"与"新文件新增 docstring"都归一化为等价;
    ② 其余所有 Constant 节点 str -> "" / bytes -> b""(f-string 是 JoinedStr,
       其 FormattedValue 表达式属于逻辑,原样保留;match 模式的字符串常量
       两端同样置空,不影响判等)。

注释本身不进 AST,所以"只加注释"的文件必然通过;任何一行代码改动都会在
dump 中出现差异而红牌。

返回码: 0 = 全绿; 1 = 有红牌(报告文件 + 首个差异 dump 行附近)。
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

try:  # Windows 下强制 UTF-8 输出,避免中文注释乱码
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


def _is_docstring_expr(stmt: ast.stmt) -> bool:
    """判断语句是否为 docstring 形态: 单独的字符串常量表达式语句。"""
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


class _Normalizer(ast.NodeTransformer):
    """一次性归一化: 删除各作用域首语句 docstring + 其余字符串常量置空。"""

    def _strip_docstring(self, body: list[ast.stmt]) -> list[ast.stmt]:
        return body[1:] if body and _is_docstring_expr(body[0]) else body

    def visit_Module(self, node: ast.Module) -> ast.Module:
        node = self.generic_visit(node)  # 先处理子节点(常量置空),再删 docstring
        node.body = self._strip_docstring(node.body)
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        node = self.generic_visit(node)
        node.body = self._strip_docstring(node.body)
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AsyncFunctionDef:
        node = self.generic_visit(node)
        node.body = self._strip_docstring(node.body)
        return node

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
        node = self.generic_visit(node)
        node.body = self._strip_docstring(node.body)
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.Constant:
        if isinstance(node.value, str):
            node.value = ""
        elif isinstance(node.value, bytes):
            node.value = b""
        return node


def normalize(source: str, filename: str) -> str:
    """解析并归一化源码,返回可判等的 dump 字符串。

    语法错误直接抛出 SyntaxError(由调用方转成红牌)。
    """
    tree = ast.parse(source, filename=filename)
    tree = _Normalizer().visit(tree)
    ast.fix_missing_locations(tree)
    return ast.dump(tree, include_attributes=False)


def head_content(path: str) -> str:
    """取 git HEAD 版本的文件内容;非跟踪文件返回 None。"""
    proc = subprocess.run(
        ["git", "show", f"HEAD:{path}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def check_one(path: str) -> tuple[bool, str]:
    """对比单个文件,返回 (是否通过, 说明)。"""
    work = Path(path)
    if not work.exists():
        return False, f"工作区不存在: {path}"
    base = head_content(path)
    if base is None:
        return False, f"HEAD 中不存在(新文件,不参与对比): {path}"

    try:
        work_src = work.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return False, f"工作区文件编码异常: {exc}"

    try:
        work_dump = normalize(work_src, path)
        base_dump = normalize(base, path)
    except SyntaxError as exc:
        return False, f"语法错误: {exc.filename}:{exc.lineno} {exc.msg}"

    if work_dump == base_dump:
        return True, "逻辑与 HEAD 一致(仅注释/docstring 差异)"

    # 定位首个差异 dump 行,便于人工复核
    w_lines, b_lines = work_dump.splitlines(), base_dump.splitlines()
    diff_at = next(
        (i for i, (a, b) in enumerate(zip(w_lines, b_lines)) if a != b),
        min(len(w_lines), len(b_lines)),
    )
    snippet = "\n".join(
        f"{'工作区' if j == diff_at else '      '} {line}"
        for j, line in enumerate(w_lines[max(0, diff_at - 3): diff_at + 4])
    )
    return False, f"逻辑与 HEAD 不一致,首个差异在 dump 第 {diff_at} 行附近:\n{snippet}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="要对比的 .py 路径;缺省=工作区改动文件")
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
        print("没有待对比的文件。")
        return 0

    fails: list[tuple[str, str]] = []
    for p in sorted(paths):
        ok, msg = check_one(p)
        if ok:
            print(f"PASS  {p}: {msg}")
        else:
            print(f"FAIL  {p}: {msg}")
            fails.append((p, msg))

    print(f"\n共 {len(paths)} 个文件,通过 {len(paths) - len(fails)},红牌 {len(fails)}。")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
