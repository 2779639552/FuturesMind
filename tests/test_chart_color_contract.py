# -*- coding: utf-8 -*-
"""图表配色契约：防止「曲线太浅」「图例与曲线不对应」两类问题复发。

背景（2026-10-21 用户报障）
--------------------------
· 「有些曲线颜色太浅」：ECharts 画在 canvas 上，**不认 CSS 变量**。
  `itemStyle: { color: 'var(--brand)' }` 这类写法不会报错，只是回落成默认灰；
  `CHART.brand` 的兜底值当时是 `#8fd2f5`（极浅），读不到变量时同样画出看不见的线。
  另有一批硬编码浅色（#fbbf24 亮度 0.76、#40c8e0 0.68）在亮色主题下几乎不可辨。
· 「图例颜色和曲线不对应」：`--candy-1` 与 `--chart-brand` 取值相同，
  多处分类色板写成 `[CHART.brand, CHART.candy1, ...]` ⇒ 图里两条线同色，
  图例上是两个一模一样的色块（实测 chart-overlay 的收盘价/自适应因子、
  wt-h-wh 的前两个仓库、饼图的仓库/厂库）。

这些断言都是**静态**的（不启浏览器），守的是"配置里不该出现什么"。
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

TPL = Path(__file__).resolve().parent.parent / "web_template.html"


def _tpl() -> str:
    return io.open(TPL, encoding="utf-8", newline="").read()


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


@pytest.mark.unit
def test_no_css_var_used_as_echarts_color():
    """ECharts 颜色里不许出现 var(...) —— canvas 解析不了，会静默回落成灰。

    只查**配色键**后面的写法（lineStyle / itemStyle / areaStyle / 轴样式）。
    模板里另有一批 `color: 'var(--x)'` 是给 HTML 面板用的（徽标、面板标题色），
    浏览器能解析，属于合法用法，不能一刀切禁掉。
    """
    s = _tpl()
    pat = re.compile(
        r"(?:lineStyle|itemStyle|areaStyle|axisLine|axisLabel|splitLine|textStyle|nameTextStyle)"
        r"\s*:\s*\{[^}]{0,90}?(var\(--[a-z0-9-]+\))")
    bad = [(s.count("\n", 0, m.start()) + 1, m.group(1)) for m in pat.finditer(s)]
    assert not bad, "ECharts 样式里出现 CSS 变量（会变灰）: %r" % bad[:6]

    # 颜色数组里的裸 var() 也危险（没有 key 可依），单独守三处已知位置
    for marker in ("_CMP_COLORS:", "const colors = {'fixed':"):
        i = s.index(marker)
        line = s[i:s.index("\n", i)]
        assert "var(--" not in line, "%s 里还有 CSS 变量（canvas 不认）: %s" % (marker, line[:80])


@pytest.mark.unit
def test_chart_series_palette_is_distinct_and_readable():
    """统一分类色板必须：8 色互不相同，且亮度都在 0.4~0.72（双主题都能看清）。"""
    s = _tpl()
    i = s.index("get series() {")
    seg = s[i:s.index("},", i)]
    cols = re.findall(r"'(#[0-9a-fA-F]{6})'", seg)
    assert len(cols) == 8, "分类色板应有 8 色，实际 %d: %r" % (len(cols), cols)
    assert len(set(c.lower() for c in cols)) == len(cols), "分类色板存在重复色: %r" % cols
    for c in cols:
        lum = _luminance(c)
        assert 0.40 <= lum <= 0.72, "%s 亮度 %.2f 超出可读区间（>0.72 白底太浅 / <0.40 暗底太黑）" % (c, lum)


@pytest.mark.unit
def test_brand_fallback_is_not_pale():
    """CHART 各色 getter 的兜底值必须是可读实色 —— 读不到 CSS 变量时它就是最终颜色。"""
    s = _tpl()
    for name in ("brand", "brand2", "candy1", "candy2", "candy3", "candy4"):
        m = re.search(r"get %s\(\) \{ return __cssVar\('[^']+', '([^']+)'\)" % name, s)
        assert m, "找不到 getter %s" % name
        fb = m.group(1)
        assert not fb.startswith("var("), "%s 的兜底是 CSS 变量（会漏到 canvas）: %s" % (name, fb)
        if fb.startswith("#"):
            assert _luminance(fb) <= 0.75, "%s 的兜底色 %s 太浅（亮度 %.2f）" % (name, fb, _luminance(fb))


@pytest.mark.unit
def test_candy1_differs_from_brand():
    """撞色之根：candy-1 与 chart-brand 不许同值（否则 [brand, candy1] 前两条同色）。"""
    s = _tpl()
    blocks = re.findall(r"--candy-1:\s*(#[0-9a-fA-F]{3,6});", s)
    brands = re.findall(r"--chart-brand:\s*(#[0-9a-fA-F]{3,6});", s)
    assert len(blocks) >= 2 and len(brands) >= 2, "没找到两套主题的 candy-1 / chart-brand"
    for a, b in zip(blocks, brands):
        assert a.lower() != b.lower(), "candy-1 与 chart-brand 同色（%s）⇒ 分类色板撞色" % a


@pytest.mark.unit
def test_value_colored_series_hide_legend_swatch():
    """按涨跌逐点变色的系列（每日增减 / 日变化 / 基差），图例不该显示单一色块。

    图例只能显示一个颜色，而柱子是红绿交替 ⇒ 色块永远"对应不上"。
    做法是 legend.data 里用 { name, icon: 'none' }，只留文字。
    """
    s = _tpl()
    for name in ("每日增减", "日变化", "基差"):
        pat = r"\{\s*name:\s*'%s',\s*icon:\s*'none'\s*\}" % re.escape(name)
        assert re.search(pat, s), "「%s」的图例项仍然显示色块（会与红绿柱对不上）" % name


@pytest.mark.unit
def test_area_fill_matches_line_color():
    """仓单量的面积填充必须与线同色（原来硬编码橙色、线是蓝色）。"""
    s = _tpl()
    i = s.index("name: '仓单量', type: 'line'")
    seg = s[i:i + 700]
    assert "CHART.brandA25" in seg and "CHART.brandA02" in seg, \
        "仓单量面积渐变没用品牌蓝（会与蓝线不同色）"
    assert "249,115,22" not in seg, "面积渐变仍是硬编码橙色"


@pytest.mark.unit
def test_default_color_injected_for_unstyled_charts():
    """未显式配色的图（饼图/排名柱/散点）要吃到统一色板，而不是 ECharts 默认色。"""
    s = _tpl()
    i = s.index("chart.setOption = function(option, notMerge, lazyUpdate) {")
    seg = s[i:i + 600]
    assert "option.color === undefined" in seg and "CHART.series" in seg, \
        "setOption 没有给未配色图表注入统一色板"


@pytest.mark.unit
def test_no_pale_hardcoded_colors_left():
    """这批硬编码浅色在两套主题下都试过：白底几乎看不见，已统一收敛，不许回退。"""
    s = _tpl()
    pale = {
        "#fbbf24": 0.76,
        "#60a5fa": 0.61,
        "#40c8e0": 0.68,
        "#38bdf8": 0.65,
        "#f472b6": 0.57,
    }
    left = [(c, s.count(c)) for c in pale if s.count(c)]
    assert not left, "仍在使用偏浅的硬编码色: %r" % left
