# -*- coding: utf-8 -*-
"""变异自检：给 `tests/test_foreign_report_layout_speed.py` 喂坏样本，确认它**真会红**。

守则（与项目其它变异脚本一致）：
  · 逐条把生产代码改坏 → 跑护栏 → 必须变红 → 立刻还原；
  · 结束时逐字节还原并校验 md5（源文件不许被污染）；
  · 多行锚点统一在 `\\n` 域内比较（外文研报两个文件的 EOL 不同：py=LF / css=CRLF）。
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYEXE = ROOT / "venv" / "Scripts" / "python.exe"
GUARD = "tests/test_foreign_report_layout_speed.py"
PY = ROOT / "foreign_report.py"
CSS = ROOT / "assets" / "foreign_report_pdf.css"

MUTATIONS = [
    ("M1 渲染退回 A4 纵版", PY, [('width=PPT_W_MM,', 'format="A4",')]),
    ("M2 多列挪回 .pg（右列会全空）", CSS, [
        ('body{font-size:9.3pt;line-height:1.60;column-count:2;column-gap:6.6mm;',
         'body{font-size:9.3pt;line-height:1.60;'),
        ('.pg{break-before:auto}', '.pg{break-before:auto;column-count:2;column-fill:auto}'),
    ]),
    ("M3 h2.sec 加回 column-span:all", CSS, [
        ('h2.sec{display:flex;align-items:center;gap:9px;font-size:12.6pt;',
         'h2.sec{column-span:all;display:flex;align-items:center;gap:9px;font-size:12.6pt;'),
    ]),
    ("M4 留白自检退回写死像素", PY, [
        ('            top = max(4, int(h * 0.045))        # 页眉带 ≈ 页高 4.5%\n'
         '            bot = min(h - 1, int(h * 0.960))    # 页脚带 ≈ 页高 4%',
         '            top = 34\n            bot = min(795, h)'),
    ]),
    ("M5 去掉翻译调用的并发闸", PY, [
        ('            with _LLM_SEM:  # 【并发闸】见 LLM_CONCURRENCY 注释\n'
         '                r = llm.invoke(prompt)',
         '            r = llm.invoke(prompt)'),
    ]),
    ("M6 翻译挪回归纳之后（串行）", PY, [
        ('        _tr_pool = ThreadPoolExecutor(max_workers=1)\n'
         '        _tr_fut = _tr_pool.submit(_do_translate)\n\n'
         '        _log(job, "正在通读全文并归纳结论、逻辑、数据、内部解读（4 路并行）…", "analyze", 12)',
         '        _log(job, "正在通读全文并归纳结论、逻辑、数据、内部解读（4 路并行）…", "analyze", 12)'),
        ('        # ── 阶段 3 收尾：等并行翻译跑完 ─────────────────────────\n'
         '        try:\n            _tr_fut.result()',
         '        # ── 阶段 3 收尾 ─────────────────────────\n'
         '        _do_translate()\n'
         '        try:\n            pass'),
    ]),
    ("M7 回退改回重渲一次", PY, [
        ('                    if _first_pdf_bytes:\n'
         '                        out_pdf.write_bytes(_first_pdf_bytes)',
         '                    if False:\n'
         '                        out_pdf.write_bytes(_first_pdf_bytes)'),
    ]),
    ("M8 grid 退回 1fr（卡片会溢出）", CSS, [
        ('.tldr{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:2.4mm;',
         '.tldr{display:grid;grid-template-columns:repeat(3,1fr);gap:2.4mm;'),
    ]),
    ("M9 摘要卡 kk 关掉 allow_br", PY, [
        ('f\'<div class="k"><div class="kk">{_rich(c.get("kk") or "", allow_br=True)}</div>\'',
         'f\'<div class="k"><div class="kk">{_rich(c.get("kk") or "")}</div>\''),
    ]),
    ("M10 色块退回整页高", CSS, [
        ('  color:#fff;border-radius:3mm;padding:4.6mm 6.4mm 3.8mm;',
         '  color:#fff;height:270mm;border-radius:3mm;padding:4.6mm 6.4mm 3.8mm;'),
    ]),
]


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def run_guard() -> tuple[bool, str]:
    p = subprocess.run([str(PYEXE), "-m", "pytest", GUARD, "-q", "--no-header"],
                       capture_output=True, text=True, timeout=900, cwd=str(ROOT))
    tail = [l for l in p.stdout.splitlines()
            if "passed" in l or "failed" in l or "error" in l]
    return p.returncode != 0, (tail[-1] if tail else "(无摘要)")


def main() -> int:
    originals = {f: f.read_bytes() for f in {PY, CSS}}
    h0 = {f: md5(b) for f, b in originals.items()}

    killed_baseline, s0 = run_guard()
    print("=" * 76)
    print("变异自检：护栏必须被坏样本**击杀**")
    print("=" * 76)
    print(f"[基线] 未变异时 {s0} -> {'✔ 基线绿' if not killed_baseline else '✘ 基线已红，先修基线'}")
    if killed_baseline:
        return 1

    results = []
    try:
        for name, target, reps in MUTATIONS:
            raw = originals[target]
            crlf = raw.count(b"\r\n") > 0
            txt = raw.decode("utf-8").replace("\r\n", "\n")
            ok_anchor = True
            for old, new in reps:
                n = txt.count(old)
                if n != 1:
                    print(f"[SKIP] {name}: 锚点命中 {n} 次（期望 1）")
                    ok_anchor = False
                    break
                txt = txt.replace(old, new, 1)
            if not ok_anchor:
                results.append((name, None))
                continue
            target.write_bytes((txt.replace("\n", "\r\n") if crlf else txt).encode("utf-8"))
            killed, s2 = run_guard()
            target.write_bytes(raw)          # 立刻还原
            results.append((name, killed))
            print(f"[{'KILLED' if killed else 'SURVIVED!!'}] {name}: {s2}")
    finally:
        for f, b in originals.items():
            f.write_bytes(b)

    print("\n[还原校验]")
    same = True
    for f in originals:
        eq = md5(f.read_bytes()) == h0[f]
        same = same and eq
        print(f"   {f.name}: {'md5 一致 ✔' if eq else 'md5 不一致 ✘'}")

    surv = [n for n, k in results if k is False]
    skip = [n for n, k in results if k is None]
    n_kill = sum(1 for _, k in results if k)
    print("\n" + "=" * 76)
    print(f"击杀 {n_kill}/{len(MUTATIONS)}  存活 {len(surv)}  跳过 {len(skip)}")
    for n in surv:
        print("   ✘ 存活（护栏有漏洞）:", n)
    for n in skip:
        print("   ⚠ 跳过（锚点失效，需更新脚本）:", n)
    print("=" * 76)
    return 0 if (not surv and not skip and same) else 1


if __name__ == "__main__":
    sys.exit(main())
