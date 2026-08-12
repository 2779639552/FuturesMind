import logging  # 【调用包】日志
import os  # 【调用包】创建缓存目录/拼缓存文件路径
import time  # 【调用包】限流退避睡眠
from typing import Annotated  # 【调用包】为参数附加业务含义标注

import pandas as pd  # 【调用包】DataFrame 处理与日期解析
import yfinance as yf  # 【调用包】Yahoo Finance 行情数据源
from stockstats import wrap  # 【调用包】stockstats 技术指标计算库
from yfinance.exceptions import YFRateLimitError  # 【调用包】捕获 yfinance 的 HTTP 429 限流异常

from .config import get_config  # 【调用包】读取 data_cache_dir 等配置
from .symbol_utils import NoMarketDataError, normalize_symbol  # 【调用包】符号归一化 + 无数据异常
from .utils import safe_ticker_component  # 【调用包】缓存文件名的安全化校验

logger = logging.getLogger(__name__)

# A vendor's latest OHLCV row this many calendar days before the requested date
# is treated as stale. Generous enough to span long holiday weekends, tight
# enough to catch the year-old frames yfinance occasionally returns (#1021).
# 【变量】OHLCV 最新行距请求日超过该天数即视为"过期"阈值(10 天, 可跨长假,
#         又能抓住 yfinance 偶尔返回的陈旧帧 #1021)
MAX_OHLCV_STALE_DAYS = 10


# 【功能】包装一次 yfinance 调用, 对 HTTP 429 限流做指数退避重试。
# 【参数】func: 要执行的 yfinance 调用(可调用对象); max_retries: 最大重试次数;
#         base_delay: 首轮退避基数秒数。
# 【返回】func() 的返回值。
# 【异常】YFRateLimitError: 重试用尽后原样上抛。
# 【关键】yfinance 对 429 只抛 YFRateLimitError 而不会内部重试, 本函数专门补这层;
#         其他异常立即上抛(不做盲重试)。
def yf_retry(func, max_retries=3, base_delay=2.0):
    """Execute a yfinance call with exponential backoff on rate limits.

    yfinance raises YFRateLimitError on HTTP 429 responses but does not
    retry them internally. This wrapper adds retry logic specifically
    for rate limits. Other exceptions propagate immediately.
    """
    for attempt in range(max_retries + 1):
        try:
            return func()
        except YFRateLimitError:
            if attempt < max_retries:
                delay = base_delay * (2**attempt)  # 【变量】指数退避: 2s, 4s, 8s ...
                logger.warning(
                    f"Yahoo Finance rate limited, retrying in {delay:.0f}s (attempt {attempt + 1}/{max_retries})"
                )
                time.sleep(delay)  # 【调用函数】退避睡眠后进入下一轮
            else:
                raise


# 【功能】把日期列统一命名为 ``Date``。
# 【关键】部分 yfinance 构建里索引无名(reset_index 后得 ``index``), 盘中数据用
#         ``Datetime``; 重命名首个日期样列, 避免指标因列名不是 Date 而静默丢失。
def _ensure_date_column(data: pd.DataFrame) -> pd.DataFrame:
    """Normalize the date column to ``Date``.

    Some yfinance builds leave the index unnamed (so ``reset_index()`` yields
    ``index``) or use ``Datetime`` for intraday data. Rename the first
    date-like column so indicators don't silently drop when it isn't ``Date``.
    """
    if "Date" in data.columns:
        return data
    for candidate in ("index", "Datetime", "date"):
        if candidate in data.columns:
            return data.rename(columns={candidate: "Date"})  # 【调用函数】重命名日期样列
    return data


# 【功能】把股票 DataFrame 规整为 stockstats 可用形态: 解析日期、删无效行、补价格缺口。
# 【关键】价格列统一转数值, 丢弃无 Close 的行, 并用 ffill/bfill 填补价格空隙。
def _clean_dataframe(data: pd.DataFrame) -> pd.DataFrame:
    """Normalize a stock DataFrame for stockstats: parse dates, drop invalid rows, fill price gaps."""
    data = _ensure_date_column(data)  # 【调用函数】统一日期列名
    data["Date"] = pd.to_datetime(data["Date"], errors="coerce")  # 【调用函数】解析日期, 无效置 NaT
    data = data.dropna(subset=["Date"])  # 【调用函数】丢弃日期无效的行

    price_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in data.columns]  # 【变量】存在的价格列
    data[price_cols] = data[price_cols].apply(pd.to_numeric, errors="coerce")  # 【调用函数】价格列转数值
    data = data.dropna(subset=["Close"])  # 【调用函数】丢弃无收盘价的行
    data[price_cols] = data[price_cols].ffill().bfill()  # 【调用函数】前向/后向填充填补价格缺口

    return data


