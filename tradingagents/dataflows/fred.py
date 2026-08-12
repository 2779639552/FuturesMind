"""FRED (Federal Reserve Economic Data) macro vendor.

Fetches macroeconomic time series — policy rates, Treasury yields, inflation,
labor, growth — from the St. Louis Fed's free API. Used by the news analyst to
ground macro commentary in actual numbers rather than headlines alone.

A free API key (https://fred.stlouisfed.org/docs/api/api_key.html) is read from
``FRED_API_KEY``; if it is unset the vendor raises ``FredNotConfiguredError`` so
the routing layer treats it as "unavailable" rather than a hard crash.
"""

import logging  # 【调用包】日志
import os  # 【调用包】读取 FRED_API_KEY 环境变量
from datetime import datetime, timedelta  # 【调用包】日期解析与窗口计算

import requests  # 【调用包】发起 FRED API 的 HTTP GET 请求

from .errors import VendorNotConfiguredError  # 【调用包】厂商未配置异常基类

logger = logging.getLogger(__name__)

# 【变量】FRED API 的 REST 基地址
FRED_API_BASE = "https://api.stlouisfed.org/fred"

# Network timeout (seconds) so a stalled request can't hang the agents,
# mirroring the Alpha Vantage client.
# 【变量】网络超时(秒), 防止请求挂起拖死智能体
REQUEST_TIMEOUT = 30

# Default trailing window when the caller does not specify one. A year captures
# the trend and the year-over-year base for most monthly/quarterly series.
# 【变量】调用方未指定时的默认回看窗口(天)= 一年, 足以覆盖多数月度/季度序列的
#         同比趋势
DEFAULT_LOOKBACK_DAYS = 365

# Rows cap for the rendered table: recent values matter most for a decision, and
# daily series (yields, VIX) over a long window would otherwise flood context.
# 【变量】渲染表格的最大行数(近值最重要, 日频序列在长窗口下会撑爆上下文)
MAX_ROWS = 40

# Curated human-friendly aliases -> FRED series IDs. Anything not listed is used
# verbatim as a raw FRED series ID, so power users are never limited to this set.
# 【变量】友好别名 -> FRED 序列 ID 映射表; 未列入的输入按原始 FRED 序列 ID 直通,
#         高级用户不受此表限制
MACRO_SERIES = {
    # Policy rate & Treasury yields
    "fed_funds_rate": "FEDFUNDS",
    "federal_funds_rate": "FEDFUNDS",
    "fed_funds": "FEDFUNDS",
    "2y_treasury": "DGS2",
    "10y_treasury": "DGS10",
    "30y_treasury": "DGS30",
    "10y_2y_spread": "T10Y2Y",
    "yield_curve": "T10Y2Y",
    # Inflation
    "cpi": "CPIAUCSL",
    "core_cpi": "CPILFESL",
    "pce": "PCEPI",
    "core_pce": "PCEPILFE",
    "inflation_expectations": "T10YIE",
    # Growth & output
    "real_gdp": "GDPC1",
    "gdp": "GDP",
    "industrial_production": "INDPRO",
    # Labor
    "unemployment_rate": "UNRATE",
    "unemployment": "UNRATE",
    "nonfarm_payrolls": "PAYEMS",
    "payrolls": "PAYEMS",
    "initial_claims": "ICSA",
    # Money & markets
    "m2": "M2SL",
    "money_supply": "M2SL",
    "vix": "VIXCLS",
    "dollar_index": "DTWEXBGS",
    # Sentiment & housing
    "consumer_sentiment": "UMCSENT",
    "housing_starts": "HOUST",
    "retail_sales": "RSAFS",
}


# 【功能】FRED 特有的"未配置"异常。
# 【关键】继承 VendorNotConfiguredError(仍是 ValueError), 使路由层的"厂商不可用"
#         处理与既有 ValueError 调用方都继续工作。
class FredNotConfiguredError(VendorNotConfiguredError):
    """Raised when FRED is selected but no API key is configured.

    A VendorNotConfiguredError (and thus still a ValueError), so the routing
    layer's "vendor unavailable" handling and existing ValueError callers both
    keep working.
    """


