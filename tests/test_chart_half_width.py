"""横贯整页的「长条图」宽度减半 —— 静态契约护栏（2026-10-21）。

【需求】用户原话：「将目前所有的长度占据整个页面长度的长图（如基本面量化中的期限结构）
缩短到原来的一半长度」。

【判据口径】"长度"= 横向宽度：这类图的共同特征是**宽≈卡片内容宽、宽高比≥4**
（期限结构 1444×240、季节性图 1492×170），问题在横成一条，不在高度。
所以只缩宽度、**高度必须保持原值** —— 高度一改就是压缩信息密度，不是这次要的。

【本文件守什么】
  1. 六个目标必须挂 chart-half（少一个 = 那张还是横贯整页）；
  2. ★ 高度一个都不能改（回归：最容易"顺手"把 height 也调小）；
  3. ★ 必须有窄屏回退 —— 手机上半宽图 x 轴标签会挤成一团；
  4. 本来就是半宽（在 grid-2 里）的图不许再压一半；
  5. resize 修复：明细视图由隐藏变可见时必须补一次全局 resize，
     否则隐藏期间 init 的图会一直画在 100px 画布上（ECharts 对 0 宽容器回落 100）。
"""

from __future__ import annotations

import io
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
TPL = ROOT / "web_template.html"

# id -> 原高度（改宽度前的高度，必须不变）
TARGETS = {
    # 【2026-10-21】chart-q-cmp（数据计算面板）已迁至基本面 A9，并与相关系数矩阵左右并排
    #   ⇒ 它现在本身就在 grid-2 的 50% 格子里，不能再挂 chart-half（会变 1/4 宽）。
    #   高度仍由 test_heights_are_unchanged 之外的单测守（见 test_a9_chart_keeps_height）。
    "chart-overlay": 420,    # 情绪 · 情绪得分 vs 价格
    "wt-h-prod": 420,        # 基本面 · 品种仓单走势
    "chart-f-term": 240,     # 基本面 · 期限结构
    "gt-chart": 170,         # 数据仓库 · 季节性图
}


def _s() -> str:
    return io.open(TPL, encoding="utf-8").read()


def _tag(el_id: str) -> str:
    m = re.search(r'<div[^>]*id="' + re.escape(el_id) + r'"[^>]*>', _s())
    assert m, "找不到容器 #%s" % el_id
    return m.group(0)


def test_all_long_charts_are_half_width():
    for el_id in TARGETS:
        tag = _tag(el_id)
        assert "chart-half" in tag, "#%s 没有 chart-half（仍是横贯整页的长图）" % el_id


def test_heights_are_unchanged():
    for el_id, h in TARGETS.items():
        tag = _tag(el_id)
        assert 'height:%dpx' % h in tag, "#%s 高度应保持 %dpx（本次只缩宽度）: %s" % (el_id, h, tag)


def test_css_rule_and_narrow_fallback():
    s = _s()
    assert ".chart-half { max-width: 50%; }" in s, "缺 .chart-half 规则"
    assert re.search(r"@media \(max-width: 900px\) \{ \.chart-half \{ max-width: 100%; \} \}", s), \
        "缺窄屏回退（手机上半宽图放不下 x 轴）"
    # 宽度必须是相对值：写死 px 会在不同视口下偏离"一半"
    # （只查 .chart-half 自己的规则 —— 全站别处本来就有 max-width:720px 的用法）
    assert not re.search(r"\.chart-half\s*\{[^}]*max-width:\s*\d+px", s), \
        "别写死像素，视口一变就不再是一半"


def test_dynamic_research_charts_also_half():
    s = _s()
    assert '<div class="chart-container chart-half" id="chart-${P}-r-' in s, \
        "研报基本面指标那 7 类是动态生成的，模板字符串里也要挂 chart-half"


def test_existing_half_width_charts_untouched():
    """本来就在 grid-2 里各占一半的图，不能再加 chart-half（会变四分之一宽）。"""
    for el_id in ("chart-f-inv", "chart-f-vd", "chart-f-basis", "chart-f-basispf",
                  "wt-rank-qty", "wt-c1", "chart-leadlag", "chart-divergence",
                  "chart-q-cmp"):
        tag = _tag(el_id)
        assert "chart-half" not in tag, "#%s 本就是半宽 grid，不该再压一半" % el_id


def test_detail_views_resize_charts_after_becoming_visible():
    """隐藏期间 init 的图（0 宽 → 100px 画布）必须在明细可见后补 resize。"""
    s = _s()
    for fn in ("qOpenDetail(code) {", "fOpenDetail(code) {"):
        i = s.index(fn)
        j = s.index("\n },", i)
        seg = s[i:j]
        assert "_resizeAllCharts()" in seg, "%s 里没有补 resize，画布会停在 100px" % fn
        assert "setTimeout" in seg, "resize 要延到布局完成后再做"


def test_template_stays_crlf():
    raw = io.open(TPL, "rb").read()
    assert raw.count(b"\n") == raw.count(b"\r\n"), "模板出现裸 LF(本项目铁律：CRLF)"


def test_a9_chart_keeps_height():
    """A9 曲线移进 grid-2 后仍必须是 360px 高（只调宽度不调高度是本次的口径）。"""
    tag = _tag("chart-q-cmp")
    assert "height:360px" in tag, "A9 曲线高度应保持 360px: %s" % tag
    assert "chart-half" not in tag, "已在 50% 格子里，不能再挂 chart-half"
