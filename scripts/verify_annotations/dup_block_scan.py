#!/usr/bin/env python
"""【QA 工具】重复代码块扫描 —— 核验注释批次没有引入代码复制。

这是本次细粒度注释批次的辅助门禁,一次性 QA 工具,不参与任何运行路径。

原理: 对每个 .py,用 tokenize 去掉注释、把字符串字面量统一为 "s" 得到
"规范化行序列",在滑动窗口 k=5 下找出"出现 >=2 次的连续 5 行"。合法的重复
(对称分支、12 策略薄包装、同类平台适配器)在 HEAD 中就存在,不算问题。

关键门禁 = 对比工作区与 HEAD 的重复窗口集合:
    工作区出现了、但 HEAD 没有的重复窗口 → 本次改动"复制了代码块"(上一轮
    注释 agent 曾把 10 语句整块复制进 commodity_debate),报告为红牌供人工删除。

另做全仓级断言: `def _run_tool_loop` 恰好 2 份
(commodity_analysts.py + sentiment_analyst.py),否则红牌。

返回码: 0 = 无新增重复、全仓 _run_tool_loop 恰为 2; 1 = 有异常。
"""
from __future__ import annotations

import argparse
import io
import subprocess
import sys
import tokenize
from pathlib import Path

try:  # Windows 下强制 UTF-8 输出,避免中文注释乱码
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

K = 5  # 滑动窗口长度


def norm_lines(source: str) -> list[tuple[int, str]]:
    """按行归一化: 去注释、字符串统一为 s。返回 [(原始行号, 规范化内容)]。"""
    result: list[tuple[int, str]] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        cur: list[str] = []
        cur_line = 1
        for tok in tokens:
            if tok.type == tokenize.COMMENT or tok.type == tokenize.NL:
                continue
            if tok.type == tokenize.ENCODING:
                continue
            if tok.type == tokenize.NEWLINE:
                if cur:
                    result.append((cur_line, " ".join(cur)))
                cur = []
                continue
            # 记录本逻辑行的起始行号: 以本行首个有效 token 为准
            if not cur:
                cur_line = tok.start[0]
            # 所有 STRING 归一为 s(含 f-string 前缀的 STRING token)
            if tok.type == tokenize.STRING:
                cur.append("s")
            else:
                cur.append(tok.string)
    except (tokenize.TokenError, IndentationError) as exc:
        # 交给调用方报告;这里的容错只保证扫描不崩
        print(f"  [warn] 规范化失败: {exc}")
        return []
    return result


def dup_windows(source: str) -> dict[tuple[str, ...], list[int]]:
    """给定源码,返回 {(5行窗口): [出现处原始行号, ...]},仅含出现 >=2 次的窗口。"""
    raw = norm_lines(source)
    if not raw:
        return {}
    lines, starts = [], []
    for i, (ln, content) in enumerate(raw):
        if content.strip():
            lines.append(content)
            starts.append(ln)
    windows: dict[tuple[str, ...], list[int]] = {}
    for i in range(0, len(lines) - K + 1):
        win = tuple(lines[i:i + K])
        if not any(win):  # 空窗口(理论上不会,已过滤)
            continue
        windows.setdefault(win, []).append(starts[i])
    return {w: pos for w, pos in windows.items() if len(pos) >= 2}


def head_source(path: str) -> str | None:
    proc = subprocess.run(
        ["git", "show", f"HEAD:{path}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return proc.stdout if proc.returncode == 0 else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="要扫描的 .py;缺省=工作区改动文件")
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
        print("没有待扫描的文件。")
    else:
        for p in sorted(paths):
            if not Path(p).exists():
                print(f"SKIP  {p}: 工作区不存在")
                continue
            base = head_source(p)
            if base is None:
                print(f"SKIP  {p}: HEAD 中不存在(新文件)")
                continue
            try:
                work_src = Path(p).read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                print(f"FAIL  {p}: 编码异常 {exc}")
                return 1
            w_dups = dup_windows(work_src)
            b_dups = dup_windows(base)
            new_dups = {w: pos for w, pos in w_dups.items() if w not in b_dups}
            if new_dups:
                print(f"FAIL  {p}: 新增 {len(new_dups)} 个重复代码块(HEAD 中不存在):")
                for win, pos in sorted(new_dups.items(), key=lambda kv: kv[1][0]):
                    print(f"      窗口起点行号 {pos},窗口首行: {win[0]!r}")
                return 1
            pre = sum(1 for w in w_dups if w in b_dups)
            print(f"PASS  {p}: 重复窗口 {len(w_dups)}(其中存量 {pre})个,无新增")

    # 全仓级断言: _run_tool_loop 恰好 2 份
    proc = subprocess.run(
        ["git", "grep", "-c", "def _run_tool_loop", "--", "*.py"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    total = 0
    for line in proc.stdout.splitlines():
        try:
            total += int(line.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            continue
    print(f"\n全仓 `def _run_tool_loop` 出现次数: {total}")
    if total != 2:
        print("FAIL  期望恰好 2 份(commodity_analysts + sentiment_analyst)")
        return 1
    print("PASS  `_run_tool_loop` 恰为 2 份")
    return 0


if __name__ == "__main__":
    sys.exit(main())
