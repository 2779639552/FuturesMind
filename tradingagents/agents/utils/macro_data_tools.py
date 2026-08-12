from typing import Annotated  # 【调用包】类型注解:给工具参数附加描述,供 LangChain 生成工具说明

from langchain_core.tools import tool  # 【调用包】LangChain 工具装饰器:把普通函数注册为 Agent 可调用的 Tool

from tradingagents.dataflows.interface import route_to_vendor  # 【调用包】路由函数:按方法名把取数请求分派到配置的数据供应商


# 【功能】从 FRED 获取一条宏观经济指标时间序列(政策利率/国债收益率/通胀/就业/增长)。
# 【参数】indicator: 友好别名(如 'cpi')或原始 FRED 系列 ID(如 'CPIAUCSL');
#        curr_date: 窗口末端日期 yyyy-mm-dd;look_back_days: 回溯窗口天数,省略=一年。
# 【返回】格式化 markdown 报告:序列标题/单位/频率/最新值/窗口变化/最近观测表。
# 【关键】纯转发给 route_to_vendor("get_macro_indicators", ...)。
@tool
def get_macro_indicators(
    indicator: Annotated[
        str,
        "Macro indicator: a friendly alias such as 'cpi', 'core_pce', "
        "'unemployment', 'fed_funds_rate', '10y_treasury', 'yield_curve', "
        "'real_gdp', 'vix', or a raw FRED series ID such as 'CPIAUCSL'.",
    ],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format; the end of the window"],
    look_back_days: Annotated[
        int | None, "Trailing window length in days; omit for a 1-year window"
    ] = None,
) -> str:
    """
    Retrieve a macroeconomic indicator time series from FRED (Federal Reserve
    Economic Data): policy rates, Treasury yields, inflation, labor, and growth.
    Returns the series title, units, frequency, the latest value, the change
    over the window, and a recent observation table. Uses the configured
    macro_data vendor.

    Args:
        indicator (str): Friendly alias or raw FRED series ID
        curr_date (str): Current date in yyyy-mm-dd format
        look_back_days (int): Trailing window length; omit for a 1-year window

    Returns:
        str: A formatted markdown report of the macro series
    """
    return route_to_vendor("get_macro_indicators", indicator, curr_date, look_back_days)  # 【调用函数】跨模块路由:按方法名分派到配置的宏观数据供应商
