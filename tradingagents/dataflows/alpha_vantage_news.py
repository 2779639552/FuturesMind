from .alpha_vantage_common import _make_api_request, format_datetime_for_api  # 【调用包】共用请求入口/日期格式转换


# 【功能】拉取指定股票/时间窗的市场新闻与情绪数据(NEWS_SENTIMENT)。
# 【参数】ticker: 股票代码; start_date/end_date: 搜索起止日期。
# 【返回】含新闻情绪数据的 dict 或 JSON 字符串。
def get_news(ticker, start_date, end_date) -> dict[str, str] | str:
    """Returns live and historical market news & sentiment data from premier news outlets worldwide.

    Covers stocks, cryptocurrencies, forex, and topics like fiscal policy, mergers & acquisitions, IPOs.

    Args:
        ticker: Stock symbol for news articles.
        start_date: Start date for news search.
        end_date: End date for news search.

    Returns:
        Dictionary containing news sentiment data or JSON string.
    """

    params = {
        "tickers": ticker,
        "time_from": format_datetime_for_api(start_date),  # 【调用函数】起始时间转 AV 格式
        "time_to": format_datetime_for_api(end_date),  # 【调用函数】截止时间转 AV 格式
    }

    return _make_api_request("NEWS_SENTIMENT", params)  # 【调用函数】AV 新闻情绪接口


# 【功能】拉取全局市场新闻与情绪数据(不限定个股), 覆盖金融/经济等宏观主题。
# 【参数】curr_date: 当前日期; look_back_days: 回看天数(默认 7); limit: 文章条数(默认 50)。
# 【返回】含全局新闻情绪数据的 dict 或 JSON 字符串。
def get_global_news(curr_date, look_back_days: int = 7, limit: int = 50) -> dict[str, str] | str:
    """Returns global market news & sentiment data without ticker-specific filtering.

    Covers broad market topics like financial markets, economy, and more.

    Args:
        curr_date: Current date in yyyy-mm-dd format.
        look_back_days: Number of days to look back (default 7).
        limit: Maximum number of articles (default 50).

    Returns:
        Dictionary containing global news sentiment data or JSON string.
    """
    from datetime import datetime, timedelta  # 【调用包】日期解析与回推

    # Calculate start date
    curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")  # 【变量】当前日期
    start_dt = curr_dt - timedelta(days=look_back_days)  # 【变量】回看起点
    start_date = start_dt.strftime("%Y-%m-%d")

    params = {
        "topics": "financial_markets,economy_macro,economy_monetary",
        "time_from": format_datetime_for_api(start_date),  # 【调用函数】起始时间转 AV 格式
        "time_to": format_datetime_for_api(curr_date),  # 【调用函数】截止时间转 AV 格式
        "limit": str(limit),
    }

    return _make_api_request("NEWS_SENTIMENT", params)  # 【调用函数】AV 新闻情绪接口(全局主题)


# 【功能】拉取关键利益相关方(创始人/高管/董事会等)的最新与历史内部人交易。
# 【参数】symbol: 股票代码。
# 【返回】含内部人交易数据的 dict 或 JSON 字符串。
def get_insider_transactions(symbol: str) -> dict[str, str] | str:
    """Returns latest and historical insider transactions by key stakeholders.

    Covers transactions by founders, executives, board members, etc.

    Args:
        symbol: Ticker symbol. Example: "IBM".

    Returns:
        Dictionary containing insider transaction data or JSON string.
    """

    params = {
        "symbol": symbol,
    }

    return _make_api_request("INSIDER_TRANSACTIONS", params)  # 【调用函数】AV 内部人交易接口
