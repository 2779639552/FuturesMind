from enum import Enum


# AnalystType —— 分析师类型枚举。
# 用于标识可选择的各类分析师:market(市场)、social(情绪)、news(新闻)、
# fundamentals(基本面),以及商品期货专用的 commodity_* 四类。
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


# ---------------------------------------------------------------------
# AssetType —— 资产类型枚举。
# 用于判断用户输入的 ticker 属于哪类资产,从而决定走哪条分析流程:
#   STOCK              股票(如 SPY)
#   CRYPTO             加密货币(如 BTC-USD)
#   COMMODITY_FUTURES  商品期货(如 RB 螺纹钢、I 铁矿石、M 豆粕)
# 在 cli/main.py 的 get_user_selections() 中,检测到 COMMODITY_FUTURES 时,
# 会进入「商品期货简化流程」:更精简的交互步骤 + 并行分析师图
# (见 _run_commodity_analysis,与 commodity_demo.py 逻辑一致)。
# ---------------------------------------------------------------------
class AssetType(str, Enum):
    STOCK = "stock"
    CRYPTO = "crypto"
    COMMODITY_FUTURES = "commodity_futures"
