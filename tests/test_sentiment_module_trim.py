# -*- coding: utf-8 -*-
r"""情绪数据页模块精简 回归护栏（2026-10-13）。

用户要求：隐藏「情绪数据」大模块下「分析工具」中的 作者 / 框架说明 / Agent验证 /
Agent回测 / 图谱（"等小模块"），以及「数据更新」里的「更新日志」。
保留「综合分析」「事件」两个子模块。

本文件守四件事：
  1. 子导航只剩 2 项（综合分析 / 事件）—— 防止被删模块回潮；
  2. 被删模块的容器 id 不残留 —— `_secScan` **按 DOM 结构扫描、不看可见性**，
     残留的 `.analysis-sub` 会变成"点了没反应的僵尸索引项"（本项目 09-30 实测过同类症状）；
  3. ★ `#update-log` 载体必须留在 DOM 里 —— `runDataUpdate()` 对它是**非 null-safe**
     调用（`log.textContent = ''` / `log.innerHTML += …`），删掉即抛 TypeError
     ⇒「一键更新」永久卡在禁用态。这条最容易被后续"清理无用元素"误删。
  4. `_SEC_IDX_VER` 已 +1（结构变了必须重建索引，否则旧浏览器残留已删模块的索引项）。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "web_template.html"

# 被移除的 5 个子模块（data-asub 值 ↔ 面板 id 后缀）
REMOVED = ("authors", "framework", "agentval", "agentbt", "graph")
# 面板里独有的容器 id（HTML 属性形态才算残留）
REMOVED_IDS = ("asub-authors", "asub-framework", "asub-agentval", "asub-agentbt", "asub-graph",
               "authors-table", "chart-graph", "chart-sankey",
               "btn-val-start", "btn-bt-start", "val-detail", "bt-detail",
               "chart-val-dir", "chart-val-score", "chart-bt-compare")


def _src() -> str:
    return TEMPLATE.read_text(encoding="utf-8", errors="replace")


def _subnav_block(src: str) -> str:
    i = src.index('id="analysis-subnav"')
    return src[i: src.index("</div>", i)]


class TestSubnavTrimmed:
    def test_only_two_submodules_left(self):
        nav = _subnav_block(_src())
        n = nav.count("data-asub=")
        assert n == 2, f"分析工具子导航应有 2 个按钮(综合分析/事件)，实际 {n} 个"
        assert 'data-asub="comprehensive"' in nav and 'data-asub="events"' in nav

    def test_analysis_sub_panel_count(self):
        src = _src()
        n = len(re.findall(r'class="analysis-sub"', src))
        assert n == 2, f'源码里 class="analysis-sub" 面板应为 2 个，实际 {n} 个'


class TestRemovedModulesGone:
    def test_no_removed_buttons(self):
        src = _src()
        for k in REMOVED:
            assert f'data-asub="{k}"' not in src, f"已移除的子模块按钮回潮: {k}"

    def test_no_removed_containers(self):
        src = _src()
        for i in REMOVED_IDS:
            assert f'id="{i}"' not in src, f"已移除模块的容器 id 残留: {i}"

    def test_switch_has_no_dead_branches(self):
        src = _src()
        assert "if (sub === 'authors')" not in src, "switchAnalysisSub 里的 authors 死分支应已删除"
        assert "if (sub === 'graph')" not in src, "switchAnalysisSub 里的 graph 死分支应已删除"

    def test_orphan_functions_have_no_callsite(self):
        """渲染函数保留(便于将来恢复)，但不得再被任何地方调用 —— 否则会引用不存在的容器。"""
        src = _src()
        for call in ("this.loadAuthors(", "this.loadGraphChart(", "this.loadSankeyChart(",
                     "App.startAgentValidation(", "App.startBatchBacktest("):
            assert call not in src, f"孤儿函数仍被调用: {call}"


class TestUpdateLogCarrierKept:
    """★ 本类是最重要的一条：载体删了会炸「一键更新」。"""

    def test_carrier_exists_in_dom(self):
        src = _src()
        assert 'id="update-log"' in src, (
            "#update-log 载体被删了 —— runDataUpdate() 对它是非 null-safe 调用，"
            "删掉会抛 TypeError 让「一键更新」永久卡在禁用态"
        )

    def test_carrier_is_hidden_and_not_a_card(self):
        src = _src()
        m = re.search(r"<div[^>]*id=\"update-log\"[^>]*>", src)
        assert m, "找不到 #update-log 的标签"
        tag = m.group(0)
        assert "display:none" in tag.replace(" ", ""), f"载体应保持隐藏: {tag}"
        assert "card" not in tag, f"载体不应是 .card（否则会进小模块索引）: {tag}"

    def test_no_visible_log_heading(self):
        src = _src()
        assert "<h3>更新日志</h3>" not in src, "「更新日志」标题卡应已移除"


class TestSecIndexVerBumped:
    """本次精简必须触发索引重建。

    ★ 这里刻意**不写死具体版本号**：精确值的守门人是
    `test_page_tools_variety_state.py::test_sec_index_version_bumped_for_matrix_move`
    （它按设计故意咬死精确值，用来抓"改了结构忘了 +1"）。
    本类只断言"版本 ≠ 精简前的 23" —— 这既证明了本次改动确实 bump 过，
    又不会在下次 bump 时变成第三处需要同步维护的硬编码。
    """

    PRE_TRIM = '23'

    def test_version_moved_off_pre_trim_value(self):
        src = _src()
        m = re.search(r"_SEC_IDX_VER:\s*'(\d+)'", src)
        assert m, "未找到 _SEC_IDX_VER"
        assert m.group(1) != self.PRE_TRIM, (
            f"_SEC_IDX_VER 仍是 {self.PRE_TRIM}（精简前值）⇒ 索引不会重建，"
            "旧浏览器会残留已删模块的僵尸索引项"
        )

    def test_version_is_at_least_24(self):
        src = _src()
        m = re.search(r"_SEC_IDX_VER:\s*'(\d+)'", src)
        assert m and int(m.group(1)) >= 24, "版本号不应低于本次精简对应的 24"

    def test_sentiment_not_whitelisted_for_flat(self):
        """情绪页仍参与折叠 ⇒ 才需要重建索引；若哪天被加入平铺名单，本组断言需同步复核。

        ⚠️ 锚点必须用 `new Set([` 而不是 `_SEC_FLAT_TABS` —— 后者在源码里先被
        多处**注释**提到（"见 _SEC_FLAT_TABS 注释"），拿它 index() 会切到注释区间，
        断言变成对注释取子串（写这条时第一版就这么翻的车）。
        """
        src = _src()
        blk = src[src.index("_SEC_FLAT_TABS: new Set(["):]
        blk = blk[:blk.index("])")]
        assert "'sentiment'" not in blk, "sentiment 被加入平铺名单，需复核索引版本号断言"
