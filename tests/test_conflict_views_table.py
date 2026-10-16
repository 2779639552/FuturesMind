"""「二、观点对比与冲突」兜底解析器(_parse_llm_conflict_views)单元测试。

2026-09-17 起每日/每周总结 prompt 把第二节从"每品种一条列表"升级为
markdown 表格(品种|分歧状态|各家观点与逻辑|分歧焦点|分歧根源),
解析器必须兼容表格行,同时保留旧列表行解析(历史缓存 md 仍是列表)。

【为什么重要】LLM 常不输出第一节总表 → _parse_llm_views_table 为空 →
第二节解析结果是总表"一句话核心观点"的兜底源,解析坏了总表观点列就空。
不真调 LLM,纯函数用例。
"""

import web_app


# ---------------------------------------------------------------------------
# 表格新格式(2026-09-17 prompt)
# ---------------------------------------------------------------------------
_TABLE_BODY = """## 一、当日观点总表
| 品种 | 发行方 | 方向 | 置信度 | 一句话核心观点 | 头寸建议 |
| --- | --- | --- | --- | --- | --- |
| SC | 华泰/国君 | 看多/看空 | 0.7/0.5 | 供应分歧主导 | 一家多一家空 |

## 二、观点对比与冲突
| 品种 | 分歧状态 | 各家观点与逻辑 | 分歧焦点 | 分歧根源 |
| --- | --- | --- | --- | --- |
| SC | 多空对立 | 华泰 多(0.7)—OPEC 减产兑现 <br> 国君 空(0.5)—需求走弱 | 减产兑现 vs 需求回落 | 时间尺度不同 |
| TA | 一致看空 | 华泰 空(0.6)—投产压力 | 共识:加工费压缩 | — |
| MEG | 节奏分歧 | 东证 多(0.5)—港口去库提前 | 去库时点 | 数据口径不同 |

## 三、可信程度分析
- SC 可信度高。
"""


def test_table_rows_extract_views_from_third_column():
    views = web_app._parse_llm_conflict_views(_TABLE_BODY)
    assert "SC" in views and "TA" in views
    # <br> 换行被折成";",成为一句可读观点
    assert "华泰 多(0.7)—OPEC 减产兑现；国君 空(0.5)—需求走弱" == views["SC"]
    assert "华泰 空(0.6)—投产压力" == views["TA"]


def test_table_row_chinese_name_first_cell_resolved():
    views = web_app._parse_llm_conflict_views(_TABLE_BODY)
    # 「MEG」行正常归键(中文别名「乙二醇」同样可反查,由中文名用例单独覆盖)
    assert "MEG" in views
    assert "东证 多(0.5)—港口去库提前" == views["MEG"]


def test_table_separator_rows_and_section_boundary_ignored():
    views = web_app._parse_llm_conflict_views(_TABLE_BODY)
    # 分隔行 |---| 不得产生键;三节内容不得混入
    assert all("—" != v for v in views.values())
    assert not any("可信度" in v for v in views.values())
    assert set(views) == {"SC", "TA", "MEG"}


def test_table_first_cell_code_with_suffix():
    body = (
        "## 二、观点对比与冲突\n"
        "| SC原油 | 多空对立 | 华泰 多—减产 | 减产 | 口径 |\n"
    )
    views = web_app._parse_llm_conflict_views(body)
    assert views.get("SC") == "华泰 多—减产"


# ---------------------------------------------------------------------------
# 旧列表格式(历史缓存 md 兼容,不得回归)
# ---------------------------------------------------------------------------
def test_legacy_list_lines_still_parsed():
    body = (
        "## 二、观点对比与冲突\n"
        "- SC原油: 华泰看多减产逻辑,国君看空需求走弱\n"
        "- 对二甲苯: 两家一致看空投产压力\n"
        "## 三、可信程度分析\n"
        "- SC 可信度高\n"
    )
    views = web_app._parse_llm_conflict_views(body)
    assert "华泰看多减产逻辑" in views.get("SC", "")
    assert "PX" in views  # 对二甲苯 → PX(中文名反查)


def test_empty_and_section_free_body_return_empty():
    assert web_app._parse_llm_conflict_views("") == {}
    assert web_app._parse_llm_conflict_views("没有分节标记的正文") == {}


def test_summary_prompt_requires_conflict_table():
    """prompt 快照:第二节必须是表格化要求(逐家/分歧焦点/分歧根源)。"""
    assert hasattr(web_app, "_generate_daily_summary")
