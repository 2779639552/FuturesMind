from datetime import datetime  # 【调用包】解析/格式化日期字符串
from typing import Annotated  # 【调用包】为参数附加业务含义标注(供 LLM 工具描述)

import pandas as pd  # 【调用包】DataFrame 数据处理与 CSV 序列化
import yfinance as yf  # 【调用包】Yahoo Finance 行情/基本面/财务报表数据源
from dateutil.relativedelta import relativedelta  # 【调用包】按天数回推/推进日期(处理 yfinance end 排他语义)

from .stockstats_utils import (  # 【调用包】技术指标计算、OHLCV 缓存与过期校验、财务表日期过滤、yfinance 限流重试
    StockstatsUtils,
    _assert_ohlcv_not_stale,
    filter_financials_by_date,
    load_ohlcv,
    yf_retry,
)
from .symbol_utils import NoMarketDataError, normalize_symbol  # 【调用包】券商/外盘符号归一化为 Yahoo 约定 + 无数据异常


# 【功能】从 Yahoo Finance 在线拉取某标的在 [start_date, end_date] 的日线 OHLCV,
#         返回带元信息头的 CSV 字符串(供 LLM/报告直接使用)。
# 【参数】symbol: 股票/期货/外汇/加密货币代码; start_date/end_date: 起止日期(yyyy-mm-dd)。
# 【返回】"# 头信息 + CSV 文本"拼接的字符串。
# 【异常】NoMarketDataError: 标的不存在/已退市(返回空表)或数据过期(最新行远超 end_date)。
# 【关键】① normalize_symbol 先把券商/外盘符号映射为 Yahoo 约定(XAUUSD+ -> GC=F);
#         ② yfinance 的 end 是"排他"的, 故请求 end+1 天以保证区间含 end_date;
#         ③ 空表或过期帧都抛 NoMarketDataError, 由路由层转成"无数据"信号,
#            避免智能体基于不存在的价格瞎编。
def get_YFin_data_online(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
):

    datetime.strptime(start_date, "%Y-%m-%d")  # 【调用函数】校验 start_date 格式
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")  # 【变量】end_date 解析为 datetime

    # Resolve broker/forex symbols to Yahoo's convention (XAUUSD+ -> GC=F).
    canonical = normalize_symbol(symbol)  # 【调用函数】符号归一化(无网络调用, 纯语法映射)
    ticker = yf.Ticker(canonical)  # 【调用函数】构造 Yahoo Ticker 对象

    # yfinance treats ``end`` as EXCLUSIVE, so it would drop the requested
    # end_date row (and the current day when end_date is today). Request one day
    # past end_date so the requested range is actually inclusive (#986/#987).
    end_inclusive = (end_dt + relativedelta(days=1)).strftime("%Y-%m-%d")  # 【变量】end 后移一天, 使请求区间含 end_date
    data = yf_retry(lambda: ticker.history(start=start_date, end=end_inclusive))  # 【调用函数】拉取历史行情(带限流重试)

    # Empty result means the symbol is unknown/delisted. Raise a typed error
    # instead of returning prose: the routing layer turns it into a single
    # unambiguous "no data" signal so the agent never fabricates a price.
    if data.empty:
        raise NoMarketDataError(symbol, canonical, f"no rows between {start_date} and {end_date}")

    # Remove timezone info from index for cleaner output
    if data.index.tz is not None:
        data.index = data.index.tz_localize(None)  # 【调用函数】去掉索引时区, 使输出更干净

    # Reject a stale frame (e.g. a year-old partial response) before it is
    # formatted into the report. Raises NoMarketDataError, which the router
    # turns into one clear unavailable signal (#1021).
    _assert_ohlcv_not_stale(data, end_date, symbol, canonical)  # 【调用函数】校验最新行未过期(避免喂入旧价)

    # Round numerical values to 2 decimal places for cleaner display
    numeric_columns = ["Open", "High", "Low", "Close", "Adj Close"]  # 【变量】需要四舍五入的数值列名
    for col in numeric_columns:
        if col in data.columns:
            data[col] = data[col].round(2)  # 【调用函数】价格列保留两位小数

    # Convert DataFrame to CSV string
    csv_string = data.to_csv()  # 【调用函数】DataFrame 序列化为 CSV 文本

    # Add header information; note the resolved symbol when it differs so the
    # agent (and user) can see which instrument was actually priced.
    label = canonical if canonical == symbol.upper() else f"{canonical} (from {symbol})"  # 【变量】展示用标的标签(解析结果与原始不同时注明来源)
    header = f"# Stock data for {label} from {start_date} to {end_date}\n"
    header += f"# Total records: {len(data)}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    return header + csv_string