# 【功能】从 OHLCV 帧中取出解析好的日期序列(Date 列或索引皆可)。
# 【返回】pd.Series(datetime64); 无任何日期样列时返回空 Series。
def _coerce_ohlcv_dates(data: pd.DataFrame) -> pd.Series:
    """Return parsed dates from an OHLCV frame, whether Date is a column or the index."""
    if "Date" in data.columns:
        return pd.to_datetime(data["Date"], errors="coerce").dropna()
    # yfinance keeps the dates in the index (a DatetimeIndex, sometimes unnamed).
    if isinstance(data.index, pd.DatetimeIndex):  # 【变量】日期在索引(DatetimeIndex)
        return pd.Series(pd.to_datetime(data.index, errors="coerce")).dropna()
    # Fallback: expose the index and look for any date-like column.
    df = data.reset_index()  # 【调用函数】把索引暴露为列再找日期列
    for col in ("Date", "Datetime", "date", "index"):
        if col in df.columns:
            parsed = pd.to_datetime(df[col], errors="coerce").dropna()
            if not parsed.empty:
                return parsed
    return pd.Series(dtype="datetime64[ns]")


# 【功能】拒绝"最新行远早于 curr_date"的 OHLCV 帧(防陈旧数据)。
# 【参数】data: 待校验的 OHLCV; curr_date: 请求的分析日期; symbol/canonical: 供报错;
#         max_stale_days: 允许的最大陈旧天数(默认 MAX_OHLCV_STALE_DAYS=10)。
# 【返回】None; 过期时抛 NoMarketDataError。
# 【异常】NoMarketDataError(带"stale"细节), 让路由层按"厂商无可用数据"处理——
#         试下一个厂商, 最后给出一个明确的无数据信号。空帧留给调用方处理。
# 【关键】只防"有行但过期"(如 yfinance 偶发返回一年前的帧, 会喂错价格给智能体 #1021)。
def _assert_ohlcv_not_stale(
    data: pd.DataFrame,
    curr_date: str,
    symbol: str,
    canonical: str | None = None,
    *,
    max_stale_days: int = MAX_OHLCV_STALE_DAYS,
) -> None:
    """Reject OHLCV whose latest row is far older than curr_date.

    Raises NoMarketDataError (with a stale-specific detail) so the router treats
    it like any other "no usable data from this vendor" — try the next vendor,
    then emit one clear unavailable signal. Empty frames are left to the
    caller's existing no-data handling; this guards only the dangerous case of
    present-but-stale rows (a vendor returning a year-old frame that would
    otherwise feed wrong prices to the agent, #1021).
    """
    if data is None or data.empty:
        return
    requested = pd.to_datetime(curr_date, errors="coerce")  # 【变量】请求日期解析
    if pd.isna(requested):
        return
    requested = requested.normalize()  # 【调用函数】归一化到天
    dates = _coerce_ohlcv_dates(data)  # 【调用函数】取帧内日期序列
    if dates.empty:
        return
    latest = dates.max().normalize()  # 【变量】帧内最新交易日
    stale_days = (requested - latest).days  # 【变量】请求日与最新行的间隔天数
    if stale_days > max_stale_days:
        raise NoMarketDataError(
            symbol,
            canonical,
            f"latest row is {latest.date()}, {stale_days} days before the "
            f"requested {requested.date()} (stale) — refusing to use it",
        )


