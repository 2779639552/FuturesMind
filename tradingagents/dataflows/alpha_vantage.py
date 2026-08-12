# Aggregates the per-category Alpha Vantage implementations into one module the
# vendor router imports from; the imports below are the public surface.
# 【调用包】把各分类 Alpha Vantage 实现汇总成一个模块, 供厂商路由层导入; 下列导入即公共面。
from .alpha_vantage_fundamentals import (
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_income_statement,
)  # 【调用包】基本面(概览/资产负债表/现金流/利润表)
from .alpha_vantage_indicator import get_indicator  # 【调用包】技术指标
from .alpha_vantage_news import get_global_news, get_insider_transactions, get_news  # 【调用包】新闻情绪/内部人交易
from .alpha_vantage_stock import get_stock  # 【调用包】日线行情


# 【变量】对外导出的 Alpha Vantage 函数清单
__all__ = [
    "get_balance_sheet",
    "get_cashflow",
    "get_fundamentals",
    "get_income_statement",
    "get_indicator",
    "get_global_news",
    "get_insider_transactions",
    "get_news",
    "get_stock",
]
