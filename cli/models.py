from enum import Enum  # 【调用包】定义字符串枚举(str, Enum),成员值即 wire 字符串


# AnalystType —— 分析师类型枚举。
# 用于标识可选择的各类分析师:market(市场)、social(情绪)、news(新闻)、
# fundamentals(基本面),以及商品期货专用的 commodity_* 四类。
class AnalystType(str, Enum):
    MARKET = "market"  # 【变量】市场分析师
    # Wire value stays "social" for saved-config and string-keyed-caller
    # back-compat; the user-facing label is "Sentiment Analyst".
    SOCIAL = "social"  # 【变量】情绪分析师(wire 值保持 "social" 以兼容旧配置/调用方)
    NEWS = "news"  # 【变量】新闻分析师
    FUNDAMENTALS = "fundamentals"  # 【变量】基本面分析师
    # Commodity futures analysts
    COMMODITY_TECHNICAL = "commodity_technical"  # 【变量】商品期货技术面分析师
    COMMODITY_FUNDAMENTAL = "commodity_fundamental"  # 【变量】商品期货基本面分析师
    COMMODITY_MACRO = "commodity_macro"  # 【变量】商品期货宏观/新闻分析师
    COMMODITY_SENTIMENT = "commodity_sentiment"  # 【变量】商品期货情绪分析师


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
    STOCK = "stock"  # 【变量】普通股票
    CRYPTO = "crypto"  # 【变量】加密货币
    COMMODITY_FUTURES = "commodity_futures"  # 【变量】商品期货(走精简并行分析图)
