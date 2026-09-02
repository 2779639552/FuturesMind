"""
External Data Injection Layer for Commodity Futures.

Allows users to inject higher-quality data from paid sources (Mysteel, Wind, etc.)
or manual collection into the automated analysis pipeline.

How it works:
  1. Drop a JSON/CSV file in ~/.tradingagents/external_data/
  2. Each data function checks external source FIRST
  3. If external data exists and is fresh enough → use it, labeled with source
  4. If external data is missing/expired → fall through to free API automatically
  5. Every response is annotated with its data source, so the LLM knows provenance

File format (~/.tradingagents/external_data/RB.json):
{
  "variety": "RB",
  "updated": "2026-07-15T16:00:00",
  "source": "Mysteel Weekly 2026-W28",
  "data": {
    "social_inventory": {
      "value": 700.08,
      "unit": "万吨",
      "change_wow": 15.0,
      "change_wow_pct": 2.2,
      "trend": "连续三周累库",
      "note": "35城螺纹钢社会库存"
    },
    "mill_inventory": {
      "value": 320.5,
      "unit": "万吨",
      "change_wow": -8.3,
      "note": "钢厂库存"
    },
    "capacity_utilization": {
      "bf_operating_rate": 86.5,
      "eaf_operating_rate": 52.3,
      "unit": "%",
      "note": "高炉/电炉开工率"
    },
    "iron_ore_port_inventory": {
      "value": 16700,
      "unit": "万吨",
      "change_wow": 120,
      "note": "铁矿石港口库存"
    },
    "spot_price": {
      "value": 3089,
      "unit": "元/吨",
      "date": "2026-07-14"
    },
    "profit_margin": {
      "bf_mill_profit": 50,
      "eaf_mill_profit": -80,
      "unit": "元/吨",
      "note": "高炉/电炉吨钢利润"
    }
  }
}

Staleness check: data older than `max_age_hours` is treated as stale
and the function falls through to the free API with a warning annotation.
"""

# ===========================================================================
# 【本文件在数据流中的角色】
#   这是"外部数据注入层"(External Data Injection Layer),是 Hybrid Mode 的规范
#   与实现所在地。commodity_futures.py 里的基差/库存/供需函数会调用本文件的
#   load_external_data / merge_basis_data / merge_inventory_data,
#   从而实现"外部 JSON 优先,免费 API 兜底"。
#
# 【为什么需要它】
#   免费接口(AKShare)拿不到 Mysteel/Wind 级别的行业数据(社会库存、钢厂利润、
#   周度产量、关键事件等)。用户可把从付费渠道或手工收集的数据按约定格式存成
#   JSON 文件,注入到自动分析管线里,让大模型看到更高质量的数据。
#
# 【核心规则(Hybrid Mode 回退逻辑)】
#   1. 到 ~/.tradingagents/external_data/ 目录找 {品种大写}.json(也支持 .yaml);
#   2. 找到且 "updated" 时间不超过 MAX_AGE_HOURS(默认168小时=7天) -> 采用外部数据;
#   3. 找不到文件 / 已过期 / JSON 损坏 / 声明的品种不匹配 -> 返回 None,
#      由调用方自然"落回"免费 AKShare;
#   4. 每次返回的数据都会带来源标注(get_external_source_label / annotate_with_source),
#      让大模型知道"这段数据来自外部还是免费接口",从而自行决定权重。
#
# 【与 signal_analyzer 回测引擎数据源的区别】
#   signal_analyzer 回测引擎也读 ~/.tradingagents/external_data/ 目录,但它读的是
#   *_sentiment.json 做离线情绪回测;本文件是给"实时分析"时注入比免费接口更好的
#   外部数据。二者数据目录重叠,但用途不同:一个是回测数据源,一个是实时分析的
#   数据增强层。
# ===========================================================================

import json  # 【调用包】JSON 读写(外部数据文件解析/样本落盘)
import logging  # 【调用包】日志输出(读取失败/过期告警)
import os  # 【调用包】路径拼接与环境变量读取(数据目录覆盖)
from datetime import datetime, timedelta, timezone  # 【调用包】时间戳解析与过期判断
from pathlib import Path  # 【调用包】路径对象与文件操作
from typing import Any  # 【调用包】任意类型注解(外部数据字典)

