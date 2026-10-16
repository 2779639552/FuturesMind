# -*- coding: utf-8 -*-
"""变异自检：给 tests/test_weread_collector.py 的护栏喂坏样本，确认它们**真会红**。

为什么必须做：守卫脚本最常见的失败模式是**永真**（断言写得太宽 / 锚点漂了 /
断言命中注释）—— 自己永远绿，等于没有守卫。这里逐条把生产代码改坏，跑守卫，
必须变红；跑完**原样还原并校验 md5**。

⚠️ 两个 EOL 坑（本项目已踩过多次）：
   · 多行锚点在 **CRLF 文件**里会命中 0 次（`\\n` 与 `\\r\\n` 不匹配）
     ⇒ 统一在 `\\n` 域内做替换，写回时按目标文件的原 EOL 还原。
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / "venv" / "Scripts" / "python.exe"
GUARD = "tests/test_weread_collector.py"

COLLECTOR = ROOT / "wechat_weread_collector.py"
SCHEDULER = ROOT / "scheduler.py"

# (说明, 文件, [(old, new), ...])
MUTATIONS = [
    ("M1 原文链接回退成模板固定短链（真实事故的复现）", COLLECTOR, [
        ('m = re.search(r\'var\\s+msg_link\\s*=\\s*"([^"]+)"\', html)',
         'm = None  # 故意不用 msg_link'),
    ]),
    ("M2 URL 不还原 &amp;（链接带 HTML 实体）", COLLECTOR, [
        ('url = m.group(1).replace("&amp;", "&").strip()',
         'url = m.group(1).strip()'),
    ]),
    ("M3 只读第一层 reviews，不展开 subReviews（漏子文章）", COLLECTOR, [
        ("    for rv in (payload.get(\"reviews\") or []):\n"
         "        push_t = int(rv.get(\"createTime\") or 0)\n"
         "        for sr in (rv.get(\"subReviews\") or []):",
         "    for rv in (payload.get(\"reviews\") or []):\n"
         "        push_t = int(rv.get(\"createTime\") or 0)\n"
         "        for sr in ([] if True else (rv.get(\"subReviews\") or [])):"),
    ]),
    ("M4 inbox 改成另一个文件（下游会静默收不到）", COLLECTOR, [
        ('F_INBOX = WECHAT_DIR / "wechat_inbox.jsonl"',
         'F_INBOX = WEREAD_DIR / "weread_inbox_typo.jsonl"'),
    ]),
    ("M5 书架发现退回裸 /shelf/sync（私有域下不是 JSON）", COLLECTOR, [
        ('r = _get("/web/shelf/sync", {}, timeout=60)',
         'r = _get("/shelf/sync", {}, timeout=60)'),
    ]),
    ("M6 正文过短也判 ok（成功率虚高）", COLLECTOR, [
        ('return {"ok": bool(text), "text": text, "url": url, "title": title,\n'
         '            "chars": len(text), "raw_chars": raw_chars, "how": how or "未提取到",',
         'return {"ok": True, "text": text, "url": url, "title": title,\n'
         '            "chars": len(text), "raw_chars": raw_chars, "how": how or "未提取到",'),
    ]),
    ("M7 调度里的读书通道脚本名被改坏（该路会整条失效）", SCHEDULER, [
        # 用**单行**锚点：多行锚点对缩进极敏感（本项目多次踩过）。
        ('["wechat_weread_collector.py", "--daily"], 1800)',
         '["nonexistent_weread_script.py"], 1800)'),
    ]),
    ("M8 计数去掉 mtime 闸（会读到上一轮的旧数字）", SCHEDULER, [
        # 锚点必须**限定在 _wechat_weread_count 里**：单看 `if os.path.exists(p) and
        # os.path.getmtime(p) >= since:` 会在 _wechat_collect_count 里也命中一次。
        ('    p = os.path.join(WEREAD_STATE_DIR, "mp_collect_result.json")\n'
         '    try:\n'
         '        if os.path.exists(p) and os.path.getmtime(p) >= since:',
         '    p = os.path.join(WEREAD_STATE_DIR, "mp_collect_result.json")\n'
         '    try:\n'
         '        if os.path.exists(p):'),
    ]),
    ("M9 告警只报合集一路（看不出读书通道的新增）", SCHEDULER, [
        ('f"读书通道新发现 {w_collected} 篇"',
         'f""'),
    ]),
]


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def run_guard() -> tuple[bool, str]:
    p = subprocess.run([str(PY), "-m", "pytest", GUARD, "-q", "--no-header"],
                       capture_output=True, text=True, timeout=600, cwd=str(ROOT))
    tail = [l for l in p.stdout.splitlines()
            if "passed" in l or "failed" in l or "error" in l]
    return p.returncode != 0, (tail[-1] if tail else "(无摘要)")


def main() -> int:
    # 备份原件（内存 + 字节），结束时逐字节还原
    originals: dict[Path, bytes] = {}
    for f in {COLLECTOR, SCHEDULER}:
        originals[f] = f.read_bytes()
    h0 = {f: md5(b) for f, b in originals.items()}

    ok, summary = run_guard()
    print("=" * 76)
    print("变异自检：护栏必须被坏样本**击杀**")
    print("=" * 76)
    print(f"[基线] 未变异时 {summary}  ->  {'✔ 基线绿' if not ok else '✘ 基线就是红的，先修基线'}")

    results = []
    try:
        for name, target, reps in MUTATIONS:
            raw = originals[target]
            crlf = raw.count(b"\r\n") > 0
            txt = raw.decode("utf-8").replace("\r\n", "\n")

            hit_ok = True
            for old, new in reps:
                n = txt.count(old)
                if n != 1:
                    print(f"[SKIP] {name}: 锚点命中 {n} 次（期望 1）—— 变异体本身失效")
                    hit_ok = False
                    break
                txt = txt.replace(old, new, 1)
            if not hit_ok:
                results.append((name, None))
                continue

            out = txt.replace("\n", "\r\n") if crlf else txt
            target.write_bytes(out.encode("utf-8"))
            killed, s2 = run_guard()
            target.write_bytes(raw)          # 立刻还原
            results.append((name, killed))
            print(f"[{'KILLED' if killed else 'SURVIVED!!'}] {name}: {s2}")
    finally:
        for f, b in originals.items():
            f.write_bytes(b)

    # 还原校验
    print("\n[还原校验]")
    same = all(md5(f.read_bytes()) == h0[f] for f in originals)
    for f in originals:
        print(f"   {f.name}: {'md5 一致 ✔' if md5(f.read_bytes()) == h0[f] else 'md5 不一致 ✘'}")

    surv = [n for n, k in results if k is False]
    skip = [n for n, k in results if k is None]
    print("\n" + "=" * 76)
    print(f"击杀 {sum(1 for _, k in results if k)}/{len(MUTATIONS)}"
          f"  存活 {len(surv)}  跳过 {len(skip)}")
    if surv:
        print("✘ 存活的变异（护栏有漏洞）：")
        for n in surv:
            print("   ·", n)
    if skip:
        print("⚠ 跳过的（锚点失效，需更新变异脚本）：")
        for n in skip:
            print("   ·", n)
    print("=" * 76)
    return 0 if (not surv and not skip and same) else 1


if __name__ == "__main__":
    sys.exit(main())
