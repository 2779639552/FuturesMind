# -*- coding: utf-8 -*-
"""基本面量化 · 社会库存季节性图（2026-10-21 用户要求「够多年的图改成季节性」）。

【本次改动的边界】
用户原话：「按照之前的方法，将所有图都改为季节性图」。
实测各图数据跨度后（见下），**只有社会库存够多年**，其余改不了：

| 图 | 跨度 | 结论 |
|---|---|---|
| 社会库存 | 2023-10 ~ 2026-10，≈154 点（3 年） | ✅ 季节性 |
| 交易所仓单 | 2026-04 ~ 2026-10，118 点（半年） | ❌ 只有 1 年 |
| 基差/基差率 | 2025-09 起 259 点（上游 cap≈1 年，days=1000 也不更长） | ❌ |
| 仓单历史 | 25 个交易日 | ❌ |
| 研报指标 7 类 | 约 2 个月 | ❌ |
| 期限结构 / 四象 / 饼图 / 柱状 | 非时间序列 | ❌ 结构上不适用 |

【本文件守什么】
  1. 季节性图有独立容器 `#chart-f-soc`，且**复用**数据仓库那套口径（不另写一套）；
  2. 判据是「≥2 个年份」—— 1 条线的"季节性图"没有对比价值，必须回退；
  3. 回退时 `display:none`，不许留一块 300px 的空白；
  4. 仓单那条日频走势不能被季节性改造顺手删掉（它只有半年，不参与季节性）；
  5. 不许把 `chart-f-inv` 的 id 复制成两个（历史测试按 id 断言，重复 id 会静默取错）。
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

TPL = Path(__file__).resolve().parent.parent / "web_template.html"


def _tpl() -> str:
    return io.open(TPL, "r", encoding="utf-8", newline="").read()


def _method_body() -> str:
    s = _tpl()
    i = s.index("_renderFundInventory(s, so) {")
    # 到下一个同级方法定义为止（用「渲染库存周期四象限文字卡」的注释块当边界）
    j = s.index("_renderFundCycle(cyc) {", i)
    return s[i:j]


@pytest.mark.unit
def test_seasonal_container_exists_once():
    """季节性图必须有**独立**容器，且 id 不能与仓单图重复。"""
    s = _tpl()
    assert s.count('id="chart-f-soc"') == 1, "季节性容器缺失或重复"
    assert s.count('id="chart-f-inv"') == 1, "chart-f-inv 被复制成多个（按 id 取实例会取错）"
    assert re.search(r'id="chart-f-soc"\s+style="height:\s*300px"', s), \
        "季节性图高度应显式声明（数据仓库同款 300px 档），否则 init 时可能 0 高"


@pytest.mark.unit
def test_reuses_existing_seasonal_engine():
    """★ 必须复用 `_seasonalFromSeries` + `_drawGtjaSeasonal`，不许再写一套。

    【为什么】数据仓库那套已经踩过闰年对齐、同日去重、当年聚焦等坑；各自实现一份，
    两个页面的"季节性"就会在闰年差一格 —— 而这种错**不报错**，只能靠肉眼发现。
    """
    body = _method_body()
    assert "this._seasonalFromSeries(socRows.map(r => [r.date, r.qty]))" in body, \
        "没有复用 _seasonalFromSeries（社会库存序列没被整形成季节性结构）"
    assert "this._drawGtjaSeasonal(cs," in body, "没有复用 _drawGtjaSeasonal（另写了一套绘制）"


@pytest.mark.unit
def test_seasonal_requires_two_years():
    """★ 判据必须是「≥2 个年份」，且不足时回退 + 隐藏容器。"""
    body = _method_body()
    assert "const socYearN = Object.keys(socSeason.years || {}).length;" in body
    assert "const invSeason = socYearN >= 2;" in body, "判据不是「≥2 年」"
    assert "socEl.style.display = 'none';" in body, "不足 2 年时没隐藏容器（会留一块空白）"


@pytest.mark.unit
def test_warehouse_receipt_chart_survives():
    """★★ 仓单只有半年数据 ⇒ 不参与季节性，但它的日频走势必须还在。

    【判据】仓单分支同时要有「注册仓单」折线与「日变化」柱 —— 只留折线等于把
    每日增减（判断"集中注销/集中注册"的关键）悄悄丢了。
    """
    body = _method_body()
    assert "if (ckRows.length && !socForDual.length) {" in body, "缺「只有仓单」分支"
    assert "name: '注册仓单', type: 'line'" in body
    assert "name: '日变化', type: 'bar'" in body, "仓单日变化柱被删了"


@pytest.mark.unit
def test_social_not_drawn_twice():
    """社会库存已用季节性表达后，下方的图不能再画一遍（同一序列两种画法会互相打脸）。"""
    body = _method_body()
    assert "const socForDual = invSeason ? [] : socRows;" in body, \
        "已有季节性时仍把社会库存塞进下方图（重复表达）"


@pytest.mark.unit
def test_subtitle_switches_with_form():
    """小标题要随形态变：有季节性时下面只是仓单；没有时退回双口径对照。"""
    body = _method_body()
    assert "f-inv-ck-title" in body
    assert "交易所仓单 · 日频走势" in body
    assert "社会库存不足 2 年" in body


@pytest.mark.unit
def test_scope_reason_recorded():
    """★ 把「为什么只有这一张能改」写进注释。

    【为什么值得一条测试】下次有人看到「所有图都改季节性」，会以为这里漏改了 ——
    注释里没有数据跨度依据，就只剩一句口口相传。
    """
    body = _method_body()
    assert "154" in body and "3 年" in body, "缺社会库存跨度依据（≈154 点 / 3 年）"
    assert "半年" in body, "缺仓单跨度依据（半年，只有 1 年）"


@pytest.mark.unit
def test_null_is_not_zero():
    """★ `Number(null) === 0`：空值不能变成「0」这个真实读数（本次护栏抓到的真 bug）。"""
    s = _tpl()
    i = s.index("_seasonalFromSeries(series) {")
    body = s[i:i + 900]
    assert "const v = Number(p && p[1]);" not in body, "空值仍会被 Number(null) 转成 0"
    assert "_raw === null || _raw === undefined || _raw === ''" in body, "缺空值判断"
