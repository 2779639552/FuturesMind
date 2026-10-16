# -*- coding: utf-8 -*-
"""从恢复锚还原被变异脚本改坏（且未还原）的源文件。

【为什么需要】变异脚本可能被 SIGTERM / 超时**杀死**，此时 `finally` 不执行 ⇒
  源文件残留某个坏变异，而脚本自身"发现不了"（下次跑基线直接红，甚至误判为已击杀）。

用法：
    python probe/_restore_mutation_anchor.py            # 校验并还原
    python probe/_restore_mutation_anchor.py --check    # 只校验不写
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANCHOR = ROOT / "probe" / "_mutation_anchor.json"
BYTES = ROOT / "probe" / "_mutation_anchor_bytes.bin"
TARGETS = [ROOT / "quant_module.py", ROOT / "web_template.html"]


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def main() -> int:
    check = "--check" in sys.argv
    if not ANCHOR.exists() or not BYTES.exists():
        print("✘ 恢复锚不存在 —— 无法还原（只能从 backup/ 手工恢复）")
        return 2
    want = json.loads(ANCHOR.read_text(encoding="utf-8"))
    blob = BYTES.read_bytes()
    # 按路径排序拼接，与写入顺序一致
    ok, fixed = True, []
    off = 0
    for f in sorted(TARGETS, key=str):
        n = None
        for cand in TARGETS:
            if str(cand) == str(f):
                n = cand
        size = len(Path(f).read_bytes())
        chunk = blob[off:off + size]
        off += size
        cur = Path(f).read_bytes()
        if md5(cur) == want[str(f)]:
            print(f"  ✔ {f.name}: 与锚一致")
            continue
        ok = False
        if check:
            print(f"  ✘ {f.name}: 与锚**不一致**（残留变异）")
        else:
            Path(f).write_bytes(chunk)
            same = md5(Path(f).read_bytes()) == want[str(f)]
            print(f"  {'✔' if same else '✘'} {f.name}: 已还原（校验{'通过' if same else '失败'}）")
            fixed.append(f.name)
    print()
    print("结论：" + ("全部一致 ✔" if ok else (
        f"已还原 {fixed}" if not check else "存在不一致，请去掉 --check 执行还原")))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
