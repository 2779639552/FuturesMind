# -*- coding: utf-8 -*-
"""变异自检：社会库存序列 + ④ 卡双口径图（2026-10-16）。

【判据】每个变异都必须让 `tests/test_inventory_dual.py` 变红；
若全绿 ⇒ "存活" ⇒ 护栏有洞（或变异本身没构成缺陷）。

【纪律】
  · 每次变异后从原始字节恢复，跑完核对 md5；
  · **不得与全量回归并发**（本脚本会临时改写模板/后端）。
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / "venv" / "Scripts" / "python.exe")
QM = ROOT / "quant_module.py"
TPL = ROOT / "web_template.html"
JUNIT = ROOT / "probe" / "_mut_soc.xml"


def run() -> tuple[bool, str]:
    if JUNIT.exists():
        JUNIT.unlink()
    subprocess.run([PY, "-m", "pytest", "tests/test_inventory_dual.py", "-q",
                    "--no-header", "-p", "no:warnings", "--junitxml=%s" % JUNIT],
                   cwd=str(ROOT), capture_output=True, text=True, errors="replace")
    n = 0
    if JUNIT.exists():
        try:
            t = ET.parse(JUNIT)
            s = t.getroot()
            s = s.find("testsuite") if s.tag == "testsuites" else s
            n = int(s.get("failures", 0)) + int(s.get("errors", 0))
        except Exception:  # noqa: BLE001
            n = 1
    else:
        n = 1
    return n > 0, ("pytest %d 红" % n if n else "全绿")


MUTATIONS = [
    # ── 后端：序列 ──────────────────────────────────────────────────────────
    ("M1 社会库存块不回传序列（回到缺陷态）", QM,
     '"chg_pct": s.get("chg_pct"), "dir": s.get("dir"), "rows": rows}',
     '"chg_pct": s.get("chg_pct"), "dir": s.get("dir")}', 1),
    ("M2 首期 change 记 0（冒充有前值）", QM,
     '"change": (None if prev is None else qty[i] - prev)})',
     '"change": (0 if prev is None else qty[i] - prev)})', 1),
    ("M3 change 算成末值本身（不是差值）", QM,
     '"change": (None if prev is None else qty[i] - prev)})',
     '"change": (None if prev is None else qty[i])})', 1),
    ("M4 序列窗口切错：不按日期切（全量回传）", QM,
     "    keep = _social_keep(dates)", "    keep = len(dates)", 1),
    ("M5 日期解析失败时丢数据（不是全传）", QM,
     "    except Exception:  # noqa: BLE001\n        return len(dates)",
     "    except Exception:  # noqa: BLE001\n        return 0", 1),
    # ── 前端：根因（传参）────────────────────────────────────────────────────
    ("M6 ★ 根因：不把 social 传给渲染函数", TPL,
     "this._renderFundInventory(d.inventory || {}, d.social || {});",
     "this._renderFundInventory(d.inventory || {});", 1),
    ("M7 渲染函数签名不接收社会库存", TPL,
     "_renderFundInventory(s, so) {", "_renderFundInventory(s) { const so = {};", 1),
    # ── 前端：口径纪律 ──────────────────────────────────────────────────────
    ("M8 ★ 社会库存挂到仓单那条轴（量纲不可比）", TPL,
     "type: 'line', xAxisIndex: 0, yAxisIndex: 1, data: _step('qty'),",
     "type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: _step('qty'),", 1),
    # ⚠ 锚点须带 series 名：`step: 'end'` 那段在**两个分支**里都出现
    #   （"只有社会库存"的单栏 / 双口径的上栏）⇒ 裸串命中 2（实测被跳过）。
    ("M9 ★ 社会库存线改 smooth（插值成日频假读数）", TPL,
     "name: '社会库存', type: 'line', xAxisIndex: 0, yAxisIndex: 1, data: _step('qty'),\n"
     "    showSymbol: false, step: 'end',",
     "name: '社会库存', type: 'line', xAxisIndex: 0, yAxisIndex: 1, data: _step('qty'),\n"
     "    showSymbol: false, smooth: true,", 1),
    ("M10 阶梯前推判据写反（把未来读数提前画上）", TPL,
     "while (k < socRows.length && String(socRows[k].date) <= String(dates[i]))",
     "while (k < socRows.length)", 1),
    ("M11 去掉「只有仓单」分支（BZ/CJ 变半空图）", TPL,
     " if (ckRows.length && !socRows.length) {", " if (false) {", 1),
    ("M12 去掉「只有社会库存」分支", TPL,
     " if (!ckRows.length) {", " if (false) {", 1),
    ("M13 变化柱摊成每天（阶梯前推，误导成天天变化）", TPL,
     " const socChg = new Array(dates.length).fill(null);",
     " const socChg = _step('change');", 1),
    ("M14 tooltip 去掉量纲提示", TPL,
     "只看走势与背离", "各口径", 1),
]


def main() -> int:
    orig = {p: p.read_bytes() for p in (QM, TPL)}
    md5_orig = {p: hashlib.md5(b).hexdigest() for p, b in orig.items()}

    print("=== 基线（应全绿）===")
    bad, info = run()
    if bad:
        print("  ★ 基线就是红的 ⇒ 先修基线：%s" % info)
        return 1
    print("  基线 %s ✓\n" % info)

    killed, skipped = 0, []
    print("=== 变异 ===")
    for tag, path, old, new, n in MUTATIONS:
        b0 = orig[path]
        eol = "\r\n" if b0.count(b"\r\n") else "\n"
        t = b0.decode("utf-8")
        o = old.replace("\n", eol)
        nn = new.replace("\n", eol)
        got = t.count(o)
        if got != n:
            print("  %-42s 锚点命中 %d(期望 %d) —— 跳过" % (tag, got, n))
            skipped.append(tag)
            continue
        path.write_bytes(t.replace(o, nn, 1).encode("utf-8"))
        try:
            bad, info = run()
        finally:
            path.write_bytes(orig[path])
        if bad:
            killed += 1
            print("  ✔ 击杀  %-40s %s" % (tag, info))
        else:
            print("  ★ 存活  %-40s ← 护栏有洞或变异无效" % tag)

    drift = [str(p) for p, b in orig.items() if hashlib.md5(p.read_bytes()).hexdigest() != md5_orig[p]]
    for p, b in orig.items():
        p.write_bytes(b)
    total = len(MUTATIONS) - len(skipped)
    print("\n结果：%d/%d 击杀；跳过 %d；文件复原 %s"
          % (killed, total, len(skipped), "全部一致 ✓" if not drift else "★ 不一致"))
    if skipped:
        print("跳过的（请修锚点）：" + "; ".join(skipped))
    if JUNIT.exists():
        JUNIT.unlink()
    return 0 if (killed == total and not drift and not skipped) else 1


if __name__ == "__main__":
    sys.exit(main())
