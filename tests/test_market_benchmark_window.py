"""市场(买入持有) benchmark must follow the selected 区间 in multi-strategy compare.

Regression test for 2026-08-25: `runMultiCompare` fetched the benchmark price with a
hard-coded `?days=365` and anchored the return at the FIRST price of that fixed
365-day fetch. So 买入持有 = (today / 365-days-ago − 1), independent of the user's
startDate → the number never changed when the 区间 was adjusted, while the strategy
curves (which run on the selected window) did.

Fix: fetch days is computed from the window span (+30d buffer, ≤730d), and the
return is anchored at the FIRST price *inside* the window.
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TPL = ROOT / "web_template.html"


@pytest.mark.unit
class TestMarketBenchmarkWindow:
    @pytest.fixture(scope="class")
    def multi_compare_src(self):
        txt = TPL.read_text(encoding="utf-8")
        m = re.search(r"async runMultiCompare\(\) \{\n(.*?)\n  \},\n", txt, re.S)
        assert m, "runMultiCompare not found in web_template.html"
        return m.group(1)

    def test_benchmark_fetch_days_dynamic(self, multi_compare_src):
        # must no longer hard-code days=365
        assert "?days=365" not in multi_compare_src
        assert "fetchDays" in multi_compare_src
        assert "?days=' + fetchDays" in multi_compare_src

    def test_benchmark_anchor_is_window_start(self, multi_compare_src):
        # the buy-and-hold base must be the first price inside the window,
        # not the first price of the whole fetch
        assert "commonDates.length === 0) base = p.close" in multi_compare_src
