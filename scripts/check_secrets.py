# -*- coding: utf-8 -*-
"""提交前密钥扫描：防止 API Key / 私钥 / 令牌被推上 GitHub。

【为什么需要】
2026-10-23 对仓库做过一次全历史审计：58 个提交、`.env` 从未入库、严格模式零命中。
但「这次没漏」不等于「以后不会漏」——只要有人把值写进某个 .py 或 .md，
`git add -A && push` 就会永久留在历史里。所以把检查前置到提交那一刻。

【用法】
  python scripts/check_secrets.py              # 扫全部已跟踪文件
  python scripts/check_secrets.py --staged     # 只扫暂存区（pre-commit 用）
  python scripts/check_secrets.py --history    # 扫全部历史提交（较慢，定期跑）
  python scripts/check_secrets.py --path a.py b.md
【退出码】0=干净；1=发现疑似密钥（可直接作为 pre-commit 的拦截条件）
"""
import argparse
import os
import re
import subprocess
import sys

# 只放"高置信度"模式，避免误报把人逼到 --no-verify。
PATTERNS = [
    ("OpenAI/DeepSeek 风格 key", re.compile(r"\bsk-[A-Za-z0-9_-]{24,}")),
    ("火山 Ark key", re.compile(r"\bark-[0-9a-fA-F][0-9a-fA-F-]{23,}")),
    ("GitHub Token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}")),
    ("AWS AK", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{10,}")),
    ("Google API Key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Slack Token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}")),
    ("私钥块", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY")),
    ("硬编码赋值", re.compile(
        r"(?i)\b(?:api[_-]?key|apikey|secret|access[_-]?token|auth[_-]?token|password|passwd)"
        r"\s*[:=]\s*['\"]([^'\"]{20,})['\"]")),
]

# 明显的占位/示例，不算泄漏
PLACEHOLDER = re.compile(
    r"(?i)your[_-]|example|placeholder|change[_-]?me|dummy|xxxx|<[^>]+>|\.\.\.|"
    r"^\$\{|^%\(|^os\.|^process\.|^env\.|^读取|^填入|test[_-]?token|fs-test")

SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz",
            ".woff", ".woff2", ".ttf", ".bin", ".pkl", ".db", ".sqlite", ".exe", ".dll"}
MAX_BYTES = 2 * 1024 * 1024


def is_binary(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return b"\x00" in f.read(8192)
    except OSError:
        return True


def mask(s: str) -> str:
    s = s.strip()
    return (s[:6] + "…" + f"(len={len(s)})") if len(s) > 8 else "***"


def scan_text(text: str, label: str):
    hits = []
    for name, rx in PATTERNS:
        for m in rx.finditer(text):
            raw = m.group(1) if rx.groups else m.group(0)
            if PLACEHOLDER.search(raw):
                continue
            line = text.count("\n", 0, m.start()) + 1
            hits.append((label, line, name, mask(raw)))
    return hits


def scan_paths(paths):
    hits = []
    for p in paths:
        if not os.path.isfile(p):
            continue
        if os.path.splitext(p)[1].lower() in SKIP_EXT:
            continue
        if os.path.getsize(p) > MAX_BYTES or is_binary(p):
            continue
        try:
            txt = open(p, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        hits += scan_text(txt, p)
    return hits


def tracked_files():
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout
    return [l for l in out.splitlines() if l]


def staged_files():
    out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                         capture_output=True, text=True).stdout
    return [l for l in out.splitlines() if l]


def scan_history():
    """逐提交 grep。命中只报 commit + 文件，不打印内容。"""
    revs = subprocess.run(["git", "rev-list", "--all"], capture_output=True, text=True).stdout.split()
    rx = "|".join(p.pattern for _, p in PATTERNS if not p.groups)
    found = set()
    for c in revs:
        r = subprocess.run(["git", "grep", "-I", "-l", "-E", rx, c, "--"],
                           capture_output=True, text=True)
        for line in r.stdout.splitlines():
            found.add(line)
    return sorted(found)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--staged", action="store_true", help="只扫暂存区")
    ap.add_argument("--history", action="store_true", help="扫全部历史提交")
    ap.add_argument("--path", nargs="*", help="指定文件")
    a = ap.parse_args()

    if a.history:
        print(f"扫描 {len(subprocess.run(['git','rev-list','--all'],capture_output=True,text=True).stdout.split())} 个历史提交…")
        found = scan_history()
        if found:
            print("⚠ 历史中发现疑似密钥：")
            for f in found:
                print("  ", f)
            print("\n注意：历史泄漏只能通过 rewrite history + 轮换密钥处理，删当前文件无效。")
            return 1
        print("✓ 历史无命中")
        return 0

    files = a.path or (staged_files() if a.staged else tracked_files())
    hits = scan_paths(files)
    if not hits:
        print(f"✓ 密钥扫描通过（{len(files)} 个文件）")
        return 0
    print(f"✗ 发现 {len(hits)} 处疑似密钥：")
    for path, line, name, masked in hits[:40]:
        print(f"  {path}:{line}  [{name}]  {masked}")
    if len(hits) > 40:
        print(f"  …另有 {len(hits)-40} 处")
    print("\n处理建议：改用 os.getenv / .env（已被 .gitignore 覆盖），并轮换已泄漏的 key。")
    print("确属误报可加注释 '# nosecret' 或改用 --no-verify（不推荐）。")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