# 从 research_data.py(研报注入层)导入研报聚合读取接口,供 merge_basis_data /
# merge_inventory_data 把"人工上传研报"作为最高优先级数据源并入(优先级链
# RESEARCH > EXTERNAL > FREE_API)。research_data 不反向依赖本模块,无循环导入。
from tradingagents.dataflows.research_data import load_research_data  # 【调用包】研报注入层(人工上传研报,可信优先级最高)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# 【配置项】
# DEFAULT_DATA_DIR: 外部数据默认目录 = ~/.tradingagents/external_data/
#                   (即用户主目录下的 .tradingagents/external_data)。
# MAX_AGE_HOURS:    数据有效期上限(小时)。默认 168 = 7 天,
#                   对应 Mysteel 周度报告节奏。超过即视为"过期"。
# _get_data_dir():  可通过环境变量 TRADINGAGENTS_EXTERNAL_DATA_DIR 覆盖目录。

DEFAULT_DATA_DIR = os.path.join(os.path.expanduser("~"), ".tradingagents", "external_data")  # 【变量】外部数据默认目录 = ~/.tradingagents/external_data
MAX_AGE_HOURS = 168  # 7 days — one week, matches Mysteel's weekly cadence


# 【功能】返回外部数据目录,优先读取环境变量,未设置则用默认目录。
# 【参数】无。
# 【返回】目录路径字符串。
# 【关键逻辑】os.environ.get("TRADINGAGENTS_EXTERNAL_DATA_DIR", DEFAULT_DATA_DIR):
#           若设置了该环境变量就返回它的值,否则返回 DEFAULT_DATA_DIR。
def _get_data_dir() -> str:
    """Get external data directory, respecting env override."""
    return os.environ.get("TRADINGAGENTS_EXTERNAL_DATA_DIR", DEFAULT_DATA_DIR)


# ---------------------------------------------------------------------------
# Core: load & validate external data
# ---------------------------------------------------------------------------


# 【功能】加载某个品种的外部数据文件(外部数据注入的核心入口)。
# 【参数】variety: 品种代码,如 "RB"。
# 【返回】完整的外部数据 dict;找不到文件/过期/格式损坏/品种不匹配时返回 None。
# 【关键逻辑】1) 依次尝试 .json / .yaml / .yml 三种扩展名;
#           2) YAML 需要 PyYAML,未安装则跳过该文件;
#           3) 校验文件里声明的 variety 与请求一致,否则忽略;
#           4) 校验 "updated" 时间戳是否在 MAX_AGE_HOURS(7天)内,
#              过期则记 warning 并返回 None(调用方落回免费 API);
#           5) 所有失败都记日志后继续尝试下一个文件,不会抛异常。
def load_external_data(variety: str) -> dict[str, Any] | None:
    """Load external data for a commodity variety.

    Looks for {variety}.json in the external data directory.
    Returns None if no file found, data is stale, or JSON is invalid.

    Args:
        variety: Variety code, e.g. "RB"

    Returns:
        Full external data dict, or None if unavailable/stale.
    """
    data_dir = Path(_get_data_dir())
    data_dir.mkdir(parents=True, exist_ok=True)

    # Try both .json and .yaml
    for ext in (".json", ".yaml", ".yml"):
        filepath = data_dir / f"{variety.upper()}{ext}"
        if not filepath.exists():
            continue

        try:
            with open(filepath, encoding="utf-8") as f:
                if ext in (".yaml", ".yml"):
                    try:
                        import yaml  # 【调用包】YAML 解析(外部数据支持 .yaml/.yml 格式)

                        raw = yaml.safe_load(f)  # 【调用函数】解析 YAML 外部数据文件
                    except ImportError:
                        logger.warning("PyYAML not installed, skipping %s", filepath)
                        continue
                else:
                    raw = json.load(f)  # 【调用函数】解析 JSON 外部数据文件
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read %s: %s", filepath, e)
            continue

        if not isinstance(raw, dict):
            logger.warning("External data in %s is not a dict, ignoring.", filepath)
            continue

        # Validate variety matches
        declared = raw.get("variety", "").upper()
        if declared and declared != variety.upper():
            logger.warning(
                "External data file %s declares variety=%s, expected %s. Ignoring.",
                filepath,
                declared,
                variety,
            )
            continue

        # Check staleness
        updated_str = raw.get("updated", "")
        if updated_str:
            try:
                updated = datetime.fromisoformat(updated_str)  # 【调用函数】解析 ISO 时间戳(用于计算数据年龄)
                age = datetime.now(timezone.utc).replace(tzinfo=None) - updated.replace(tzinfo=None)
                if age > timedelta(hours=MAX_AGE_HOURS):
                    logger.warning(
                        "External data for %s is %.1f hours old (max %d). "
                        "Treating as stale, will fall through to API.",
                        variety,
                        age.total_seconds() / 3600,
                        MAX_AGE_HOURS,
                    )
                    return None
            except ValueError:
                logger.warning("Could not parse 'updated' timestamp in %s", filepath)

        logger.info(
            "Loaded external data for %s from %s (source: %s)",
            variety,
            filepath,
            raw.get("source", "unknown"),
        )
        return raw

    return None