# 【功能】计算某技术指标在 [curr_date 往前 look_back_days] 窗口内每天的取值,
#         返回带指标说明的文本报告。
# 【参数】symbol: 标的代码; indicator: 指标名(close_50_sma/macd/rsi 等);
#         curr_date: 当前交易日(yyyy-mm-dd); look_back_days: 回看天数。
# 【返回】格式化字符串: 每天一行 "日期: 值", 末尾附指标用法说明。
# 【异常】ValueError: 不支持的指标名; NoMarketDataError: 标的不存在。
# 【关键】优先走"一次拉全量数据再批量计算"的 _get_stock_stats_bulk 优化路径;
#         失败则逐日回退到 get_stockstats_indicator。非交易日显示 "N/A: Not a
#         trading day (weekend or holiday)"。
def get_stock_stats_indicators_window(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to get the analysis and report of"],
    curr_date: Annotated[str, "The current trading date you are trading on, YYYY-mm-dd"],
    look_back_days: Annotated[int, "how many days to look back"],
) -> str:

    # 【变量】各指标的中文/英文解释字典: 指标名 -> (展示名, 用法说明)
    best_ind_params = {
        # Moving Averages
        "close_50_sma": (
            "50 SMA: A medium-term trend indicator. "
            "Usage: Identify trend direction and serve as dynamic support/resistance. "
            "Tips: It lags price; combine with faster indicators for timely signals."
        ),
        "close_200_sma": (
            "200 SMA: A long-term trend benchmark. "
            "Usage: Confirm overall market trend and identify golden/death cross setups. "
            "Tips: It reacts slowly; best for strategic trend confirmation rather than frequent trading entries."
        ),
        "close_10_ema": (
            "10 EMA: A responsive short-term average. "
            "Usage: Capture quick shifts in momentum and potential entry points. "
            "Tips: Prone to noise in choppy markets; use alongside longer averages for filtering false signals."
        ),
        # MACD Related
        "macd": (
            "MACD: Computes momentum via differences of EMAs. "
            "Usage: Look for crossovers and divergence as signals of trend changes. "
            "Tips: Confirm with other indicators in low-volatility or sideways markets."
        ),
        "macds": (
            "MACD Signal: An EMA smoothing of the MACD line. "
            "Usage: Use crossovers with the MACD line to trigger trades. "
            "Tips: Should be part of a broader strategy to avoid false positives."
        ),
        "macdh": (
            "MACD Histogram: Shows the gap between the MACD line and its signal. "
            "Usage: Visualize momentum strength and spot divergence early. "
            "Tips: Can be volatile; complement with additional filters in fast-moving markets."
        ),
        # Momentum Indicators
        "rsi": (
            "RSI: Measures momentum to flag overbought/oversold conditions. "
            "Usage: Apply 70/30 thresholds and watch for divergence to signal reversals. "
            "Tips: In strong trends, RSI may remain extreme; always cross-check with trend analysis."
        ),
        # Volatility Indicators
        "boll": (
            "Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands. "
            "Usage: Acts as a dynamic benchmark for price movement. "
            "Tips: Combine with the upper and lower bands to effectively spot breakouts or reversals."
        ),
        "boll_ub": (
            "Bollinger Upper Band: Typically 2 standard deviations above the middle line. "
            "Usage: Signals potential overbought conditions and breakout zones. "
            "Tips: Confirm signals with other tools; prices may ride the band in strong trends."
        ),
        "boll_lb": (
            "Bollinger Lower Band: Typically 2 standard deviations below the middle line. "
            "Usage: Indicates potential oversold conditions. "
            "Tips: Use additional analysis to avoid false reversal signals."
        ),
        "atr": (
            "ATR: Averages true range to measure volatility. "
            "Usage: Set stop-loss levels and adjust position sizes based on current market volatility. "
            "Tips: It's a reactive measure, so use it as part of a broader risk management strategy."
        ),
        # Volume-Based Indicators
        "vwma": (
            "VWMA: A moving average weighted by volume. "
            "Usage: Confirm trends by integrating price action with volume data. "
            "Tips: Watch for skewed results from volume spikes; use in combination with other volume analyses."
        ),
        "mfi": (
            "MFI: The Money Flow Index is a momentum indicator that uses both price and volume to measure buying and selling pressure. "
            "Usage: Identify overbought (>80) or oversold (<20) conditions and confirm the strength of trends or reversals. "
            "Tips: Use alongside RSI or MACD to confirm signals; divergence between price and MFI can indicate potential reversals."
        ),
    }

    if indicator not in best_ind_params:
        raise ValueError(
            f"Indicator {indicator} is not supported. Please choose from: {list(best_ind_params.keys())}"
        )

    end_date = curr_date
    curr_date_dt = datetime.strptime(curr_date, "%Y-%m-%d")  # 【变量】当前日期解析为 datetime
    before = curr_date_dt - relativedelta(days=look_back_days)  # 【变量】窗口起点(向前 look_back_days 天)

    # Optimized: Get stock data once and calculate indicators for all dates
    try:
        indicator_data = _get_stock_stats_bulk(symbol, indicator, curr_date)  # 【调用函数】批量拉数据并一次算完指标

        # Generate the date range we need
        current_dt = curr_date_dt
        date_values = []  # 【变量】收集 (日期字符串, 指标值) 的有序列表

        while current_dt >= before:
            date_str = current_dt.strftime("%Y-%m-%d")

            # Look up the indicator value for this date
            if date_str in indicator_data:
                indicator_value = indicator_data[date_str]
            else:
                indicator_value = "N/A: Not a trading day (weekend or holiday)"  # 【变量】非交易日占位说明

            date_values.append((date_str, indicator_value))
            current_dt = current_dt - relativedelta(days=1)

        # Build the result string
        ind_string = ""
        for date_str, value in date_values:
            ind_string += f"{date_str}: {value}\n"

    except NoMarketDataError:
        raise  # Unknown/delisted symbol — let the router emit the sentinel
    except Exception as e:
        print(f"Error getting bulk stockstats data: {e}")
        # Fallback to original implementation if bulk method fails
        ind_string = ""  # 【变量】回退路径: 逐日单独计算
        curr_date_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        while curr_date_dt >= before:
            indicator_value = get_stockstats_indicator(  # 【调用函数】逐日回退计算单日指标值
                symbol, indicator, curr_date_dt.strftime("%Y-%m-%d")
            )
            ind_string += f"{curr_date_dt.strftime('%Y-%m-%d')}: {indicator_value}\n"
            curr_date_dt = curr_date_dt - relativedelta(days=1)

    result_str = (
        f"## {indicator} values from {before.strftime('%Y-%m-%d')} to {end_date}:\n\n"
        + ind_string
        + "\n\n"
        + best_ind_params.get(indicator, "No description available.")
    )

    return result_str


