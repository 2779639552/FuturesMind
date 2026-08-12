import logging  # 【调用包】日志输出(供应商路由失败/降级告警)

from .alpha_vantage import (  # 【调用包】Alpha Vantage 供应商实现(股票/基本面/新闻)
    get_balance_sheet as get_alpha_vantage_balance_sheet,
    get_cashflow as get_alpha_vantage_cashflow,
    get_fundamentals as get_alpha_vantage_fundamentals,
    get_global_news as get_alpha_vantage_global_news,
    get_income_statement as get_alpha_vantage_income_statement,
    get_indicator as get_alpha_vantage_indicator,
    get_insider_transactions as get_alpha_vantage_insider_transactions,
    get_news as get_alpha_vantage_news,
    get_stock as get_alpha_vantage_stock,
)
from .commodity_futures import (  # 【调用包】商品期货供应商(行情/基差/库存/新闻/宏观/情绪/核验快照)
    get_futures_basis,
    get_futures_indicators,
    get_futures_inventory,
    get_futures_macro,
    get_futures_news,
    get_futures_price,
    get_futures_sentiment,
    get_futures_supply_demand,
    get_variety_info,
    get_verified_quote,
)
from .config import get_config  # 【调用包】读取运行时配置(供应商选择/工具级覆盖)
from .errors import (  # 【调用包】供应商错误类型体系(路由层按类型分流处理)
    NoMarketDataError,
    VendorNotConfiguredError,
    VendorRateLimitError,
)
from .fred import get_macro_data as get_fred_macro_data  # 【调用包】FRED 宏观数据供应商(Fed 经济数据库)
from .polymarket import get_prediction_markets as get_polymarket_prediction_markets  # 【调用包】Polymarket 预测市场供应商(事件概率)
from .y_finance import (  # 【调用包】yfinance 供应商实现(股票数据/基本面/技术指标窗口)
    get_balance_sheet as get_yfinance_balance_sheet,
    get_cashflow as get_yfinance_cashflow,
    get_fundamentals as get_yfinance_fundamentals,
    get_income_statement as get_yfinance_income_statement,
    get_insider_transactions as get_yfinance_insider_transactions,
    get_stock_stats_indicators_window,
    get_YFin_data_online,
)
from .yfinance_news import get_global_news_yfinance, get_news_yfinance  # 【调用包】yfinance 新闻接口(全球/个股新闻)

logger = logging.getLogger(__name__)

# Tools organized by category
TOOLS_CATEGORIES = {  # 【变量】工具分类注册表:分类→(描述, 工具方法列表),供 Agent 工具清单与路由查分类用
    "core_stock_apis": {"description": "OHLCV stock price data", "tools": ["get_stock_data"]},
    "technical_indicators": {
        "description": "Technical analysis indicators",
        "tools": ["get_indicators"],
    },
    "fundamental_data": {
        "description": "Company fundamentals",
        "tools": ["get_fundamentals", "get_balance_sheet", "get_cashflow", "get_income_statement"],
    },
    "news_data": {
        "description": "News and insider data",
        "tools": [
            "get_news",
            "get_global_news",
            "get_insider_transactions",
        ],
    },
    "macro_data": {
        "description": "Macroeconomic indicators (rates, inflation, labor, growth)",
        "tools": [
            "get_macro_indicators",
        ],
    },
    "prediction_markets": {
        "description": "Market-implied probabilities for forward-looking events",
        "tools": [
            "get_prediction_markets",
        ],
    },
    # --- Commodity Futures categories ---
    "futures_price": {
        "description": "Futures OHLCV price + open interest data",
        "tools": [
            "get_futures_price",
            "get_futures_indicators",
            "get_variety_info",
        ],
    },
    "futures_basis": {
        "description": "Spot-futures basis and term structure data",
        "tools": [
            "get_futures_basis",
        ],
    },
    "futures_inventory": {
        "description": "Warehouse inventory and supply-side data",
        "tools": [
            "get_futures_inventory",
        ],
    },
    "futures_news": {
        "description": "Commodity market news and policy updates",
        "tools": [
            "get_futures_news",
        ],
    },
    "futures_macro": {
        "description": "China macroeconomic indicators (GDP, PMI, FAI, real estate, IP)",
        "tools": [
            "get_futures_macro",
        ],
    },
    "futures_supply_demand": {
        "description": "Supply-demand indicators (production, transaction volume, inventory)",
        "tools": [
            "get_futures_supply_demand",
        ],
    },
    "futures_sentiment": {
        "description": "Social media sentiment data (multi-platform: Weibo, Zhihu, XHS)",
        "tools": [
            "get_futures_sentiment",
        ],
    },
    "futures_verified": {
        "description": "Verified price snapshot — single source of truth for numeric claims",
        "tools": [
            "get_verified_quote",
        ],
    },
}

VENDOR_LIST = [
    "yfinance",
    "fred",
    "polymarket",
    "alpha_vantage",
    "commodity_futures",
]  # 【变量】支持的供应商名单(配置校验与展示用)

