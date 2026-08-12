"""Deterministic market-data verification snapshot.

The market analyst is an LLM that can confabulate exact numbers — citing a
Bollinger band or a "historically validated bounce" that the underlying data
doesn't support (#830). This module computes a ground-truth snapshot (latest
OHLCV row on or before the analysis date, common indicators, recent closes)
the analyst is told to treat as the source of truth for any exact numeric
claim. Deterministic, no LLM involved.
"""

from __future__ import annotations  # 【调用包】启用延迟求值的类型注解

from collections.abc import Iterable  # 【调用包】类型标注(可迭代指标名集合)

import pandas as pd  # 【调用包】DataFrame 处理与格式转换
from stockstats import wrap  # 【调用包】stockstats 指标计算

from tradingagents.dataflows.stockstats_utils import load_ohlcv  # 【调用包】加载缓存 OHLCV(含防前视过滤)

# A fixed, common indicator set so the snapshot is the same shape every run.
# 【变量】固定的常用指标集合, 保证每次快照形状一致
DEFAULT_SNAPSHOT_INDICATORS: tuple[str, ...] = (
    "close_10_ema",
    "close_50_sma",
    "close_200_sma",
    "rsi",
    "boll",
    "boll_ub",
    "boll_lb",
    "macd",
    "macds",
    "macdh",
    "atr",
)


# 【功能】取 curr_date 当日或之前的 OHLCV, 按日期排序。
# 【参数】symbol: 标的; curr_date: 分析日期。
# 【返回】pd.DataFrame(仅含 Date <= curr_date 的行)。
# 【异常】ValueError: 无可用数据或无当日及之前行。
# 【关键】load_ohlcv 已归一化日期列并过滤前视行, 但这里防御性重放一次 cutoff——
#         这是校验路径, 不能信任输入已被预先过滤。
def _verified_rows(symbol: str, curr_date: str) -> pd.DataFrame:
    """OHLCV on or before curr_date, date-sorted. Raises if nothing usable.

    ``load_ohlcv`` already normalizes the Date column and filters out
    look-ahead rows, but we re-apply the cutoff defensively — this is a
    verification path, so it must not trust its input to be pre-filtered.
    """
    data = load_ohlcv(symbol, curr_date)  # 【调用函数】加载(缓存)OHLCV
    if data is None or data.empty:
        raise ValueError(f"No OHLCV data available for {symbol}.")

    df = data.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")  # 【调用函数】解析日期
    df = df.dropna(subset=["Date"])  # 【调用函数】丢弃日期无效行
    df = df[df["Date"] <= pd.to_datetime(curr_date)].sort_values("Date")  # 【调用函数】防御性重放 cutoff 并按日期排序
    if df.empty:
        raise ValueError(f"No OHLCV rows on or before {curr_date} for {symbol}.")
    return df


# 【功能】把各种类型的值格式化为展示字符串(统一快照样式)。
# 【关键】NaN/None -> "N/A"; Timestamp -> 日期串; float -> 两位小数; 其余 str。
def _fmt(value) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int,)):
        return str(value)
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


# 【功能】渲染"经校验的市场数据快照": 最新 OHLCV 行 + 常用指标 + 近期收盘。
# 【参数】symbol: 标的; curr_date: 分析日期; look_back_days: 近期收盘行数(上限 30);
#         indicators: 要算的指标名集合(None 用默认集)。
# 【返回】markdown 快照文本, 末尾附一段"以本快照为准、不得虚构"的约束说明。
# 【关键】供市场分析师把它当作任何精确数字主张的事实来源(LLM 可能编造布林带或
#         "历史验证的反弹", #830)。本模块确定性强、不涉 LLM。
def build_verified_market_snapshot(
    symbol: str,
    curr_date: str,
    look_back_days: int = 30,
    indicators: Iterable[str] | None = None,
) -> str:
    """Render a ground-truth snapshot: latest OHLCV row, indicators, recent closes."""
    # `df` keeps the original capitalized OHLCV columns (Open/High/Low/Close/
    # Volume); stockstats `wrap()` lowercases columns and adds indicator
    # columns, so read raw prices from `df` and indicators from `stock_df`.
    df = _verified_rows(symbol, curr_date)  # 【调用函数】取校验过的 OHLCV
    stock_df = wrap(df.copy())  # 【调用函数】封装为 stockstats 对象以算指标

    selected = tuple(indicators or DEFAULT_SNAPSHOT_INDICATORS)  # 【变量】本次要算的指标集
    indicator_values: dict[str, str] = {}  # 【变量】指标名 -> 格式化值(最新行)
    for name in selected:
        try:
            stock_df[name]  # triggers stockstats calculation  # 【调用函数】读取即触发指标计算
            indicator_values[name] = _fmt(stock_df.iloc[-1][name])
        except Exception as exc:  # noqa: BLE001 — one bad indicator shouldn't sink the snapshot
            indicator_values[name] = f"N/A ({type(exc).__name__})"  # 【变量】单个指标失败不拖垮整个快照

    latest = df.iloc[-1]  # 【变量】最新一行 OHLCV
    latest_date = _fmt(latest["Date"])  # 【变量】最新交易日字符串
    window = max(1, min(int(look_back_days), 30))  # 【变量】近期收盘行数(夹在 1~30)
    recent = df.tail(window)  # 【变量】近期收盘的 DataFrame 片段

    lines = [
        f"## Verified market data snapshot for {symbol.upper()}",
        "",
        f"- Requested analysis date: {curr_date}",
        f"- Latest trading row used: {latest_date}",
        "- Rows after the requested analysis date are excluded before verification.",
        "",
        "### Latest verified OHLCV row",
        "",
        "| Field | Value |",
        "|---|---:|",
    ]
    for field in ("Open", "High", "Low", "Close", "Volume"):  # 【变量】快照表要展示的 OHLCV 字段
        lines.append(f"| {field} | {_fmt(latest.get(field))} |")

    lines += [
        "",
        "### Verified technical indicators (latest row)",
        "",
        "| Indicator | Value |",
        "|---|---:|",
    ]
    for name, value in indicator_values.items():
        lines.append(f"| {name} | {value} |")

    lines += [
        "",
        f"### Recent verified closes (last {len(recent)} rows)",
        "",
        "| Date | Close |",
        "|---|---:|",
    ]
    for _, row in recent.iterrows():
        lines.append(f"| {_fmt(row['Date'])} | {_fmt(row.get('Close'))} |")

    lines += [
        "",
        "Use this snapshot as the source of truth for exact OHLCV, price-level, "
        "and indicator-value claims. If another tool output conflicts with it, "
        "flag the discrepancy rather than inventing a reconciled number. Do not "
        "claim historical validation, support/resistance bounces, or exact "
        "percentage moves unless directly supported by tool output with concrete "
        "dates and prices.",
    ]
    return "\n".join(lines)
