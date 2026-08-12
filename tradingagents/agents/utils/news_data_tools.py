from typing import Annotated  # 【调用包】类型注解:给工具参数附加描述,供 LangChain 生成工具说明

from langchain_core.tools import tool  # 【调用包】LangChain 工具装饰器:把普通函数注册为 Agent 可调用的 Tool

from tradingagents.dataflows.interface import route_to_vendor  # 【调用包】路由函数:按方法名把取数请求分派到配置的数据供应商


# 【功能】获取指定股票在日期区间内的公司新闻。
# 【参数】ticker: 股票代码;start_date/end_date: 起止日期 yyyy-mm-dd。
# 【返回】格式化新闻文本。
# 【关键】纯转发给 route_to_vendor("get_news", ...)。
@tool
def get_news(
    ticker: Annotated[str, "Ticker symbol"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """
    Retrieve news data for a given ticker symbol.
    Uses the configured news_data vendor.
    Args:
        ticker (str): Ticker symbol
        start_date (str): Start date in yyyy-mm-dd format
        end_date (str): End date in yyyy-mm-dd format
    Returns:
        str: A formatted string containing news data
    """
    return route_to_vendor("get_news", ticker, start_date, end_date)  # 【调用函数】跨模块路由:按方法名分派到配置的新闻数据供应商


# 【功能】获取全球宏观新闻(不限个股);look_back_days/limit 省略时继承配置默认值。
# 【参数】curr_date: 当前日期 yyyy-mm-dd;look_back_days: 回溯天数;limit: 返回文章数上限。
# 【返回】格式化新闻文本。
# 【关键】纯转发;默认值来自 DEFAULT_CONFIG(global_news_lookback_days / global_news_article_limit)。
@tool
def get_global_news(
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[
        int | None, "Days to look back; omit to use the configured default"
    ] = None,
    limit: Annotated[
        int | None, "Max articles to return; omit to use the configured default"
    ] = None,
) -> str:
    """
    Retrieve global news data.
    Uses the configured news_data vendor. Defaults for look_back_days and
    limit come from DEFAULT_CONFIG (global_news_lookback_days,
    global_news_article_limit); pass explicit values to override.

    Args:
        curr_date (str): Current date in yyyy-mm-dd format
        look_back_days (int): Number of days to look back; omit to inherit config
        limit (int): Maximum number of articles to return; omit to inherit config

    Returns:
        str: A formatted string containing global news data
    """
    return route_to_vendor("get_global_news", curr_date, look_back_days, limit)  # 【调用函数】跨模块路由:按方法名分派到配置的新闻数据供应商


# 【功能】获取公司内部人(高管/大股东)交易信息,用于评估内部人动向信号。
# 【参数】ticker: 股票代码。
# 【返回】格式化内部人交易报告文本。
# 【关键】纯转发给 route_to_vendor("get_insider_transactions", ...)。
@tool
def get_insider_transactions(
    ticker: Annotated[str, "ticker symbol"],
) -> str:
    """
    Retrieve insider transaction information about a company.
    Uses the configured news_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
    Returns:
        str: A report of insider transaction data
    """
    return route_to_vendor("get_insider_transactions", ticker)  # 【调用函数】跨模块路由:按方法名分派到配置的新闻数据供应商
