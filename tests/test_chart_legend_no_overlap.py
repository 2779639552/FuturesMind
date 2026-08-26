"""/_baseChartOptions must stack the legend below the title.

Regression test for 2026-08-25: with `title: { left: 'center' }` and a legend
left at its ECharts default (`top: auto`), ECharts lays the legend out on the
SAME row as the centered title — so the title text and legend items overlapped
in every chart built from `_baseChartOptions` (incl. 运行分析 → 预测 vs 实际对比).

Verified with Playwright bounding-rect measurement: title/legend/grid are three
non-overlapping rows after the fix. This test guards the config statically so
the overlap can't silently regress.
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TPL = ROOT / "web_template.html"


@pytest.mark.unit
class TestChartLegendNoOverlap:
    @pytest.fixture(scope="class")
    def base_opts_src(self):
        txt = TPL.read_text(encoding="utf-8")
        m = re.search(r"_baseChartOptions\(title\) \{\n(.*?)\n  \},\n", txt, re.S)
        assert m, "_baseChartOptions not found in web_template.html"
        return m.group(1)

    def test_legend_has_explicit_top(self, base_opts_src):
        # Without explicit `top`, ECharts puts the legend on the title's row → overlap.
        m = re.search(r"legend:\s*\{\s*top:\s*(\d+)", base_opts_src)
        assert m, "base legend must have an explicit `top` below the title"
        assert int(m.group(1)) >= 20

    def test_title_pinned_to_top_row(self, base_opts_src):
        m = re.search(r"title:\s*\{[^}]*?top:\s*(\d+)", base_opts_src)
        assert m, "base title must have an explicit top"
        assert int(m.group(1)) < 15

    def test_grid_top_reserves_both_rows(self, base_opts_src):
        # grid.top must leave room for title + legend rows (>= ~52px).
        m = re.search(r"grid:\s*\{[^}]*?top:\s*(\d+)", base_opts_src)
        assert m and int(m.group(1)) >= 52, "grid.top too small → legend/plot overlap"

    def test_bare_legend_pattern_removed(self, base_opts_src):
        # The old form `legend: { textStyle: {...} }` (top auto → overlap) must be gone.
        assert not re.search(r"legend:\s*\{\s*textStyle", base_opts_src)
