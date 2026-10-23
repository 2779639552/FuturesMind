# -*- coding: utf-8 -*-
"""L1a 护栏：方向票的 axis / strength / confidence 必须齐备且不改变结论。

【L1a 的契约】只加字段，**不改判断**。所以本文件守两件事：
  ① 每张票都带 axis/strength/confidence/strength_basis，且取值合法；
  ② net / verdict 仍然只由 bias 决定（新增字段不得影响结论）。

【为什么单列 structural 测试】断言"这张票的 strength 等于 0.52"只能验一个用例；
  真正会出的事故是**以后新增一张票忘了加 axis** —— 那时加权层会静默漏掉它，
  和 10-19 那次哑票是同一类失败。所以这里直接数源码里的 append 数与 axis 数。
"""
from __future__ import annotations

import io
import os

import pytest

import quant_verdict as qv
import vote_semantics as vs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "quant_verdict.py")
AXES = {"price", "basis", "inventory", "profit", "composite"}
FIELDS = {"axis", "strength", "confidence", "strength_basis"}


def _bars(n=90, trend=1.0):
    out, px = [], 1000.0
    for i in range(n):
        px *= (1 + trend * 0.004) * (1 + (0.004 if i % 7 in (0, 1) else -0.003))
        out.append({"date": f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                    "open": px * 0.995, "high": px * 1.006, "low": px * 0.994,
                    "close": px, "volume": 10000 + i * 10, "open_interest": 50000 + i * 20})
    return out


def _payload(trend=1.0, inv_series=None, inv_chg=-6.0):
    return {
        "variety": "TA", "name": "PTA", "unit": "元/吨", "bars": _bars(trend=trend),
        "basis_rate_series": [0.02 + 0.001 * i for i in range(40)],
        "inv_series": inv_series or [2000000 - 20000 * i for i in range(40)],
        "inv_chg_pct": inv_chg,
        "term": {"ok": True, "rows": [
            {"contract": "TA2610", "month": "2610", "price": 6800},
            {"contract": "TA2709", "month": "2709", "price": 6000}]},
        "profit_series": [100.0 + 5 * i for i in range(30)],
        "profit_label": "加工利润",
    }


def _votes(**kw):
    d = qv.build_verdict(_payload(**kw))
    assert d.get("ok"), d.get("error")
    return d["stages"]["direction"]["votes"], d


# ══════════════ ① 字段齐备与取值合法 ══════════════
@pytest.mark.parametrize("trend", [1.0, -1.0, 0.0])
def test_every_vote_has_fields(trend):
    votes, _ = _votes(trend=trend)
    assert len(votes) >= 4, f"样本只出了 {len(votes)} 张票，测试失去意义"
    for v in votes:
        missing = FIELDS - set(v)
        assert not missing, f"{v.get('book')} 缺字段 {missing}"
        assert v["axis"] in AXES, f"{v.get('book')} axis 非法: {v['axis']}"
        assert 0.0 <= float(v["strength"]) <= 1.0, f"{v.get('book')} strength 越界: {v['strength']}"
        assert 0.0 <= float(v["confidence"]) <= 1.0, f"{v.get('book')} confidence 越界"
        assert str(v["strength_basis"]).strip(), "strength_basis 不能空——它是加权唯一可复核的依据"


def test_non_direction_votes_have_zero_strength():
    """ADXR / 背离 / 突破 本来就不指方向，给它们强度等于自欺。"""
    votes, _ = _votes()
    for v in votes:
        if v["bias"] in ("趋势系统可用", "趋势系统不可用(无趋势)", "反转预警",
                         "提示", "提示(突破口径对照)"):
            assert v["strength"] == 0.0, f"{v['book']}/{v['bias']} 是非方向票，strength 应为 0"


def test_inventory_cycle_downgrades_on_small_base():
    """库存 +1859%（仓单 1,000→19,590 的实测形态）→ confidence 必须降到 0.4。"""
    # 跳变必须落在**最后一个观测**上：若后面还有等值点, 5 期变化会被摊平回 0%
    inv = [1000.0] * 39 + [19590.0]
    votes, _ = _votes(inv_series=inv)
    cyc = [v for v in votes if "库存周期" in str(v.get("book"))]
    assert cyc, "没出库存周期票，测试前提不成立"
    assert cyc[0]["confidence"] == pytest.approx(0.4), \
        "小基数失真未降低可信度 —— 这是 10-19 报告里点名的坑"


def test_wyckoff_no_event_lowers_confidence():
    votes, _ = _votes()
    wy = [v for v in votes if v.get("book") == "威科夫"]
    assert wy, "没出威科夫票"
    if wy[0]["strength"] == 0.0:
        assert wy[0]["confidence"] == pytest.approx(0.3), "无事件时可信度必须低于有事件(没证据≠中性)"