# 【功能】带缓存的 OHLCV 加载, 并过滤未来数据防止前视偏差。
# 【参数】symbol: 标的代码; curr_date: 分析日期(yyyy-mm-dd)。
# 【返回】清洗后的 pd.DataFrame(仅含 Date <= curr_date 的行)。
# 【异常】NoMarketDataError: 下载返回空或缓存中毒时抛。
# 【关键】① 下载近 5 年到今天的数据, 每符号一个缓存文件; ② 空/无 Close 列的
#         缓存视为失效并重新下载, 避免永久服务坏缓存; ③ 行过滤到 curr_date 保证
#         回测永远看不到未来价; ④ 校验帧未过期(见 _assert_ohlcv_not_stale)。
def load_ohlcv(symbol: str, curr_date: str) -> pd.DataFrame:
    """Fetch OHLCV data with caching, filtered to prevent look-ahead bias.

    Downloads 5 years of data up to today and caches per symbol. On
    subsequent calls the cache is reused. Rows after curr_date are
    filtered out so backtests never see future prices.
    """
    # Resolve broker/forex symbols (XAUUSD+ -> GC=F) to Yahoo's convention,
    # then reject values that would escape the cache directory when
    # interpolated into the cache filename (e.g. ``../../tmp/x``).
    canonical = normalize_symbol(symbol)  # 【调用函数】符号归一化为 Yahoo 约定
    safe_symbol = safe_ticker_component(canonical)  # 【调用函数】校验并安全化缓存文件名成分

    config = get_config()  # 【调用函数】读取 data_cache_dir 配置
    curr_date_dt = pd.to_datetime(curr_date)  # 【变量】请求日期解析

    # Cache uses a fixed window (5y to today) so one file per symbol.
    today_date = pd.Timestamp.today()  # 【变量】今天
    start_date = today_date - pd.DateOffset(years=5)  # 【变量】回看 5 年起点
    start_str = start_date.strftime("%Y-%m-%d")
    # yfinance ``end`` is EXCLUSIVE; request tomorrow so today's row is included
    # when curr_date is the current day (#986). Look-ahead is still prevented by
    # the curr_date filter below.
    end_str = (today_date + pd.Timedelta(days=1)).strftime("%Y-%m-%d")  # 【变量】end 排他, 请求到明天以包含今天

    os.makedirs(config["data_cache_dir"], exist_ok=True)  # 【调用函数】确保缓存目录存在
    data_file = os.path.join(  # 【变量】缓存文件路径(按符号+窗口命名)
        config["data_cache_dir"],
        f"{safe_symbol}-YFin-data-{start_str}-{end_str}.csv",
    )

    # A cached file may be empty if a prior fetch failed (unknown symbol,
    # transient rate limit). Treat an empty/columnless cache as a miss and
    # re-fetch rather than serving the poisoned file forever.
    data = None  # 【变量】最终使用的数据帧(命中缓存则复用)
    if os.path.exists(data_file):
        cached = pd.read_csv(data_file, on_bad_lines="skip", encoding="utf-8")  # 【调用函数】读缓存 CSV
        if not cached.empty and "Close" in cached.columns:
            data = cached

    if data is None:
        downloaded = yf_retry(  # 【调用函数】带限流重试的 yfinance 下载
            lambda: yf.download(
                canonical,
                start=start_str,
                end=end_str,
                multi_level_index=False,
                progress=False,
                auto_adjust=True,
            )
        )
        downloaded = _ensure_date_column(downloaded.reset_index())  # 【调用函数】统一日期列名
        # Only cache real data — never persist an empty frame.
        if downloaded.empty or "Close" not in downloaded.columns:
            raise NoMarketDataError(symbol, canonical, "Yahoo Finance returned no rows")
        downloaded.to_csv(data_file, index=False, encoding="utf-8")  # 【调用函数】落盘缓存
        data = downloaded

    data = _clean_dataframe(data)  # 【调用函数】规整数据(日期解析/补价格缺口)

    # Filter to curr_date to prevent look-ahead bias in backtesting
    data = data[data["Date"] <= curr_date_dt]  # 【调用函数】过滤掉 curr_date 之后的未来行

    # Reject a stale frame (latest row far older than curr_date) rather than
    # feeding year-old prices into indicators (#1021).
    _assert_ohlcv_not_stale(data, curr_date, symbol, canonical)  # 【调用函数】校验帧未过期

    return data


# 【功能】删除财务报表中 fiscal 期结束日晚于 curr_date 的列(防前视偏差)。
# 【参数】data: 财务表 DataFrame(列=财政期结束日); curr_date: 截止日期。
# 【返回】仅保留列日期 <= curr_date 的 DataFrame。
# 【关键】yfinance 财务报表以财政期结束日作为列名, 晚于 curr_date 的列代表未来
#         数据, 必须移除。
def filter_financials_by_date(data: pd.DataFrame, curr_date: str) -> pd.DataFrame:
    """Drop financial statement columns (fiscal period timestamps) after curr_date.

    yfinance financial statements use fiscal period end dates as columns.
    Columns after curr_date represent future data and are removed to
    prevent look-ahead bias.
    """
    if not curr_date or data.empty:
        return data
    cutoff = pd.Timestamp(curr_date)  # 【变量】过滤截止时间
    mask = pd.to_datetime(data.columns, errors="coerce") <= cutoff  # 【变量】列日期是否 <= 截止
    return data.loc[:, mask]


# 【功能】技术指标静态工具类: 基于缓存 OHLCV 计算单日指标值。
class StockstatsUtils:
    @staticmethod
    def get_stock_stats(
        symbol: Annotated[str, "ticker symbol for the company"],
        indicator: Annotated[
            str, "quantitative indicators based off of the stock data for the company"
        ],
        curr_date: Annotated[str, "curr date for retrieving stock price data, YYYY-mm-dd"],
    ):
        data = load_ohlcv(symbol, curr_date)  # 【调用函数】加载(缓存)OHLCV
        df = wrap(data)  # 【调用函数】封装为 stockstats 对象
        df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
        curr_date_str = pd.to_datetime(curr_date).strftime("%Y-%m-%d")  # 【变量】请求日期字符串

        df[indicator]  # trigger stockstats to calculate the indicator  # 【调用函数】读取即触发指标派生列生成
        matching_rows = df[df["Date"].str.startswith(curr_date_str)]  # 【变量】请求日期对应的数据行

        if not matching_rows.empty:
            indicator_value = matching_rows[indicator].values[0]
            return indicator_value
        else:
            return "N/A: Not a trading day (weekend or holiday)"
