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

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_DATA_DIR = os.path.join(os.path.expanduser("~"), ".tradingagents", "external_data")
MAX_AGE_HOURS = 168  # 7 days — one week, matches Mysteel's weekly cadence


def _get_data_dir() -> str:
    """Get external data directory, respecting env override."""
    return os.environ.get("TRADINGAGENTS_EXTERNAL_DATA_DIR", DEFAULT_DATA_DIR)


# ---------------------------------------------------------------------------
# Core: load & validate external data
# ---------------------------------------------------------------------------


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
                        import yaml

                        raw = yaml.safe_load(f)
                    except ImportError:
                        logger.warning("PyYAML not installed, skipping %s", filepath)
                        continue
                else:
                    raw = json.load(f)
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
                updated = datetime.fromisoformat(updated_str)
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


def get_external_field(variety: str, field_path: str, default: Any = None) -> Any | None:
    """Get a specific field from external data by dot-separated path.

    Args:
        variety: Variety code, e.g. "RB"
        field_path: Dot-separated path, e.g. "data.social_inventory.value"
        default: Value to return if field not found

    Returns:
        Field value or default.
    """
    external = load_external_data(variety)
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


def get_external_source_label(variety: str) -> str:
    """Get human-readable source label from external data metadata.

    Returns empty string if no external data available.
    """
    external = load_external_data(variety)
    if external is None:
        return ""

    source = external.get("source", "external")
    updated = external.get("updated", "unknown")
    return f"[source: {source}, updated: {updated}]"


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
        source_label = get_external_source_label(variety)
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


def merge_inventory_data(variety: str, api_csv: str) -> tuple[str, bool]:
    """Merge warehouse receipt data (API) with social inventory data (external).

    If external social inventory is available, append it as a separate
    clearly-labeled section. The LLM gets both data sources and can
    reason about their differences.

    Args:
        variety: Variety code
        api_csv: CSV string from free API (warehouse receipts)

    Returns:
        (merged_content, used_external) tuple.
    """
    external = load_external_data(variety)
    if external is None:
        return annotate_with_source(variety, api_csv, is_external=False), False

    data = external.get("data", {})
    social_inv = data.get("social_inventory")
    mill_inv = data.get("mill_inventory")

    has_external = social_inv is not None or mill_inv is not None
    if not has_external:
        return annotate_with_source(variety, api_csv, is_external=False), False

    source_label = get_external_source_label(variety)

    # Build the merged output
    parts = [
        "# ============================================================",
        f"# COMBINED INVENTORY DATA for {variety}",
        "# ============================================================",
        "",
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


def merge_basis_data(variety: str, api_csv: str) -> tuple[str, bool]:
    """Merge basis data from API with external spot price if available.

    Args:
        variety: Variety code
        api_csv: CSV string from free API

    Returns:
        (merged_content, used_external) tuple.
    """
    external = load_external_data(variety)
    if external is None:
        return annotate_with_source(variety, api_csv, is_external=False), False

    spot = external.get("data", {}).get("spot_price")
    if spot is None:
        return annotate_with_source(variety, api_csv, is_external=False), False

    source_label = get_external_source_label(variety)
    note = (
        f"# EXTERNAL SPOT PRICE: {spot.get('value')} {spot.get('unit', '元/吨')} "
        f"as of {spot.get('date', 'N/A')} ({source_label})\n"
        f"# Compare with the API-derived spot prices below. "
        f"If they diverge significantly, prefer the external source.\n"
        f"# ---\n"
    )
    return note + annotate_with_source(variety, api_csv, is_external=False), True


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
