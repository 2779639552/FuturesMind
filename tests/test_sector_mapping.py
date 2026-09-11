"""Tests for `build_sector_to_varieties` (板块归并, 2026-08-25).

The sector-composite fallback needs a reverse map: VARIETY_METADATA.sector_cn
(broad bucket, may carry a parenthesized sub-sector like "黑色系(合金)") →
the list of variety codes in that broad bucket. This test locks:

  - parentheses are stripped ("黑色系(合金)" -> "黑色系"),
  - the real VARIETY_METADATA merges into exactly the 6 expected broad buckets
    (黑色系=8 / 能化=24 / 农产品=14 / 有色=12 / 航运=1 / 金融=3;
    2026-09-09 +PS 多晶硅入 有色(新能源) → 62 键).

No network; the real-metadata test uses the in-repo VARIETY_METADATA table.
"""


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


def test_real_metadata_merges_four_buckets():
    # Locks the pool reverse-map used by the sector-composite fallback.
    sector_map = build_sector_to_varieties()  # default: real VARIETY_METADATA
    assert sorted(sector_map["黑色系"]) == ["HC", "I", "J", "JM", "RB", "SF", "SM", "WR"]
    # 2026-09-01:能化 6 → 19(扩 12 能化 + 烧碱 SH);2026-09-08:+BR/PG/BZ/PR/SP
    assert sorted(sector_map["能化"]) == [
        "BR", "BU", "BZ", "EB", "EG", "FG", "FU", "L", "LU", "MA", "NR",
        "PF", "PG", "PP", "PR", "PX", "RU", "SA", "SC", "SH", "SP", "TA", "UR", "V",
    ]
    # 2026-09-01:农产品 8 → 14(扩 C/CS/JD/LH/P/Y)
    assert sorted(sector_map["农产品"]) == [
        "AP", "C", "CF", "CJ", "CS", "JD", "LH", "M", "OI", "P", "PK", "RM", "SR", "Y",
    ]
    # 2026-09-01 新增有色桶:贵金属 AG/AU + 工业金属 AL/AO/CU/NI/PB/SN/ZN + 新能源 LC/SI
    # 2026-09-09 +PS 多晶硅(sector_cn "有色(新能源)" 去括号归入"有色")
    assert sorted(sector_map["有色"]) == [
        "AG", "AL", "AO", "AU", "CU", "LC", "NI", "PB", "PS", "SI", "SN", "ZN",
    ]
    # 2026-09-08 新增桶:EC 航运 + IF/TS/TL 金融
    assert sector_map["航运"] == ["EC"]
    assert sorted(sector_map["金融"]) == ["IF", "TL", "TS"]


def test_real_metadata_covers_all_62_pool_varieties():
    sector_map = build_sector_to_varieties()
    pooled = (
        set(sector_map["黑色系"])
        | set(sector_map["能化"])
        | set(sector_map["农产品"])
        | set(sector_map["有色"])
        | set(sector_map["航运"])
        | set(sector_map["金融"])
    )
    assert pooled == {
        "RB", "HC", "I", "JM", "J", "SM", "SF", "WR",                             # 黑色系(8)
        "TA", "MA", "FG", "SA", "UR", "PF",                                        # 能化(原6)
        "SC", "LU", "FU", "BU", "RU", "NR", "EB", "V", "PP", "L", "EG", "PX",      # 能化(+12)
        "SH",                                                                      # 能化(+烧碱)
        "PG", "BZ", "PR", "SP", "BR",                                              # 能化(2026-09-08 +5)
        "AG", "AL", "AO", "AU", "CU", "NI", "PB", "SN", "ZN", "LC", "SI",          # 有色(11)
        "PS",                                                                      # 有色(2026-09-09 +多晶硅)
        "M", "CF", "SR", "OI", "RM", "AP", "CJ", "PK",                             # 农产品(原8)
        "C", "CS", "JD", "LH", "P", "Y",                                           # 农产品(+6)
        "EC",                                                                      # 航运(2026-09-08)
        "IF", "TS", "TL",                                                          # 金融(2026-09-08)
    }
