# -*- coding: utf-8 -*-
"""L1b 变异自检：把聚合口径改坏，看护栏会不会红。"""
import hashlib
import io
import subprocess
import sys

ROOT = "D:/AgentSense/AgentSense/"
PY = ROOT + "venv/Scripts/python.exe"
QV = "quant_verdict.py"
VA = "vote_aggregate.py"


def rd(p):
    return io.open(ROOT + p, encoding="utf-8", newline="").read()


def wr(p, s):
    io.open(ROOT + p, "w", encoding="utf-8", newline="").write(s)


def md5(p):
    return hashlib.md5(io.open(ROOT + p, "rb").read()).hexdigest()


CASES = [
    (QV, "四象不拆轴（退回 composite 单票）",
     '                          "axis_parts": {\r\n'
     '                              "basis": _signed(-(vd.get("value") or 0)),\r\n'
     '                              "inventory": _signed(vd.get("drive") or 0),\r\n'
     '                          }})',
     '                          })',
     "test_no_composite_axis_in_output"),
    (VA, "轴内只取方向（幅度被抹平，L1a 踩过的坑）",
     '        axes[ax] = {"dir": round(d, 4), "conv": round(conv, 4),\n'
     '                    "score": round(d * conv, 4), "n": a["n"]}',
     '        axes[ax] = {"dir": round(d, 4), "conv": round(conv, 4),\n'
     '                    "score": round(d, 4), "n": a["n"]}',
     "test_single_vote_axis_keeps_magnitude"),
    (VA, "缺失轴也参与分母（把没数据当中性票）",
     '        wsum = sum(w[ax] for ax in active)            # ★ 只对"有证据的轴"归一',
     '        wsum = sum(w.values())                        # ★ 只对"有证据的轴"归一',
     "test_missing_axes_do_not_dilute"),
    (VA, "权重参数被忽略",
     '    if weights:\n        w.update({k: float(v) for k, v in weights.items() if k in AXES})',
     '    if False:\n        w.update({k: float(v) for k, v in weights.items() if k in AXES})',
     "test_weights_are_overridable"),
    (VA, "可信度不参与（数据脏也不打折）",
     '            acc[ax]["num"] += signed_m * c\n            acc[ax]["den"] += abs(signed_m) * c',
     '            acc[ax]["num"] += signed_m\n            acc[ax]["den"] += abs(signed_m)',
     "test_confidence_scales_contribution"),
    (QV, "weighted 反过来影响 verdict",
     '    if net > 0:\r\n        final_dir, dir_desc = "做多", f"多书方向票净 {net:+d}"',
     '    if weighted["score"] < 0:\r\n        final_dir, dir_desc = "做空", f"加权分 {weighted[\'score\']:+.2f}"\r\n'
     '    elif net > 0:\r\n        final_dir, dir_desc = "做多", f"多书方向票净 {net:+d}"',
     "test_verdict_independent_of_weighted"),
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
