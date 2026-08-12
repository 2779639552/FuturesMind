import json  # 【调用包】解析/区分 JSON 错误响应
import os  # 【调用包】读取 ALPHA_VANTAGE_API_KEY 环境变量
from datetime import datetime  # 【调用包】日期格式转换
from io import StringIO  # 【调用包】CSV 字符串的 in-memory 文件对象

import pandas as pd  # 【调用包】CSV 解析与日期过滤
import requests  # 【调用包】发起 Alpha Vantage API 的 HTTP GET 请求

from .errors import VendorNotConfiguredError, VendorRateLimitError  # 【调用包】厂商未配置/限流异常基类

# 【变量】Alpha Vantage 查询接口基地址
API_BASE_URL = "https://www.alphavantage.co/query"

# Network timeout (seconds) so a stalled Alpha Vantage request can't hang the
# CLI/agents indefinitely (#990).
# 【变量】网络超时(秒), 防止请求挂起拖死 CLI/智能体
REQUEST_TIMEOUT = 30


# 【功能】Alpha Vantage 特有的"未配置"异常。
# 【关键】继承 VendorNotConfiguredError(仍是 ValueError), 使路由层"厂商不可用"
#         处理与既有 ValueError 调用方都继续工作。
class AlphaVantageNotConfiguredError(VendorNotConfiguredError):
    """Raised when Alpha Vantage is selected but no API key is configured.

    A VendorNotConfiguredError (and thus still a ValueError), so the routing
    layer's "vendor unavailable" handling and existing ValueError callers both
    keep working.
    """

    pass


# 【功能】从环境变量读取 Alpha Vantage API Key。
# 【异常】AlphaVantageNotConfiguredError: 未设置 ALPHA_VANTAGE_API_KEY。
def get_api_key() -> str:
    """Retrieve the API key for Alpha Vantage from environment variables."""
    api_key = os.getenv("ALPHA_VANTAGE_API_KEY")  # 【调用函数】读环境变量
    if not api_key:
        raise AlphaVantageNotConfiguredError(
            "ALPHA_VANTAGE_API_KEY environment variable is not set."
        )
    return api_key


# 【功能】把各种日期格式转成 Alpha Vantage 要求的 YYYYMMDDTHHMM 格式。
# 【参数】date_input: 字符串或 datetime。
# 【返回】格式化字符串。
# 【异常】ValueError: 无法识别的日期格式。
# 【关键】已是 "YYYYMMDDTHHMM"(长度 13 且含 T)的字符串原样返回; 纯日期补 T0000。
def format_datetime_for_api(date_input) -> str:
    """Convert various date formats to YYYYMMDDTHHMM format required by Alpha Vantage API."""
    if isinstance(date_input, str):
        # If already in correct format, return as-is
        if len(date_input) == 13 and "T" in date_input:
            return date_input
        # Try to parse common date formats
        try:
            dt = datetime.strptime(date_input, "%Y-%m-%d")
            return dt.strftime("%Y%m%dT0000")  # 【调用函数】纯日期 -> 当天 00:00
        except ValueError:
            try:
                dt = datetime.strptime(date_input, "%Y-%m-%d %H:%M")
                return dt.strftime("%Y%m%dT%H%M")
            except ValueError:
                raise ValueError(f"Unsupported date format: {date_input}") from None
    elif isinstance(date_input, datetime):
        return date_input.strftime("%Y%m%dT%H%M")
    else:
        raise ValueError(f"Date must be string or datetime object, got {type(date_input)}")


# 【功能】Alpha Vantage 特有的"限流"异常。
class AlphaVantageRateLimitError(VendorRateLimitError):
    """Raised when the Alpha Vantage API rate limit is exceeded."""

    pass