# 【功能】批量优化版指标计算: 一次拉取 OHLCV 并让 stockstats 算出所有日期的指标值。
# 【参数】symbol: 标的代码; indicator: 要计算的指标列名; curr_date: 参考日期。
# 【返回】dict: {日期字符串: 指标值字符串}, NaN 归一为 "N/A"。
# 【关键】load_ohlcv 已做缓存 + 防未来数据过滤; 访问 df[indicator] 是触发 stockstats
#         惰性计算的副作用(仅读取该列即触发派生列生成)。
def _get_stock_stats_bulk(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to calculate"],
    curr_date: Annotated[str, "current date for reference"],
) -> dict:
    """
    Optimized bulk calculation of stock stats indicators.
    Fetches data once and calculates indicator for all available dates.
    Returns dict mapping date strings to indicator values.
    """
    from stockstats import wrap  # 【调用包】stockstats 技术指标计算库

    data = load_ohlcv(symbol, curr_date)  # 【调用函数】加载(缓存)的 OHLCV 数据
    df = wrap(data)  # 【调用函数】封装为 stockstats 对象, 触发指标列惰性计算
    df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")

    # Calculate the indicator for all rows at once
    df[indicator]  # This triggers stockstats to calculate the indicator  # 【调用函数】读取即触发该指标派生列生成

    # Create a dictionary mapping date strings to indicator values
    result_dict = {}  # 【变量】收集 {日期: 指标值} 的结果字典
    for _, row in df.iterrows():
        date_str = row["Date"]
        indicator_value = row[indicator]

        # Handle NaN/None values
        if pd.isna(indicator_value):
            result_dict[date_str] = "N/A"
        else:
            result_dict[date_str] = str(indicator_value)

    return result_dict


