"""研究内容检查护栏（2026-10-22）。

【它守什么】
现有 3200+ 条测试全是格式护栏，没有一条检查**研究内容**。本文件守六条规则，
每一条都对应一次真实踩坑：

  R1 缺口顶替（「不知道」被写成「中性」）
  R2 关键变量缺失（可用的读数一个都没提）
  R3 口径未注明（基差率不写分母 —— 2026-10-22 实测就栽在这）
  R4 证据悬空（方向句附近没有任何数值）
  R5 失效条件缺失（给了方向不说什么情况下作废）
  R6 周频滞后未标注（用当日口吻描述 13 天前的库存）

【判定原则】
只抓**客观缺失**，不评判观点对错。所以每条都配一个「反例」：写全了就不能报。
误报比漏报更伤 —— 用户一旦觉得检查器在瞎叫，就会整体忽略它。
"""
from __future__ import annotations

import pytest

from tradingagents import research_review as R


def _cards() -> list[dict]:
    return [
        {"id": "gtja.basis.abs", "label": "基差（绝对值）", "kind": "price_diff",
         "value": 550, "display": "550", "as_of": "2026-10-21", "age_days": 1, "unit": "元/吨"},
        {"id": "gtja.warehouse.qty", "label": "交易所注册仓单", "kind": "qty",
         "value": 34728, "display": "34,728", "as_of": "2026-10-21", "age_days": 1, "unit": "吨"},
        {"id": "gtja.inventory.social", "label": "社会库存", "kind": "qty",
         "value": 1385241, "display": "1,385,241", "as_of": "2026-10-09", "age_days": 13, "unit": "吨"},
        {"id": "gtja.profit.processing", "label": "加工利润", "kind": "qty",
         "value": 23.3, "display": "23.3", "as_of": "2026-10-20", "age_days": 2, "unit": "元/吨"},
    ]


# ---------------------------------------------------------------------------
# R0 空报告
# ---------------------------------------------------------------------------
def test_empty_report_fails_loudly():
    r = R.review("", symbol="TA", cards=_cards())
    assert r["ok"] is False and r["pass"] is False
    assert r["issues"][0]["code"] == "R0_empty"


# ---------------------------------------------------------------------------
# R1 缺口顶替
# ---------------------------------------------------------------------------
def test_r1_flags_direction_without_any_data():
    r = R.review("综合判断：偏多，建议逢低做多。", symbol="TA", cards=[])
    codes = [i["code"] for i in r["issues"]]
    assert "R1_gap_masked" in codes
    assert r["pass"] is False, "high 级问题必须让 pass=False"


def test_r1_ok_when_gap_is_stated():
    r = R.review("四源取数失败，本次无量化结论；基于价格结构暂偏多。", cards=[])
    assert "R1_gap_masked" not in [i["code"] for i in r["issues"]], \
        "已经说明了缺口，就不该报「顶替」"


# ---------------------------------------------------------------------------
# R2 关键变量缺失
# ---------------------------------------------------------------------------
def test_r2_flags_most_readings_missing():
    r = R.review("技术面偏多。", symbol="TA", cards=_cards())
    assert "R2_missing_key_variable" in [i["code"] for i in r["issues"]]


def test_r2_ok_when_three_of_four_mentioned():
    text = "基差 550（7.89%，现货分母），仓单 34,728，加工利润 23.3。"
    r = R.review(text, symbol="TA", cards=_cards())
    assert "R2_missing_key_variable" not in [i["code"] for i in r["issues"]]


def test_r2_tolerates_one_missing():
    """漏一个容忍（报告可以有侧重），漏两个才算关键变量缺失。"""
    text = "基差 550（7.89%，现货分母），仓单 34,728，加工利润 23.3。"
    r = R.review(text, symbol="TA", cards=_cards())
    assert "R2_missing_key_variable" not in [i["code"] for i in r["issues"]], \
        "只漏了「社会库存」一项，不该判定关键变量缺失"


