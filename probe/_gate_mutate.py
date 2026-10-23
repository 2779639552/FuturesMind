# -*- coding: utf-8 -*-
"""C（闸门搬进 weighted）的变异自检。"""
import hashlib
import io
import subprocess
import sys

ROOT = "D:/AgentSense/AgentSense/"
PY = ROOT + "venv/Scripts/python.exe"
VA = "vote_aggregate.py"
QV = "quant_verdict.py"


def rd(p):
    return io.open(ROOT + p, encoding="utf-8", newline="").read()


def wr(p, s):
    io.open(ROOT + p, "w", encoding="utf-8", newline="").write(s)


def md5(p):
    return hashlib.md5(io.open(ROOT + p, "rb").read()).hexdigest()


CASES = [
    (VA, "闸门不压制方向（等于没搬）",
     '    direction = "暂不操作" if gate_list else raw_direction',
     '    direction = raw_direction',
     "test_gate_suppresses_direction_but_keeps_score"),
    (QV, "quant_verdict 不再把闸门传给 aggregate",
     '    _hard_gates = ["double_break"] if (geo.get("ok") and geo.get("double_break")) else []',
     '    _hard_gates = []',
     "test_quant_verdict_feeds_gate_into_weighted"),
    (VA, "无条件压成暂不操作（没有闸门也拦）",
     '    direction = "暂不操作" if gate_list else raw_direction',
     '    direction = "暂不操作"',
     "test_no_gate_direction_equals_raw"),
    (VA, "闸门顺手改证据分（那就不再是证据了）",
     '        "score": round(total, 4),',
     '        "score": round(total * (0.5 if gate_list else 1.0), 4),',
     "test_gate_suppresses_direction_but_keeps_score"),
]

killed = 0
for path, name, old, new, target in CASES:
    orig = rd(path)
    h0 = md5(path)
    assert orig.count(old) == 1, "[%s] 锚点命中 %d 次" % (name, orig.count(old))
    try:
        wr(path, orig.replace(old, new, 1))
        r = subprocess.run([PY, "-m", "pytest", "tests/test_vote_aggregate.py", "-q", "-k", target],
                           capture_output=True, text=True, cwd=ROOT)
        if "no tests ran" in (r.stdout or ""):
            print("INVALID  " + name + " → 没选中用例: " + target)
            continue
        hit = r.returncode != 0
        print(("KILLED   " if hit else "SURVIVED ") + name + " → " + target)
        killed += 1 if hit else 0
    finally:
        wr(path, orig)
    assert md5(path) == h0, "复原失败: " + path

print("\n复原 md5 一致；击杀 %d/%d" % (killed, len(CASES)))
sys.exit(0 if killed == len(CASES) else 1)