# 【功能】取某标的某指标在单个日期的值(走 StockstatsUtils 缓存路径)。
# 【参数】symbol: 标的代码; indicator: 指标名; curr_date: 交易日(yyyy-mm-dd)。
# 【返回】指标值字符串; 非交易日返回 "N/A: Not a trading day..."。
# 【异常】NoMarketDataError: 标的不存在/退市(上抛给路由层); 其他异常打印日志并返回空串。
def get_stockstats_indicator(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to get the analysis and report of"],
    curr_date: Annotated[str, "The current trading date you are trading on, YYYY-mm-dd"],
) -> str:

    curr_date_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    curr_date = curr_date_dt.strftime("%Y-%m-%d")  # 【变量】格式化后的日期串

    try:
        indicator_value = StockstatsUtils.get_stock_stats(  # 【调用函数】委托静态方法取单日指标值
            symbol,
            indicator,
            curr_date,
        )
    except NoMarketDataError:
        raise  # Unknown/delisted symbol — let the router emit the sentinel
    except Exception as e:
        print(
            f"Error getting stockstats indicator data for indicator {indicator} on {curr_date}: {e}"
        )
        return ""

    return str(indicator_value)


# 【功能】从 yfinance 拉取公司基本面概览(市值/PE/营收/利润/财务比率等), 返回
#         带头信息的键值对文本。
# 【参数】ticker: 标的代码; curr_date: 参考日期(yfinance 路径不使用, 仅为接口对齐)。
# 【返回】"# 头信息 + 各字段一行"的字符串; 出错时返回 "Error retrieving..." 字符串。
# 【异常】NoMarketDataError: 标的不存在/所有字段为空(避免输出空表头让智能体瞎编)。
def get_fundamentals(
    ticker: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "current date (not used for yfinance)"] = None,
):
    """Get company fundamentals overview from yfinance."""
    canonical = normalize_symbol(ticker)  # 【调用函数】符号归一化
    try:
        ticker_obj = yf.Ticker(canonical)  # 【调用函数】构造 Yahoo Ticker 对象
        info = yf_retry(lambda: ticker_obj.info)  # 【调用函数】拉取公司信息(带限流重试)

        if not info:
            raise NoMarketDataError(ticker, canonical, "no fundamentals returned")

        fields = [
            ("Name", info.get("longName")),
            ("Sector", info.get("sector")),
            ("Industry", info.get("industry")),
            ("Market Cap", info.get("marketCap")),
            ("PE Ratio (TTM)", info.get("trailingPE")),
            ("Forward PE", info.get("forwardPE")),
            ("PEG Ratio", info.get("pegRatio")),
            ("Price to Book", info.get("priceToBook")),
            ("EPS (TTM)", info.get("trailingEps")),
            ("Forward EPS", info.get("forwardEps")),
            ("Dividend Yield", info.get("dividendYield")),
            ("Beta", info.get("beta")),
            ("52 Week High", info.get("fiftyTwoWeekHigh")),
            ("52 Week Low", info.get("fiftyTwoWeekLow")),
            ("50 Day Average", info.get("fiftyDayAverage")),
            ("200 Day Average", info.get("twoHundredDayAverage")),
            ("Revenue (TTM)", info.get("totalRevenue")),
            ("Gross Profit", info.get("grossProfits")),
            ("EBITDA", info.get("ebitda")),
            ("Net Income", info.get("netIncomeToCommon")),
            ("Profit Margin", info.get("profitMargins")),
            ("Operating Margin", info.get("operatingMargins")),
            ("Return on Equity", info.get("returnOnEquity")),
            ("Return on Assets", info.get("returnOnAssets")),
            ("Debt to Equity", info.get("debtToEquity")),
            ("Current Ratio", info.get("currentRatio")),
            ("Book Value", info.get("bookValue")),
            ("Free Cash Flow", info.get("freeCashflow")),
        ]

        lines = []  # 【变量】收集非空字段的文本行
        for label, value in fields:
            if value is not None:
                lines.append(f"{label}: {value}")

        # yfinance returns a stub dict (e.g. {"trailingPegRatio": None}) for
        # unknown symbols, so `info` is truthy but every field is empty. Treat
        # "no usable fields" as no data rather than emitting a bare header the
        # agent might fabricate around.
        if not lines:
            raise NoMarketDataError(ticker, canonical, "no fundamental fields returned")  # 【调用函数】未知标的返回空字段时按"无数据"处理

        header = f"# Company Fundamentals for {canonical}\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + "\n".join(lines)

    except NoMarketDataError:
        raise
    except Exception as e:
        return f"Error retrieving fundamentals for {ticker}: {str(e)}"