# ---------------------------------------------------------------------------
# R3 口径未注明（本轮的核心）
# ---------------------------------------------------------------------------
def test_r3_flags_basis_rate_without_denominator():
    text = "当前基差率 8.56%，处于历史高位，偏多。失效条件：基差率跌破 3%。"
    r = R.review(text, symbol="TA", cards=_cards())
    issues = [i for i in r["issues"] if i["code"] == "R3_caliber_unstated"]
    assert issues, "基差率不写分母必须报出来"
    assert "8.56" in issues[0]["detail"]


def test_r3_ok_when_denominator_stated():
    text = "基差率 8.56%（以期货价为分母），另按现货分母为 7.89%。偏多。失效条件：基差率跌破 3%。"
    r = R.review(text, symbol="TA", cards=_cards())
    assert "R3_caliber_unstated" not in [i["code"] for i in r["issues"]]


def test_r3_ok_with_nearby_spot_futures_words():
    text = "现货 6,972 / 期货 6,422，基差率 8.56%。偏多。失效条件：跌回 6,100。"
    r = R.review(text, symbol="TA", cards=_cards())
    assert "R3_caliber_unstated" not in [i["code"] for i in r["issues"]], \
        "邻近已写明现货/期货价，视为口径清楚"


def test_r3_ignores_percentile_readings():
    """「基差率 84% 分位」里的 84% 是分位，不是基差率值 —— 实测误报模式。"""
    text = "基差率 84% 分位、深度 BACK 价差 -788（-11.64%），基本面偏多。失效条件：基差率跌破 3%。"
    r = R.review(text, symbol="TA", cards=_cards())
    assert "R3_caliber_unstated" not in [i["code"] for i in r["issues"]], \
        "把分位当成基差率值报了 —— 这是 2026-10-22 17:17 那份报告的实际误报"


# ---------------------------------------------------------------------------
# R4 证据悬空
# ---------------------------------------------------------------------------
def test_r4_flags_bare_direction():
    text = "综合来看建议做多。"
    r = R.review(text, symbol="TA", cards=_cards())
    assert "R4_evidence_void" in [i["code"] for i in r["issues"]]


def test_r4_ok_when_numbers_nearby():
    text = "建议做多，因基差 550 处于 80% 分位，仓单 34,728。"
    r = R.review(text, symbol="TA", cards=_cards())
    assert "R4_evidence_void" not in [i["code"] for i in r["issues"]]


# ---------------------------------------------------------------------------
# R5 失效条件缺失
# ---------------------------------------------------------------------------
def test_r5_flags_direction_without_invalidation():
    text = "基差 550（现货分母），仓单 34,728，库存 1,385,241，利润 23.3。综合偏多。"
    r = R.review(text, symbol="TA", cards=_cards())
    assert "R5_no_invalidation" in [i["code"] for i in r["issues"]]


def test_r5_ok_when_invalidation_present():
    text = "基差 550（现货分母）。偏多，失效条件：基差率跌破 3% 或库存转累库。"
    r = R.review(text, symbol="TA", cards=_cards())
    assert "R5_no_invalidation" not in [i["code"] for i in r["issues"]]


# ── R5 2026-10-23 收紧：从「全文有失效二字」改为「逐句声明」 ──────────
# 【为什么单列一组】旧实现是全文任意位置命中"失效"就算有 —— 于是一句
#   「双方失效价位均未触发」就能让整份没写条件的报告通过。这三条正例 + 四条反例
#   分别是三版判据被真实报告打回时的原句，钉住它们防止下次"顺手放宽"。
@pytest.mark.parametrize("sent", [
    # ① 条件框架 + 失效词
    "若库存连续两周累库，本结论作废。",
    "一旦基差率回到 3% 以下，多头逻辑失效。",
    # ② 条件框架 + 触发标记（不含"失效"二字）
    "若放量增仓突破 6588/6672，则会削弱空头逻辑、转向偏多验证。",
    "- 若跌破 5233，短线转弱。",
    # ③ 失效词 + 数字（不带框架也算给了位置）
    "失效条件：基差率跌破 3%。",
    "止损位 6,100。",
])
def test_r5_accepts_real_invalidation_phrasings(sent):
    text = "综合偏多。" + sent
    r = R.review(text, symbol="TA", cards=_cards())
    codes = [i["code"] for i in r["issues"]]
    assert "R5_no_invalidation" not in codes, f"合法写法被误报: {sent}"


