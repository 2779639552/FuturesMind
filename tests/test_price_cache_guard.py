"""Price cache must not silently truncate across end_date requests.

Regression test for the guard added 2026-08-25: `_response_cache` keys on
``price:{main_sym}`` only, so within the 5-min TTL a request with a NEWER
end_date would hit the old snapshot and silently return data only up to the
old date. `_cached_covers_end` treats that case as a cache miss and refetches.
"""

import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tradingagents.dataflows.commodity_futures import (
    _cached_covers_end,
    _response_cache,
    get_futures_price,
)


def _df_upto(n_days, start="2025-01-01"):
    """Raw akshare-style frame with Chinese columns, ending on the nth day."""
    base = datetime.strptime(start, "%Y-%m-%d")
    dates = [(base + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n_days)]
    return pd.DataFrame(
        {
            "日期": dates,
            "开盘价": [100.0] * n_days,
            "最高价": [101.0] * n_days,
            "最低价": [99.0] * n_days,
            "收盘价": [100.5] * n_days,
            "成交量": [1000] * n_days,
            "持仓量": [2000] * n_days,
        }
    )


@pytest.mark.unit
class TestCacheCoversEnd:
    def test_covers_when_cached_reaches_end(self):
        assert _cached_covers_end(_df_upto(6), "2025-01-05") is True

    def test_not_covers_when_end_newer(self):
        assert _cached_covers_end(_df_upto(5), "2025-01-06") is False

    def test_missing_date_column_means_refetch(self):
        df = pd.DataFrame({"close": [1.0]})
        assert _cached_covers_end(df, "2025-01-06") is False

    def test_empty_frame_means_refetch(self):
        assert _cached_covers_end(_df_upto(0), "2025-01-06") is False


@pytest.mark.unit
class TestPriceRefetchGuard:
    @patch.dict(sys.modules, {"akshare": None}, clear=False)
    def test_newer_end_date_refetches(self):
        _response_cache.clear()
        fake = MagicMock()
        fake.futures_main_sina.side_effect = lambda **kw: _df_upto(5)  # ends 2025-01-05
        sys.modules["akshare"] = fake
        try:
            # First request populates the cache.
            get_futures_price("RB", "2025-01-01", "2025-01-05")
            assert fake.futures_main_sina.call_count == 1
            # Same window again → pure cache hit, no refetch.
            get_futures_price("RB", "2025-01-01", "2025-01-05")
            assert fake.futures_main_sina.call_count == 1
            # Older end_date within cached coverage → cache hit, no refetch.
            get_futures_price("RB", "2025-01-01", "2025-01-03")
            assert fake.futures_main_sina.call_count == 1
            # NEWER end_date than cached max → guard forces a refetch.
            get_futures_price("RB", "2025-01-01", "2025-01-06")
            assert fake.futures_main_sina.call_count == 2
        finally:
            sys.modules.pop("akshare", None)
            _response_cache.clear()
