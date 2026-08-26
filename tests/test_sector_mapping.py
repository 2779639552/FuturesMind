"""Tests for `build_sector_to_varieties` (板块归并, 2026-08-25).

The sector-composite fallback needs a reverse map: VARIETY_METADATA.sector_cn
(broad bucket, may carry a parenthesized sub-sector like "黑色系(合金)") →
the list of variety codes in that broad bucket. This test locks:

  - parentheses are stripped ("黑色系(合金)" -> "黑色系"),
  - the real VARIETY_METADATA merges into exactly the 3 expected broad buckets
    for the 21-pool (黑色系=7 / 能化=6 / 农产品=8).

No network; the real-metadata test uses the in-repo VARIETY_METADATA table.
"""

import pytest

from tradingagents.dataflows.sentiment_data import build_sector_to_varieties


def _custom_metadata():
    """A small synthetic metadata table with parenthesized sub-sectors."""
    return {
        "RB": {"sector_cn": "黑色系(合金)"},
        "HC": {"sector_cn": "黑色系(合金)"},
        "I": {"sector_cn": "黑色系"},
        "TA": {"sector_cn": "能化(聚酯)"},
        "MA": {"sector_cn": "能化"},
        "AP": {"sector_cn": "农产品"},
        "PK": {"sector_cn": "农产品(坚果)"},
    }


def test_parenthesized_sub_sector_stripped():
    sector_map = build_sector_to_varieties(_custom_metadata())
    assert set(sector_map) == {"黑色系", "能化", "农产品"}
    assert sorted(sector_map["黑色系"]) == ["HC", "I", "RB"]
    assert sector_map["能化"] == ["TA", "MA"]
    assert sorted(sector_map["农产品"]) == ["AP", "PK"]


def test_empty_sector_cn_skipped():
    meta = {
        "RB": {"sector_cn": "黑色系"},
        "ZZ": {"sector_cn": ""},          # empty → skipped
        "YY": {"sector_cn": "   "},       # whitespace-only → skipped
        "XX": {},                          # missing → skipped
    }
    sector_map = build_sector_to_varieties(meta)
    assert sector_map == {"黑色系": ["RB"]}


def test_real_metadata_merges_three_buckets():
    # Locks the 21-pool reverse-map used by the sector-composite fallback.
    sector_map = build_sector_to_varieties()  # default: real VARIETY_METADATA
    assert sorted(sector_map["黑色系"]) == ["HC", "I", "J", "JM", "RB", "SF", "SM"]
    assert sorted(sector_map["能化"]) == ["FG", "MA", "PF", "SA", "TA", "UR"]
    assert sorted(sector_map["农产品"]) == ["AP", "CF", "CJ", "M", "OI", "PK", "RM", "SR"]


def test_real_metadata_covers_all_21_pool_varieties():
    sector_map = build_sector_to_varieties()
    pooled = (
        set(sector_map["黑色系"])
        | set(sector_map["能化"])
        | set(sector_map["农产品"])
    )
    assert pooled == {
        "RB", "HC", "I", "JM", "J", "SM", "SF",          # 黑色系
        "TA", "MA", "FG", "SA", "UR", "PF",               # 能化
        "M", "CF", "SR", "OI", "RM", "AP", "CJ", "PK",    # 农产品
    }