# 【功能】按"点号分隔的路径"从外部数据里取某一个字段值。
# 【参数】variety: 品种代码;field_path: 形如 "data.social_inventory.value";
#        default: 字段不存在时返回的默认值。
# 【返回】字段值,或 default。
# 【关键逻辑】先 load_external_data(variety)(可能返回 None),再逐级拆分路径
#           data.social_inventory.value 依次向下取 dict 键,任一级不存在就返回 default。
def get_external_field(variety: str, field_path: str, default: Any = None) -> Any | None:
    """Get a specific field from external data by dot-separated path.

    Args:
        variety: Variety code, e.g. "RB"
        field_path: Dot-separated path, e.g. "data.social_inventory.value"
        default: Value to return if field not found

    Returns:
        Field value or default.
    """
    external = load_external_data(variety)  # 【调用函数】读外部数据(无/过期返回 None)
    if external is None:
        return default

    parts = field_path.split(".")
    current = external
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


# 【功能】生成外部数据的"人类可读来源标签",用于给大模型标注数据出处。
# 【参数】variety: 品种代码。
# 【返回】形如 "[source: Mysteel Weekly 2026-W28, updated: 2026-07-15T16:00:00]";
#        无外部数据时返回空字符串。
# 【关键逻辑】读取外部 JSON 的 source 与 updated 字段拼成标签;load_external_data
#           内部已做过过期校验,所以这里拿到的必是"有效"的外部数据。
def get_external_source_label(variety: str) -> str:
    """Get human-readable source label from external data metadata.

    Returns empty string if no external data available.
    """
    external = load_external_data(variety)  # 【调用函数】读外部数据(无/过期返回 None)
    if external is None:
        return ""

    source = external.get("source", "external")
    updated = external.get("updated", "unknown")
    return f"[source: {source}, updated: {updated}]"


# 【功能】在返回给大模型的内容前,加上"数据来源"标注头。
# 【参数】variety: 品种代码;content: 数据内容(CSV/文本);is_external: 是否外部数据。
# 【返回】加了来源标注头的内容字符串。
# 【关键逻辑】这是数据透明机制的关键:
#           is_external=True  -> 标注 EXTERNAL + 来源标签,并提示"可能优于免费接口";
#           is_external=False -> 标注 FREE_API (AKShare),并提示"关键决策需对
#                                 照付费源核验"。
#           若 is_external=True 但拿不到来源标签,则原样返回 content 不加头。
def annotate_with_source(variety: str, content: str, is_external: bool = False) -> str:
    """Prepend data-source annotation to content returned to the LLM.

    This is the key transparency mechanism: every data response tells the
    LLM exactly where the data came from, so it can weight it accordingly.

    Args:
        variety: Variety code
        content: Data content string (CSV, text, etc.)
        is_external: Whether this data came from external injection

    Returns:
        Annotated content string.
    """
    if is_external:
        source_label = get_external_source_label(variety)  # 【调用函数】生成外部来源标签(无外部数据时为空串)
        if source_label:
            header = (
                f"# DATA_SOURCE: EXTERNAL {source_label}\n"
                f"# This data was injected from an external source "
                f"(e.g., Mysteel, Wind, manual collection).\n"
                f"# It may differ from free API data. Treat as higher quality.\n"
                f"# ---\n"
            )
            return header + content
        else:
            return content
    else:
        header = (
            "# DATA_SOURCE: FREE_API (AKShare)\n"
            "# This data comes from free public APIs. Verify against paid "
            "sources (Mysteel, Wind) for critical decisions.\n"
            "# ---\n"
        )
        return header + content