# Optional enrichment categories. These add macro/event context to the news
# analyst but are not core to a decision, so a vendor failure here degrades to a
# sentinel instead of aborting the run (a bad LLM-supplied indicator, a missing
# key, or a network blip should not crash an analysis over flavour data). Core
# categories (prices, fundamentals, news) still raise so a broken primary is loud.
OPTIONAL_CATEGORIES = {"macro_data", "prediction_markets"}  # 【变量】可选增强分类:供应商失败时降级为哨兵文本,不中断整次运行

# Mapping of methods to their vendor-specific implementations
VENDOR_METHODS = {  # 【变量】方法→各供应商实现函数映射表(路由分发依据)
    # core_stock_apis
    "get_stock_data": {
        "alpha_vantage": get_alpha_vantage_stock,
        "yfinance": get_YFin_data_online,
    },
    # technical_indicators
    "get_indicators": {
        "alpha_vantage": get_alpha_vantage_indicator,
        "yfinance": get_stock_stats_indicators_window,
    },
    # fundamental_data
    "get_fundamentals": {
        "alpha_vantage": get_alpha_vantage_fundamentals,
        "yfinance": get_yfinance_fundamentals,
    },
    "get_balance_sheet": {
        "alpha_vantage": get_alpha_vantage_balance_sheet,
        "yfinance": get_yfinance_balance_sheet,
    },
    "get_cashflow": {
        "alpha_vantage": get_alpha_vantage_cashflow,
        "yfinance": get_yfinance_cashflow,
    },
    "get_income_statement": {
        "alpha_vantage": get_alpha_vantage_income_statement,
        "yfinance": get_yfinance_income_statement,
    },
    # news_data
    "get_news": {
        "alpha_vantage": get_alpha_vantage_news,
        "yfinance": get_news_yfinance,
    },
    "get_global_news": {
        "yfinance": get_global_news_yfinance,
        "alpha_vantage": get_alpha_vantage_global_news,
    },
    "get_insider_transactions": {
        "alpha_vantage": get_alpha_vantage_insider_transactions,
        "yfinance": get_yfinance_insider_transactions,
    },
    # macro_data
    "get_macro_indicators": {
        "fred": get_fred_macro_data,
    },
    # prediction_markets
    "get_prediction_markets": {
        "polymarket": get_polymarket_prediction_markets,
    },
    # --- Commodity futures methods ---
    "get_futures_price": {
        "commodity_futures": get_futures_price,
    },
    "get_futures_indicators": {
        "commodity_futures": get_futures_indicators,
    },
    "get_futures_basis": {
        "commodity_futures": get_futures_basis,
    },
    "get_futures_inventory": {
        "commodity_futures": get_futures_inventory,
    },
    "get_futures_news": {
        "commodity_futures": get_futures_news,
    },
    "get_variety_info": {
        "commodity_futures": get_variety_info,
    },
    "get_futures_macro": {
        "commodity_futures": get_futures_macro,
    },
    "get_futures_supply_demand": {
        "commodity_futures": get_futures_supply_demand,
    },
    "get_futures_sentiment": {
        "commodity_futures": get_futures_sentiment,
    },
    "get_verified_quote": {
        "commodity_futures": get_verified_quote,
    },
}


# 【功能】根据方法名返回其所属的工具分类。
# 【参数】method: 数据方法名(如 "get_futures_price")。
# 【返回】分类名(TOOLS_CATEGORIES 的键)。
# 【关键】遍历 TOOLS_CATEGORIES 查找包含该方法的 tools 列表;找不到则抛 ValueError。
def get_category_for_method(method: str) -> str:
    """Get the category that contains the specified method."""
    for category, info in TOOLS_CATEGORIES.items():
        if method in info["tools"]:
            return category
    raise ValueError(f"Method '{method}' not found in any category")


# 【功能】获取某分类或某具体方法当前配置的供应商名。
# 【参数】category: 数据分类名;method: 具体方法名(可选)。
# 【返回】供应商名字符串(如 "yfinance")或 "default" 哨兵(表示未显式配置)。
# 【关键】方法级配置 tool_vendors 优先于分类级 data_vendors;两者都未配置时回 "default"。
def get_vendor(category: str, method: str = None) -> str:
    """Get the configured vendor for a data category or specific tool method.
    Tool-level configuration takes precedence over category-level.
    """
    config = get_config()  # 【调用函数】取当前配置快照(含 tool_vendors/data_vendors)

    # Check tool-level configuration first (if method provided)
    if method:
        tool_vendors = config.get("tool_vendors", {})
        if method in tool_vendors:
            return tool_vendors[method]

    # Fall back to category-level configuration
    return config.get("data_vendors", {}).get(category, "default")


