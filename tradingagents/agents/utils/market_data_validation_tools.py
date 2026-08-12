from typing import Annotated  # 【调用包】类型注解:给工具参数附加描述,供 LangChain 生成工具说明

from langchain_core.tools import tool  # 【调用包】LangChain 工具装饰器:把普通函数注册为 Agent 可调用的 Tool

from tradingagents.dataflows.market_data_validator import build_verified_market_snapshot  # 【调用包】确定性核验器:生成"已验证的行情快照",作为数值主张的唯一真相源


# 【功能】返回"确定性核验行情快照":截至 curr_date 的最新 OHLCV、常用技术指标与近期收盘。
# 【参数】symbol: 股票代码;curr_date: 当前交易日 yyyy-mm-dd;
#        look_back_days: 纳入 sanity-check 的近期交易行数,默认 30。
# 【返回】快照文本;若日期无数据返回带原因的说明。
# 【关键】在陈述精确价位/布林带/RSI/MACD/均线/支撑阻力/历史对比之前必须先调用它。
@tool
def get_verified_market_snapshot(
    symbol: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "the current trading date, YYYY-mm-dd"],
    look_back_days: Annotated[
        int, "number of recent trading rows to include for sanity-checking"
    ] = 30,
) -> str:
    """Deterministic verification snapshot for exact market-data claims.

    Returns the latest OHLCV row on or before curr_date, common technical
    indicators, and recent closes. Call this before making exact claims about
    price levels, Bollinger bands, RSI, MACD, moving averages, support /
    resistance, or historical comparisons, and treat it as the source of truth.
    """
    return build_verified_market_snapshot(symbol, curr_date, look_back_days)  # 【调用函数】生成确定性核验快照:行情数据与指标交叉校验后返回
