# -*- coding: utf-8 -*-
r"""变异自检：证明 test_theme_deepblue_gold_promotion.py 的断言不是永真守卫。

做法：把 web_template.html 复制到 /tmp，逐個施加一个"退步"变异，用
THEME_TEMPLATE 指向副本跑守卫测试，必须看到 FAILED。若某个变异后测试仍全绿，
说明对应断言打不中（永真），需要修断言。
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(r"D:\AgentSense\AgentSense")
TEMPLATE = ROOT / "web_template.html"
PYTEST = ROOT / "venv" / "Scripts" / "python.exe"

MUTATIONS = [
    ("M1 品牌色退回糖果紫",
     "--brand: var(--gold-d);", "--brand: #5b5bd6;", 1),
    ("M2 标题 serif 规则缩回 quant 作用域（字体倒退）",
     ".card h3,\n.expander-header,\n.report-content h1, .report-content h2, .report-content h3 { font-family: var(--serif); }",
     '[data-theme="light"] #tab-quant .card h3,\n.expander-header,\n.report-content h1, .report-content h2, .report-content h3 { font-family: var(--serif); }', 1),
    ("M3 删掉切主题重染调用",
     "    this._repaintCharts(before);\n", "", 1),
    ("M4 图表配色退回 isDark 三元（十处样板全退）",
     "const textColor = CHART.axis;", "const textColor = isDark ? CHART.axis : '#666';", 10),
    ("M5 信号横幅退回糖果蓝渐变",
     "linear-gradient(135deg, #f5f7fa 0%, #eef2f7 100%)",
     "linear-gradient(135deg, #f0f4ff 0%, #e8f0fe 100%)", 1),
    ("M6 快照调用挪到 setAttribute 之后",
     "    const before = this._snapshotChartColors();\n    document.documentElement.setAttribute('data-theme', next);",
     "    document.documentElement.setAttribute('data-theme', next);\n    const before = this._snapshotChartColors();", 1),
    ("M7 注释里混入 */（2026-10-13 真实事故：提前闭合吞掉 .card h3 规则）",
     "--btn-primary-bg / --thead-bg", "--btn-primary*/--thead-bg", 1),
    ("M8 表头 th 漏出数字栈（只留 td，同表头脚字族打架）",
     "table th, table td { font-family: var(--num); }",
     "table td { font-family: var(--num); }", 1),
    ("M9 研报正文标题漏出宋体栈",
     ".report-content h1, .report-content h2, .report-content h3 { font-family: var(--serif); }",
     ".report-content h4 { font-family: var(--serif); }", 1),
    ("M10 正文 body 退回 Inter 栈",
     "  font-family: var(--sans);",
     "  font-family: 'Inter', -apple-system, 'Segoe UI', system-ui, sans-serif;", 1),
]


def run_guard(template: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(PYTEST), "-m", "pytest", "tests/test_theme_deepblue_gold_promotion.py", "-q", "--no-header", "-x"],
        capture_output=True, text=True, timeout=300, cwd=str(ROOT),
        env={**os.environ, "THEME_TEMPLATE": str(template)},
    )


def main() -> int:
    tmpdir = Path(tempfile.mkdtemp(prefix="deepblue_mut_"))
    ok = True
    try:
        for name, old, new, expect in MUTATIONS:
            src = TEMPLATE.read_text(encoding="utf-8")
            n = src.count(old)
            if n != expect:
                print(f"[SKIP] {name}: 锚点命中 {n} 次（预期 {expect}），跳过")
                ok = False
                continue
            mutated = tmpdir / f"mut_{name.split()[0]}.html"
            mutated.write_text(src.replace(old, new), encoding="utf-8")
            proc = run_guard(mutated)
            tail = [l for l in proc.stdout.splitlines() if "passed" in l or "failed" in l or "error" in l]
            verdict = tail[-1] if tail else "(无摘要)"
            killed = proc.returncode != 0
            ok = ok and killed
            print(f"[{'KILLED' if killed else 'SURVIVED!!'}] {name}: {verdict}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    print("\n结论:", "全部变异被守卫击杀 ✔" if ok else "存在漏杀变异，必须修守卫 ✘")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
