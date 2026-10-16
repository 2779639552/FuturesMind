"""品种池覆盖度守卫:池内每个品种必须在各"手抄清单"里都出现(AST 提取,不发网络请求)。

【要防的回归类型】2026-09-30 加 P 棕榈油时实测发现的缺口 —— 项目里有一批**手抄的
  品种清单**,它们不跟随 ACTIVE_VARIETIES 自动变。漏配的后果分两种:

  · 静默取空型(最危险):`price_fetcher.REALTIME_NAME_OVERRIDE` 类别名表漏配 ⇒
    ak.futures_zh_realtime 抛 KeyError 被 except 吞掉 ⇒ 该品种实时行情**永远缺**,
    日志无痕。实测 PX/FU 已因此静默取空(存量缺陷)。
  · 采集漏采型:`news_collector_bloomberg.VARIETY_KEYWORDS` / `batch_collect.
    DEFAULT_KEYWORDS_*` 漏配 ⇒ 该品种的舆情/外文新闻**一条都采不到**,
    但采集器退出码仍是 0,页面上只表现为"这个品种没新闻"。

两类都不会报错,所以必须有断言钉住。本文件只做**静态覆盖度**检查(快、无网络),
"清单漏项"是这类缺陷唯一可控的检出点。
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VALIDATE = ROOT / "data_collection" / "validate"

from tradingagents.dataflows.commodity_futures import (  # noqa: E402
    ACTIVE_VARIETIES,
    VARIETY_METADATA,
)


# ──────────────────────────────────────────────────────────────────────────
# 1) 外文新闻关键词(Bloomberg)—— 漏配即在英文新闻里彻底失声
# ──────────────────────────────────────────────────────────────────────────
def test_bloomberg_keywords_cover_whole_pool():
    from news_collector_bloomberg import VARIETY_KEYWORDS

    missing = set(ACTIVE_VARIETIES) - set(VARIETY_KEYWORDS)
    assert not missing, f"彭博新闻关键词表缺品种(该品种英文新闻将一条都打不上标): {sorted(missing)}"
    # 每个品种至少要有 1 个非空英文关键词
    empty = [c for c, kws in VARIETY_KEYWORDS.items() if not kws]
    assert not empty, f"这些品种的关键词表为空: {empty}"


def test_bloomberg_p_has_palm_keywords():
    from news_collector_bloomberg import VARIETY_KEYWORDS

    kws = [k.lower() for k in VARIETY_KEYWORDS["P"]]
    assert any("palm" in k for k in kws), VARIETY_KEYWORDS["P"]


# ──────────────────────────────────────────────────────────────────────────
# 2) 六平台采集关键词(batch_collect)—— 漏配即该平台采不到该品种
# ──────────────────────────────────────────────────────────────────────────
def _ast_str_lists(path: Path) -> dict[str, list[str]]:
    """AST 提取模块级"字符串列表"常量(不执行模块)。

    【为什么不能 exec_module】batch_collect.py 顶部 `from ner import FuturesNER`,
      而 ner 只在 data_collection/validate 目录内可导入 ⇒ 直接加载会 ModuleNotFoundError。
      这类"纯常量覆盖度"检查**不需要执行模块**,AST 读取反而更稳、更快、无副作用。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: dict[str, list[str]] = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id.startswith("DEFAULT_KEYWORDS_")
                and isinstance(node.value, ast.List)):
            vals = [e.value for e in node.value.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            out[node.targets[0].id] = vals
    return out


def _collect_keyword_lists() -> dict[str, list[str]]:
    return _ast_str_lists(VALIDATE / "batch_collect.py")


def test_six_platform_keywords_cover_palm_oil():
    """棕榈油必须进全部 6 个平台的关键词列表(每个平台词形不同:期货后缀/短词)。"""
    lists = _collect_keyword_lists()
    assert len(lists) == 6, f"应有 6 个平台关键词列表, 实际 {sorted(lists)}"
    missing = [name for name, kws in lists.items()
               if not any("棕榈" in kw for kw in kws)]
    assert not missing, f"这些平台关键词表缺棕榈油(该平台将采不到棕榈油舆情): {missing}"


def test_keyword_lists_have_no_duplicates():
    """词池重复会白耗本轮采集预算(抖音轮转尤其敏感)。"""
    dup = {name: [w for w in set(kws) if kws.count(w) > 1]
           for name, kws in _collect_keyword_lists().items()}
    dup = {k: v for k, v in dup.items() if v}
    assert not dup, f"关键词列表存在重复词: {dup}"


def test_dev_and_prod_batch_collect_keyword_lists_in_sync():
    """dev 与 prod(思路2/validate)两份 batch_collect 的关键词表必须一致。

    【为什么单独守】两份是**真正会被执行**的双副本:网页/调度器可能读任一份。
    只改一份 ⇒ "本机测试全绿但线上采不到",是这类双副本项目最典型的假成功。
    """
    prod = ROOT.parent.parent / "思路2" / "validate" / "batch_collect.py"
    if not prod.exists():
        prod = Path(r"D:/AgentSense/思路2/validate/batch_collect.py")
    if not prod.exists():
        pytest.skip("生产副本不存在")

    dev_lists = _collect_keyword_lists()
    prod_lists = _ast_str_lists(prod)

    diffs = {k: (dev_lists.get(k) != prod_lists.get(k)) for k in set(dev_lists) | set(prod_lists)}
    bad = {k: v for k, v in diffs.items() if v}
    assert not bad, f"两副本关键词表不一致: {sorted(bad)}"


# ──────────────────────────────────────────────────────────────────────────
# 3) 采集器/导入器的品种别名表
# ──────────────────────────────────────────────────────────────────────────
def test_htfc_aliases_cover_whole_pool():
    """华泰采集器的别名表:池内每个品种都要有别名(否则该品种研报被整体丢弃)。"""
    import research_collector_htfc as rc

    missing = set(ACTIVE_VARIETIES) - set(rc.CODE_ALIASES)
    assert not missing, f"华泰 CODE_ALIASES 缺品种: {sorted(missing)}"


def test_gtja_tag_map_covers_whole_pool():
    """国君采集器受池约束,池内每个品种必须有 infoTag 映射。"""
    import research_collector_gtja as rg

    missing = set(ACTIVE_VARIETIES) - set(rg.TAG_TO_CODE.values())
    assert not missing, f"国君 TAG_TO_CODE 缺品种: {sorted(missing)}"


def test_local_pdf_aliases_cover_whole_pool():
    """本地导入别名表:池内每个品种都要有(否则导入时该品种研报被判"无关")。"""
    import ingest_local_pdfs as ilp

    missing = set(ACTIVE_VARIETIES) - set(ilp.CODE_ALIASES)
    assert not missing, f"本地导入 CODE_ALIASES 缺品种: {sorted(missing)}"


def test_foreign_report_order_covers_whole_pool():
    """外文研报提示词白名单的 order 表:漏配会让该品种提示词缺一行(静默但不报错)。"""
    import foreign_report as fr

    _, codes = fr._whitelist_text()
    if not codes:  # 依赖不可用时该函数返回空列表,不是本测试的职责
        pytest.skip("_whitelist_text 返回空(依赖不可用)")
    missing = set(ACTIVE_VARIETIES) - set(codes)
    assert not missing, f"外文研报白名单缺品种: {sorted(missing)}"


# ──────────────────────────────────────────────────────────────────────────
# 4) 池 ⊆ 元数据,且元数据字段齐备(扩池时最容易漏的老字段)
# ──────────────────────────────────────────────────────────────────────────
def test_pool_subset_of_metadata_and_fields_complete():
    missing = set(ACTIVE_VARIETIES) - set(VARIETY_METADATA)
    assert not missing, f"池内代码缺元数据: {sorted(missing)}"

    required = ("name", "name_en", "exchange", "exchange_cn", "main_contract",
                "spot_code", "unit", "price_limit", "margin_rate",
                "trading_hours", "sector_cn", "description")
    bad = []
    for code in sorted(ACTIVE_VARIETIES):
        meta = VARIETY_METADATA[code]
        for f in required:
            if not meta.get(f):
                bad.append(f"{code}.{f}")
        if not meta.get("key_factors"):
            bad.append(f"{code}.key_factors")
    assert not bad, f"池内品种元数据字段缺失: {bad}"
