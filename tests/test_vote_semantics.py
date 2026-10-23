# -*- coding: utf-8 -*-
"""方向票语义护栏（2026-10-19 L0）。

【守的是什么】
  这一天实跑发现：9 张方向票里有 4 张**永远计 0**（期限结构的「偏多/偏空」不在 dirmap 里），
  而且不报错、不告警、测试还是绿的 —— 因为测试把生产代码那张表抄了一份来自证。

本文件钉四件事：
  A. 「偏多/偏空」必须计 ±1（回归钉，防止再被静默吞掉）
  B. 生产代码里**不许再出现 dirmap 字面量**（单一权威来源，禁止多入口复制）
  C. 测试**不许复制映射表**（自指陷阱：期望值与被测代码同源 ⇒ 恒绿）
  D. 前后端同一口径（前端 _FRK_TEXT_RANK 早就把「偏多」当 +1）
"""
from __future__ import annotations

import io
import os
import re

import pytest

import vote_semantics as vs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROD_FILES = ["quant_verdict.py", "fund_module.py", "quant_methodology.py"]
TPL = os.path.join(ROOT, "web_template.html")


def _read(p: str) -> str:
    return io.open(os.path.join(ROOT, p), encoding="utf-8").read()


# ══════════════ A. 回归钉：偏多/偏空 ══════════════
@pytest.mark.parametrize("bias,expect", [
    ("偏多", 1), ("偏空", -1),                       # ★ 本次修的正是这两个
    ("做多", 1), ("单边做多", 1),
    ("做空", -1), ("单边做空", -1),
    ("中性", 0), ("暂不操作", 0),
])
def test_direction_values(bias, expect):
    assert vs.sign(bias) == expect


def test_weak_and_nondirection_are_zero_but_classified():
    """这些计 0 是**有意的**，但必须与"认不出来"区分开。"""
    assert vs.classify("跨期正套") == (0, vs.NON_DIRECTION)
    assert vs.classify("跨期反套") == (0, vs.NON_DIRECTION)
    assert vs.classify("趋势系统可用") == (0, vs.NON_DIRECTION)
    assert vs.classify("提示(突破口径对照)") == (0, vs.NON_DIRECTION)
    assert vs.classify("部分共振(2/3 偏多)") == (0, vs.WEAK_DIRECTION)
    # ★ 这两个是 L0 漂移对比时审计抓出来的（此前同样静默计 0，且读代码发现不了）。
    #   它们语义上是"别做单边"，所以方向必须为 0；但必须被**认出**，不能落进 unknown。
    assert vs.classify("套利/对冲(适合正套)") == (0, vs.NON_DIRECTION)
    assert vs.classify("套利/对冲(适合反套, 结合仓单有效期)") == (0, vs.NON_DIRECTION)
    assert vs.classify("中性") == (0, vs.NEUTRAL)


def test_unknown_is_surfaced_not_silently_zero():
    """认不出的取值：符号可以是 0，但**必须**进 unclassified。"""
    net, audit = vs.net_of([
        {"book": "甲", "bias": "做多"},
        {"book": "乙", "bias": "某种没人登记过的新说法"},
    ])
    assert net == 1
    assert audit["unclassified"] == [{"book": "乙", "bias": "某种没人登记过的新说法"}]
    assert audit["counted"] == 1


def test_audit_counts_match_votes():
    votes = [{"book": "A", "bias": "做多"}, {"book": "B", "bias": "偏空"},
             {"book": "C", "bias": "暂不操作"}, {"book": "D", "bias": "跨期正套"}]
    net, audit = vs.net_of(votes)
    assert net == 0                      # +1 -1 相抵
    assert audit["counted"] == 2
    assert audit["neutral"] == ["C"]
    assert audit["non_direction"] == ["D"]
    assert audit["unclassified"] == []


# ══════════════ B. 生产代码不得再出现 dirmap ══════════════
# ★ 禁的是「方向词 → 数字」这个映射**本身**，不是某个变量名。
#   第一版只禁 `dirmap` 这个词，变异自检时把表改名成 `_dm` 就绕过去了（存活）——
#   这就是"守名字不守事实"的典型漏洞。
_DIRMAP_LITERAL = re.compile(
    r"""["'](?:做多|单边做多|偏多|做空|单边做空|偏空)["']\s*:\s*[+-]?\d""")