# 【功能】从 yfinance 拉取资产负债表(年度/季度), 按 curr_date 过滤防未来数据,
#         返回带头信息的 CSV 文本。
# 【参数】ticker: 标的代码; freq: "annual" 或 "quarterly"; curr_date: 截止日期。
# 【返回】"# 头信息 + CSV"字符串; 出错时返回 "Error retrieving..." 字符串。
# 【关键】filter_financials_by_date 会删除 fiscal 期结束日在 curr_date 之后的列,
#         防止回测看到未来财报。
def get_balance_sheet(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency of data: 'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date in YYYY-MM-DD format"] = None,
):
    """Get balance sheet data from yfinance."""
    canonical = normalize_symbol(ticker)  # 【调用函数】符号归一化
    try:
        ticker_obj = yf.Ticker(canonical)  # 【调用函数】构造 Yahoo Ticker 对象

        if freq.lower() == "quarterly":
            data = yf_retry(lambda: ticker_obj.quarterly_balance_sheet)  # 【调用函数】季度资产负债表(带限流重试)
        else:
            data = yf_retry(lambda: ticker_obj.balance_sheet)  # 【调用函数】年度资产负债表(带限流重试)

        data = filter_financials_by_date(data, curr_date)  # 【调用函数】按日期过滤未来报表列

        if data.empty:
            raise NoMarketDataError(ticker, canonical, "no balance sheet data")

        # Convert to CSV string for consistency with other functions
        csv_string = data.to_csv()

        # Add header information
        header = f"# Balance Sheet data for {canonical} ({freq})\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + csv_string

    except NoMarketDataError:
        raise
    except Exception as e:
        return f"Error retrieving balance sheet for {ticker}: {str(e)}"