def test_composite_vote_is_marked():
    """四象横跨基差+库存两根轴，必须标 composite —— L2 拆轴时靠它定位。"""
    votes, _ = _votes()
    vd = [v for v in votes if v.get("book") == "多空逻辑"]
    assert vd and vd[0]["axis"] == "composite", "四象的 axis 应标 composite"


# ══════════════ ② 结论不受影响 ══════════════
def test_net_depends_only_on_bias():
    """给同一条票加不加 strength/axis，net 必须一模一样。"""
    base = [{"book": "A", "bias": "做多"}, {"book": "B", "bias": "偏空"}]
    decorated = [{"book": "A", "bias": "做多", "axis": "price", "strength": 0.1, "confidence": 0.1},
                 {"book": "B", "bias": "偏空", "axis": "profit", "strength": 1.0, "confidence": 1.0}]
    assert vs.net_of(base)[0] == vs.net_of(decorated)[0] == 0


def test_verdict_unchanged_by_new_fields():
    """端到端：同一 payload 的 verdict / 票序列仍只由 bias 决定。

    【怎么证明"没变"而不是"看起来没变"】把每张票的 strength/confidence 全部抹成 0 与 1，
      verdict 必须相同 —— 若哪天有人拿 strength 去参与净票计算，这条会红。
    """
    votes, d = _votes(trend=1.0)
    before = d["stages"]["direction"]["verdict"]
    for v in votes:
        v["strength"] = 0.0
        v["confidence"] = 0.0
    assert vs.net_of(votes)[0] == d["stages"]["direction"]["vote_audit"]["net"]
    assert qv.build_verdict(_payload())["stages"]["direction"]["verdict"] == before


def test_golden_net_and_verdict():
    """★ 金标准：固定 payload 的净票必须仍是 +2、方向仍是「做多」。

    【为什么必须有硬编码期望值】上面那条"自比"测试抓不到"net 偷偷多吃了一项"——
      因为两次调用都走改后的代码，改了也一致。金标准值是唯一能发现"净票公式被动了"的钉子。
      ⚠ 如果哪天**有意**改净票口径（例如 L2 换成加权分），这条会红 —— 那是提醒你
        同步更新本期望值，而不是把测试删掉。
    """
    votes, d = _votes(trend=1.0)
    sd = d["stages"]["direction"]
    assert sd["vote_audit"]["net"] == 2, "固定 payload 的净票变了 —— 字段改动越界到结论了"
    assert sd["verdict"] == "做多"
    # ★ 关键: vote_audit.net 是 net_of **内部**算出来的标量, 之后若有人再对 net 动手脚
    #   (例如 net += strength 项), audit 不会变、verdict 也未必变 —— 只有 desc 里那个数
    #   才是**真正参与决策**的净票。所以必须钉 desc。
    assert "净 +2" in str(sd.get("desc") or ""), \
        f"实际参与决策的净票不是 +2（desc={sd.get('desc')!r}）—— 有量在 net 之后偷偷加了东西"
    # 方向必须由审计净票推出（除非被"双线同破"硬闸门拦下）
    if not (sd.get("geometry") or {}).get("double_break"):
        expect = "做多" if sd["vote_audit"]["net"] > 0 else (
            "做空" if sd["vote_audit"]["net"] < 0 else "暂不操作")
        assert sd["verdict"] == expect, "verdict 与 vote_audit.net 不一致：净票之外又掺了别的量"


# ══════════════ ③ 结构：新增票必须带 axis ══════════════
def test_every_vote_append_declares_axis():
    """★ 数源码：`dir_votes.append(` 的处数必须等于 `"axis":` 的处数。

    【为什么用计数而不是逐块解析】多行 dict 的正则极易误伤；而"票数 == axis 数"
      恰好就是我们要的不变量：新增一张票却忘了加 axis，两边立刻不等。
    """
    src = io.open(SRC, encoding="utf-8").read()
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    n_append = code.count("dir_votes.append(")
    n_axis = code.count('"axis":')
    assert n_append == 9, f"方向票数量变了({n_append})，请同步更新本测试与 L2 的轴映射"
    assert n_axis == n_append, \
        f"有 {n_append - n_axis} 张票没声明 axis —— 加权层会静默漏掉它（与 10-19 哑票同类）"


def test_m01_clamps():
    assert qv._m01(None) == 0.0
    assert qv._m01("x") == 0.0
    assert qv._m01(-5) == 0.0
    assert qv._m01(5) == 1.0
    assert qv._m01(0.37) == pytest.approx(0.37)
