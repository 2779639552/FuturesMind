"""库存回退链守卫:东财无数据(SC/WR)→ 交易所仓单日报 dailystock.dat(2026-09-01)。

背景: 东财 futures_inventory_em 对 SC(原油)/WR(线材)在其 RPT_FUTU_POSITIONCODE 表内
      不存在 → 接口抛 TypeError → 看板库存空白。回退源 = 上期所/上期能源官网
      dailystock.dat(JSON,聚合 SHFE+INE 全品种),实测本机可达(数据至 ~2025Q4)。

本测试全部 mock 掉联网(akshare + 交易所 .dat 抓取);fixture 隔离磁盘缓存目录,
并 monkeypatch merge_inventory_data 成直通(避免读外部 JSON)。
"""

from datetime import date
from unittest import mock

import pandas as pd
import pytest

from tradingagents.dataflows import commodity_futures as cf_mod
from tradingagents.dataflows.commodity_futures import (
    VARIETY_METADATA,
    _fetch_exchange_inventory,
    _find_exchange_cutoff,
    _inventory_cache,
    get_futures_inventory,
)

# 数据截止锚点: 与实测本机旧版 .dat 端点一致(2026 起 404)
_CUTOFF = "2025-11-07"


def _fake_em_df() -> pd.DataFrame:
    """合成东财仓单库存 DataFrame(中文列名,与 futures_inventory_em 输出同构)。"""
    dates = pd.date_range("2026-08-20", "2026-09-01").strftime("%Y-%m-%d").tolist()
    n = len(dates)
    return pd.DataFrame({
        "日期": dates,
        "库存": [1000.0 + i * 10 for i in range(n)],
        "变化": [10.0] * n,
    })


def _make_dailystock(varid: str, total: float) -> dict:
    """合成交易所 dailystock.dat JSON 载荷(单行)."""
    return {"o_cursor": [
        {"VARID": varid, "VARNAME": f"{varid}$$X", "WRTWGHTS": str(total), "WRTCHANGE": "0"},
    ]}


@pytest.fixture(autouse=True)
def _clean_cache(tmp_path, monkeypatch):
    """清空内存缓存 + 隔离磁盘缓存目录 + 直通 hybrid 合并(不读外部 JSON)。"""
    _inventory_cache.clear()
    monkeypatch.setattr(cf_mod, "_INVENTORY_CACHE_DIR", tmp_path)
    monkeypatch.setattr(cf_mod, "_exchange_cutoff_cache", {})
    monkeypatch.setattr(cf_mod, "merge_inventory_data", lambda code, csv: (csv, False))
    yield
    _inventory_cache.clear()


# ---------------------------------------------------------------------------
# 回退触发:东财抛错/空 → 走交易所;东财正常 → 不走
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_em_missing_symbol_routes_to_exchange():
    """SC/WR 在东财 POSITIONCODE 表内不存在 → futures_inventory_em 抛 TypeError → 回退。"""
    fallback_df = pd.DataFrame({
        "date": ["2025-11-05", "2025-11-06"],
        "inventory": [9.4e6, 9.4e6],
        "change": [0.0, 0.0],
    })
    with mock.patch("akshare.futures_inventory_em", side_effect=TypeError("'NoneType' object is not subscriptable")), \
         mock.patch.object(cf_mod, "_fetch_exchange_inventory", return_value=fallback_df) as fb:
        out = get_futures_inventory("SC")
    assert fb.call_count == 1  # 回退源被调用
    assert "date,inventory,change" in out
    assert "9400000.0" in out


@pytest.mark.unit
def test_em_success_skips_fallback():
    with mock.patch("akshare.futures_inventory_em", return_value=_fake_em_df()) as em, \
         mock.patch.object(cf_mod, "_fetch_exchange_inventory", return_value=None) as fb:
        out = get_futures_inventory("RB")
    assert em.call_count == 1
    assert fb.call_count == 0  # 【关键】东财有数据就不打扰回退源
    assert "date,inventory,change" in out


@pytest.mark.unit
def test_em_empty_routes_to_exchange():
    with mock.patch("akshare.futures_inventory_em", return_value=pd.DataFrame()), \
         mock.patch.object(cf_mod, "_fetch_exchange_inventory", return_value=pd.DataFrame({
             "date": ["2025-11-05"], "inventory": [1.0], "change": [0.0],
         })) as fb:
        out = get_futures_inventory("SC")
    assert fb.call_count == 1
    assert "1.0" in out


@pytest.mark.unit
def test_fallback_empty_returns_no_data():
    with mock.patch("akshare.futures_inventory_em", side_effect=TypeError("missing")), \
         mock.patch.object(cf_mod, "_fetch_exchange_inventory", return_value=None):
        out = get_futures_inventory("SC")
    assert out.startswith("NO_DATA_AVAILABLE")


