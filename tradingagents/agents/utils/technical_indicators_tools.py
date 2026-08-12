from typing import Annotated  # 【调用包】类型注解:给工具参数附加描述,供 LangChain 生成工具说明

from langchain_core.tools import tool  # 【调用包】LangChain 工具装饰器:把普通函数注册为 Agent 可调用的 Tool

from tradingagents.dataflows.interface import route_to_vendor  # 【调用包】路由函数:按方法名把取数请求分派到配置的数据供应商


# 【功能】获取单个(或逗号分隔的多个)技术指标的分析报告。
# 【参数】symbol: 股票代码;indicator: 指标名(如 'rsi'/'macd'),可逗号分隔多个;
#        curr_date: 交易日 yyyy-mm-dd;look_back_days: 回溯天数,默认 30。
# 【返回】各指标报告以空行拼接的文本;未知指标返回其错误信息而非中断。
# 【关键】LLM 常把多个指标写成逗号分隔字符串,这里拆开逐个调用并合并结果。
@tool
def get_indicators(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to get the analysis and report of"],
    curr_date: Annotated[str, "The current trading date you are trading on, YYYY-mm-dd"],
    look_back_days: Annotated[int, "how many days to look back"] = 30,
) -> str:
    """
    Retrieve a single technical indicator for a given ticker symbol.
    Uses the configured technical_indicators vendor.
    Args:
        symbol (str): Ticker symbol of the company, e.g. AAPL, TSM
        indicator (str): A single technical indicator name, e.g. 'rsi', 'macd'. Call this tool once per indicator.
        curr_date (str): The current trading date you are trading on, YYYY-mm-dd
        look_back_days (int): How many days to look back, default is 30
    Returns:
        str: A formatted dataframe containing the technical indicators for the specified ticker symbol and indicator.
    """
    # LLMs sometimes pass multiple indicators as a comma-separated string;
    # split and process each individually.
    indicators = [i.strip().lower() for i in indicator.split(",") if i.strip()]  # 【变量】把逗号分隔的指标串拆成单个指标列表(LLM 常一次传多个)
    results = []  # 【变量】各指标的结果片段列表,最后用空行拼接
    for ind in indicators:
        try:
            results.append(
                route_to_vendor("get_indicators", symbol, ind, curr_date, look_back_days)  # 【调用函数】跨模块路由:逐个指标分派到配置的技术指标供应商
            )
        except ValueError as e:
            results.append(str(e))  # 【变量】未知指标的错误信息原样入列,不让单个失败中断整次调用
    return "\n\n".join(results)
