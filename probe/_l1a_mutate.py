# -*- coding: utf-8 -*-
"""L1a 变异自检：把字段改坏，看护栏会不会红。改完立刻复原并核 md5。"""
import hashlib
import io
import subprocess
import sys

ROOT = "D:/AgentSense/AgentSense/"
PY = ROOT + "venv/Scripts/python.exe"
SRC = "quant_verdict.py"


def rd(p):
    return io.open(ROOT + p, encoding="utf-8", newline="").read()


def wr(p, s):
    io.open(ROOT + p, "w", encoding="utf-8", newline="").write(s)


def md5(p):
    return hashlib.md5(io.open(ROOT + p, "rb").read()).hexdigest()


CASES = [
    ("有一张票漏了 axis（模拟新增票忘加）",
     '                          "axis": "profit",\r\n', "",
     "test_every_vote_append_declares_axis"),
    ("strength 越界",
     '"strength": _m01(abs(pct - 0.5) / 0.5)', '"strength": 2.5',
     "test_every_vote_has_fields"),
    ("小基数失真不再降可信度",
     '"confidence": 0.4 if abs(float(icp.get("inv_chg_pct") or 0)) > 100 else 1.0',
     '"confidence": 1.0',
     "test_inventory_cycle_downgrades_on_small_base"),
    ("非方向票被硬塞强度",
     '            "strength": 0.0,\r\n            "confidence": 1.0,\r\n'
     '            "strength_basis": f"不进方向',
     '            "strength": 1.0,\r\n            "confidence": 1.0,\r\n'
     '            "strength_basis": f"不进方向',
     "test_non_direction_votes_have_zero_strength"),
    ("四象不再标 composite",
     '"axis": "composite",', '"axis": "price",',
     "test_composite_vote_is_marked"),
    ("net 开始吃 strength（最危险的一种）",
     "    net, vote_audit = vs.net_of(dir_votes)  # 【变量】方向净票 + 计入审计",
     "    net, vote_audit = vs.net_of(dir_votes)\r\n"
     "    net += int(sum(1 for v in dir_votes if v.get('strength', 0) > 0.99))",
     # ★ 必须选这条：test_net_depends_only_on_bias 只调 net_of, 根本走不到 build_verdict
     #   内部那行, 选它会得到"假存活"。真正覆盖端到端一致性的是下面这条。
     "test_golden_net_and_verdict"),
]

killed = 0
for name, old, new, target in CASES:
    orig = rd(SRC)
    h0 = md5(SRC)
    assert orig.count(old) == 1, "[%s] 锚点命中 %d 次" % (name, orig.count(old))
    try:
        wr(SRC, orig.replace(old, new, 1))
        r = subprocess.run([PY, "-m", "pytest", "tests/test_vote_strength.py", "-q", "-k", target],
                           capture_output=True, text=True, cwd=ROOT)
        if "no tests ran" in (r.stdout or ""):
            print("INVALID  " + name + " → 没选中用例: " + target)
            continue
        hit = r.returncode != 0
        print(("KILLED   " if hit else "SURVIVED ") + name + " → " + target)
        killed += 1 if hit else 0
    finally:
        wr(SRC, orig)
    assert md5(SRC) == h0, "复原失败"

print("\n复原 md5 一致；击杀 %d/%d" % (killed, len(CASES)))
sys.exit(0 if killed == len(CASES) else 1)
