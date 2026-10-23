# -*- coding: utf-8 -*-
"""变异自检: 国君四源注入护栏是否真能抓到回退。改坏→跑→复原→核 md5。"""
import hashlib, io, subprocess, sys

PYEXE = "./venv/Scripts/python.exe"
BUNDLE = "tradingagents/dataflows/analysis_bundle.py"
ANALYST = "tradingagents/agents/analysts/commodity_analysts.py"

def md5(p): return hashlib.md5(io.open(p, "rb").read()).hexdigest()

MUTS = [
    (BUNDLE, "基差率不再自算(直接用接口值)", "s += f\"（{cur / spot_v * 100:.2f}%）\"", "s += f\"（{cur:.2f}%）\"",
     "test_basis_rate_is_computed_not_taken_from_api"),
    (BUNDLE, "小基数仍用百分比", "if abs(p) > 300:", "if False:",
     "test_small_base_uses_absolute_delta"),
    (BUNDLE, "不剔除未来日期点", "if date.fromisoformat(d) > today:", "if False:",
     "test_future_points_are_dropped_and_counted"),
    (ANALYST, "基本面节点不注入", "if gtja_ctx:", "if False:",
     "test_fundamental_prompt_contains_gtja_block"),
]

killed = 0
for path, name, old, new, target in MUTS:
    orig = io.open(path, encoding="utf-8", newline="").read()
    h0 = md5(path)
    assert orig.count(old) == 1, (name, orig.count(old))
    try:
        io.open(path, "w", encoding="utf-8", newline="").write(orig.replace(old, new, 1))
        r = subprocess.run([PYEXE, "-m", "pytest", "tests/test_analysis_bundle.py", "-q", "-k", target],
                           capture_output=True, text=True)
        ok = r.returncode != 0
        print(("KILLED   " if ok else "SURVIVED ") + name)
        killed += 1 if ok else 0
    finally:
        io.open(path, "w", encoding="utf-8", newline="").write(orig)
    assert md5(path) == h0, "复原失败: " + path

print("复原 md5 全部一致; 击杀 %d/%d" % (killed, len(MUTS)))
sys.exit(0 if killed == len(MUTS) else 1)
