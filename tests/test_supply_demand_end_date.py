"""宏观指数截止日裁剪护栏（2026-10-23）。

【为什么】`get_futures_supply_demand` 里的建筑业指数 / 房地产景气指数原先直接
`iloc[-1]` 取最新 —— 和 `get_verified_quote` 的窗口 bug 同源：
**收了截止日，却取序列尾**。回看某一天的分析时，会读到该日之后公布的宏观读数，
把"当时的需求"讲成"现在的需求"。

【判据】给了 `end_date` 就裁到该日（含）；没给就不裁；裁不掉（日期列解析失败）
原样返回 —— **绝不因为裁剪失败丢数据**。
"""
from __future__ import annotations

import pandas as pd
import pytest

from tradingagents.dataflows.commodity_futures import _clip_series_to_end


def _df():
    return pd.DataFrame({
        "日期": ["2026-09-30", "2026-10-10", "2026-10-20", "2026-11-05"],
        "指数值": [100.0, 101.0, 102.0, 103.0],
    })


def test_clips_to_end_date_inclusive():
    out = _clip_series_to_end(_df(), "日期", "2026-10-20")
    assert list(out["指数值"]) == [100.0, 101.0, 102.0], "没有裁到 end_date（含当日）"


def test_no_end_date_returns_original():
    df = _df()
    out = _clip_series_to_end(df, "日期", "")
    assert len(out) == len(df), "未给截止日时不该裁"


def test_empty_end_date_none_safe():
    assert _clip_series_to_end(None, "日期", "2026-10-20") is None
    assert len(_clip_series_to_end(pd.DataFrame(), "日期", "2026-10-20")) == 0


def test_unparyseable_date_column_returns_original():
    """日期列解析不了就原样返回 —— 宁可不用截断，也不能把数据整没了。"""
    df = pd.DataFrame({"日期": ["不是日期", "也不是"], "指数值": [1.0, 2.0]})
    out = _clip_series_to_end(df, "日期", "2026-10-20")
    assert len(out) == 2, "裁剪异常时丢了数据"


def test_all_rows_after_end_yields_empty():
    out = _clip_series_to_end(_df(), "日期", "2026-09-01")
    assert len(out) == 0, "全部晚于截止日时应返回空表（由调用方判空降级）"
