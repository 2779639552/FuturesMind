# -*- coding: utf-8 -*-
"""变异自检：给 `tests/test_quant_overview.py` 喂坏样本，确认它**真会红**。

⚠️ 两个 EOL：`quant_module.py` 是 **LF**、`web_template.html` 是 **CRLF**
   ⇒ 统一在 `\\n` 域内匹配替换，写回按各自原 EOL 还原（否则锚点命中 0 次）。
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYEXE = ROOT / "venv" / "Scripts" / "python.exe"
GUARD = "tests/test_quant_overview.py"
QM = ROOT / "quant_module.py"
TPL = ROOT / "web_template.html"

MUTATIONS = [
    ("M1 板块归并去掉前缀兜底（扩池会漏）", QM, [
        ('''    for pre in ("能化", "有色", "黑色", "农产品", "贵金属", "金融"):
        if s.startswith(pre):
            return pre
    return s''', '    return s'),
    ]),
    ("M2 去掉 ADX 闸门（横盘也硬给方向）", QM, [
        ('    if adx is not None and adx < _OV_ADX_FLAT:\n'
         '        return {"dir": "flat", "label": "震", "strength": 0, "adx": adx, "bars": nbars}',
         '    if False:\n'
         '        return {"dir": "flat", "label": "震", "strength": 0, "adx": adx, "bars": nbars}'),
    ]),
    ("M3 趋势强度退回饱和口径", QM, [
        ('    quality = min(adx or 0.0, _OV_STRENGTH_ADX_REF) / _OV_STRENGTH_ADX_REF\n'
         '    st = int(round(votes / 3 * 99 * quality))',
         '    st = int(round(votes / 3 * 99))'),
    ]),
    ("M4 强度符号反向", QM, [
        ('    return {"dir": "up" if votes > 0 else "down",\n'
         '            "label": "多" if votes > 0 else "空",\n'
         '            "strength": st, "adx": adx, "bars": nbars}',
         '    return {"dir": "up" if votes > 0 else "down",\n'
         '            "label": "多" if votes > 0 else "空",\n'
         '            "strength": -st, "adx": adx, "bars": nbars}'),
    ]),
    ("M5 置信度分母用回已就绪周期数（虚高）", QM, [
        ('    denom = len(_OV_PERIODS)\n'
         '    row["confidence"] = int(round(row["resonance"] / denom * 60 + abs(score) / 5 * 40))',
         '    denom = row.get("period_ready") or len(_OV_PERIODS)\n'
         '    row["confidence"] = int(round(row["resonance"] / denom * 60 + abs(score) / 5 * 40))'),
    ]),
    ("M6 状态映射反向（偏多改偏空）", QM, [
        ('    row["state"] = {"up": "偏多", "down": "偏空"}.get(day_dir, "震荡")',
         '    row["state"] = {"up": "偏空", "down": "偏多"}.get(day_dir, "震荡")'),
    ]),
    ("M7 流动性只分两档", QM, [
        ("        out[c] = \"低\" if q < 1 / 3 else (\"高\" if q > 2 / 3 else \"中\")",
         "        out[c] = \"低\" if q < 0.5 else \"高\""),
    ]),
    ("M8 兜底行去掉 error 字段", QM, [
        ('            "confidence": 0, "key_signal": "数据获取失败", "error": err[:120]}',
         '            "confidence": 0, "key_signal": "数据获取失败"}'),
    ]),
    ("M9 前端：点中品种被静默替换回默认", TPL, [
        (''' if (want && !valid(want)) {''', ''' if (false) {'''),
    ]),
    ("M10 前端：结论配色反向（多=绿/空=红）", TPL, [
        ("const vCol = r.verdict === '多' ? 'var(--red)' : (r.verdict === '空' ? 'var(--green)' : 'var(--text-muted)');",
         "const vCol = r.verdict === '多' ? 'var(--green)' : (r.verdict === '空' ? 'var(--red)' : 'var(--text-muted)');"),
    ]),
    ("M11 前端：删掉返回总览入口", TPL, [
        ("App.qBackOverview()", "void 0"),
    ]),
    ("M12 前端：表格少一列（删关键信号）", TPL, [
        ('<th style="text-align:left;padding:8px">关键信号</th>', ''),
    ]),
]


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def run_guard() -> tuple[bool, str]:
    try:
        p = subprocess.run([str(PYEXE), "-m", "pytest", GUARD, "-q", "--no-header"],
                           capture_output=True, text=True, timeout=180, cwd=str(ROOT))
    except subprocess.TimeoutExpired:
        # ⚠️ 超时必须当作"击杀"处理并**继续**（否则外层可能被整体杀死 ⇒ 源文件残留变异）。
        #    护栏跑超时本身也说明有问题，这里显式标红由调用方判断。
        return True, "(超时 180s -> 视为击杀, 需人工复核)"
    tail = [l for l in p.stdout.splitlines()
            if "passed" in l or "failed" in l or "error" in l]
    return p.returncode != 0, (tail[-1] if tail else "(无摘要)")


def main() -> int:
    originals = {f: f.read_bytes() for f in {QM, TPL}}
    h0 = {f: md5(b) for f, b in originals.items()}

    # ⚠️⚠️ 防污染（本脚本第一版就被这个坑咬过）：整个脚本可能被 SIGTERM/超时**杀死**，
    #   那时 `finally` 根本来不及跑 ⇒ **源文件残留着某个坏变异**，而脚本自己不知道
    #   （下一次跑"基线"就直接红，或者更糟：以为某条被击杀了）。所以：
    #   ① 把原字节落一份"恢复锚"到磁盘（被杀死后也能手工/自动还原）；
    #   ② 每轮结束**立即校验已还原**（不等 finally）；
    #   ③ 装 SIGTERM/SIGINT 处理器，尽力还原后再退。
    anchor = ROOT / "probe" / "_mutation_anchor.json"
    import json as _json
    anchor.write_text(_json.dumps({str(f): h0[f] for f in originals}), encoding="utf-8")
    (ROOT / "probe" / "_mutation_anchor_bytes.bin").write_bytes(
        b"".join(originals[f] for f in sorted(originals, key=str)))
    print(f"[防污染] 恢复锚已写：probe/_mutation_anchor.json（若中途被杀死，"
          f"用 probe/_restore_mutation_anchor.py 还原）")

    def _restore_all(*_a):
        for f, b in originals.items():
            try:
                f.write_bytes(b)
            except Exception:
                pass
        for f in originals:
            if md5(f.read_bytes()) != h0[f]:
                print(f"✘✘ 还原失败：{f}", flush=True)
        print("\n[中断] 已尽力还原源文件后退出", flush=True)
        raise SystemExit(2)

    import signal as _sig
    for _s in (_sig.SIGTERM, _sig.SIGINT):
        try:
            _sig.signal(_s, _restore_all)
        except Exception:
            pass

    base_failed, s0 = run_guard()
    print("=" * 76)
    print("变异自检：护栏必须被坏样本**击杀**")
    print("=" * 76)
    print(f"[基线] {s0} -> {'✔ 基线绿' if not base_failed else '✘ 基线已红，先修基线'}")
    if base_failed:
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
            target.write_bytes(raw)
            # ★ 每轮就校验还原（不能只依赖 finally —— 被 SIGTERM 时它不执行）
            if md5(target.read_bytes()) != h0[target]:
                print(f"[FATAL] {target.name} 还原失败（残留变异）—— 立即中止", flush=True)
                _restore_all()
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
    print("\n" + "=" * 76)
    print(f"击杀 {sum(1 for _, k in results if k)}/{len(MUTATIONS)}"
          f"  存活 {len(surv)}  跳过 {len(skip)}")
    for n in surv:
        print("   ✘ 存活（护栏有漏洞）:", n)
    for n in skip:
        print("   ⚠ 跳过（锚点失效）:", n)
    print("=" * 76)
    return 0 if (not surv and not skip and same) else 1


if __name__ == "__main__":
    sys.exit(main())
