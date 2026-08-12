from typing import Annotated  # 【调用包】类型注解:给工具参数附加描述,供 LangChain 生成工具说明

from langchain_core.tools import tool  # 【调用包】LangChain 工具装饰器:把普通函数注册为 Agent 可调用的 Tool

from tradingagents.dataflows.interface import route_to_vendor  # 【调用包】路由函数:按方法名把取数请求分派到配置的数据供应商


# 【功能】获取指定股票的综合基本面数据报告(财务指标/估值等)。
# 【参数】ticker: 股票代码;curr_date: 交易日期 yyyy-mm-dd。
# 【返回】格式化基本面报告文本;供应商异常时返回错误信息。
# 【关键】纯转发给 route_to_vendor("get_fundamentals", ...)。
@tool
def get_fundamentals(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"],
) -> str:
    """
    Retrieve comprehensive fundamental data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A formatted report containing comprehensive fundamental data
    """
    return route_to_vendor("get_fundamentals", ticker, curr_date)  # 【调用函数】跨模块路由:按方法名分派到配置的基本面数据供应商


# 【功能】获取指定股票的资产负债表数据。
# 【参数】ticker: 股票代码;freq: 报告频率 annual/quarterly(默认 quarterly);
#        curr_date: 交易日期 yyyy-mm-dd(可选)。
# 【返回】格式化资产负债表报告文本。
# 【关键】纯转发给 route_to_vendor("get_balance_sheet", ...)。
@tool
def get_balance_sheet(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """
    Retrieve balance sheet data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        freq (str): Reporting frequency: annual/quarterly (default quarterly)
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A formatted report containing balance sheet data
    """
    return route_to_vendor("get_balance_sheet", ticker, freq, curr_date)  # 【调用函数】跨模块路由:按方法名分派到配置的基本面数据供应商


# 【功能】获取指定股票的现金流量表数据(经营/投资/筹资现金流)。
# 【参数】ticker: 股票代码;freq: 报告频率 annual/quarterly(默认 quarterly);
#        curr_date: 交易日期 yyyy-mm-dd(可选)。
# 【返回】格式化现金流量表报告文本。
# 【关键】纯转发给 route_to_vendor("get_cashflow", ...)。
@tool
def get_cashflow(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """
    Retrieve cash flow statement data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        freq (str): Reporting frequency: annual/quarterly (default quarterly)
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A formatted report containing cash flow statement data
    """
    return route_to_vendor("get_cashflow", ticker, freq, curr_date)  # 【调用函数】跨模块路由:按方法名分派到配置的基本面数据供应商


# 【功能】获取指定股票的利润表数据(营收/净利/每股收益等)。
# 【参数】ticker: 股票代码;freq: 报告频率 annual/quarterly(默认 quarterly);
#        curr_date: 交易日期 yyyy-mm-dd(可选)。
# 【返回】格式化利润表报告文本。
# 【关键】纯转发给 route_to_vendor("get_income_statement", ...)。
@tool
def get_income_statement(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """
    Retrieve income statement data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        freq (str): Reporting frequency: annual/quarterly (default quarterly)
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A formatted report containing income statement data
    """
    return route_to_vendor("get_income_statement", ticker, freq, curr_date)  # 【调用函数】跨模块路由:按方法名分派到配置的基本面数据供应商
