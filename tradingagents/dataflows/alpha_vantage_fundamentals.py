import json  # 【调用包】解析/序列化基本面 JSON 响应

from .alpha_vantage_common import _make_api_request  # 【调用包】共用 Alpha Vantage 请求入口


# 【功能】丢弃报告日期晚于 curr_date 的年度/季度报告, 防止前视(未来数据泄漏)。
# 【参数】result: AV 返回的基本面载荷(JSON 字符串); curr_date: 当前日期(yyyy-mm-dd)。
# 【返回】过滤后重新序列化的 JSON 字符串。
# 【关键】_make_api_request 返回 JSON 字符串, 故需解析->过滤->再序列化; 非 JSON 主体
#         或 curr_date 为空时原样返回。
def _filter_reports_by_date(result, curr_date: str):
    """Drop annual/quarterly reports dated after curr_date to prevent look-ahead.

    ``_make_api_request`` returns the fundamentals payload as a JSON string, so
    parse, filter, and re-serialize. A non-JSON body or an unset ``curr_date`` is
    returned unchanged.
    """
    if not curr_date or not isinstance(result, str):
        return result
    try:
        payload = json.loads(result)  # 【调用函数】解析 JSON 字符串
    except json.JSONDecodeError:
        return result
    if not isinstance(payload, dict):
        return result
    for key in ("annualReports", "quarterlyReports"):  # 【变量】两类报告字段
        if isinstance(payload.get(key), list):
            payload[key] = [r for r in payload[key] if r.get("fiscalDateEnding", "") <= curr_date]  # 【变量】仅保留 fiscalDateEnding <= curr_date 的报告
    return json.dumps(payload)  # 【调用函数】重新序列化


# 【功能】拉取公司概览(OVERVIEW)基本面数据: 财务比率与关键指标。
# 【参数】ticker: 股票代码; curr_date: 当前交易日(AV 不用)。
# 【返回】AV 返回的公司概览 JSON 字符串。
def get_fundamentals(ticker: str, curr_date: str = None) -> str:
    """
    Retrieve comprehensive fundamental data for a given ticker symbol using Alpha Vantage.

    Args:
        ticker (str): Ticker symbol of the company
        curr_date (str): Current date you are trading at, yyyy-mm-dd (not used for Alpha Vantage)

    Returns:
        str: Company overview data including financial ratios and key metrics
    """
    params = {
        "symbol": ticker,
    }

    return _make_api_request("OVERVIEW", params)  # 【调用函数】AV OVERVIEW 接口


# 【功能】拉取资产负债表(BALANCE_SHEET), 并按 curr_date 过滤报告, 防止前视。
def get_balance_sheet(ticker: str, freq: str = "quarterly", curr_date: str = None):
    """Retrieve balance sheet data for a given ticker symbol using Alpha Vantage."""
    result = _make_api_request("BALANCE_SHEET", {"symbol": ticker})  # 【调用函数】AV 资产负债表接口
    return _filter_reports_by_date(result, curr_date)  # 【调用函数】按日期过滤防前视


# 【功能】拉取现金流量表(CASH_FLOW), 并按 curr_date 过滤报告, 防止前视。
def get_cashflow(ticker: str, freq: str = "quarterly", curr_date: str = None):
    """Retrieve cash flow statement data for a given ticker symbol using Alpha Vantage."""
    result = _make_api_request("CASH_FLOW", {"symbol": ticker})  # 【调用函数】AV 现金流量表接口
    return _filter_reports_by_date(result, curr_date)  # 【调用函数】按日期过滤防前视


# 【功能】拉取利润表(INCOME_STATEMENT), 并按 curr_date 过滤报告, 防止前视。
def get_income_statement(ticker: str, freq: str = "quarterly", curr_date: str = None):
    """Retrieve income statement data for a given ticker symbol using Alpha Vantage."""
    result = _make_api_request("INCOME_STATEMENT", {"symbol": ticker})  # 【调用函数】AV 利润表接口
    return _filter_reports_by_date(result, curr_date)  # 【调用函数】按日期过滤防前视
