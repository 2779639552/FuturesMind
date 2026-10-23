# -*- coding: utf-8 -*-
"""研究内容检查的「闭环」护栏（2026-10-23）。

背景：这一版之前 `review()` 只被 `logger.warning` 消费 —— 检查出来的问题只进日志，
既不影响结论、也不出现在报告里。那叫自检日志，不叫检查节点。

现在闭环分两处，本文件逐条钉住：

  事前（synthesis 之前）  review → gate_block → 注入综合研判提示词
  事后（synthesis 之后）  review → acceptance_section → 追加进报告正文

最关键的一条是 `test_gate_block_reaches_synthesis_prompt`：它直接捕获送进
LLM 的 prompt 并断言约束块在场 —— 只测 `gate_block()` 返回非空是不够的，
函数写对了却没接上，和没写一样。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tradingagents.research_review import (
    acceptance_section,
    apply_acceptance,
    gate_block,
    review,
)


def _cards() -> list[dict]:
    """最小可用的数据卡集合（R1/R2/R6 依赖它）。"""
    return [
        {"id": "gtja.basis.abs", "label": "基差", "value": 550,
         "kind": "price_diff", "age_days": 1, "as_of": "2026-10-22"},
        {"id": "gtja.inventory.social", "label": "社会库存", "value": 1385241,
         "kind": "qty", "age_days": 13, "as_of": "2026-10-09"},
        {"id": "gtja.warehouse.qty", "label": "交易所注册仓单", "value": 34728,
         "kind": "qty", "age_days": 1, "as_of": "2026-10-22"},
    ]


def _bad_result() -> dict:
    """一份「全都有问题」的报告：无分母、无失效条件、无产业链、无数据时点。"""
    return review("基差 550，基差率 8.56%，偏多。", symbol="TA", cards=_cards())


def _good_result() -> dict:
    return review(
        "基差 550（现货分母 7.89% / 期货分母 8.56%），分位 80%；"
        "社会库存 1,385,241，截至 2026-10-09（周频，滞后 13 天）；"
        "交易所仓单 34,728，截至 2026-10-22。"
        "产业链上游石脑油、下游聚酯均需关注，本系统无该环节数据。\n"
        "偏多，置信度中。失效条件：基差率跌破 3%，或库存连续两周累库。",
        symbol="TA",
        cards=_cards(),
    )


# ---------------------------------------------------------------------------
# 1. 事前约束块
# ---------------------------------------------------------------------------
class TestGateBlock:
    def test_pass_returns_empty(self):
        assert gate_block(_good_result()) == "", "通过的检查不该产生任何约束文本"

    def test_none_and_empty_are_safe(self):
        assert gate_block(None) == ""
        assert gate_block({}) == ""
        assert gate_block({"ok": False}) == ""

    def test_fail_returns_block_with_header(self):
        block = gate_block(_bad_result())
        assert block, "未通过时必须产出约束块"
        assert "质量门" in block and "未通过" in block
        assert "硬性要求" in block

    def test_high_issues_come_first(self):
        block = gate_block(_bad_result())
        body = [ln for ln in block.splitlines() if ln[:2] in ("1.", "2.", "3.", "4.")]
        assert body, "应至少列出一条问题"
        assert body[0].startswith("1. [HIGH]"), f"第一条应是 HIGH，实际: {body[0][:40]}"

    def test_low_severity_excluded(self):
        result = {
            "ok": True, "pass": False, "score": 95,
            "issues": [{"code": "R4_evidence_void", "severity": "low",
                        "detail": "方向句附近无数值", "evidence": ""}],
        }
        assert gate_block(result) == "", "low 级不该占用提示词预算"

    def test_at_most_four_issues(self):
        issues = [{"code": "R3_caliber_unstated", "severity": "high",
                   "detail": "问题%d" % i, "evidence": ""} for i in range(8)]
        block = gate_block({"ok": True, "pass": False, "score": 0, "issues": issues})
        numbered = [ln for ln in block.splitlines() if ln[:2] in ("1.", "2.", "3.", "4.", "5.")]
        assert len(numbered) == 4, f"最多列 4 条，实际 {len(numbered)}"

    def test_within_char_budget(self):
        """方法论块已超预算 2.3 倍，这块必须短。"""
        issues = [{"code": "R3_caliber_unstated", "severity": "high",
                   "detail": "很长的细节" * 20, "evidence": ""} for _ in range(8)]
        block = gate_block({"ok": True, "pass": False, "score": 0, "issues": issues})
        assert len(block) <= 720, f"约束块 {len(block)} 字，超出 700 预算"

    def test_each_issue_carries_actionable_hint(self):
        block = gate_block(_bad_result())
        assert "必须" in block or "不许" in block, "只指出问题不给动作，模型无从执行"


# ---------------------------------------------------------------------------
# 2. 事后验收小节
# ---------------------------------------------------------------------------
class TestAcceptanceSection:
    def test_pass_returns_empty(self):
        assert acceptance_section(_good_result()) == ""

    def test_fail_lists_all_issues(self):
        result = _bad_result()
        sec = acceptance_section(result)
        assert sec and "质量门" in sec
        for it in result["issues"]:
            assert it["detail"][:20] in sec, f"问题 {it['code']} 未出现在验收小节"

    def test_declares_it_does_not_change_rating(self):
        """验收小节不能偷偷改评级 —— 它只陈述事实。"""
        sec = acceptance_section(_bad_result())
        assert "不参与评级" in sec

    def test_renders_as_markdown_section(self):
        sec = acceptance_section(_bad_result())
        assert sec.lstrip().startswith("## "), "应能直接拼进报告（以二级标题开头）"


# ---------------------------------------------------------------------------
# 2b. 拼进报告的接线（原来内联在 Flask 路由里，测不到）
# ---------------------------------------------------------------------------
class TestApplyAcceptance:
    def test_failed_review_appends_to_synthesis(self):
        out, sec = apply_acceptance({"synthesis": "RATING: 偏多 | CONFIDENCE: 中 | SCORE: 7"},
                                    _bad_result())
        assert sec, "未通过却渲染出空小节"
        assert out["synthesis"].endswith(sec.strip()[-20:]), "小节没拼在报告末尾"
        assert "质量门" in out["synthesis"]

    def test_rating_header_survives(self):
        """拼小节不能破坏首行 RATING 头（下游正则依赖它）。"""
        head = "RATING: 偏多 | CONFIDENCE: 中 | SCORE: 7"
        out, _ = apply_acceptance({"synthesis": head + "\n\n正文"}, _bad_result())
        assert out["synthesis"].startswith("RATING: 偏多"), "评级头被破坏"

    def test_does_not_mutate_input(self):
        src = {"synthesis": "原文"}
        out, _ = apply_acceptance(src, _bad_result())
        assert src["synthesis"] == "原文", "不该原地修改调用方传进来的 dict"
        assert out is not src

    def test_passing_review_leaves_report_untouched(self):
        src = {"synthesis": "原文"}
        out, sec = apply_acceptance(src, _good_result())
        assert out["synthesis"] == "原文" and sec == ""

    def test_no_synthesis_key_is_safe(self):
        out, sec = apply_acceptance({"technical_analyst": "x"}, _bad_result())
        assert sec, "小节仍应生成（供接口单独返回）"
        assert "synthesis" not in out


# ---------------------------------------------------------------------------
# 3. 事前节点
# ---------------------------------------------------------------------------
class TestReviewNodeContract:
    def _node(self):
        from commodity_demo import create_research_review_node

        return create_research_review_node()

    def _state(self, text: str) -> dict:
        return {
            "company_of_interest": "TA",
            "technical_report": text,
            "fundamental_report": "",
            "macro_report": "",
            "sentiment_report": "",
            "discussion_summary": "",
        }

    def test_failing_content_produces_gate(self):
        with patch(
            "tradingagents.dataflows.analysis_bundle.build_cards", return_value=[]
        ):
            out = self._node()(self._state("基差率 8.56%，偏多。"))
        assert out["quality_gate"], "内容有问题却没产出约束块"
        assert out["research_review"]["pass"] is False

    def test_passing_content_produces_no_gate(self):
        good = (
            "基差 550（现货分母 7.89% / 期货分母 8.56%）；"
            "社会库存 1,385,241（截至 2026-10-09，周频，滞后 13 天）；"
            "交易所仓单 34,728（截至 2026-10-22）。偏多。失效条件：基差率跌破 3%。"
            "产业链上游石脑油、下游聚酯需关注，本系统无该环节数据。"
        )
        with patch(
            "tradingagents.dataflows.analysis_bundle.build_cards",
            return_value=_cards(),
        ):
            out = self._node()(self._state(good))
        assert out["quality_gate"] == "", f"合格内容不应被约束: {out['quality_gate'][:80]}"

    def test_fail_open_on_exception(self):
        """检查器自己炸了，不能拖垮分析链路。"""
        with patch(
            "tradingagents.research_review.review", side_effect=RuntimeError("boom")
        ):
            out = self._node()(self._state("随便写点什么"))
        assert out == {"research_review": {}, "quality_gate": ""}

    def test_cards_failure_does_not_break_node(self):
        with patch(
            "tradingagents.dataflows.analysis_bundle.build_cards",
            side_effect=RuntimeError("gtja down"),
        ):
            out = self._node()(self._state("基差率 8.56%，偏多。"))
        assert "quality_gate" in out and "research_review" in out


# ---------------------------------------------------------------------------
# 4. ★ 闭环的关键证据：约束块真的进了综合研判的提示词
# ---------------------------------------------------------------------------
class _CapturingLLM:
    def __init__(self):
        self.prompts: list[str] = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return MagicMock(content="RATING: 中性 | CONFIDENCE: 中 | SCORE: 5")


class TestGateReachesSynthesis:
    def _run(self, gate: str) -> str:
        from commodity_demo import create_synthesis_node

        llm = _CapturingLLM()
        node = create_synthesis_node(llm)
        node(
            {
                "company_of_interest": "TA",
                "technical_report": "技术面偏多",
                "fundamental_report": "基差 550",
                "macro_report": "宏观中性",
                "sentiment_report": "",
                "discussion_summary": "双方分歧在库存",
                "quality_gate": gate,
            }
        )
        assert llm.prompts, "节点没有调用 LLM"
        return llm.prompts[0]

    def test_gate_block_reaches_synthesis_prompt(self):
        gate = gate_block(_bad_result())
        prompt = self._run(gate)
        assert gate.strip() in prompt, (
            "约束块没有进入综合研判的提示词 —— 事前约束形同未接"
        )

    def test_gate_placed_before_task_instructions(self):
        """约束必须在模型「还在决定怎么判」的位置，而不是文末。"""
        gate = gate_block(_bad_result())
        prompt = self._run(gate)
        assert prompt.index(gate.strip()) < prompt.index("**Your Task**"), (
            "约束块出现在任务说明之后，会被模型当作补充材料而不是前提"
        )

    def test_prompt_unchanged_when_gate_empty(self):
        prompt = self._run("")
        assert "质量门" not in prompt, "通过检查时不该出现任何质量门字样"
        assert "**Your Task**" in prompt, "提示词结构被破坏"


# ---------------------------------------------------------------------------
# 5. 图结构：节点位置正确，且四种模块组合都能编译
# ---------------------------------------------------------------------------
class TestGraphPlacement:
    def _build(self, **kw):
        from commodity_demo import build_commodity_graph

        cfg = {"llm_provider": "mock", "quick_think_llm": "q", "deep_think_llm": "d"}
        client = MagicMock()
        client.get_llm.return_value = MagicMock()
        with patch("commodity_demo.create_llm_client", return_value=client):
            return build_commodity_graph(cfg, enable_feedback=False, **kw)

    @pytest.mark.parametrize(
        "kw,expect_in",
        [
            (dict(include_debate=True), {"debate_moderator"}),
            (dict(include_debate=False),
             {"technical_analyst", "fundamental_analyst", "macro_analyst"}),
        ],
    )
    def test_review_sits_between_debate_and_synthesis(self, kw, expect_in):
        app, _ = self._build(include_sentiment=False, include_synthesis=True, **kw)
        edges = {(e.source, e.target) for e in app.get_graph().edges}
        into = {s for s, t in edges if t == "research_review"}
        outof = {t for s, t in edges if s == "research_review"}
        assert into == expect_in, f"入边应为 {expect_in}，实际 {into}"
        assert outof == {"synthesis"}, f"research_review 必须只接 synthesis，实际 {outof}"

    def test_absent_in_minimal_mode(self):
        app, _ = self._build(
            include_sentiment=False, include_debate=False,
            include_synthesis=False, include_scenario=False,
        )
        assert "research_review" not in set(app.get_graph().nodes)


# ---------------------------------------------------------------------------
# 6. 事后验收的范围：只看最终报告，不许拿分析师报告"顶包"
# ---------------------------------------------------------------------------
class TestPostReviewScope:
    def test_post_review_reads_synthesis_only(self):
        """实测：14 份报告里综合研判单独看只有 1 份缺失效条件，
        拼接上技术/基本面后**全部通过** —— 因为分析师报告里的"失效价位"给结论顶了包。
        读者只看最终结论，验收就必须按最终结论判。
        """
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "web_app.py").read_text(
            encoding="utf-8", errors="ignore")
        i = src.index("_review_text =")
        seg = src[i:i + 400]
        assert "_reports.get(\"synthesis\")" in seg, "事后检查没有取 synthesis"
        for k in ("technical_analyst", "fundamental_analyst"):
            assert k not in seg, f"事后检查又把 {k} 拼进来了 —— 会让分析师报告替结论顶包"
