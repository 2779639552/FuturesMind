"""
Commodity Futures tool wrappers for LangChain agents.
Wraps the commodity_futures data vendor functions as @tool-decorated callables.
"""

# ===========================================================================
# 【本文件在数据流中的角色】
#   这是"工具封装层":把 tradingagents/dataflows/commodity_futures.py 里那些普通
#   Python 函数,用 LangChain 的 @tool 装饰器包装成"Agent 可调用的工具(Tool)"。
#   Agent(大模型)本身不能直接执行 Python 代码,只能通过工具与外部世界交互;
#   本文件就是 Agent 操作期货数据的"遥控器按钮面板"。
#
# 【@tool 装饰器的作用】
#   @tool 来自 langchain_core.tools,它会把被装饰的函数变成一个 Tool 对象,并读取:
#     - 函数名 get_futures_* : 工具的唯一标识;
#     - 文档字符串(docstring): 工具的说明文字;
#     - 参数注解 Annotated[str, "描述"]: 每个参数的作用说明。
#   大模型(如期货分析师)会"看名字 + 看说明 + 看参数描述"来决定在什么场景下
#   调用哪个工具。因此这里每个工具的 name/description 写得好不好,
#   直接决定 Agent 会不会在正确时机调用它。
#
# 【与底层数据层的关系】
#   所有工具函数本身几乎不写逻辑,只是把参数原样转发给
#   tradingagents/dataflows/interface.py 的 route_to_vendor(),
#   由路由层按配置找到 commodity_futures 供应商并调用真正的实现,
#   再把结果字符串返回给大模型。
#
# 【与 signal_analyzer 回测引擎数据源的区别】
#   本文件提供的工具给 Agent 做"实时分析"(联网取数,数据来自 AKShare/外部 JSON);
#   signal_analyzer 回测引擎则是离线读本地 JSON 做回测,两者数据用途不同。
# ===========================================================================

from typing import Annotated  # 【调用包】类型注解:给工具参数附加描述,供 LangChain 生成工具说明

from langchain_core.tools import tool  # 【调用包】LangChain 工具装饰器:把普通函数注册为 Agent 可调用的 Tool

# route_to_vendor: 路由函数,按"方法名 -> 配置的供应商"找到实现并调用。
# 这里只传方法名和参数,真正的取数逻辑在 dataflows 层(commodity_futures.py)。
from tradingagents.dataflows.interface import route_to_vendor  # 【调用包】路由函数:按方法名把取数请求分派到配置的数据供应商