# ---------------------------------------------------------------------------
# External data merge helpers for specific data types
# ---------------------------------------------------------------------------


# 【功能】从研报聚合 dict 中查找最新一份含指定数据点字段的研报,返回该数据点。
# 【参数】research: load_research_data 返回的聚合 dict 或 None;key: 数据点键
#           (如 "spot_price" / "social_inventory" / "mill_inventory")。
# 【返回】dict | None:数据点 {value, unit, date, ...};无研报或该字段为空返回 None。
# 【关键逻辑】reports 列表最新在前;逐份找第一个 value 不为空的数据点即返回,
#           保证"最新研报优先"。这是研报(最高优先级)并入 merge_* 的统一入口。
def _research_point(research, key):
    """Find the newest research report's data point for `key`, or None."""
    if not research or not research.get("reports"):
        return None
    for r in research["reports"]:
        dps = r.get("data_points") or {}
        if isinstance(dps, dict):
            pt = dps.get(key)
            if isinstance(pt, dict) and pt.get("value") is not None:
                return pt
    return None


# 【功能】构造"研报库存"区块文本行(Part 0),供 merge_inventory_data 并入。
# 【参数】res_social/res_mill: 研报社会库存/厂库数据点(dict 或 None)。
# 【返回】list[str]:研报库存区块的文本行(含来源标注),无任何研报库存时返回空列表。
def _research_inventory_section(res_social, res_mill) -> list:
    """Build the RESEARCH inventory section lines (Part 0) for the merged output.

    Returns an empty list when neither research inventory exists, so callers
    never emit a "Part 0" header with no data underneath (would mislead the LLM
    into thinking research data is present).
    """
    if res_social is None and res_mill is None:
        return []
    lines = [
        "## Part 0: Research Report Inventory (研报库存, 可信优先级最高)",
        "# 研报为人工上传的一手材料,优先于 EXTERNAL 与 FREE_API;若与免费数据分歧,优先采信研报。",
        "# ---",
    ]
    if res_social:
        lines += [
            "Research Social Inventory (研报社会库存):",
            f"  Value: {res_social.get('value')} {res_social.get('unit', '万吨')}",
            f"  Date: {res_social.get('date', 'N/A')}",
            "",
        ]
    if res_mill:
        lines += [
            "Research Mill Inventory (研报厂库):",
            f"  Value: {res_mill.get('value')} {res_mill.get('unit', '万吨')}",
            f"  Date: {res_mill.get('date', 'N/A')}",
            "",
        ]
    return lines


# 【功能】研报库存存在、外部库存不存在时:研报 Part 0 + 免费 API 仓单 + 解读指引。
# 【参数】variety: 品种代码;api_csv: 免费 API 仓单 CSV;res_social/res_mill: 研报库存数据点。
# 【返回】str:合并后的库存报告文本(used 标记由调用方置 True)。
def _research_only_inventory(variety, api_csv, res_social, res_mill) -> str:
    parts = [
        "# ============================================================",
        f"# COMBINED INVENTORY DATA for {variety}",
        "# ============================================================",
        "",
    ]
    parts.extend(_research_inventory_section(res_social, res_mill))  # 【调用函数】研报库存区块(Part 0,最高优先级)
    parts += [
        "## Part 1: Warehouse Receipts (仓单库存) — FREE API",
        "# Source: SHFE via AKShare (daily, exchange-registered warrants)",
        "# This reflects deliverable supply only, NOT total market inventory.",
        "# ---",
        api_csv,
        "",
        "# ============================================================",
        "# INTERPRETATION GUIDE for the LLM:",
        "# - 研报库存(社会/厂库)为全市场口径, 交易所仓单仅代表可交割供给;",
        "# - 研报方向与库存同向 → 观点有数据支撑; 反向 → 谨慎并说明分歧。",
        "# ============================================================",
    ]
    return "\n".join(parts)


