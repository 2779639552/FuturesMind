from datetime import datetime  # 【调用包】解析日期/取当前时间

from .alpha_vantage_common import _filter_csv_by_date_range, _make_api_request  # 【调用包】共用请求入口与 CSV 过滤


# 【功能】拉取某标的每日调整后行情(日线 OHLCV + 复权收盘 + 拆股/分红事件), 并按日期过滤。
# 【参数】symbol: 股票代码; start_date/end_date: 起止日期(yyyy-mm-dd)。
# 【返回】过滤到日期区间的 CSV 字符串。
# 【关键】outputsize 依请求区间是否落在最近 100 天选择: compact(最近 100 点)或 full。
def get_stock(symbol: str, start_date: str, end_date: str) -> str:
    """
    Returns raw daily OHLCV values, adjusted close values, and historical split/dividend events
    filtered to the specified date range.

    Args:
        symbol: The name of the equity. For example: symbol=IBM
        start_date: Start date in yyyy-mm-dd format
        end_date: End date in yyyy-mm-dd format

    Returns:
        CSV string containing the daily adjusted time series data filtered to the date range.
    """
    # Parse dates to determine the range
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")  # 【变量】请求起点
    today = datetime.now()  # 【变量】当前时间

    # Choose outputsize based on whether the requested range is within the latest 100 days
    # Compact returns latest 100 data points, so check if start_date is recent enough
    days_from_today_to_start = (today - start_dt).days  # 【变量】起点距今天数
    outputsize = "compact" if days_from_today_to_start < 100 else "full"  # 【变量】100 天内用 compact, 否则 full

    params = {
        "symbol": symbol,
        "outputsize": outputsize,
        "datatype": "csv",
    }

    response = _make_api_request("TIME_SERIES_DAILY_ADJUSTED", params)  # 【调用函数】AV 日线复权行情接口

    return _filter_csv_by_date_range(response, start_date, end_date)  # 【调用函数】过滤到指定区间
