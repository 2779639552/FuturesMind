"""基差缓存守卫:get_futures_basis 6h TTL 缓存 + 起止区间覆盖(2026-09-01 新增)。

背景: futures_spot_price_daily 是东财网页爬取接口,实测单次 13~175s(2026-09-01 实测 175s),
是数据看板刷新慢的根因。给 get_futures_basis 加了 _basis_cache(键 "basis:{品种}",6h TTL),
并靠 _cached_covers_range 保证缓存同时覆盖请求的起止区间,避免窄缓存静默截断宽请求
(同品种 days=60 与 days=365 复用同一条缓存)。本测试用 mock 掉 akshare,不联网。
"""

from unittest import mock

import pandas as pd
import pytest

from tradingagents.dataflows.commodity_futures import (
    _basis_cache,
    _cached_covers_range,
    get_futures_basis,
)

_DATES = ("2026-08-25", "2026-08-26", "2026-08-27")


def _fake_df() -> pd.DataFrame:
    """合成东财基差 DataFrame(中文列名,与 futures_spot_price_daily 输出同构)。"""
    return pd.DataFrame({
        "日期": list(_DATES),
        "品种": ["螺纹钢"] * 3,
        "现货价格": [3400.0, 3410.0, 3420.0],
        "近月合约": ["RB2510"] * 3,
        "近月合约价格": [3380.0, 3390.0, 3400.0],
        "近月基差": [20.0, 20.0, 20.0],
        "近月基差率": [0.59, 0.58, 0.58],
        "主力合约": ["RB2601"] * 3,
        "主力合约价格": [3370.0, 3380.0, 3390.0],
        "主力基差": [30.0, 30.0, 30.0],
        "主力基差率": [0.88, 0.88, 0.88],
    })


@pytest.fixture(autouse=True)
def _clean_cache():
    _basis_cache.clear()
    yield
    _basis_cache.clear()


@pytest.mark.unit
def test_basis_cache_second_call_hits_without_network():
    with mock.patch("akshare.futures_spot_price_daily", return_value=_fake_df()) as m:
        r1 = get_futures_basis("RB", "2026-08-25", "2026-08-27")
        assert m.call_count == 1
        r2 = get_futures_basis("RB", "2026-08-25", "2026-08-27")
        assert m.call_count == 1  # 【关键】第二次命中缓存,不再爬东财
        assert r1 == r2
        assert "date,spot_price" in r1  # 输出仍是基差 CSV(merge_basis_data 幂等)


@pytest.mark.unit
def test_basis_cache_refetches_when_start_wider():
    with mock.patch("akshare.futures_spot_price_daily", return_value=_fake_df()) as m:
        get_futures_basis("RB", "2026-08-25", "2026-08-27")  # 缓存仅覆盖 25~27
        get_futures_basis("RB", "2026-08-10", "2026-08-27")  # 请求起点更早 → 不覆盖 → 重拉
        assert m.call_count == 2


@pytest.mark.unit
def test_basis_cache_refetches_when_end_far_beyond_tolerance():
    with mock.patch("akshare.futures_spot_price_daily", return_value=_fake_df()) as m:
        get_futures_basis("RB", "2026-08-25", "2026-08-27")
        # 终点 09-20 远超缓存末行(08-27)+7 天容差 → 视为不覆盖,重拉
        get_futures_basis("RB", "2026-08-25", "2026-09-20")
        assert m.call_count == 2


@pytest.mark.unit
def test_basis_cache_expired_ttl_refetches():
    with mock.patch("akshare.futures_spot_price_daily", return_value=_fake_df()) as m:
        get_futures_basis("RB", "2026-08-25", "2026-08-27")
        old_ts, df = _basis_cache["basis:RB"]
        _basis_cache["basis:RB"] = (old_ts - 7 * 3600, df)  # 【关键】把时间戳改老 7 小时 → 超 6h TTL
        get_futures_basis("RB", "2026-08-25", "2026-08-27")
        assert m.call_count == 2


@pytest.mark.unit
def test_basis_cache_different_symbol_does_not_hit():
    fake = _fake_df()
    with mock.patch("akshare.futures_spot_price_daily", return_value=fake) as m:
        get_futures_basis("RB", "2026-08-25", "2026-08-27")
        get_futures_basis("HC", "2026-08-25", "2026-08-27")
        assert m.call_count == 2  # 不同品种不同缓存键,各自拉取


@pytest.mark.unit
def test_cached_covers_range_edges():
    df = _fake_df()  # 覆盖 2026-08-25~08-27
    assert _cached_covers_range(df, "2026-08-25", "2026-08-27") is True
    # 起点略早于首行(差 1 天,落在 7 天容差内,start 常落在非交易日)→ 视为覆盖
    assert _cached_covers_range(df, "2026-08-24", "2026-08-27") is True
    # 起点远超容差(8-01 vs 首行 8-25)→ 宽窗口请求,不覆盖 → 重拉
    assert _cached_covers_range(df, "2026-08-01", "2026-08-27") is False
    # 终点在 7 天容差内(数据源日频发布滞后)→ 视为覆盖,避免天天白爬
    assert _cached_covers_range(df, "2026-08-25", "2026-08-28") is True
    # 终点远超容差 → 不覆盖
    assert _cached_covers_range(df, "2026-08-25", "2026-09-20") is False
    empty = _fake_df().iloc[0:0]
    assert _cached_covers_range(empty, "2026-08-25", "2026-08-27") is False  # 空表 → 不覆盖