# ---------------------------------------------------------------------------
# 缓存:二次命中不重拉、无数据结论缓存、磁盘跨重启
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_inventory_cache_second_call_skips_em():
    with mock.patch("akshare.futures_inventory_em", return_value=_fake_em_df()) as em, \
         mock.patch.object(cf_mod, "_fetch_exchange_inventory", return_value=None) as fb:
        get_futures_inventory("RB")
        get_futures_inventory("RB")
    assert em.call_count == 1  # 【关键】二次命中缓存,不再调东财
    assert fb.call_count == 0


@pytest.mark.unit
def test_inventory_no_data_cached_avoids_rescan():
    """东财与回退源都无数据 → 无数据结论缓存,避免每次看板加载都重扫 404。"""
    with mock.patch("akshare.futures_inventory_em", side_effect=TypeError("missing")), \
         mock.patch.object(cf_mod, "_fetch_exchange_inventory", return_value=None) as fb:
        r1 = get_futures_inventory("SC")
        r2 = get_futures_inventory("SC")
    assert r1 == r2 == "NO_DATA_AVAILABLE: No inventory data for 原油(SC)."
    assert fb.call_count == 1  # 二次命中无数据缓存


@pytest.mark.unit
def test_inventory_cache_persists_to_disk_across_restart(tmp_path, monkeypatch):
    d = tmp_path / "cache"
    monkeypatch.setattr(cf_mod, "_INVENTORY_CACHE_DIR", d)
    with mock.patch("akshare.futures_inventory_em", return_value=_fake_em_df()) as em:
        get_futures_inventory("RB")
        assert (d / "inventory_RB.pkl").exists()
        _inventory_cache.clear()  # 【关键】模拟进程重启
        get_futures_inventory("RB")
    assert em.call_count == 1  # 磁盘命中,不再调东财


# ---------------------------------------------------------------------------
# 回退源解析器:VARID 匹配 + WRTWGHTS 汇总 + 截止日二分
# ---------------------------------------------------------------------------
def _fake_dailystock_until(cutoff: date):
    """构造按日期响应的 _fetch_exchange_dailystock mock:截止前给数据,之后 404。"""
    def _fake(d: str) -> dict | None:
        if date.fromisoformat(d) <= cutoff:
            return _make_dailystock("wr", 120.0)
        return None
    return _fake


@pytest.mark.unit
def test_find_exchange_cutoff_binary_search(monkeypatch):
    monkeypatch.setattr(cf_mod, "_fetch_exchange_dailystock", _fake_dailystock_until(date(2025, 11, 7)))
    got = _find_exchange_cutoff()
    assert got == _CUTOFF  # 二分精确定位到最后一个有数据日


@pytest.mark.unit
def test_exchange_parser_sums_wrtweights(monkeypatch):
    """VARID 匹配(wr)+ WRTWGHTS 逐日求和 → 升序 (date,inventory,change)。"""
    monkeypatch.setattr(cf_mod, "_fetch_exchange_dailystock", _fake_dailystock_until(date(2025, 11, 7)))
    df = _fetch_exchange_inventory("WR", VARIETY_METADATA["WR"])
    assert df is not None
    assert not df.empty
    assert list(df.columns) == ["date", "inventory", "change"]
    assert df["date"].iloc[-1] == _CUTOFF  # 序列止于数据截止日
    assert (df["inventory"] == 120.0).all()  # 逐日 WRTWGHTS 求和
    assert df["change"].iloc[0] == 0.0  # 首日无前值 → 0
    assert df["change"].iloc[-1] == 0.0  # 恒值 → 变化 0


@pytest.mark.unit
def test_exchange_parser_ignores_other_varids(monkeypatch):
    """载荷里混入其他品种(如 sc)的行不影响 wr 的汇总。"""
    def _mixed(d: str) -> dict | None:
        if date.fromisoformat(d) > date(2025, 11, 7):
            return None
        return {"o_cursor": [
            {"VARID": "wr", "VARNAME": "线材$$X", "WRTWGHTS": "5", "WRTCHANGE": "0"},
            {"VARID": "sc", "VARNAME": "原油$$X", "WRTWGHTS": "999", "WRTCHANGE": "0"},
        ]}
    monkeypatch.setattr(cf_mod, "_fetch_exchange_dailystock", _mixed)
    df = _fetch_exchange_inventory("WR", VARIETY_METADATA["WR"])
    assert (df["inventory"] == 5.0).all()  # 只累计 VARID=wr 的行