# 【功能】把数据方法调用按配置路由到指定供应商实现,支持多供应商降级链。
# 【参数】method: 方法名(如 "get_futures_price");*args/**kwargs: 透传给具体供应商实现。
# 【返回】供应商返回的数据;全部供应商失败时按规则抛错或返回哨兵文本。
# 【关键】1) 供应商链只含用户显式配置且实际可用的供应商,不静默回退到未配置者;
#        2) 按错误类型分流:限流/未配置→跳过尝试下一个;无数据→记 last_no_data;
#           其它异常→记 first_error;
#        3) 无数据时返回明确的 NO_DATA_AVAILABLE 哨兵(含具体原因),不让 Agent 编造数值;
#        4) 可选分类(OPTIONAL_CATEGORIES)失败降级为 DATA_UNAVAILABLE,不抛异常。
def route_to_vendor(method: str, *args, **kwargs):
    """Route method calls to appropriate vendor implementation with fallback support."""
    category = get_category_for_method(method)  # 【调用函数】查方法所属分类(决定后续可选降级)
    vendor_config = get_vendor(category, method)  # 【调用函数】取该方法的供应商配置(方法级优先于分类级)
    primary_vendors = [v.strip() for v in vendor_config.split(",")]

    if method not in VENDOR_METHODS:
        raise ValueError(f"Method '{method}' not supported")

    all_available_vendors = list(VENDOR_METHODS[method].keys())

    # The configured vendor list IS the chain: we do NOT silently fall back to
    # vendors the user did not choose (#988/#289) — that returned data from an
    # unexpected source and caused cross-vendor inconsistencies. For multi-vendor
    # fallback, list them in order, e.g. data_vendors="yfinance,alpha_vantage".
    # The "default" sentinel (no explicit config) uses all available vendors.
    explicit = [v for v in primary_vendors if v and v != "default"]
    if explicit:
        vendor_chain = [v for v in explicit if v in VENDOR_METHODS[method]]
        if not vendor_chain:
            raise ValueError(
                f"Configured vendor(s) {explicit} not available for '{method}'. "
                f"Available: {all_available_vendors}."
            )
    else:
        vendor_chain = all_available_vendors

    last_no_data: NoMarketDataError | None = None
    first_error: Exception | None = None
    for vendor in vendor_chain:
        vendor_impl = VENDOR_METHODS[method][vendor]
        impl_func = vendor_impl[0] if isinstance(vendor_impl, list) else vendor_impl

        try:
            return impl_func(*args, **kwargs)  # 【调用函数】调用具体供应商实现(参数透传;可能抛供应商错误)
        except VendorRateLimitError:
            logger.warning("Vendor %r rate-limited for %s; trying next vendor.", vendor, method)
            continue
        except VendorNotConfiguredError as e:
            logger.warning("Vendor %r not configured for %s; trying next vendor.", vendor, method)
            if first_error is None:
                first_error = e  # Surface it if no other vendor can serve the call.
            continue
        except NoMarketDataError as e:
            last_no_data = e  # No data here; another configured vendor may have it
            continue
        except Exception as e:
            # Don't let one vendor's failure crash the call when another can
            # serve it, but never swallow silently: a broken primary must be
            # visible in the logs (#989), not hidden behind a fallback's verdict.
            logger.warning("Vendor %r failed for %s: %s", vendor, method, e)
            if first_error is None:
                first_error = e
            continue

    # If any vendor reported "no data", the symbol is genuinely unavailable.
    # Return one explicit, instructive sentinel rather than a vendor-specific
    # empty string, so the agent reports "unavailable" instead of inventing a
    # value. This takes precedence over incidental fallback errors.
    if last_no_data is not None:
        if first_error is not None:
            # A vendor also hit a real error; surface it in logs so the no-data
            # verdict can't hide a broken primary (network/auth/etc.).
            logger.warning(
                "Returning NO_DATA for %s, but a vendor errored earlier: %s",
                method,
                first_error,
            )
        sym = last_no_data.symbol
        canonical = last_no_data.canonical
        resolved = "" if canonical == sym else f" (resolved to '{canonical}')"
        # Surface the typed error's detail (e.g. "latest row is 2025-06-11 ...
        # stale") so the agent sees the specific reason — invalid symbol, no
        # coverage, or stale data — not just a generic "unavailable".
        reason = f" ({last_no_data.detail})" if last_no_data.detail else ""
        return (
            f"NO_DATA_AVAILABLE: No usable market data for '{sym}'{resolved} from "
            f"any configured vendor{reason}. The symbol may be invalid, delisted, "
            f"not covered, or the vendor returned stale data. Do not estimate or "
            f"fabricate values — report that data is unavailable for this symbol."
        )

    # No vendor returned data and none reported clean "no data" — surface the
    # first real error (e.g. the primary vendor's network failure). Optional
    # enrichment categories degrade to a sentinel instead, so flavour data can't
    # abort the run.
    if first_error is not None:
        if category in OPTIONAL_CATEGORIES:
            logger.warning("Optional %s unavailable for %s: %s", category, method, first_error)
            return (
                f"DATA_UNAVAILABLE: optional {category} could not be retrieved "
                f"({first_error}). Proceed without it; do not fabricate values."
            )
        raise first_error

    raise RuntimeError(f"No available vendor for '{method}'")