# 【功能】从环境变量读取 FRED API Key。
# 【异常】FredNotConfiguredError: FRED_API_KEY 未设置时抛出, 路由层按"不可用"处理。
def get_api_key() -> str:
    """Retrieve the FRED API key from the environment."""
    api_key = os.getenv("FRED_API_KEY")  # 【调用函数】读环境变量
    if not api_key:
        raise FredNotConfiguredError(
            "FRED_API_KEY environment variable is not set. Get a free key at "
            "https://fred.stlouisfed.org/docs/api/api_key.html."
        )
    return api_key


# 【功能】把友好别名解析为 FRED 序列 ID, 或让原始 ID 直通。
# 【参数】indicator: 别名(如 "cpi")或原始 FRED 序列 ID(如 "CPIAUCSL")。
# 【返回】规范化的 FRED 序列 ID 字符串。
# 【异常】ValueError: 输入既不是已知别名也不像合法序列 ID(通常是 LLM 传了描述性
#         短语, 如 "bank of japan rate")——提前拒绝并给指引, 而不是让 API 400。
# 【关键】FRED ID 短且只含字母数字, 故 >30 字符或含空白的输入直接判非法。
def _resolve_series_id(indicator: str) -> str:
    """Map a friendly alias to a FRED series ID, or pass a raw ID through.

    Raises ``ValueError`` when the input is neither a known alias nor a plausible
    series ID — typically a descriptive phrase the LLM passed instead (e.g.
    "bank of japan rate"). FRED IDs are short and alphanumeric, so this rejects
    it up front with guidance rather than letting it 400 the API.
    """
    key = indicator.strip().lower().replace(" ", "_").replace("-", "_")  # 【变量】别名归一化(空格/连字符换下划线)
    if key in MACRO_SERIES:
        return MACRO_SERIES[key]
    candidate = indicator.strip().upper()  # 【变量】按原始 ID 处理: 去空白并大写
    # FRED series IDs never contain whitespace and are short; reject anything
    # else (a descriptive phrase the LLM passed) rather than 400ing the API.
    if not candidate or len(candidate) > 30 or any(c.isspace() for c in candidate):
        raise ValueError(
            f"'{indicator}' is not a known macro alias or a valid FRED series ID. "
            f"Use an alias (e.g. 'cpi', 'unemployment', '10y_treasury') or a raw "
            f"FRED series ID (e.g. 'CPIAUCSL')."
        )
    return candidate


# 【功能】对 FRED 端点发 GET 请求, 并把 FRED 的错误 JSON 体转成明确报错。
# 【参数】path: 端点路径(如 "series"/"series/observations"); params: 查询参数。
# 【返回】解析后的 JSON dict。
# 【异常】ValueError: FRED 返回 400 且带 {"error_message":...} 时转成可读报错;
#         其他 HTTP 错误经 raise_for_status 上抛。
# 【关键】自动附加 api_key 与 file_type="json"。
def _request(path: str, params: dict) -> dict:
    """GET a FRED endpoint, surfacing FRED's JSON error body on a bad request."""
    api_params = {**params, "api_key": get_api_key(), "file_type": "json"}  # 【变量】注入 Key 与 JSON 格式参数的完整参数表
    response = requests.get(f"{FRED_API_BASE}/{path}", params=api_params, timeout=REQUEST_TIMEOUT)  # 【调用函数】外部 API GET
    # FRED returns 400 with a JSON {"error_message": ...} for unknown series IDs
    # or malformed params; turn that into a clear, actionable error.
    if response.status_code == 400:
        try:
            message = response.json().get("error_message", response.text)  # 【变量】提取 FRED 的错误信息
        except ValueError:
            message = response.text
        raise ValueError(f"FRED request failed: {message}")
    response.raise_for_status()  # 【调用函数】其他 HTTP 错误上抛
    return response.json()  # 【调用函数】解析 JSON 响应


