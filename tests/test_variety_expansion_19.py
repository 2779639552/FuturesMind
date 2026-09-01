"""19 非金融品种(2026-09-01 扩充)的覆盖一致性守卫。

背景:分析池 VARIETY_METADATA 由 33 扩到 52,一次扩入 19 个非金融品种
(AG/AU/AL/AO/CU/NI/PB/SN/ZN/WR 有色与黑色线材 + C/CS/JD/LH/P/Y 农产品
+ SH 氯碱 + LC/SI 新能源金属;PG 需新增情绪链、金融期货 IF/IC/IH/IM/T/TF/TL/TS
不在本次范围)。本测试用 AST 直接解析源文件(不依赖可导入模块,与
test_energy_variety_coverage.py 同范式)锁死:

  1. 19 个代码全部进入 VARIETY_METADATA,且板块归属符合预期桶
     (有色=11 / 能化含 SH / 农产品含 C/CS/JD/LH/P/Y / 黑色系含 WR);
  2. 每个品种在 dev 与 prod 两份 VARIETY_SYMBOLS 中都有价格主连代码;
  3. 每个品种都有情绪映射(VARIETY_NAME_TO_SYMBOL);
  4. 每个品种都有 NER 别名条目(沪X 金属走既有"铜/铝/…"裸名别名);
  5. 东财股吧采集关键词覆盖新增的 6 个品种(氧化铝/沪铅/沪锡/烧碱/线材/淀粉)。
"""

import ast
from pathlib import Path

import pytest

from path_utils import resolve_think2_dir
from tradingagents.dataflows.commodity_futures import VARIETY_METADATA

_REPO = Path(__file__).resolve().parent.parent
_VALIDATE = _REPO / "data_collection" / "validate"

# 2026-09-01 扩充的 19 个非金融品种(值为价格表/情绪表/NER 使用的规范名)
NEW_19 = {
    "AG": "白银", "AU": "黄金", "AL": "沪铝", "AO": "氧化铝", "CU": "沪铜",
    "NI": "沪镍", "PB": "沪铅", "SN": "沪锡", "ZN": "沪锌", "WR": "线材",
    "C": "玉米", "CS": "淀粉", "JD": "鸡蛋", "LH": "生猪",
    "P": "棕榈油", "Y": "豆油", "SH": "烧碱", "LC": "碳酸锂", "SI": "工业硅",
}

# 沪X 金属在 NER 里用裸名(铜/铝/镍/铅/锡/锌),非"沪铜"——既有命名分歧,兜底替换
_NER_BARE_METAL = {
    "沪铜": "铜", "沪铝": "铝", "沪镍": "镍",
    "沪铅": "铅", "沪锡": "锡", "沪锌": "锌",
}

# 新增 6 个采集关键词品种 → 东财股吧必须存在的核心词(带"期货"后缀)
NEW_GUBA_KEYWORDS = [
    "氧化铝期货", "沪铅期货", "沪锡期货", "烧碱期货", "线材期货", "淀粉期货",
]

# 板块预期(剥括号后):有色 11 / 黑色系含 WR / 能化含 SH / 农产品含 6
NEW_SECTORS = {
    "有色": {"AG", "AL", "AO", "AU", "CU", "LC", "NI", "PB", "SI", "SN", "ZN"},
    "黑色系": {"WR"},
    "能化": {"SH"},
    "农产品": {"C", "CS", "JD", "LH", "P", "Y"},
}


def _ast_assign(module: Path, name: str):
    tree = ast.parse(module.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"`{name}` not found in {module.name}")


def _ast_keys(module: Path, name: str) -> set[str]:
    val = _ast_assign(module, name)
    assert isinstance(val, dict), f"`{name}` in {module.name} is not a dict"
    return set(val)


def _prod_validate() -> Path:
    prod = resolve_think2_dir()
    if not (prod / "price_fetcher.py").exists():
        pytest.skip("生产副本不存在(思路2/validate),跳过 dev/prod 一致性检查")
    return prod


# ---------------------------------------------------------------------------
# 1) 分析池:19 个代码全部进入 VARIETY_METADATA,板块归属符合预期桶
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_new_19_in_variety_metadata():
    missing = set(NEW_19) - set(VARIETY_METADATA)
    assert not missing, f"19 品种中未入 VARIETY_METADATA: {sorted(missing)}"


@pytest.mark.unit
def test_new_19_sector_assignment():
    from tradingagents.dataflows.sentiment_data import build_sector_to_varieties

    sector_map = build_sector_to_varieties()
    for bucket, expect_codes in NEW_SECTORS.items():
        missing = expect_codes - set(sector_map[bucket])
        assert not missing, f"'{bucket}'桶缺少 19 品种: {sorted(missing)}"


# ---------------------------------------------------------------------------
# 2) 价格符号:dev 与 prod 两份 VARIETY_SYMBOLS 都含 19 品种主连代码
# ---------------------------------------------------------------------------
def _price_codes(module: Path) -> set[str]:
    symbols = _ast_assign(module, "VARIETY_SYMBOLS")
    return {code.rstrip("0") for code in symbols.values()}


@pytest.mark.unit
def test_new_19_have_price_symbols():
    codes = set(NEW_19)
    dev_codes = _price_codes(_VALIDATE / "price_fetcher.py")
    missing_dev = codes - dev_codes
    assert not missing_dev, f"dev price_fetcher 缺价格符号: {sorted(missing_dev)}"


@pytest.mark.unit
def test_new_19_have_price_symbols_in_prod():
    prod = _prod_validate()
    prod_codes = _price_codes(prod / "price_fetcher.py")
    missing = set(NEW_19) - prod_codes
    assert not missing, f"prod price_fetcher 缺价格符号: {sorted(missing)}"


# ---------------------------------------------------------------------------
# 3) 情绪映射:每个品种都有 VARIETY_NAME_TO_SYMBOL 映射
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_new_19_have_sentiment_mapping():
    n2s = _ast_assign(_VALIDATE / "generate_tradingagents_sentiment.py",
                      "VARIETY_NAME_TO_SYMBOL")
    mapped = set(n2s.values())
    missing = set(NEW_19) - mapped
    assert not missing, f"有价格符号但缺情绪映射: {sorted(missing)}"


# ---------------------------------------------------------------------------
# 4) NER 别名:每个品种都有 VARIETY_KB 条目(沪X 金属走裸名兜底)
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_new_19_have_ner_entries():
    kb = _ast_keys(_VALIDATE / "ner.py", "VARIETY_KB")
    missing = []
    for name in NEW_19.values():
        if name in kb:
            continue
        bare = _NER_BARE_METAL.get(name)
        if bare and bare in kb:
            continue
        missing.append(name)
    assert not missing, f"19 品种中缺 NER 条目(含裸名兜底后): {sorted(missing)}"


# ---------------------------------------------------------------------------
# 5) 东财股吧关键词覆盖新增的 6 个品种
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_eastmoney_keywords_cover_new_6():
    em = _ast_assign(_VALIDATE / "batch_collect.py",
                     "DEFAULT_KEYWORDS_EASTMONEY_GUBA")
    missing = [kw for kw in NEW_GUBA_KEYWORDS if kw not in em]
    assert not missing, f"东财股吧缺核心词: {missing}"
