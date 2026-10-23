# -*- coding: utf-8 -*-
"""L0 变异自检：把 vote_semantics 与调用点改坏，看护栏会不会红。

【纪律】本脚本会**临时修改生产文件**，因此：
  · 必须与全量回归**串行**执行（并发会读到变异中间态，得出假结论）；
  · 每个变异跑完立刻复原，并用 md5 校验复原成功（不是"看起来恢复了"）。
"""
import hashlib
import io
import subprocess
import sys

ROOT = "D:/AgentSense/AgentSense/"
PY = ROOT + "venv/Scripts/python.exe"


def rd(p):
    return io.open(ROOT + p, encoding="utf-8", newline="").read()


def wr(p, s):
    io.open(ROOT + p, "w", encoding="utf-8", newline="").write(s)


def md5(p):
    return hashlib.md5(io.open(ROOT + p, "rb").read()).hexdigest()


CASES = [
    # (目标文件, 说明, old, new, 期望抓到的测试)
    ("vote_semantics.py", "偏多退回不计票(原 bug)",
     '_LONG = frozenset({"做多", "单边做多", "偏多"})',
     '_LONG = frozenset({"做多", "单边做多"})',
     "test_direction_values"),
    ("vote_semantics.py", "偏空退回不计票",
     '_SHORT = frozenset({"做空", "单边做空", "偏空"})',
     '_SHORT = frozenset({"做空", "单边做空"})',
     "test_direction_values"),
    ("vote_semantics.py", "认不出也当有效中性(重新静默)",
     "    return 0, UNKNOWN",
     "    return 0, NEUTRAL",
     "test_unknown_is_surfaced_not_silently_zero"),
    ("vote_semantics.py", "套利/对冲又变回未登记",
     '_NON_DIRECTION_PREFIX = ("套利/对冲",)',
     '_NON_DIRECTION_PREFIX = ()',
     "test_weak_and_nondirection_are_zero_but_classified"),
    ("quant_verdict.py", "生产代码重新抄一份 dirmap",
     "    net, vote_audit = vs.net_of(dir_votes)",
     '    _dm = {"做多": 1, "做空": -1}\n    net = sum(_dm.get(v["bias"], 0) for v in dir_votes)\n    vote_audit = {}',
     "test_no_direction_map_literal_in_production"),
    ("tests/test_fund_xsection.py", "测试重新抄一份映射(自指)",
     "    from vote_semantics import sign as _sign",
     '    _dm = {"做多": 1, "做空": -1}\n    from vote_semantics import sign as _sign\n    _dirmap_probe = _dm',
     "test_tests_do_not_copy_the_mapping"),
    ("web_template.html", "前端把「偏多」改成 0(前后端漂移)",
     "skill_term: { '偏多': 1, '偏空': -1 }",
     "skill_term: { '偏多': 0, '偏空': 0 }",
     "test_frontend_rank_matches_backend_for_term_structure"),
]

killed = 0
for path, name, old, new, target in CASES:
    orig = rd(path)
    h0 = md5(path)
    assert orig.count(old) == 1, "[%s] 锚点命中 %d 次" % (name, orig.count(old))
    try:
        wr(path, orig.replace(old, new, 1))
        r = subprocess.run([PY, "-m", "pytest", "tests/test_vote_semantics.py",
                            "-q", "-k", target],
                           capture_output=True, text=True, cwd=ROOT)
        # ★ 保险: `-k` 选不中任何用例时 pytest 也返回非 0(exit 5)，会被误判成"击杀"。
        #   本脚本第一版就靠这个假阳性差点放过两个缺口 —— 必须显式排除。
        if "no tests ran" in (r.stdout or "") or "deselected" == (r.stdout or "").strip():
            print("INVALID  " + name + "  → 目标测试名没选中任何用例: " + target)
            continue
        hit = r.returncode != 0
        print(("KILLED   " if hit else "SURVIVED ") + name + "  → " + target)
        if not hit:
            print("         (该变异没被发现，说明护栏有缺口)")
        killed += 1 if hit else 0
    finally:
        wr(path, orig)
    assert md5(path) == h0, "复原失败: " + path

print("\n复原 md5 全部一致；击杀 %d/%d" % (killed, len(CASES)))
sys.exit(0 if killed == len(CASES) else 1)
