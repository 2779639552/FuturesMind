"""品种池(ACTIVE_VARIETIES)锁定测试(2026-09-09 品种池收缩;2026-09-30 加入 P 棕榈油)。

用户口径:全项目 UI/采集/展示只保留 21 个品种
SC TA PX EG FU LU PG M CF CJ LC RU NR BR LH BU PS SI BZ EB P
(MEG=EG、多晶硅=PS、工业硅=SI、棕榈油=P;池外品种保留数据但隐藏)。

四道锁:
1. 池常量本身:恰好 21 个、⊆ VARIETY_METADATA。
2. /api/varieties:恰好返回 21 项,含 PS/SI/P,不含 RB/I(隐藏生效)。
3. PS 元数据完整性:16 字段齐备(inv_code 允许空串——东财仓单表无 PS)。
4. P 元数据完整性 + 展示顺序表覆盖(2026-09-30 新增)。
"""


import web_app
from tradingagents.dataflows.commodity_futures import (
    ACTIVE_VARIETIES,
    VARIETY_METADATA,
)

EXPECTED_POOL = {
    "SC", "TA", "PX", "EG", "FU", "LU", "PG",          # 能化(油系)
    "M", "CF", "CJ", "LH", "P",                        # 农产品(P=棕榈油)
    "LC", "PS", "SI",                                  # 有色(新能源)
    "RU", "NR", "BR", "BU", "BZ", "EB",                # 能化(橡胶/沥青/苯系)
}


def test_active_pool_exactly_21_and_subset_of_metadata():
    """池恰好 21 个且每个代码都在元数据表里(池 ⊆ 元数据)。"""
    assert len(ACTIVE_VARIETIES) == 21
    assert set(ACTIVE_VARIETIES) == EXPECTED_POOL
    missing = ACTIVE_VARIETIES - set(VARIETY_METADATA)
    assert not missing, f"池内代码缺元数据: {sorted(missing)}"


def test_active_pool_leg_varieties_still_in_metadata():
    """盘面利润配方的腿(I/J)虽已出池,但元数据必须保留(配方不断)。"""
    assert {"I", "J", "JM"} <= set(VARIETY_METADATA)


def test_api_varieties_returns_exactly_active_pool():
    """/api/varieties 是 UI 唯一品种入口:必须恰 21 项,PS/SI/P 在,RB/I 不在。"""
    resp = web_app.app.test_client().get("/api/varieties")
    assert resp.status_code == 200
    rows = resp.get_json()
    codes = {r["code"] for r in rows}
    assert codes == EXPECTED_POOL
    assert len(rows) == 21
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


# ──────────────────────────────────────────────────────────────────────────
# 2026-09-30 新增:P 棕榈油入池守卫
# ──────────────────────────────────────────────────────────────────────────

def test_p_metadata_complete():
    """P 棕榈油:元数据 16 字段齐备(DCE 已上市老品种,inv_code 非空)。

    ⚠️ 与 PS/BZ 不同,P 在交易所与东财仓单表里都有存货代码(inv_code="p"),
    所以此处断言 inv_code **非空** —— 若将来谁把它清空,仓单链路会静默失效。
    """
    p = VARIETY_METADATA["P"]
    assert p["name"] == "棕榈油"
    assert p["name_en"] == "Palm Oil"
    assert p["exchange"] == "DCE"
    assert p["main_contract"] == "P0"
    nonempty_fields = [
        "name", "name_en", "exchange", "exchange_cn", "main_contract",
        "spot_code", "inv_code", "unit", "price_limit", "margin_rate",
        "trading_hours", "sector_cn", "description",
    ]
    for field in nonempty_fields:
        assert p.get(field), f"P.{field} 不应为空"
    assert isinstance(p.get("key_factors"), list) and p["key_factors"]
    assert isinstance(p.get("related_varieties"), list) and p["related_varieties"]
    # 油脂板块归属:与豆油/菜油互为相关品种(价差套利的基础)
    assert set(p["related_varieties"]) & {"Y", "OI"}, p["related_varieties"]


def test_p_in_display_order_table():
    """P 必须进 _VARIETY_SECTORS 的展示顺序表,否则观点总表/覆盖表顺序漂到末尾。

    【为什么单独守】_active_variety_list() 对"池内但不在顺序表"的品种会**兜底追加**,
    所以漏配**不会报错**,只会让 P 每天排在 21 个品种的最末、脱离农产品分组 ——
    属于"能跑但不对"的静默问题,必须用断言钉住。
    """
    from web_app import _VARIETY_DISPLAY_ORDER, _VARIETY_SECTORS

    assert "P" in _VARIETY_DISPLAY_ORDER, "P 未进展示顺序表(会漂到末尾)"
    assert "P" in _VARIETY_SECTORS["农产品"], "P 应归入农产品板块"
    # 顺序表覆盖全部池内品种,无遗漏
    assert set(ACTIVE_VARIETIES) <= set(_VARIETY_DISPLAY_ORDER)


