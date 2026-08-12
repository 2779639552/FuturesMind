from typing import Annotated  # 【调用包】类型注解:给工具参数附加描述,供 LangChain 生成工具说明

from langchain_core.tools import tool  # 【调用包】LangChain 工具装饰器:把普通函数注册为 Agent 可调用的 Tool

from tradingagents.dataflows.interface import route_to_vendor  # 【调用包】路由函数:按方法名把取数请求分派到配置的数据供应商


# 【功能】获取指定股票在日期区间的 OHLCV 行情数据,供 Agent 分析价格走势。
# 【参数】symbol: 股票代码(如 AAPL);start_date/end_date: 起止日期 yyyy-mm-dd。
# 【返回】格式化 DataFrame 文本;供应商异常时返回错误信息。
# 【关键】纯转发:把三个参数交给 route_to_vendor,真正的取数逻辑在数据供应商层。
@tool
def get_stock_data(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """
    Retrieve stock price data (OHLCV) for a given ticker symbol.
    Uses the configured core_stock_apis vendor.
    Args:
        symbol (str): Ticker symbol of the company, e.g. AAPL, TSM
        start_date (str): Start date in yyyy-mm-dd format
        end_date (str): End date in yyyy-mm-dd format
    Returns:
        str: A formatted dataframe containing the stock price data for the specified ticker symbol in the specified date range.
    """
    return route_to_vendor("get_stock_data", symbol, start_date, end_date)  # 【调用函数】跨模块路由:按方法名分派到配置的行情供应商