# 【功能】把免费 API 的"仓单库存"与外部 JSON 的"社会库存/钢厂库存"合并成一份报告。
# 【参数】variety: 品种代码;api_csv: 免费接口返回的仓单 CSV 字符串。
# 【返回】元组 (merged_content, used_external):
#        merged_content 为合并后的文本;used_external 标记是否真的用到了外部数据。
# 【关键逻辑】1) 先 load_external_data;没有外部数据(或没有社会/钢厂库存字段)
#              就直接给 API 数据打 FREE_API 标注返回,used_external=False;
#           2) 有外部库存时,输出分两部分:
#              Part 1 = 免费 API 仓单库存(交易所注册仓单,仅代表可交割供给);
#              Part 2 = 外部社会库存(35城)+ 钢厂库存;
#           3) 额外附上铁矿港口库存、开工率、吨钢利润等补充数据;
#           4) 末尾给大模型一段"解读指引":仓单与社会库存同向/反向的含义。
def merge_inventory_data(variety: str, api_csv: str) -> tuple[str, bool]:
    """Merge warehouse receipt data (API) with research/external inventory data.

    Priority chain: RESEARCH (人工上传研报, Part 0) > EXTERNAL (Part 2) > FREE_API.
    Research social/mill inventory is prepended as the highest-trust section;
    external social inventory is appended for comparison; the free API warehouse
    receipts always serve as the exchange-registered baseline.

    Args:
        variety: Variety code
        api_csv: CSV string from free API (warehouse receipts)

    Returns:
        (merged_content, used_external) tuple. used_external=True when research
        OR external inventory was actually merged.
    """
    # 研报(最高优先级)先读:社会库存/厂库数据点。研报聚合 JSON 里 reports 最新在前,
    # _research_point 取最新一份含该字段的研报。
    res_social = _research_point(load_research_data(variety), "social_inventory")  # 【调用函数】研报社会库存数据点(最高优先级源)
    res_mill = _research_point(load_research_data(variety), "mill_inventory")  # 【调用函数】研报厂库数据点(最高优先级源)
    has_research = res_social is not None or res_mill is not None

    external = load_external_data(variety)  # 【调用函数】读外部数据(无/过期返回 None)
    if external is None:
        if not has_research:
            return annotate_with_source(variety, api_csv, is_external=False), False  # 【调用函数】打 FREE_API 来源标注头并原样返回
        return _research_only_inventory(variety, api_csv, res_social, res_mill), True  # 【调用函数】研报+API 组合输出

    data = external.get("data", {})
    social_inv = data.get("social_inventory")
    mill_inv = data.get("mill_inventory")

    has_external = social_inv is not None or mill_inv is not None
    if not has_external:
        if not has_research:
            return annotate_with_source(variety, api_csv, is_external=False), False  # 【调用函数】无外部/研报库存→打 FREE_API 标注原样返回
        return _research_only_inventory(variety, api_csv, res_social, res_mill), True  # 【调用函数】研报+API 组合输出

    source_label = get_external_source_label(variety)  # 【调用函数】生成外部来源标签(供标注头/标题使用)

    # Build the merged output
    parts = [
        "# ============================================================",
        f"# COMBINED INVENTORY DATA for {variety}",
        "# ============================================================",
        "",
    ]
    # 研报库存作为 Part 0 前置(最高优先级)
    parts.extend(_research_inventory_section(res_social, res_mill))  # 【调用函数】研报库存区块(Part 0)
    parts += [
        "## Part 1: Warehouse Receipts (仓单库存) — FREE API",
        "# Source: SHFE via AKShare (daily, exchange-registered warrants)",
        "# This reflects deliverable supply only, NOT total market inventory.",
        "# ---",
        api_csv,
        "",
        "## Part 2: Social & Mill Inventory (社会库存+钢厂库存) — EXTERNAL",
        f"# {source_label}",
        "# WARNING: 仓单库存 ≠ 社会库存. Social inventory covers 35-city",
        "# trader holdings; mill inventory covers factory stocks.",
        "# Use BOTH together to understand the full inventory picture.",
        "# ---",
    ]

    if social_inv:
        parts.append("Social Inventory (社会库存):")
        parts.append(f"  Value: {social_inv.get('value')} {social_inv.get('unit', '万吨')}")
        parts.append(
            f"  WoW Change: {social_inv.get('change_wow', 'N/A')} ({social_inv.get('change_wow_pct', 'N/A')}%)"
        )
        parts.append(f"  Trend: {social_inv.get('trend', 'N/A')}")
        parts.append(f"  Scope: {social_inv.get('note', '35城')}")
        parts.append("")

    if mill_inv:
        parts.append("Mill Inventory (钢厂库存):")
        parts.append(f"  Value: {mill_inv.get('value')} {mill_inv.get('unit', '万吨')}")
        parts.append(f"  WoW Change: {mill_inv.get('change_wow', 'N/A')}")
        parts.append(f"  Scope: {mill_inv.get('note', 'N/A')}")
        parts.append("")

    # Add any supplementary data
    iron_ore = data.get("iron_ore_port_inventory")
    if iron_ore:
        parts.append("Related — Iron Ore Port Inventory (铁矿港口库存):")
        parts.append(f"  Value: {iron_ore.get('value')} {iron_ore.get('unit', '万吨')}")
        parts.append(f"  WoW Change: {iron_ore.get('change_wow', 'N/A')}")
        parts.append("")

    capacity = data.get("capacity_utilization")
    if capacity:
        parts.append("Related — Capacity Utilization (开工率):")
        parts.append(f"  BF (高炉): {capacity.get('bf_operating_rate', 'N/A')}%")
        parts.append(f"  EAF (电炉): {capacity.get('eaf_operating_rate', 'N/A')}%")
        parts.append(f"  Note: {capacity.get('note', 'N/A')}")
        parts.append("")

    profit = data.get("profit_margin")
    if profit:
        parts.append("Related — Mill Profit Margin (吨钢利润):")
        parts.append(
            f"  BF (高炉): {profit.get('bf_mill_profit', 'N/A')} {profit.get('unit', '元/吨')}"
        )
        parts.append(
            f"  EAF (电炉): {profit.get('eaf_mill_profit', 'N/A')} {profit.get('unit', '元/吨')}"
        )
        parts.append(f"  Note: {profit.get('note', 'N/A')}")
        parts.append("")

    annotate = (
        "# ============================================================\n"
        "# INTERPRETATION GUIDE for the LLM:\n"
        "# - Warehouse receipts UP + Social inventory DOWN = delivery pressure, bearish for nearby\n"
        "# - Warehouse receipts FLAT + Social inventory UP = weak demand, bearish medium-term\n"
        "# - Both DOWN = tight supply across all channels, strongly bullish\n"
        "# - Both UP = oversupply, strongly bearish\n"
        "# ============================================================\n"
    )
    parts.append(annotate)

    merged = "\n".join(parts)
    return merged, True