def _root_module(name: str):
    """按**项目根目录的文件路径**加载模块,绕开同名模块遮蔽。

    【为什么不能用 `from price_fetcher import ...`】项目里有两个 price_fetcher:
      · 根目录   price_fetcher.py                ← 真正的实时行情/历史价格模块
      · data_collection/validate/price_fetcher.py ← 采集侧的另一套(无 DEFAULT_LIVE_VARIETIES)
    完整套件运行时,别的测试会把 data_collection/validate 插进 sys.path,
    于是裸 import 可能命中后者 ⇒ 报 `cannot import name ...`。
    这是**测试环境的模块遮蔽**,不是生产缺陷;按路径加载即可稳定命中根目录那份。
    """
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(f"_root_{name}", root / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_p_in_price_fetcher_live_list():
    """P 必须在实时行情默认清单里(module 级常量清单,不跟随池自动变)。"""
    pf = _root_module("price_fetcher")
    live = pf.DEFAULT_LIVE_VARIETIES
    assert "P" in live
    assert len(live) == 21
    assert len(set(live)) == 21, "实时行情清单有重复"


def test_p_realtime_name_override_is_sina_key():
    """实时行情接口的中文键是 "棕榈" 而非 "棕榈油"(实测 akshare 1.18.94)。

    【要防的回归】NAME_TO_CODE 里写的是通用名 "棕榈油" —— 那是**历史价格文件名**,
    直接喂给 ak.futures_zh_realtime 会抛 KeyError,而调用处吞异常 ⇒ 实时行情静默取空。
    故必须有显式别名覆盖;这条断言就是钉住这个覆盖不被误删。
    """
    pf = _root_module("price_fetcher")
    ov = pf.REALTIME_NAME_OVERRIDE
    assert ov.get("P") == "棕榈"
    # 同类失配的池内品种一并钉住(2026-09-30 实测)
    assert ov.get("PX") == "二甲苯"
    assert ov.get("FU") == "燃油"


def test_realtime_name_override_covers_all_broken_pool_varieties():
    """覆盖层必须恰好修掉"通用名 ≠ 接口真实键"的那些品种,不误改其他品种。

    【怎么判】用 akshare 自己的命名表 futures_symbol_mark() 当权威依据, 逐个池内品种核对:
      通用名(NAME_TO_CODE 反查)不在表里 ⇒ 必须有覆盖层条目, 且覆盖后的键必须在表里。
    这样将来 akshare 改名(或新增失配品种)时, 这条断言会先红, 而不是等用户发现"行情少了几个"。
    """
    import pytest

    ak = pytest.importorskip("akshare")
    pf = _root_module("price_fetcher")

    try:
        valid = set(ak.futures_symbol_mark()["symbol"].astype(str))
    except Exception as exc:  # noqa: BLE001 - 网络不可用时跳过(非本测试职责)
        pytest.skip(f"akshare 命名表不可用: {exc}")

    code_to_name = {v: k for k, v in pf.NAME_TO_CODE.items()}
    from tradingagents.dataflows.commodity_futures import ACTIVE_VARIETIES

    unresolved = []
    for code in sorted(ACTIVE_VARIETIES):
        ov = pf.REALTIME_NAME_OVERRIDE.get(code)
        name = ov or code_to_name.get(code)
        if not name:
            unresolved.append(f"{code}(无任何名称)")
        elif name not in valid:
            unresolved.append(f"{code}({name} 不在命名表且无覆盖)")
    assert not unresolved, "实时行情名称失配(会静默取空): " + "; ".join(unresolved)
    # 覆盖后的键必须真的是表里的合法键(否则覆盖本身也无效)
    for code, name in pf.REALTIME_NAME_OVERRIDE.items():
        assert name in valid, f"覆盖层 {code}→{name} 不是接口合法键"


def test_p_cn_name_maps_to_code():
    """中文名 "棕榈油" 必须能规范化成 P(各处按中文名反查代码的链路都依赖它)。"""
    from tradingagents.dataflows.commodity_futures import _validate_symbol

    assert _validate_symbol("棕榈油") == "P"
    assert _validate_symbol("p") == "P"
