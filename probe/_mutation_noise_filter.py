# -*- coding: utf-8 -*-
"""噪声过滤护栏的**变异自检**：把每条判据逐一打坏，护栏必须变红。

【防污染】本脚本可能被超时杀死（`finally` 不执行）⇒ 源文件会残留坏变异。
  因此：① 每轮结束**立即**校验 md5 已还原；② 装 SIGTERM/SIGINT 处理器尽力还原；
  ③ 落一份恢复锚到磁盘（`probe/_mutation_anchor_noise.json`），被杀后可手工还原。
"""
from __future__ import annotations

import hashlib
import json
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "wechat_ingest.py"
GUARD = "tests/test_wechat_ingest_noise.py"

_GATE = (
    "    if len(text) < MIN_BODY_CHARS:\n"
    "        print(\"    ! 正文过短(%d 字符 < %d),跳过: %s\" % (len(text), MIN_BODY_CHARS, title[:46]))\n"
    "        return \"short\"\n"
)
_NOISE = (
    "\n"
    "    # 【内容噪声关】2026-10-14 用户报障:招聘/活动通稿被当研报入库。\n"
    "    #   放在\"长度关\"之后:长度关挡的是空壳(0~40 字的预告),本关挡的是\n"
    "    #   **正文够长但内容不是投研**的(如 1162 字的【招聘】)。\n"
    "    _noise = noise_reason(title, text)\n"
    "    if _noise:\n"
    "        print(\"    ! 内容非投研(%s),跳过: %s\" % (_noise, title[:46]))\n"
    "        return \"noise\"\n"
)

MUTATIONS = [
    ("M1 招聘判据失效(if False)", '        if rb:\n            return "recruit(', '        if False:\n            return "recruit(', 1),
    ("M2 公告词表清空", "    for w in ANNOUNCE_TITLE:\n        if w in t:\n",
     "    for w in ():\n        if w in t:\n", 1),
    ("M3 实质阈值降为 0", "MIN_SUBSTANCE_CHARS = 150", "MIN_SUBSTANCE_CHARS = 0", 1),
    ("M4 通稿特征词门槛提高", "        if len(eb) >= 3:", "        if len(eb) >= 99:", 1),
    ("M5 免责剥离改回跨段正则", 're.compile(r"未经[^\\s]{0,6}许可")', 're.compile(r"未经.*许可")', 1),
    ("M6 实质计量不再去重复", "        elif cnt.get(k, 0) == 1:", "        elif True:", 1),
    ("M7 噪声关挪到长度关之前", _GATE + _NOISE, _NOISE + _GATE, 1),
    ("M8 skipped 不计 noise", '+ stats["noise"] + stats["nokey"]),', '+ stats["nokey"]),', 1),
    ("M9 结果文件去掉 noise 单列", '        "noise": stats["noise"],\n', '', 1),
]


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def run_guard() -> bool:
    """返回 True = 护栏变红（= 变异被击杀）。"""
    try:
        p = subprocess.run(
            [str(ROOT / "venv/Scripts/python.exe"), "-m", "pytest", GUARD,
             "-q", "--no-header", "-p", "no:warnings"],
            capture_output=True, text=True, timeout=300, cwd=str(ROOT))
    except subprocess.TimeoutExpired:
        return True
    out = p.stdout + p.stderr
    return ("failed" in out) or ("error" in out.lower())


def main() -> int:
    raw = TARGET.read_bytes()
    h0 = md5(raw)
    anchor = ROOT / "probe" / "_mutation_anchor_noise.json"
    anchor.write_text(json.dumps({"tq": None, "wechat_ingest.py": h0}), encoding="utf-8")
    print("[防污染] 恢复锚已写: probe/_mutation_anchor_noise.json (md5=%s)" % h0[:12])

    def _restore(*_a):
        TARGET.write_bytes(raw)
        same = md5(TARGET.read_bytes()) == h0
        print("\n[中断] 已尽力还原源文件: %s" % ("✔" if same else "✘ 失败！"), flush=True)
        raise SystemExit(2)

    for _s in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(_s, _restore)
        except Exception:
            pass

    print()
    print("=== 基线（应全绿）===")
    base_bad = run_guard()
    print("    基线变红: %s %s" % (base_bad, "← ★异常，先修基线" if base_bad else "✔"))
    print()
    print("=== 逐条变异 ===")
    killed = 0
    for tag, old, new, n in MUTATIONS:
        try:
            t = raw.decode("utf-8")
            got = t.count(old)
            if got != n:
                print("    %-28s 锚点命中 %d（期望 %d）—— 跳过" % (tag, got, n))
                continue
            TARGET.write_text(t.replace(old, new), encoding="utf-8", newline="")
            b = run_guard()
        finally:
            TARGET.write_bytes(raw)
        ok = md5(TARGET.read_bytes()) == h0
        if not ok:
            print("    [FATAL] 还原失败 —— 立即中止")
            _restore()
        killed += 1 if b else 0
        print("    %-28s %s" % (tag, "击杀 OK" if b else "★存活（护栏有漏洞）"))
    print()
    print("击杀 %d/%d" % (killed, len(MUTATIONS)))
    print("源文件 md5 复原:", md5(TARGET.read_bytes()) == h0)
    return 0 if killed == len(MUTATIONS) else 1


if __name__ == "__main__":
    sys.exit(main())