# 【功能】从 yfinance 拉取现金流量表(年度/季度), 按 curr_date 过滤防未来数据,
#         返回带头信息的 CSV 文本。
# 【参数】ticker: 标的代码; freq: "annual" 或 "quarterly"; curr_date: 截止日期。
# 【返回】"# 头信息 + CSV"字符串; 出错时返回 "Error retrieving..." 字符串。
def get_cashflow(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency of data: 'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date in YYYY-MM-DD format"] = None,
):
    """Get cash flow data from yfinance."""
    canonical = normalize_symbol(ticker)  # 【调用函数】符号归一化
    try:
        ticker_obj = yf.Ticker(canonical)  # 【调用函数】构造 Yahoo Ticker 对象

        if freq.lower() == "quarterly":
            data = yf_retry(lambda: ticker_obj.quarterly_cashflow)  # 【调用函数】季度现金流量表(带限流重试)
        else:
            data = yf_retry(lambda: ticker_obj.cashflow)  # 【调用函数】年度现金流量表(带限流重试)

        data = filter_financials_by_date(data, curr_date)  # 【调用函数】按日期过滤未来报表列

        if data.empty:
            raise NoMarketDataError(ticker, canonical, "no cash flow data")

        # Convert to CSV string for consistency with other functions
        csv_string = data.to_csv()

        # Add header information
        header = f"# Cash Flow data for {canonical} ({freq})\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + csv_string

    except NoMarketDataError:
        raise
    except Exception as e:
        return f"Error retrieving cash flow for {ticker}: {str(e)}"


# 【功能】从 yfinance 拉取利润表(年度/季度), 按 curr_date 过滤防未来数据,
#         返回带头信息的 CSV 文本。
# 【参数】ticker: 标的代码; freq: "annual" 或 "quarterly"; curr_date: 截止日期。
# 【返回】"# 头信息 + CSV"字符串; 出错时返回 "Error retrieving..." 字符串。
def get_income_statement(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency of data: 'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date in YYYY-MM-DD format"] = None,
):
    """Get income statement data from yfinance."""
    canonical = normalize_symbol(ticker)  # 【调用函数】符号归一化
    try:
        ticker_obj = yf.Ticker(canonical)  # 【调用函数】构造 Yahoo Ticker 对象

        if freq.lower() == "quarterly":
            data = yf_retry(lambda: ticker_obj.quarterly_income_stmt)  # 【调用函数】季度利润表(带限流重试)
        else:
            data = yf_retry(lambda: ticker_obj.income_stmt)  # 【调用函数】年度利润表(带限流重试)

        data = filter_financials_by_date(data, curr_date)  # 【调用函数】按日期过滤未来报表列

        if data.empty:
            raise NoMarketDataError(ticker, canonical, "no income statement data")

        # Convert to CSV string for consistency with other functions
        csv_string = data.to_csv()

        # Add header information
        header = f"# Income Statement data for {canonical} ({freq})\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + csv_string

    except NoMarketDataError:
        raise
    except Exception as e:
        return f"Error retrieving income statement for {ticker}: {str(e)}"


# 【功能】从 yfinance 拉取内部人交易数据, 返回带头信息的 CSV 文本。
# 【参数】ticker: 标的代码。
# 【返回】"# 头信息 + CSV"字符串; 无内部人申报时返回说明文本(属正常情况);
#         出错时返回 "Error retrieving..." 字符串。
# 【关键】与行情/报表不同, 空结果在此属正常(很多有效标的没有内部人申报),
#         故直接平铺说明, 不当作标的无效处理。
def get_insider_transactions(ticker: Annotated[str, "ticker symbol of the company"]):
    """Get insider transactions data from yfinance."""
    canonical = normalize_symbol(ticker)  # 【调用函数】符号归一化
    try:
        ticker_obj = yf.Ticker(canonical)  # 【调用函数】构造 Yahoo Ticker 对象
        data = yf_retry(lambda: ticker_obj.insider_transactions)  # 【调用函数】拉取内部人交易(带限流重试)

        # Empty is normal here (many valid symbols have no insider filings),
        # so report it plainly rather than treating the symbol as invalid.
        if data is None or data.empty:
            return f"No insider transactions reported for symbol '{canonical}'"

        # Convert to CSV string for consistency with other functions
        csv_string = data.to_csv()

        # Add header information
        header = f"# Insider Transactions data for {canonical}\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + csv_string

    except Exception as e:
        return f"Error retrieving insider transactions for {ticker}: {str(e)}"
