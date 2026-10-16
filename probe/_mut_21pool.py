"""变异测试(二进制读写, 绝不动行尾符): 验证 21 池护栏真能抓回归。

每个变异: 注入错误 → 跑指定测试 → 必须失败 → 二进制原样还原(md5 自校验)。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
PY = os.path.join(ROOT, "venv", "Scripts", "python.exe")

MUTS = [
    ("tradingagents/dataflows/commodity_futures.py",
     b'\n    "P",\r\n', b'\n    "Q",\r\n', "池中把 P 换成不存在的 Q", "tests/test_variety_pool.py"),
    ("price_fetcher.py",
     b'"P": "\xe6\xa3\x95\xe6\xa6\x88",', b'"P": "\xe6\xa3\x95\xe6\xa6\x88\xe6\xb2\xb9",', "P 实时名改成错误的中文键", "tests/test_variety_pool.py"),
    ("news_collector_bloomberg.py",
     b'"P": ["palm oil"', b'"PX": ["palm oil"', "彭博 P 关键词挪到 PX", "tests/test_pool_coverage_guard.py"),
    ("ingest_local_pdfs.py",
     b'"P": ("P", "\xe6\xa3\x95\xe6\xa6\x88\xe6\xb2\xb9"', b'"PX": ("P", "\xe6\xa3\x95\xe6\xa6\x88\xe6\xb2\xb9"', "本地PDF的 P 别名挪到 PX", "tests/test_pool_coverage_guard.py"),
]


def md5b(p: str) -> str:
    return hashlib.md5(open(p, "rb").read()).hexdigest()


killed = 0
ran = 0
for rel, old, new, label, testf in MUTS:
    p = os.path.join(ROOT, rel)
    if not os.path.isfile(p):
        print(f"[SKIP] 文件不存在: {rel}")
        continue
    raw = open(p, "rb").read()
    if old not in raw:
        print(f"[SKIP] 锚点未命中: {label}  ({rel})")
        continue
    before = md5b(p)
    ran += 1
    open(p, "wb").write(raw.replace(old, new, 1))
    try:
        r = subprocess.run([PY, "-m", "pytest", testf, "-q", "--no-header", "-p", "no:cacheprovider"],
                           capture_output=True, text=True, timeout=300, cwd=ROOT)
        failed = r.returncode != 0
        print(f"[{'KILLED' if failed else 'SURVIVED'}] {label}  (rc={r.returncode})")
        if failed:
            killed += 1
            for line in ((r.stdout or "") + (r.stderr or "")).splitlines():
                if "failed" in line.lower() or "assert" in line.lower():
                    print("      ", line.strip()[:140])
                    break
        else:
            print("       !! 变异未被发现 —— 护栏是假绿")
    finally:
        open(p, "wb").write(raw)  # 二进制原样还原, 行尾符零改动
        after = md5b(p)
        assert after == before, f"还原失败! {rel} {before}->{after}"
        print(f"       还原 md5 OK ({after[:12]})")

print(f"\n变异击杀 {killed}/{ran}")
sys.exit(0 if ran and killed == ran else 1)