# 【功能】获取商品期货日线行情(OHLCV + 持仓量)。Agent 需要历史价格时调用。
# 【参数】symbol: 品种代码;start_date/end_date: 起止日期 yyyy-mm-dd。
# 【返回】CSV 字符串(列: date, open, high, low, close, volume, open_interest)。
# 【关键逻辑】纯转发:把三个参数交给 route_to_vendor("get_futures_price", ...),
#           由底层 commodity_futures.get_futures_price 用 AKShare futures_main_sina
#           取数(主力连续合约)。工具的 name/description 让 Agent 判断
#           "问价格、问历史走势"时调用本工具。
@tool
def get_futures_price(
    symbol: Annotated[str, "Commodity variety code, e.g. RB (rebar), I (iron ore)"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """
    Retrieve daily OHLCV price data and open interest for a commodity futures contract.
    Uses the main (continuous) contract to avoid rollover gaps.
    Args:
        symbol: Variety code like RB, I, JM, J, HC, M, TA, MA
        start_date: Start date in yyyy-mm-dd format
        end_date: End date in yyyy-mm-dd format
    Returns:
        CSV with columns: date, open, high, low, close, volume, open_interest
    """
    return route_to_vendor("get_futures_price", symbol, start_date, end_date)  # 【调用函数】跨模块路由:取主力连续合约日线行情(OHLCV+持仓量)


# 【功能】计算商品期货技术指标(SMA/EMA/MACD/RSI/布林带/ATR 等)。
# 【参数】symbol: 品种代码;start_date/end_date: 起止日期 yyyy-mm-dd。
# 【返回】CSV:原行情列 + 指标列。
# 【关键逻辑】转发给 route_to_vendor("get_futures_indicators", ...),
#           底层用全量历史计算指标再按区间截取。
@tool
def get_futures_indicators(
    symbol: Annotated[str, "Commodity variety code, e.g. RB (rebar)"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """
    Calculate technical indicators for a commodity futures contract.
    Computes SMA(5/10/20/50), EMA(12/26), MACD, RSI(14), Bollinger Bands(20,2),
    ATR(14), volume momentum indicators, and open interest change analysis.
    Args:
        symbol: Variety code like RB, I, JM
        start_date: Start date in yyyy-mm-dd format
        end_date: End date in yyyy-mm-dd format
    Returns:
        CSV with price data plus computed technical indicators.
    """
    return route_to_vendor("get_futures_indicators", symbol, start_date, end_date)  # 【调用函数】跨模块路由:计算技术指标(SMA/MACD/RSI/布林带/ATR)


# 【功能】获取现货-期货基差(基差 = 现货价 - 期货价)。
# 【参数】symbol: 品种代码;start_date/end_date: 起止日期 yyyy-mm-dd。
# 【返回】CSV:现货价、主力/近月合约价、基差、基差率 + 最新基差解读。
# 【关键逻辑】转发给 route_to_vendor("get_futures_basis", ...);
#           底层走 Hybrid Mode:有外部现货价会合并进来。
@tool
def get_futures_basis(
    symbol: Annotated[str, "Commodity variety code, e.g. RB (rebar)"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """
    Get spot-futures basis (基差) data for a commodity.
    Basis = spot_price - futures_price. Positive = backwardation (spot premium, tight supply).
    Negative = contango (futures premium, carry/expectation driven).
    Args:
        symbol: Variety code like RB, I, HC
        start_date: Start date in yyyy-mm-dd format
        end_date: End date in yyyy-mm-dd format
    Returns:
        CSV with spot price, dominant contract price, basis, basis rate, and interpretation.
    """
    return route_to_vendor("get_futures_basis", symbol, start_date, end_date)  # 【调用函数】跨模块路由:取现货-期货基差(现货价-期货价)


# 【功能】获取期货品种的仓单库存数据。
# 【参数】symbol: 品种代码。
# 【返回】CSV:日期、库存、变化 + 趋势解读(累库/去库/平稳)。
# 【关键逻辑】转发给 route_to_vendor("get_futures_inventory", symbol, "", "");
#           底层只取最近 60 条,并走 Hybrid Mode 合并外部社会库存。
@tool
def get_futures_inventory(
    symbol: Annotated[str, "Commodity variety code, e.g. RB (rebar)"],
) -> str:
    """
    Get warehouse inventory data for a commodity futures variety.
    Inventory levels are a key supply-side indicator: rising inventory signals
    oversupply (bearish), declining inventory signals tightening (bullish).
    Args:
        symbol: Variety code like RB, I, HC
    Returns:
        CSV with date, inventory level, daily change, and trend analysis.
    """
    return route_to_vendor("get_futures_inventory", symbol, "", "")  # 【调用函数】跨模块路由:取仓单库存(日期留空,底层只取最近 60 条)


# 【功能】获取最新商品/宏观新闻(多来源,关键词过滤)。
# 【参数】symbol: 品种代码,仅用于追加品种关键词,新闻本身是全局的。
# 【返回】带时间与来源的新闻文本(最多 20 条)。
# 【关键逻辑】转发给 route_to_vendor("get_futures_news", symbol, "", "");
#           底层抓 Eastmoney 7x24 + SHMET 两个来源。
@tool
def get_futures_news(
    symbol: Annotated[str, "Commodity variety code (used for context, feed is global)"],
) -> str:
    """
    Get the latest commodity market news from SHMET.
    Covers macro trends, supply/demand updates, policy changes, and geopolitical events
    affecting commodity markets.
    Args:
        symbol: Variety code for context (e.g. RB, I), news feed is global.
    Returns:
        Text with latest news headlines and timestamps.
    """
    return route_to_vendor("get_futures_news", symbol, "", "")  # 【调用函数】跨模块路由:取全局商品新闻(Eastmoney 7x24 + SHMET)


# 【功能】获取品种元信息(交易所、合约规格、交易时间、关键因素、产业链品种)。
# 【参数】symbol: 品种代码。
# 【返回】JSON 字符串。
# 【关键逻辑】底层直接读 VARIETY_METADATA。工具说明里明确建议 Agent
#           "先用本工具了解品种基本面,再调用其他行情工具"。
@tool
def get_variety_info(
    symbol: Annotated[str, "Commodity variety code, e.g. RB (rebar)"],
) -> str:
    """
    Get metadata for a commodity variety: exchange, contract specs, trading hours,
    key supply/demand factors, and related varieties in the industrial chain.
    Use this FIRST before any other tool to understand the variety's fundamentals.
    Args:
        symbol: Variety code like RB, I, JM
    Returns:
        JSON with variety name, exchange, unit, price limits, key factors, related varieties.
    """
    return route_to_vendor("get_variety_info", symbol)  # 【调用函数】跨模块路由:取品种元信息(交易所/合约规格/关键因素/产业链品种)


# 【功能】获取中国宏观指标(GDP/PMI/固投/地产/工业增加值/建筑业指数)。
# 【参数】无。
# 【返回】格式化宏观报告文本。
# 【关键逻辑】转发给 route_to_vendor("get_futures_macro");底层用 akshare macro_* 系列。
@tool
def get_futures_macro() -> str:
    """
    Get key China macroeconomic indicators relevant to commodity futures analysis.
    Includes: GDP (quarterly YoY), PMI (manufacturing), Fixed Asset Investment (monthly YoY),
    Real Estate Climate Index, Industrial Production (monthly YoY), Construction Index (daily).
    Use this for macro/policy analysis of commodity demand drivers.
    Returns:
        Formatted text report with latest values, recent trends, and brief interpretations.
    """
    return route_to_vendor("get_futures_macro")  # 【调用函数】跨模块路由:取中国宏观指标(GDP/PMI/固投/地产/工业增加值)


# 【功能】获取品种供需两侧指标(产量/成交/开工率/利润/库存/事件)。
# 【参数】symbol: 品种代码。
# 【返回】格式化供需报告文本。
# 【关键逻辑】转发给 route_to_vendor("get_futures_supply_demand", symbol, "", "");
#           底层先读外部 JSON(有则优先生效),再用免费 API 补充。
@tool
def get_futures_supply_demand(
    symbol: Annotated[str, "Commodity variety code, e.g. RB (rebar)"],
) -> str:
    """
    Get supply-side and demand-side indicators for a commodity variety.
    Combines external data (weekly production, daily transaction volume, hot metal output,
    BF/EAF operating rates, mill profit, social inventory) with free API data
    (construction index, real estate climate index).
    Use this for fundamental analysis to understand supply-demand balance.
    Args:
        symbol: Variety code like RB, I, HC
    Returns:
        Formatted text report with production, transaction, inventory, and industry data.
    """
    return route_to_vendor("get_futures_supply_demand", symbol, "", "")  # 【调用函数】跨模块路由:取供需两侧指标(产量/开工率/利润/库存)


# 【功能】获取该品种"人工上传研报"的结构化摘要(方向/置信度/观点/关键数据点)。
# 【参数】symbol: 品种代码。
# 【返回】格式化研报摘要文本;无研报返回 RESEARCH_NO_DATA 哨兵(确定性结论,不允许编造)。
# 【关键逻辑】转发给 route_to_vendor("get_research_report", symbol, "", "");
#           底层读 research_data.get_research_report_text(~/.tradingagents/external_data
#           {品种}_research.json)。研报是人工上传的一手材料,可信优先级最高
#           (RESEARCH > 外部 EXTERNAL > 免费 FREE_API),基本面/宏观分析师应优先采信。
@tool
def get_research_report(
    symbol: Annotated[str, "Commodity variety code, e.g. RB (rebar)"],
) -> str:
    """
    Get manually-uploaded institutional/industry research reports for a commodity variety.

    Research reports are HUMAN-UPLOADED first-hand materials (institutional views,
    industry field surveys), the HIGHEST-trust data source in the system
    (priority: RESEARCH > external EXTERNAL > free FREE_API).

    Includes for each report: direction (看多/看空/中性), confidence, conclusion
    summary, and key data points (spot price, social/mill inventory, supply/demand).

    Use this for fundamental/macro analysis to incorporate professional views
    and resolve disagreements between other data sources. If the response is
    "RESEARCH_NO_DATA: ...", report honestly that no research has been uploaded
    rather than inventing data.
    Args:
        symbol: Variety code like RB, I, HC
    Returns:
        Formatted text summary of uploaded research reports (or RESEARCH_NO_DATA sentinel).
    """
    return route_to_vendor("get_research_report", symbol, "", "")  # 【调用函数】跨模块路由:取人工上传研报摘要(最高优先级数据源)


# 【功能】获取品种的社交媒体情绪数据(微博/知乎/小红书)。
# 【参数】symbol: 品种代码。
# 【返回】格式化情绪报告文本。
# 【关键逻辑】转发给 route_to_vendor("get_futures_sentiment", symbol, "", "");
#           底层读外部情绪 JSON(sentiment_data 模块)。
@tool
def get_futures_sentiment(
    symbol: Annotated[str, "Commodity variety code, e.g. RB (rebar)"],
) -> str:
    """
    Get social media sentiment data for a commodity futures variety.
    Data collected from Weibo, Zhihu, and Xiaohongshu (XHS) platforms,
    processed through NER variety identification + rule-engine sentiment analysis
    + LLM sentiment engine, aggregated into daily time series.

    The response includes:
    - Overall sentiment direction (bullish/bearish/neutral) and ratio
    - Multi-platform breakdown (Weibo vs Zhihu vs XHS)
    - Sentiment trend (improving/deteriorating)
    - Daily sentiment time series
    - Sentiment-price correlation backtest
    - Extreme sentiment warnings (contrarian signals)
    - Platform weight calibration

    Use this for market psychology / sentiment analysis.
    Args:
        symbol: Variety code like RB, I, JM, MA
    Returns:
        Formatted text report with sentiment data and analysis guidance.
    """
    return route_to_vendor("get_futures_sentiment", symbol, "", "")  # 【调用函数】跨模块路由:取社交媒体情绪(微博/知乎/小红书)


# 【功能】获取指定日期的"确定性核验行情快照",是数值主张的唯一真相来源。
# 【参数】symbol: 品种代码;date: 目标日期 yyyy-mm-dd。
# 【返回】VERIFIED_SNAPSHOT 文本(精确 OHLCV + 日涨跌% + SMA5/SMA20 + 位置判断)。
# 【关键逻辑】转发给 route_to_vendor("get_verified_quote", symbol, date, "", "");
#           工具说明强烈要求 Agent:数字冲突时"上报分歧"而不是自己编一个。
@tool
def get_verified_quote(
    symbol: Annotated[str, "Commodity variety code, e.g. RB (rebar)"],
    date: Annotated[str, "Target date in yyyy-mm-dd format"],
) -> str:
    """
    Get a VERIFIED, deterministic OHLCV + key levels snapshot for an exact date.

    This is the SINGLE SOURCE OF TRUTH for numeric price/indicator claims.
    Use this tool when you need to state an EXACT price, support/resistance level,
    or indicator value. If your other tools show different numbers, FLAG the
    discrepancy rather than reconciling.

    Returns: Open, High, Low, Close, Volume, Open Interest, Day Change%,
    SMA(5), SMA(20), and Price vs SMA20 position.
    Args:
        symbol: Variety code like RB, I, JM
        date: Target date in yyyy-mm-dd format (e.g. "2026-07-14")
    """
    return route_to_vendor("get_verified_quote", symbol, date, "", "")  # 【调用函数】跨模块路由:取确定性核验行情快照(数值主张的唯一真相源)


# 【功能】获取品种的实时(盘面)最新价。辩论/讨论中核对当前行情用。
# 【参数】symbol: 品种代码(如 RB, SA, FG)。
# 【返回】JSON:code/name/price/change_pct/volume/timestamp。
# 【关键逻辑】★ 不走 route_to_vendor,是唯一提供"实时/流式"数据的工具:
#           直接 import price_fetcher(web_app 上下文里的行情抓取模块),
#           用 NAME_TO_CODE 把品种代码映射成中文名,再取缓存的实时价格
#           (get_cached_prices, 60 秒缓存)。非交易时段返回最近一次价。
#           注意:仅当运行环境有 price_fetcher 模块时可用,否则返回错误说明。
@tool
def get_realtime_price(
    symbol: Annotated[str, "Commodity variety code, e.g. RB, SA, FG"],
) -> str:
    """
    Get real-time (live) futures market price for a commodity variety.

    Returns the CURRENT live price, change%, volume, and timestamp from AKShare.
    Use this during debates or discussions to check the latest market price and
    verify claims about current market conditions. This is the only tool that
    provides live/streaming data — all other price tools return historical data.

    IMPORTANT: This only works during market trading hours (Mon-Fri 9:00-15:00,
    21:00-23:00 CST). Outside trading hours, returns the last available price.

    Args:
        symbol: Variety code like RB, SA, FG, TA, MA
    Returns:
        JSON with keys: price, change_pct, volume, name, timestamp
    """
    import json  # 【调用包】JSON 序列化:构造返回给 Agent 的实时行情 JSON

    try:
        from price_fetcher import NAME_TO_CODE, get_cached_prices  # 【调用包】实时行情模块(web_app 上下文):品种名映射 + 60 秒缓存行情
    except ImportError:
        return "ERROR: price_fetcher module not available (web_app context only)"

    code_to_name = {v: k for k, v in NAME_TO_CODE.items()}  # 【变量】反转映射:品种代码→中文名,用于校验输入代码
    upper = symbol.upper().strip()  # 【变量】归一化大写品种代码(如 rb→RB)
    if upper not in code_to_name:
        return f"ERROR: Unknown variety code '{symbol}'. Use uppercase codes like RB, SA, FG."

    prices = get_cached_prices([upper])  # 【调用函数】读取 60 秒缓存的实时价格
    if upper not in prices:
        return f"NO_DATA: No live price available for {upper}. Market may be closed."

    data = prices[upper]  # 【变量】该品种实时行情字典(price/change_pct/volume/timestamp)
    return json.dumps(
        {
            "code": upper,
            "name": data.get("name", upper),
            "price": data.get("price", 0),
            "change_pct": data.get("change_pct", 0),
            "volume": data.get("volume", 0),
            "timestamp": data.get("timestamp", ""),
        },
        ensure_ascii=False,
        indent=2,
    )  # 【调用函数】把实时行情序列化为 JSON 返回给 Agent(ensure_ascii=False 保留中文名)