@pytest.mark.parametrize("sent", [
    # v1 被打回的：描述现状，不是条件
    "价格低于 MA20（5,207）约 171 点，空头排列未修复。",
    "收盘 124,420 元/吨，跌破 SMA5/10/20/50 全部均线。",
    # v2 被打回的：叙述性提及 / 已发生的事实
    "同时 14900-14985 阻力真实有效，现已被突破并转为支撑。",
    "圆桌裁决为 draw，双方失效价位均未触发。",
    "短期价格反向只会改变区间内偏向，不会根本推翻中性判断。",
])
def test_r5_rejects_mere_mentions(sent):
    text = "综合偏多。" + sent
    r = R.review(text, symbol="TA", cards=_cards())
    codes = [i["code"] for i in r["issues"]]
    assert "R5_no_invalidation" in codes, f"只提不声明却放行: {sent}"


def test_r5_date_digits_do_not_count_as_price():
    """`2026-10-09` 里的数字不该让一句话被判成"给了触发价位"。"""
    text = "综合偏多。截至 2026-10-09 的库存数据存在失效风险。"
    r = R.review(text, symbol="TA", cards=_cards())
    assert "R5_no_invalidation" in [i["code"] for i in r["issues"]]


def test_r5_neutral_stance_is_medium_not_high():
    """中性也要说清"什么情况下转向"，但它不是方向性建议，罚轻一档。"""
    text = "基差 550（现货分母）。整体维持中性，建议观望。"
    r = R.review(text, symbol="TA", cards=_cards())
    hit = [i for i in r["issues"] if i["code"] == "R5_no_invalidation"]
    assert hit, "中性立场缺失效条件应当报出"
    assert hit[0]["severity"] == "medium", hit[0]


def test_r5_neutral_with_trigger_passes():
    text = "整体中性。若放量突破 6,588 则转向偏多验证。"
    r = R.review(text, symbol="TA", cards=_cards())
    assert "R5_no_invalidation" not in [i["code"] for i in r["issues"]]


def test_r5_no_stance_at_all_is_silent():
    """纯陈述、没有立场，不该要求失效条件。"""
    text = "基差 550（现货分母），仓单 34,728，库存 1,385,241。"
    r = R.review(text, symbol="TA", cards=_cards())
    assert "R5_no_invalidation" not in [i["code"] for i in r["issues"]]


# ---------------------------------------------------------------------------
# R6 周频滞后
# ---------------------------------------------------------------------------
def test_r6_flags_stale_inventory_spoken_as_today():
    text = "库存 1,385,241（分位 23%），基差 550（现货分母），仓单 34,728。偏多，失效条件：库存转累库。"
    r = R.review(text, symbol="TA", cards=_cards())
    assert "R6_stale_not_flagged" in [i["code"] for i in r["issues"]], \
        "库存是 13 天前的周频数据，报告没写时点"


def test_r6_ok_when_as_of_written():
    text = "社会库存截至 2026-10-09 为 1,385,241（周频，滞后 13 天）。基差 550（现货分母）。偏多，失效条件：转累库。"
    r = R.review(text, symbol="TA", cards=_cards())
    assert "R6_stale_not_flagged" not in [i["code"] for i in r["issues"]]