@pytest.mark.parametrize("fname", PROD_FILES)
def test_no_direction_map_literal_in_production(fname):
    """★ 唯一的映射表在 vote_semantics。再抄一份 = 给同一个 bug 复制入口。

    【为什么这条比"断言 net 等于几"更有用】净票是个标量，漏计一张票只会让数字偏小，
      断言具体数值只能验一个用例；禁止复制表头，才能保证**所有**入口同源。
    """
    src = _read(fname)
    hits = [ln.strip() for ln in src.splitlines()
            if _DIRMAP_LITERAL.search(re.sub(r"#.*$", "", ln))]
    assert not hits, f"{fname} 又写了一张家→数字的方向映射表：{hits[:3]}"


# ══════════════ C. 测试不得复制映射表（自指陷阱） ══════════════
def test_tests_do_not_copy_the_mapping():
    """★ 曾经 tests/test_fund_xsection.py 抄了一份方向映射表算期望值 —— 生产漏了「偏多」，
    测试跟着一起漏 ⇒ 恒绿。测试的期望值必须引用 vote_semantics，不能再写一份字面量。"""
    bad = []
    tdir = os.path.join(ROOT, "tests")
    for fn in os.listdir(tdir):
        if not fn.endswith(".py") or fn == "test_vote_semantics.py":
            continue
        src = io.open(os.path.join(tdir, fn), encoding="utf-8").read()
        for i, ln in enumerate(src.splitlines(), 1):
            if _DIRMAP_LITERAL.search(re.sub(r"#.*$", "", ln)):
                bad.append(f"{fn}:{i}")
    assert not bad, f"测试里仍有映射表副本（自指陷阱）：{bad}"


# ══════════════ D. 前后端同一口径 ══════════════
def test_frontend_rank_matches_backend_for_term_structure():
    """前端 _FRK_TEXT_RANK 把「偏多」当 +1；后端若再当成 0，
    就会出现「按期限结构排序」与「净票列」互相矛盾的界面。"""
    html = io.open(TPL, encoding="utf-8").read()
    m = re.search(r"skill_term:\s*\{\s*'偏多':\s*(-?\d+),\s*'偏空':\s*(-?\d+)\s*\}", html)
    assert m, "前端找不到 skill_term 的方向映射（_FRK_TEXT_RANK）"
    assert int(m.group(1)) == vs.sign("偏多")
    assert int(m.group(2)) == vs.sign("偏空")


# ══════════════ E. 生产方实际会产出的取值，必须全部登记 ══════════════
def test_all_producer_literals_are_classified():
    """从生产代码里抽出 bias 字面量，逐个分类，不许出现 UNKNOWN。

    【反向绑定】若哪天新增一张票、填了个没登记的新词，这里会红 —— 而不是等它在
      线上静默计 0（本次就是靠人工实跑才发现的）。
    """
    lits = set()
    for fname in PROD_FILES:
        src = _read(fname)
        lits |= set(re.findall(r'"bias"\s*:\s*"([^"]+)"', src))
        lits |= set(re.findall(r"p_bias,\s*p_txt\s*=\s*\"([^\"]+)\"", src))
        lits |= set(re.findall(r'bias = \("([^"]+)"', src))
        lits |= set(re.findall(r"bias = \"([^\"]+)\"", src))
    # 期限结构的取值来自 quant_methodology，单独补进来
    qm_src = _read("quant_methodology.py")
    for v in ("偏多", "偏空", "中性"):
        if f"'{v}'" in qm_src and "shape, spot_meaning, bias" in qm_src:
            lits.add(v)

    assert lits, "没抽到任何 bias 字面量 —— 正则失效了，请更新本测试"
    unknown = sorted(x for x in lits if vs.classify(x)[1] == vs.UNKNOWN)
    assert not unknown, f"这些方向取值未在 vote_semantics 登记：{unknown}"
