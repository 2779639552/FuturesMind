# -*- coding: utf-8 -*-
"""隆众主体品种映射(oilchem_pool)护栏。

【为什么需要】`oilchem_pool.SUBJECT_MAP` 是**硬编码**的「隆众主体词 → 池内码」表：
  · 池子变更（扩池/缩池）后映射表可能指向**已出池**的码 ⇒ 归属校验会把人改到池外；
  · 宽词可能抢走窄词（「橡胶」抢「天然橡胶」）⇒ 判错主体。
这两类都是**静默**的（不报错，只是判错），必须用断言钉住。

【关键判据】
  1. 映射表每个值都必须在 ACTIVE_VARIETIES 内（防漂移）；
  2. 综合文（早读/聚焦/专题/周报/月报）**永不**判成 out_of_pool
     —— 这是"不做采集端硬门禁"的依据，必须钉住；
  3. 最长优先：天然橡胶 必须先于 橡胶 命中；
  4. 宽产业链词（橡胶/塑料…）不得作为主体映射到某个码
     —— 实测隆众常把"橡胶"当下游需求方顺口提及，误判过 RU 共 5 次。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import oilchem_pool as op  # noqa: E402
from tradingagents.dataflows.commodity_futures import ACTIVE_VARIETIES  # noqa: E402


class TestMapIntegrity:
    def test_all_mapped_codes_are_in_pool(self):
        """映射表的每个值都必须在当前品种池内 —— 防池变更后映射漂移。"""
        bad = sorted({c for c in op.SUBJECT_MAP.values() if c not in ACTIVE_VARIETIES})
        assert not bad, "SUBJECT_MAP 指向了池外码(池变更后忘记同步): %s" % bad

    def test_pool_is_nonempty_and_loaded(self):
        """池必须成功加载(而非回退到内置副本后与项目脱节)。"""
        assert len(op.POOL) >= 20, "POOL 加载异常: %s" % sorted(op.POOL)
        assert op.POOL == frozenset(ACTIVE_VARIETIES), "POOL 与项目 ACTIVE_VARIETIES 不一致"

    def test_broad_chain_words_not_mapped_to_a_code(self):
        """宽产业链词不得映射到具体码 —— 它们是"泛指"，映射会造成系统性误判。

        【实测背景】隆众文章常写「下游橡胶/塑料需求偏弱」，
        早期兜底表把「橡胶」当主体 ⇒ 基础油/磷酸/成品油/醚后 4 篇全被误配成 RU。
        """
        for w in ("橡胶", "塑料", "烯烃", "芳烃", "裂解", "新材料"):
            assert w not in op.SUBJECT_MAP, "宽词 %s 不应映射到具体品种" % w


class TestSubjectCode:
    def test_longest_match_wins(self):
        """「天然橡胶」必须先于「橡胶」命中，否则被宽词抢走。"""
        assert op.subject_code("[隆众聚焦]：天然橡胶价格走强") == "RU"
        assert op.subject_code("[隆众聚焦]：顺丁橡胶价格上行") == "BR"

    def test_bracket_subject_precision(self):
        assert op.subject_code("[沥青]：三季度沥青库存下降") == "BU"
        assert op.subject_code("[成品油]：航空燃油附加费上涨") == "SC"
        assert op.subject_code("[基础油]：市场大幅上涨") is None

    def test_downstream_polyester_maps_to_ta(self):
        """涤纶/聚酯是 PTA 的直接下游，池内锚点取 TA。

        【用户 2026-10-14 明确】"涤纶长丝是聚酯下游，是 TA/PX/EG 的下游产品，不是上游"。
        """
        assert op.subject_code("[隆众聚焦]：涤纶长丝双向挤压") == "TA"
        assert op.subject_code("[隆众聚焦]：聚酯瓶片价格上行") == "TA"

    def test_glue_free_when_unknown(self):
        """判不出主体必须返回 None（上层据此"不动"），不得瞎猜。"""
        assert op.subject_code("隆众能化早读：地缘局势提升供应风险") is None
        assert op.subject_code("[隆众聚焦]：市场情绪转弱") is None


class TestClassify:
    def test_comprehensive_never_out_of_pool(self):
        """★ 综合/深度文永不判 out_of_pool —— 这是"不做采集端硬门禁"的核心依据。

        实测按"主体池外即拦"会误伤 3 篇含池内实质内容的文章
        （DOTP→TA、磷酸铁→LC、丙酮→BZ），故门禁被否决。
        """
        comprehensive = [
            "隆众能化早读：地缘局势提升供应风险",
            "[隆众聚焦]：浮法玻璃四季度展望",
            "[国庆专题]：长假DMF市场明稳暗降",
            "[煤炭周报]：隆众资讯煤炭周报",
            "[PTA月报]：隆众资讯PTA月报",
        ]
        for t in comprehensive:
            assert op.is_comprehensive(t), "%s 应被识别为综合文" % t
            assert op.classify(t) != "out_of_pool", "%s 不应判 out_of_pool" % t

    def test_explicit_out_of_pool_detected(self):
        """明确池外且有方括号主体 ⇒ out_of_pool（仅用于报告，不做拦截）。"""
        assert op.classify("[基础油]：10月上旬基础油市场大幅上涨") == "out_of_pool"
        assert op.classify("[聚乙烯]：成本重掌定价权") == "out_of_pool"

    def test_in_pool_detected(self):
        assert op.classify("[沥青]：三季度市场供需双增") == "in_pool"
        assert op.classify("[成品油]：供应风险难消") == "in_pool"

    def test_unknown_falls_back_to_adjacent(self):
        """判不出主体时保守归 adjacent（放行），宁可多花 LLM 也不错杀内容。"""
        assert op.classify("某个没有主体词的标题") == "adjacent"

    def test_report_counts(self):
        rep = op.report(["[沥青]：a", "[基础油]：b", "隆众能化早读：c"])
        assert rep["pool_size"] == len(op.POOL)
        assert rep["counts"].get("in_pool") == 1
        assert rep["counts"].get("out_of_pool") == 1
        assert rep["counts"].get("adjacent") == 1