# 【功能】统一的 Alpha Vantage API 请求与响应处理入口。
# 【参数】function_name: AV 函数名(如 SMA/OVERVIEW); params: 业务参数。
# 【返回】数据响应: CSV 时为原始文本, JSON 数据时为文本; 出错时抛异常。
# 【异常】AlphaVantageRateLimitError: 命中限流提示;
#         AlphaVantageNotConfiguredError: Key 无效/缺失。
# 【关键】自动注入 function/apikey/source; 错误响应是 JSON("Information"/"Note"),
#         数据响应多为 CSV; 先试解析 JSON, 失败说明是正常数据; 对限流措辞与
#         "API key" 措辞分类处理, 避免把真正的限流与坏 Key 混为一谈(#991)。
def _make_api_request(function_name: str, params: dict) -> dict | str:
    """Helper function to make API requests and handle responses.

    Raises:
        AlphaVantageRateLimitError: When API rate limit is exceeded
    """
    # Create a copy of params to avoid modifying the original
    api_params = params.copy()  # 【变量】参数副本(避免改动调用方原始 dict)
    api_params.update(
        {
            "function": function_name,
            "apikey": get_api_key(),
            "source": "trading_agents",
        }
    )

    # Handle entitlement parameter if present in params or global variable
    current_entitlement = globals().get("_current_entitlement")  # 【变量】模块级 entitlement 覆盖
    entitlement = api_params.get("entitlement") or current_entitlement  # 【变量】entitlement 取值

    if entitlement:
        api_params["entitlement"] = entitlement
    elif "entitlement" in api_params:
        # Remove entitlement if it's None or empty
        api_params.pop("entitlement", None)

    response = requests.get(API_BASE_URL, params=api_params, timeout=REQUEST_TIMEOUT)  # 【调用函数】外部 API GET
    response.raise_for_status()  # 【调用函数】HTTP 错误上抛

    response_text = response.text  # 【变量】原始响应文本

    # Error responses are JSON; data responses are usually CSV (or data-keyed
    # JSON). A non-JSON body is normal data.
    try:
        response_json = json.loads(response_text)  # 【调用函数】尝试按 JSON 解析
    except json.JSONDecodeError:
        return response_text  # 【调用函数】非 JSON 即正常数据(CSV), 原样返回

    # Alpha Vantage reports problems via "Information" / "Note". Classify so a
    # genuine rate limit and an invalid/missing key aren't conflated (#991):
    # rate-limit phrasing is checked first because those notices also mention
    # "API key" ("your API key ... 25 requests per day").
    notice = response_json.get("Information") or response_json.get("Note")  # 【变量】AV 的提示文本
    if notice:
        low = notice.lower()
        if any(m in low for m in ("rate limit", "requests per day", "call frequency", "premium")):
            raise AlphaVantageRateLimitError(f"Alpha Vantage rate limit exceeded: {notice}")  # 【调用函数】命中限流措辞
        if "api key" in low or "apikey" in low:
            # Reuse the existing "not configured" error so a bad key surfaces as
            # a real, actionable failure rather than a mislabeled rate limit (#991).
            raise AlphaVantageNotConfiguredError(  # 【调用函数】坏 Key 复用"未配置"错误, 给可行动报错
                f"Alpha Vantage API key invalid or missing: {notice}"
            )

    return response_text


# 【功能】把 Alpha Vantage 返回的 CSV 过滤到指定日期区间。
# 【参数】csv_data: AV 的 CSV 字符串; start_date/end_date: 起止日期(yyyy-mm-dd)。
# 【返回】过滤后的 CSV 字符串; 过滤失败时原样返回并打警告。
# 【关键】假定第一列是日期列(timestamp); 空输入直接返回。
def _filter_csv_by_date_range(csv_data: str, start_date: str, end_date: str) -> str:
    """
    Filter CSV data to include only rows within the specified date range.

    Args:
        csv_data: CSV string from Alpha Vantage API
        start_date: Start date in yyyy-mm-dd format
        end_date: End date in yyyy-mm-dd format

    Returns:
        Filtered CSV string
    """
    if not csv_data or csv_data.strip() == "":
        return csv_data

    try:
        # Parse CSV data
        df = pd.read_csv(StringIO(csv_data))  # 【调用函数】解析 CSV 字符串

        # Assume the first column is the date column (timestamp)
        date_col = df.columns[0]  # 【变量】假定首列为日期列
        df[date_col] = pd.to_datetime(df[date_col])  # 【调用函数】日期列转 datetime

        # Filter by date range
        start_dt = pd.to_datetime(start_date)  # 【变量】区间起点
        end_dt = pd.to_datetime(end_date)  # 【变量】区间终点

        filtered_df = df[(df[date_col] >= start_dt) & (df[date_col] <= end_dt)]  # 【变量】区间内行

        # Convert back to CSV string
        return filtered_df.to_csv(index=False)  # 【调用函数】重新序列化为 CSV

    except Exception as e:
        # If filtering fails, return original data with a warning
        print(f"Warning: Failed to filter CSV data by date range: {e}")
        return csv_data
