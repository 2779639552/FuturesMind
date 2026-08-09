"""
Commodity Futures tool wrappers for LangChain agents.
Wraps the commodity_futures data vendor functions as @tool-decorated callables.
"""

from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.interface import route_to_vendor


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
    return route_to_vendor("get_futures_price", symbol, start_date, end_date)


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
    return route_to_vendor("get_futures_indicators", symbol, start_date, end_date)


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
    return route_to_vendor("get_futures_basis", symbol, start_date, end_date)


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
    return route_to_vendor("get_futures_inventory", symbol, "", "")


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
    return route_to_vendor("get_futures_news", symbol, "", "")


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
    return route_to_vendor("get_variety_info", symbol)


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
    return route_to_vendor("get_futures_macro")


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
    return route_to_vendor("get_futures_supply_demand", symbol, "", "")


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
    return route_to_vendor("get_futures_sentiment", symbol, "", "")


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
    return route_to_vendor("get_verified_quote", symbol, date, "", "")


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
    import json

    try:
        from price_fetcher import NAME_TO_CODE, get_cached_prices
    except ImportError:
        return "ERROR: price_fetcher module not available (web_app context only)"

    code_to_name = {v: k for k, v in NAME_TO_CODE.items()}
    upper = symbol.upper().strip()
    if upper not in code_to_name:
        return f"ERROR: Unknown variety code '{symbol}'. Use uppercase codes like RB, SA, FG."

    prices = get_cached_prices([upper])
    if upper not in prices:
        return f"NO_DATA: No live price available for {upper}. Market may be closed."

    data = prices[upper]
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
    )
