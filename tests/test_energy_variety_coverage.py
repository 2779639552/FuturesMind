"""能化整组 12 品种(2026-09-01 扩充)的覆盖一致性守卫。

背景:分析池 VARIETY_METADATA 长期 21 个,而情绪采集池覆盖 57 个,存在
"分析池 vs 情绪池" 分裂。2026-09-01 一次性扩入能化整组 12 个
(SC/LU/FU/BU/RU/NR/EB/V/PP/L/EG/PX)。本测试用 AST 直接解析源文件
(不依赖可导入模块,与 test_symbol_specific_coverage.py 同范式)锁死:

  1. 12 个能化代码全部进入 VARIETY_METADATA(且属"能化"板块);
  2. 每个能拉价格的品种(VARIETY_SYMBOLS)都有 NER 条目(VARIETY_KB);
  3. 每个能拉价格的品种都有情绪映射(VARIETY_NAME_TO_SYMBOL);
  4. 东财股吧采集关键词覆盖 12 个新品种(DEFAULT_KEYWORDS_EASTMONEY_GUBA);
  5. 开发副本(data_collection/validate)与生产副本(思路2/validate)的
     品种表 / 情绪映射 / NER 别名表键集一致(防复刻 21-vs-57 分裂)。
"""

import ast
from pathlib import Path

import pytest

from path_utils import resolve_think2_dir
from tradingagents.dataflows.commodity_futures import VARIETY_METADATA

_REPO = Path(__file__).resolve().parent.parent
_VALIDATE = _REPO / "data_collection" / "validate"

# 2026-09-01 扩充的能化整组 12 品种(值为价格表/情绪表/NER 使用的规范名;
# PP 的规范名是 "PP",塑料链的中文名"聚丙烯"只在 VARIETY_METADATA 展示层使用)
ENERGY_12 = {
    "SC": "原油", "LU": "低硫燃料油", "FU": "燃料油", "BU": "沥青",
    "RU": "橡胶", "NR": "20号胶", "EB": "苯乙烯", "V": "PVC",
    "PP": "PP", "L": "塑料", "EG": "乙二醇", "PX": "对二甲苯",
}

# 每个品种在东财股吧关键词表中必须存在的核心词(带"期货"后缀)
CORE_GUBA_KEYWORDS = {
    "SC": "原油期货", "LU": "低硫燃料油期货", "FU": "燃料油期货",
    "BU": "沥青期货", "RU": "橡胶期货", "NR": "20号胶期货",
    "EB": "苯乙烯期货", "V": "PVC期货", "PP": "PP期货",
    "L": "塑料期货", "EG": "乙二醇期货", "PX": "对二甲苯期货",
}


def _ast_assign(module: Path, name: str):
    """从源文件 AST 提取指定顶层赋值(字典/列表)的值(ast.literal_eval)。"""
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


# ---------------------------------------------------------------------------
# 1) 分析池:12 个能化代码全部进入 VARIETY_METADATA 且归"能化"板块
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_energy_12_in_variety_metadata():
    missing = set(ENERGY_12) - set(VARIETY_METADATA)
    assert not missing, f"能化12中未入 VARIETY_METADATA: {sorted(missing)}"


@pytest.mark.unit
def test_energy_12_sector_is_energy():
    from tradingagents.dataflows.sentiment_data import build_sector_to_varieties

    sector_map = build_sector_to_varieties()
    energy_bucket = set(sector_map["能化"])
    missing = set(ENERGY_12) - energy_bucket
    assert not missing, f"能化12中未归入'能化'板块: {sorted(missing)}"


# ---------------------------------------------------------------------------
# 2) 价格 ↔ NER:本次新增的 12 个能化品种都有 NER 别名条目
#    (注:整表全量比对会撞既有"沪铜" vs "铜" 的命名差异,非本次改动,
#     故只守卫本次新增的 12 个品种名)
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_energy_12_all_have_ner_entries():
    ner_kb = _ast_keys(_VALIDATE / "ner.py", "VARIETY_KB")
    missing = set(ENERGY_12.values()) - ner_kb
    assert not missing, f"能化12中有价格符号但缺 NER 条目: {sorted(missing)}"


# ---------------------------------------------------------------------------
# 3) 价格 ↔ 情绪映射:每个能拉价格的品种都有情绪映射
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_price_symbols_all_have_sentiment_mapping():
    symbols = _ast_assign(_VALIDATE / "price_fetcher.py", "VARIETY_SYMBOLS")
    ntos = _ast_assign(_VALIDATE / "generate_tradingagents_sentiment.py",
                       "VARIETY_NAME_TO_SYMBOL")
    mapped_codes = set(ntos.values())
    unmapped = {v for v in symbols.values() if v.rstrip("0") not in mapped_codes}
    assert not unmapped, f"有价格符号但缺情绪映射: {sorted(unmapped)}"


# ---------------------------------------------------------------------------
# 4) 东财股吧关键词覆盖 12 个新品种
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_eastmoney_keywords_cover_energy_12():
    em = _ast_assign(_VALIDATE / "batch_collect.py",
                     "DEFAULT_KEYWORDS_EASTMONEY_GUBA")
    assert isinstance(em, list)
    missing = {code: kw for code, kw in CORE_GUBA_KEYWORDS.items() if kw not in em}
    assert not missing, f"东财股吧缺核心词: {missing}"


# ---------------------------------------------------------------------------
# 5) 开发副本 ↔ 生产副本 键集一致(防 21-vs-57 分裂复发)
# ---------------------------------------------------------------------------
def _prod_validate() -> Path:
    prod = resolve_think2_dir()
    if not (prod / "price_fetcher.py").exists():
        pytest.skip("生产副本不存在(思路2/validate),跳过 dev/prod 一致性检查")
    return prod


@pytest.mark.unit
def test_dev_prod_symbol_tables_in_sync():
    prod = _prod_validate()
    for fname, dict_name in (
        ("price_fetcher.py", "VARIETY_SYMBOLS"),
        ("generate_tradingagents_sentiment.py", "VARIETY_NAME_TO_SYMBOL"),
        ("ner.py", "VARIETY_KB"),
    ):
        dev_keys = _ast_keys(_VALIDATE / fname, dict_name)
        prod_keys = _ast_keys(prod / fname, dict_name)
        only_dev = dev_keys - prod_keys
        only_prod = prod_keys - dev_keys
        assert not only_dev, f"{fname}.{dict_name} 仅开发副本有: {sorted(only_dev)}"
        assert not only_prod, f"{fname}.{dict_name} 仅生产副本有: {sorted(only_prod)}"