# 【功能】拉取一条 FRED 宏观经济序列并渲染为格式化 markdown 报告。
# 【参数】indicator: 友好别名或原始 FRED 序列 ID; curr_date: 窗口终点(yyyy-mm-dd,
#         不返回其后观测值, 过去日期不会泄漏未来数据); look_back_days: 回看窗口
#         (None 用 DEFAULT_LOOKBACK_DAYS)。
# 【返回】markdown 报告(含序列标题/单位/频率/最新值/窗口变化/近期观测表)。
# 【关键】① 非法别名返回指引文本而非抛异常, 避免中断整个 run;
#         ② FRED 对缺失观测用 "." 编码, 需过滤; ③ 观测过多时只显示最近 MAX_ROWS。
def get_macro_data(
    indicator: str,
    curr_date: str,
    look_back_days: int | None = None,
) -> str:
    """Fetch a FRED macroeconomic series as a formatted markdown report.

    Args:
        indicator: A friendly alias (e.g. "cpi", "unemployment", "10y_treasury")
            or a raw FRED series ID (e.g. "CPIAUCSL", "DGS10").
        curr_date: End of the window (yyyy-mm-dd); no later observations are
            returned, so a past date never leaks future data.
        look_back_days: Trailing window length; ``None`` uses DEFAULT_LOOKBACK_DAYS.

    Returns:
        A markdown report with the series title, units, frequency, the latest
        value, the change over the window, and a recent observation table.
    """
    if look_back_days is None:
        look_back_days = DEFAULT_LOOKBACK_DAYS

    end_dt = datetime.strptime(curr_date, "%Y-%m-%d")  # 【变量】窗口终点
    start_date = (end_dt - timedelta(days=look_back_days)).strftime("%Y-%m-%d")  # 【变量】窗口起点

    # Invalid LLM-supplied indicator: return guidance rather than raising, so a
    # bad argument doesn't abort the run (the routing layer also degrades macro
    # data, but a specific message is more useful to the analyst).
    try:
        series_id = _resolve_series_id(indicator)  # 【调用函数】解析别名/原始 ID
    except ValueError as e:
        return f"FRED: {e}"

    meta = _request("series", {"series_id": series_id}).get("seriess") or []  # 【调用函数】查序列元信息
    if not meta:
        return (
            f"FRED series '{series_id}' not found. Pass a known alias "
            f"(e.g. 'cpi', 'unemployment') or a valid FRED series ID."
        )
    info = meta[0]
    title = info.get("title", series_id)  # 【变量】序列标题
    units = info.get("units_short") or info.get("units", "")  # 【变量】单位
    frequency = info.get("frequency", "")  # 【变量】频率
    seasonal = info.get("seasonal_adjustment_short", "")  # 【变量】季节调整标记

    observations = _request(  # 【调用函数】拉取窗口内观测值
        "series/observations",
        {
            "series_id": series_id,
            "observation_start": start_date,
            "observation_end": curr_date,
            "sort_order": "asc",
        },
    ).get("observations", [])

    # FRED encodes a missing observation as ".".
    points = [  # 【变量】过滤掉缺失值(".")后的 (日期, 值) 点列表
        (o["date"], o["value"]) for o in observations if o.get("value") not in (".", None, "")
    ]

    header = (
        f"## FRED: {title} ({series_id})\n"
        f"- Units: {units}\n"
        f"- Frequency: {frequency}"
        f"{f' ({seasonal})' if seasonal else ''}\n"
        f"- Window: {start_date} to {curr_date}\n"
    )

    if not points:
        return header + (
            f"\nNo observations for {series_id} in this window. The series may "
            f"report less frequently than the window length; widen look_back_days."
        )

    first_date, first_val = points[0]  # 【变量】窗口首个观测
    last_date, last_val = points[-1]  # 【变量】窗口末个观测
    try:
        delta = float(last_val) - float(first_val)  # 【变量】窗口内数值变化
        base = float(first_val)  # 【变量】窗口起点数值(百分比基准)
        pct = f" ({delta / base * 100:+.2f}%)" if base != 0 else ""  # 【变量】变化百分比(基准为 0 时省略)
        summary = (
            f"\n**Latest:** {last_val} ({last_date}) | "
            f"**Change over window:** {delta:+.2f}{pct} "
            f"from {first_val} ({first_date})\n"
        )
    except ValueError:
        summary = f"\n**Latest:** {last_val} ({last_date})\n"  # 【变量】数值不可转 float 时只给最新值

    shown = points  # 【变量】实际展示的观测点(可能截断)
    note = ""
    if len(points) > MAX_ROWS:
        shown = points[-MAX_ROWS:]  # 【变量】只保留最近 MAX_ROWS 行
        note = f"\n_(showing the most recent {MAX_ROWS} of {len(points)} observations)_\n"

    table = (
        "\n| Date | Value |\n| --- | --- |\n" + "\n".join(f"| {d} | {v} |" for d, v in shown) + "\n"
    )

    return header + summary + note + table