# ---------------------------------------------------------------------------
# 综合判定
# ---------------------------------------------------------------------------
def test_good_report_passes():
    text = (
        "## 结论\n"
        "基差 550（现货分母 7.89% / 期货分母 8.56%），分位 80%；"
        "交易所仓单 34,728，5 日 +304%（基数小）；"
        "社会库存截至 2026-10-09 为 1,385,241（周频，滞后 13 天），分位 23%；"
        "加工利润 23.3，分位 25%。\n"
        "**产业链**：上游 PX（池内）与石脑油、下游聚酯 —— 后两者本系统无数据源，"
        "本报告不对其给出数值。\n"
        "综合偏多，置信度中。\n"
        "**失效条件**：基差率跌破 3%，或库存连续两周累库。\n"
    )
    r = R.review(text, symbol="TA", cards=_cards())
    assert r["pass"] is True, f"合格报告被判不合格: {r['issues']}"
    assert r["score"] == 100, f"扣分项: {[i['code'] for i in r['issues']]}"


def test_r7_flags_missing_chain_nodes():
    """PTA 的 cost/需求两端（石脑油、聚酯）本系统无数据，报告整段不提就是缺口。"""
    text = (
        "基差 550（现货分母 7.89%），仓单 34,728，社会库存截至 2026-10-09 为 1,385,241（周频），"
        "加工利润 23.3。综合偏多。失效条件：基差率跌破 3%。"
    )
    r = R.review(text, symbol="TA", cards=_cards())
    assert "R7_chain_gap" in [i["code"] for i in r["issues"]]


def test_r7_ok_when_one_chain_node_mentioned():
    text = (
        "基差 550（现货分母 7.89%），仓单 34,728，社会库存截至 2026-10-09 为 1,385,241（周频），"
        "加工利润 23.3。上游石脑油无本系统数据。综合偏多。失效条件：基差率跌破 3%。"
    )
    r = R.review(text, symbol="TA", cards=_cards())
    assert "R7_chain_gap" not in [i["code"] for i in r["issues"]], \
        "提到一个关键环节就不该再报（报告可以有侧重）"


def test_r7_silent_for_unknown_variety():
    """没有链条数据的品种不该被 R7 误伤。"""
    r = R.review("基差 550（现货分母）。偏多。失效条件：跌破 3%。", symbol="ZZ", cards=_cards())
    assert "R7_chain_gap" not in [i["code"] for i in r["issues"]]


def test_bad_report_fails_with_multiple_issues():
    text = "基差率 8.56% 很高，综合看多。"
    r = R.review(text, symbol="TA", cards=_cards())
    codes = {i["code"] for i in r["issues"]}
    assert "R3_caliber_unstated" in codes
    assert "R5_no_invalidation" in codes
    assert "R2_missing_key_variable" in codes
    assert r["pass"] is False
    assert r["score"] < 60


def test_single_rule_failure_does_not_break_review(monkeypatch):
    """某条规则抛异常时，其余规则仍要出结果。"""
    def _boom(*a, **k):
        raise RuntimeError("rule crashed")

    monkeypatch.setattr(R, "_r3_caliber_unstated", _boom)
    r = R.review("基差 550。偏多。", symbol="TA", cards=_cards())
    assert r["ok"] is True, "单条规则异常不该让整份检查失效"
    assert isinstance(r["issues"], list)


def test_summarize_is_one_line():
    r = R.review("基差率 8.56%。看多。", symbol="TA", cards=_cards())
    s = R.summarize(r)
    assert "\n" not in s and "研究内容检查" in s


# ---------------------------------------------------------------------------
# 用真实 TA 报告做冒烟（若存在则跑，不存在跳过）
# ---------------------------------------------------------------------------
def test_real_ta_report_smoke():
    import json
    import os

    p = os.path.join(os.path.expanduser("~"), ".tradingagents", "logs")
    if not os.path.isdir(p):
        pytest.skip("无历史分析日志")
    files = sorted(
        (os.path.join(p, f) for f in os.listdir(p) if f.startswith("commodity_TA_")),
        reverse=True,
    )
    if not files:
        pytest.skip("无 TA 分析日志")

    text = open(files[0], encoding="utf-8", errors="ignore").read()
    r = R.review(text, symbol="TA", cards=[])
    assert r["ok"] is True
    assert r["checked_chars"] > 1000
    assert isinstance(r["issues"], list)
    print("\n真实报告检查:", R.summarize(r))
