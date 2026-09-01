"""基差缓存守卫:get_futures_basis 6h TTL 缓存 + 起止区间覆盖 + 最宽合并 + 磁盘持久化(2026-09-01)。

背景: futures_spot_price_daily 是东财网页爬取接口,实测单次 13~175s(2026-09-01 实测 175s),
是数据看板刷新慢的根因。给 get_futures_basis 加了 _basis_cache(键 "basis:{品种}",6h TTL),
并靠 _cached_covers_range 保证缓存同时覆盖请求的起止区间,避免窄缓存静默截断宽请求。

2026-09-01 二次加固(实测发现"切档位必冷拉"):
  · 冷拉后 _basis_cache_merge_widest 合并到【最宽】日期覆盖 → 60/120/180/365 档位切来切去
    只有首次最宽档位冷拉一次,此后全部命中;
  · _basis_cache_save/_load 落盘 ~/.tradingagents/cache/basis → 服务重启后 6h 内仍命中。

本测试用 mock 掉 akshare,不联网;fixture 把磁盘缓存目录隔离到临时目录,测试互不串。
"""

from unittest import mock

import pandas as pd
import pytest

from tradingagents.dataflows import commodity_futures as cf_mod
from tradingagents.dataflows.commodity_futures import (
    _basis_cache,
    _cached_covers_range,
    get_futures_basis,
)


def _fake_df(start: str = "2026-08-25") -> pd.DataFrame:
    """合成东财基差 DataFrame(中文列名,与 futures_spot_price_daily 输出同构)。

    默认生成 08-25~08-27 三天;传更早的 start 则生成从 start 到 08-27 的序列
    (供"更宽窗口请求返回更宽数据"的合并测试用)。
    """
    dates = pd.date_range(start, "2026-08-27").strftime("%Y-%m-%d").tolist()
    n = len(dates)
    return pd.DataFrame({
        "日期": dates,
        "品种": ["螺纹钢"] * n,
        "现货价格": [3400.0 + i for i in range(n)],
        "近月合约": ["RB2510"] * n,
        "近月合约价格": [3380.0 + i for i in range(n)],
        "近月基差": [20.0] * n,
        "近月基差率": [0.59] * n,
        "主力合约": ["RB2601"] * n,
        "主力合约价格": [3370.0 + i for i in range(n)],
        "主力基差": [30.0] * n,
        "主力基差率": [0.88] * n,
    })


@pytest.fixture(autouse=True)
def _clean_cache(tmp_path, monkeypatch):
    _basis_cache.clear()
    # 【关键】隔离磁盘持久化目录:否则先写的磁盘缓存会被后续测试读到,污染 call_count。
    monkeypatch.setattr(cf_mod, "_BASIS_CACHE_DIR", tmp_path)
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


# ---------------------------------------------------------------------------
# 2026-09-01 加固:合并到最宽覆盖(切档位不冷拉) + 磁盘持久化(重启不丢)
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_basis_cache_merge_extends_wider_window():
    def _side_effect(start_day, end_day, vars_list):
        # akshare 传的是去横杠日期 "20260810";按请求起点返回对应宽度的假数据
        s = f"{start_day[:4]}-{start_day[4:6]}-{start_day[6:]}"
        return _fake_df(start=s)

    with mock.patch("akshare.futures_spot_price_daily", side_effect=_side_effect) as m:
        get_futures_basis("RB", "2026-08-25", "2026-08-27")  # 窄档 → 缓存仅 25~27
        assert m.call_count == 1
        # 切更宽档 10~27 → 不覆盖 → 重拉(返回 10~27),合并后缓存覆盖 10~27
        get_futures_basis("RB", "2026-08-10", "2026-08-27")
        assert m.call_count == 2
        # 中间档 15~27 落在合并后的宽覆盖内 → 【关键】命中,不再爬
        get_futures_basis("RB", "2026-08-15", "2026-08-27")
        assert m.call_count == 2
        # 再请求完整 10~27 → 命中
        get_futures_basis("RB", "2026-08-10", "2026-08-27")
        assert m.call_count == 2


@pytest.mark.unit
def test_basis_cache_persists_to_disk_across_restart(tmp_path, monkeypatch):
    d = tmp_path / "cache"
    monkeypatch.setattr(cf_mod, "_BASIS_CACHE_DIR", d)
    with mock.patch("akshare.futures_spot_price_daily", return_value=_fake_df()) as m:
        get_futures_basis("RB", "2026-08-25", "2026-08-27")
        assert m.call_count == 1
        assert (d / "basis_RB.pkl").exists()  # 已写盘
        _basis_cache.clear()  # 【关键】模拟进程重启:内存缓存清空
        get_futures_basis("RB", "2026-08-25", "2026-08-27")
        assert m.call_count == 1  # 磁盘命中,不再爬


@pytest.mark.unit
def test_basis_cache_disk_corrupt_refetches_without_crash(tmp_path, monkeypatch):
    d = tmp_path / "cache"
    d.mkdir(parents=True, exist_ok=True)
    (d / "basis_RB.pkl").write_bytes(b"not a pickle")  # 损坏文件
    monkeypatch.setattr(cf_mod, "_BASIS_CACHE_DIR", d)
    with mock.patch("akshare.futures_spot_price_daily", return_value=_fake_df()) as m:
        get_futures_basis("RB", "2026-08-25", "2026-08-27")
        assert m.call_count == 1  # 损坏 → 视为未命中重拉,不崩
        assert (d / "basis_RB.pkl").exists()  # 且已用新数据覆盖


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
