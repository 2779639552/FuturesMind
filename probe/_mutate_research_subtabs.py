# -*- coding: utf-8 -*-
"""变异自检: 研报页签护栏能不能抓到回退。改坏→跑→复原→核 md5。"""
import hashlib, io, subprocess, sys
P = "web_template.html"; PY = "./venv/Scripts/python.exe"
def md5(p): return hashlib.md5(io.open(p, "rb").read()).hexdigest()
orig = io.open(P, encoding="utf-8", newline="").read(); h0 = md5(P)
MUTS = [
    ("默认视图改成观点总览", "def: 'daily'", "def: 'views'", "test_four_views_and_default"),
    ("去掉问答容器联动", "toolsIn: 'qa'", "toolsIn: ''", "test_qa_card_container_follows_tab"),
    ("认不出归属就隐藏", "(!hit || hit.k === k)", "(hit && hit.k === k)", "test_only_toggles_top_level_cards"),
    ("吞掉原 switchTab", "orig.apply(this, arguments)", "({})", "test_switch_hook_is_wrapped_not_replaced"),
    ("把只读开关带进生产", "(function () {", "window.__LAB_NO_LLM = true;\n(function () {", "test_no_lab_only_flags_leak"),
]
killed = 0
for name, old, new, target in MUTS:
    assert orig.count(old) >= 1, (name, orig.count(old))
    try:
        io.open(P, "w", encoding="utf-8", newline="").write(
            orig.replace(old, new, 1).replace("\n", "\r\n") if "\n" in new else orig.replace(old, new, 1))
        r = subprocess.run([PY, "-m", "pytest", "tests/test_research_subtabs.py", "-q", "-k", target],
                           capture_output=True, text=True)
        ok = r.returncode != 0
        print(("KILLED   " if ok else "SURVIVED ") + name); killed += 1 if ok else 0
    finally:
        io.open(P, "w", encoding="utf-8", newline="").write(orig)
assert md5(P) == h0, "复原失败"
print("复原 md5 OK; 击杀 %d/%d" % (killed, len(MUTS)))
sys.exit(0 if killed == len(MUTS) else 1)