# 【功能】把免费 API 的基差数据与外部 JSON 的现货价合并。
# 【参数】variety: 品种代码;api_csv: 免费接口返回的基差 CSV 字符串。
# 【返回】元组 (merged_content, used_external)。
# 【关键逻辑】1) 没有外部数据或外部 JSON 里没有 spot_price -> 原样返回 API 数据
#              并打 FREE_API 标注,used_external=False;
#           2) 有外部现货价 -> 在 API 结果前追加一行外部现货价说明
#              (值/单位/日期/来源标签),提示大模型:若与接口现货价分歧较大,
#              优先采信外部源;used_external=True。
def merge_basis_data(variety: str, api_csv: str) -> tuple[str, bool]:
    """Merge basis data from API with research/external spot price if available.

    Priority chain: RESEARCH (人工上传研报) > EXTERNAL > FREE_API. Research spot
    price is used first; external spot price is the fallback; otherwise the free
    API data is annotated and returned unchanged.

    Args:
        variety: Variety code
        api_csv: CSV string from free API

    Returns:
        (merged_content, used_external) tuple. used_external=True when research
        OR external spot was merged.
    """
    # 研报现货价(最高优先级)优先;无研报现货价再回退外部现货价。
    res_spot = _research_point(load_research_data(variety), "spot_price")  # 【调用函数】研报现货价数据点(最高优先级源)
    external = load_external_data(variety)  # 【调用函数】读外部数据(无/过期返回 None)
    ext_spot = external.get("data", {}).get("spot_price") if external else None

    if res_spot:
        note = (
            f"# RESEARCH SPOT PRICE: {res_spot.get('value')} {res_spot.get('unit', '元/吨')} "
            f"as of {res_spot.get('date', 'N/A')} (人工上传研报, 可信优先级最高)\n"
            f"# Compare with the API-derived spot prices below. "
            f"If they diverge significantly, prefer the research report value.\n"
            f"# ---\n"
        )
        return note + annotate_with_source(variety, api_csv, is_external=False), True  # 【调用函数】研报现货价说明拼接在 API 数据前(标注 FREE_API)

    if ext_spot is None:
        return annotate_with_source(variety, api_csv, is_external=False), False  # 【调用函数】无研报/外部现货价→打 FREE_API 标注原样返回

    source_label = get_external_source_label(variety)  # 【调用函数】生成外部来源标签(追加到现货价说明)
    note = (
        f"# EXTERNAL SPOT PRICE: {ext_spot.get('value')} {ext_spot.get('unit', '元/吨')} "
        f"as of {ext_spot.get('date', 'N/A')} ({source_label})\n"
        f"# Compare with the API-derived spot prices below. "
        f"If they diverge significantly, prefer the external source.\n"
        f"# ---\n"
    )
    return note + annotate_with_source(variety, api_csv, is_external=False), True  # 【调用函数】外部现货价说明拼接在 API 数据前(标注 FREE_API)


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Create a sample external data file
    sample = {
        "variety": "RB",
        "updated": "2026-07-15T16:00:00",
        "source": "Mysteel Weekly 2026-W28",
        "data": {
            "social_inventory": {
                "value": 700.08,
                "unit": "万吨",
                "change_wow": 15.0,
                "change_wow_pct": 2.2,
                "trend": "连续三周累库",
                "note": "35城螺纹钢社会库存",
            },
            "mill_inventory": {
                "value": 320.5,
                "unit": "万吨",
                "change_wow": -8.3,
                "note": "钢厂库存，环比下降",
            },
            "capacity_utilization": {
                "bf_operating_rate": 86.5,
                "eaf_operating_rate": 52.3,
                "unit": "%",
                "note": "高炉开工率持稳，电炉因亏损减产",
            },
            "iron_ore_port_inventory": {
                "value": 16700,
                "unit": "万吨",
                "change_wow": 120,
                "note": "铁矿石港口库存，同比大增",
            },
            "profit_margin": {
                "bf_mill_profit": 50,
                "eaf_mill_profit": -80,
                "unit": "元/吨",
                "note": "高炉微利，电炉持续亏损",
            },
        },
    }

    data_dir = Path(_get_data_dir())
    data_dir.mkdir(parents=True, exist_ok=True)
    sample_path = data_dir / "RB.json.sample"
    with open(sample_path, "w", encoding="utf-8") as f:
        json.dump(sample, f, ensure_ascii=False, indent=2)

    print(f"Sample external data saved to: {sample_path}")
    print("Copy to RB.json to activate: cp RB.json.sample RB.json")
    print()

    # Test loading
    print("Testing load_external_data('RB')...")
    result = load_external_data("RB")
    if result:
        print(f"  Loaded: source={result.get('source')}, updated={result.get('updated')}")
        print(f"  Social inventory: {result['data']['social_inventory']['value']} 万吨")
    else:
        print("  No external data found (expected — RB.json.sample won't match)")

    print()
    print("Testing get_external_field...")
    val = get_external_field("RB", "data.social_inventory.value")
    print(f"  data.social_inventory.value = {val}")

    print("\n[DONE] External data layer ready.")
