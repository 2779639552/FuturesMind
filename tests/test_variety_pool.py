"""品种池(ACTIVE_VARIETIES)锁定测试(2026-09-09 品种池收缩)。

用户口径:全项目 UI/采集/展示只保留 20 个品种
SC TA PX EG FU LU PG M CF CJ LC RU NR BR LH BU PS SI BZ EB
(MEG=EG、多晶硅=PS、工业硅=SI;池外品种保留数据但隐藏)。

三道锁:
1. 池常量本身:恰好 20 个、⊆ VARIETY_METADATA。
2. /api/varieties:恰好返回 20 项,含 PS/SI,不含 RB/I(隐藏生效)。
3. PS 元数据完整性:16 字段齐备(inv_code 允许空串——东财仓单表无 PS)。
"""


import web_app
from tradingagents.dataflows.commodity_futures import (
    ACTIVE_VARIETIES,
    VARIETY_METADATA,
)

EXPECTED_POOL = {
    "SC", "TA", "PX", "EG", "FU", "LU", "PG",          # 能化(油系)
    "M", "CF", "CJ", "LH",                             # 农产品
    "LC", "PS", "SI",                                  # 有色(新能源)
    "RU", "NR", "BR", "BU", "BZ", "EB",                # 能化(橡胶/沥青/苯系)
}


def test_active_pool_exactly_20_and_subset_of_metadata():
    """池恰好 20 个且每个代码都在元数据表里(池 ⊆ 元数据)。"""
    assert len(ACTIVE_VARIETIES) == 20
    assert set(ACTIVE_VARIETIES) == EXPECTED_POOL
    missing = ACTIVE_VARIETIES - set(VARIETY_METADATA)
    assert not missing, f"池内代码缺元数据: {sorted(missing)}"


def test_active_pool_leg_varieties_still_in_metadata():
    """盘面利润配方的腿(I/J)虽已出池,但元数据必须保留(配方不断)。"""
    assert {"I", "J", "JM"} <= set(VARIETY_METADATA)


def test_api_varieties_returns_exactly_active_pool():
    """/api/varieties 是 UI 唯一品种入口:必须恰 20 项,PS/SI 在,RB/I 不在。"""
    resp = web_app.app.test_client().get("/api/varieties")
    assert resp.status_code == 200
    rows = resp.get_json()
    codes = {r["code"] for r in rows}
    assert codes == EXPECTED_POOL
    assert len(rows) == 20
    # 每行结构完整(UI 下拉依赖 code/name/exchange/sector)
    for r in rows:
        assert r["code"] and r["name"] and r["sector"]


def test_ps_metadata_complete():
    """PS 多晶硅为 2026-09-09 新建条目:16 字段齐备。

    inv_code 允许空串(东财仓单表无 PS,显式声明无库存数据源,同 BZ/PR 先例);
    其余字段必须非空。
    """
    ps = VARIETY_METADATA["PS"]
    assert ps["name"] == "多晶硅"
    assert ps["exchange"] == "GFEX"
    assert ps["main_contract"] == "PS0"
    nonempty_fields = [
        "name", "name_en", "exchange", "exchange_cn", "main_contract",
        "spot_code", "unit", "price_limit", "margin_rate", "trading_hours",
        "sector_cn", "description",
    ]
    for field in nonempty_fields:
        assert ps.get(field), f"PS.{field} 不应为空"
    assert isinstance(ps.get("key_factors"), list) and ps["key_factors"]
    assert isinstance(ps.get("related_varieties"), list) and ps["related_varieties"]
    assert ps.get("inv_code") == ""  # 显式空串,非缺键


def test_symbol_specific_has_ps_keywords():
    """symbol_specific(函数内局部 dict)须含 PS 及其关键词(AST 提取)。"""
    import ast
    from pathlib import Path

    module = Path(
        __import__("tradingagents.dataflows.commodity_futures", fromlist=["x"]).__file__
    )
    tree = ast.parse(module.read_text(encoding="utf-8"))
    ps_keywords = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "symbol_specific":
                    assert isinstance(node.value, ast.Dict)
                    for key, value in zip(node.value.keys, node.value.values, strict=False):
                        if key is not None and key.value == "PS":
                            ps_keywords = [elt.value for elt in value.elts]
    assert ps_keywords is not None, "symbol_specific 未找到 PS 键"
    assert any("多晶硅" in kw or "硅料" in kw for kw in ps_keywords)
