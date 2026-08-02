from enum import Enum


class AnalystType(str, Enum):
    MARKET = "market"
    # Wire value stays "social" for saved-config and string-keyed-caller
    # back-compat; the user-facing label is "Sentiment Analyst".
    SOCIAL = "social"
    NEWS = "news"
    FUNDAMENTALS = "fundamentals"
    # Commodity futures analysts
    COMMODITY_TECHNICAL = "commodity_technical"
    COMMODITY_FUNDAMENTAL = "commodity_fundamental"
    COMMODITY_MACRO = "commodity_macro"
    COMMODITY_SENTIMENT = "commodity_sentiment"


class AssetType(str, Enum):
    STOCK = "stock"
    CRYPTO = "crypto"
    COMMODITY_FUTURES = "commodity_futures"
