"""库存接口代码契约守卫(2026-09-01)。

背景: 17 个 ZCE 品种的 inv_code 配成小写(ta/ma/ap…),而 akshare
futures_inventory_em 的 symbol 表内 ZCE 品种【仅存大写】(TA/MA/AP…),其余
交易所仅存小写(ag/cu/rb…) → 小写的 ZCE 品种仓单库存必然抛 ValueError,看板
库存图空白。2026-09-01 修复:ZCE 品种 inv_code 统一大写。

本测试锁死每个品种的 inv_code 都在 akshare futures_inventory_em_symbol_dict
表内(该表是接口唯一合法代码来源),防止将来大小写约定再回退。akshare 懒加载,
不拖慢其他测试收集。
"""

import pytest

from tradingagents.dataflows.commodity_futures import VARIETY_METADATA


@pytest.mark.unit
def test_all_inv_codes_valid_for_akshare():
    import akshare as ak  # 【懒加载】仅本测试承担 akshare 导入开销

    mod = __import__(
        ak.futures_inventory_em.__module__, fromlist=["futures_inventory_em_symbol_dict"]
    )
    valid = mod.futures_inventory_em_symbol_dict
    bad = {
        code: meta["inv_code"]
        for code, meta in VARIETY_METADATA.items()
        if meta.get("inv_code") not in valid
    }
    assert not bad, f"以下品种 inv_code 不在 akshare 表内: {bad}"


@pytest.mark.unit
def test_zce_inv_codes_are_uppercase():
    """结构守卫:ZCE 品种 inv_code 必须大写(akshare 表内 ZCE 仅大写)。"""
    bad = {
        code: meta["inv_code"]
        for code, meta in VARIETY_METADATA.items()
        if meta.get("exchange") == "ZCE" and not meta["inv_code"].isupper()
    }
    assert not bad, f"ZCE 品种 inv_code 应为大写: {bad}"
