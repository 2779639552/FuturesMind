import functools  # 【调用包】lru_cache:缓存品种身份解析结果(每进程每标的至多查一次网络)
import logging  # 【调用包】日志:记录身份解析失败等调试信息
from collections.abc import Mapping  # 【调用包】只读映射类型:身份字典/状态字典的参数注解
from typing import Any  # 【调用包】动态类型注解

import yfinance as yf  # 【调用包】Yahoo 财经行情:拉取标的身份元数据(公司名/行业/交易所等)
from langchain_core.messages import HumanMessage, RemoveMessage  # 【调用包】LangChain 消息:构造上下文占位消息与删除旧消息操作

# Import tools from separate utility files
from tradingagents.agents.utils.core_stock_tools import get_stock_data  # 【调用包】股票行情工具(重导出给代理/图统一使用)
from tradingagents.agents.utils.fundamental_data_tools import (  # 【调用包】基本面数据工具(资产负债表/现金流/财务指标/利润表)
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_income_statement,
)
from tradingagents.agents.utils.macro_data_tools import get_macro_indicators  # 【调用包】宏观数据工具(FRED 指标)
from tradingagents.agents.utils.market_data_validation_tools import get_verified_market_snapshot  # 【调用包】行情核验工具(确定性快照)
from tradingagents.agents.utils.news_data_tools import (  # 【调用包】新闻数据工具(个股新闻/全球新闻/内部人交易)
    get_global_news,
    get_insider_transactions,
    get_news,
)
from tradingagents.agents.utils.prediction_markets_tools import get_prediction_markets  # 【调用包】预测市场工具(Polymarket)
from tradingagents.agents.utils.technical_indicators_tools import get_indicators  # 【调用包】技术指标工具

# Public surface: the data tools are imported here so agents and the graph
# import them from one place, plus the instrument/language helpers defined below.
__all__ = [  # 【变量】公开导出清单:数据工具 + 标的/语言辅助函数,供代理与图从一处导入
    "get_stock_data",
    "get_indicators",
    "get_fundamentals",
    "get_balance_sheet",
    "get_cashflow",
    "get_income_statement",
    "get_news",
    "get_global_news",
    "get_insider_transactions",
    "get_macro_indicators",
    "get_prediction_markets",
    "get_verified_market_snapshot",
    "build_instrument_context",
    "resolve_instrument_identity",
    "get_instrument_context_from_state",
    "get_language_instruction",
    "create_msg_delete",
]

logger = logging.getLogger(__name__)  # 【变量】模块级日志器


# 【功能】返回按配置语言写报告的提示指令;英语(默认)时返回空串以省 token。
# 【返回】配置为非英语时返回 " Write your entire response in {lang}.";英语返回 ""。
# 【关键】作用于所有输出会进入保存报告/记忆日志的代理,保证整篇报告单一语言。
def get_language_instruction() -> str:
    """Return a prompt instruction for the configured output language.

    Returns empty string when English (default), so no extra tokens are used.
    Applied to every agent whose output reaches the saved report —
    analysts, researchers, debaters, research manager, trader, and
    portfolio manager — so a non-English run produces a fully localized
    report rather than a mix of languages.
    """
    from tradingagents.dataflows.config import get_config  # 【调用包】运行时配置:读取 output_language

    lang = get_config().get("output_language", "English")  # 【变量】配置的输出语言(默认 English)
    if lang.strip().lower() == "english":
        return ""
    return f" Write your entire response in {lang}."


# 【功能】清理身份元数据值:去首尾空白,空串/占位值(如 "N/A")返回 None。
# 【参数】value: 原始值(可能非字符串)。
# 【返回】trim 后的字符串;非字符串或占位值时返回 None。
def _clean_identity_value(value: Any) -> str | None:
    """Return a trimmed string, or None for empty / placeholder-ish values."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.lower() in {"none", "n/a", "nan", "null"}:
        return None
    return cleaned


# 【功能】解析标的的确定性身份元数据(公司名/行业/交易所/quote_type),防止模型凭图形幻觉出错误公司。
# 【参数】ticker: 标的代码(如 AAPL / XAUUSD)。
# 【返回】身份字典;yfinance 不可用/未识别时返回 {} 由调用方退化为仅代码上下文。
# 【关键】尽力而为 + 缓存(lru_cache 256):每进程每标的至多查一次;先 normalize_symbol 使身份与价格路径取同一标的(#983)。
@functools.lru_cache(maxsize=256)
def resolve_instrument_identity(ticker: str) -> dict:
    """Resolve deterministic identity metadata (company name, sector, …) for a ticker.

    This exists to stop the pipeline from hallucinating a *different* company
    when a chart pattern suggests a different industry than the real one
    (#814): without a ground-truth name, the market analyst would pattern-match
    the price action to a narrative and invent an identity that then cascaded
    through every downstream agent.

    Best-effort by design: if yfinance is unavailable, rate-limited, or doesn't
    recognise the ticker, we return ``{}`` and the caller falls back to
    ticker-only context rather than failing before analysis starts. Cached so
    the lookup happens at most once per ticker per process.

    The symbol is normalized first (e.g. ``XAUUSD`` -> ``GC=F``) so identity
    resolves for the same instrument the price path actually fetches (#983).
    """
    from tradingagents.dataflows.symbol_utils import normalize_symbol  # 【调用包】符号归一化(如 XAUUSD→GC=F)

    try:
        info = yf.Ticker(normalize_symbol(ticker)).info or {}  # 【调用函数】Yahoo 财经:拉取标的身份信息(限流/网络失败则异常)
    except Exception as exc:  # noqa: BLE001 — fail open, never block the run
        logger.debug("Could not resolve instrument identity for %s: %s", ticker, exc)
        return {}

    identity: dict[str, str] = {}  # 【变量】解析出的身份元数据字典(company_name/sector/industry/exchange/quote_type)
    company_name = _clean_identity_value(info.get("longName")) or _clean_identity_value(  # 【变量】公司全名(缺省回退短名)
        info.get("shortName")
    )
    if company_name:
        identity["company_name"] = company_name
    for source_key, target_key in (  # 把 yfinance 键名映射为系统内统一键名
        ("sector", "sector"),
        ("industry", "industry"),
        ("exchange", "exchange"),
        ("quoteType", "quote_type"),
    ):
        value = _clean_identity_value(info.get(source_key))
        if value:
            identity[target_key] = value
    return identity


# 【功能】构造"标的上下文"提示文本,让代理锚定真实标的与代码,防止图形→错误公司。
# 【参数】ticker: 标的代码;asset_type: 资产类型(默认 stock);identity: 确定性身份字典(可省略)。
# 【返回】提示上下文字符串(含精确代码与已解析身份,加密资产时额外注明)。
# 【关键】身份来自 resolve_instrument_identity;无身份时退化为纯代码提示,仍要求保留交易所后缀。
def build_instrument_context(
    ticker: str,
    asset_type: str = "stock",
    identity: Mapping[str, str] | None = None,
) -> str:
    """Describe the exact instrument so agents preserve identity and ticker.

    When ``identity`` is provided (resolved deterministically via
    :func:`resolve_instrument_identity`), the company name and business
    classification are injected so agents anchor to the real company rather
    than pattern-matching the price chart to a wrong one (#814).
    """
    is_crypto = asset_type == "crypto"  # 【变量】是否加密货币资产(影响措辞与基本面可用性)
    instrument_label = "asset" if is_crypto else "instrument"  # 【变量】标的称呼:加密货币叫 asset,否则叫 instrument
    context = (
        f"The {instrument_label} to analyze is `{ticker}`. "
        "Use this exact ticker in every tool call, report, and recommendation, "
        "preserving any exchange suffix (e.g. `.TO`, `.L`, `.HK`, `.T`, `-USD`)."
    )

    details = []  # 【变量】已解析身份的可读细节列表,注入提示词
    if identity:
        name = identity.get("company_name") or identity.get("name")
        if name:
            details.append(f"{'Name' if is_crypto else 'Company'}: {name}")
        sector, industry = identity.get("sector"), identity.get("industry")
        if sector and industry:
            details.append(f"Business classification: {sector} / {industry}")
        elif sector:
            details.append(f"Sector: {sector}")
        elif industry:
            details.append(f"Industry: {industry}")
        if identity.get("exchange"):
            details.append(f"Exchange: {identity['exchange']}")

    if details:
        context += (
            f" Resolved identity: {'; '.join(details)}. "
            "Do not substitute a different company or ticker unless a tool "
            "result explicitly disproves this resolved identity."
        )

    if is_crypto:
        context += (
            " Treat it as a crypto asset rather than a company, and do not "
            "assume company fundamentals are available."
        )
    return context


# 【功能】返回当前运行用的标的上下文:优先用运行开始时算好并存在状态上的身份上下文。
# 【参数】state: 图状态(可含 instrument_context / company_of_interest / asset_type)。
# 【返回】上下文字符串;状态无身份上下文时退化为纯代码上下文(不联网)。
# 【关键】保证下游代理中途无需再发起 yfinance 网络查询(裸程序化状态/测试也安全)。
def get_instrument_context_from_state(state: Mapping[str, Any]) -> str:
    """Return the instrument context for the current run.

    Prefers the identity-resolved context computed once at run start and
    stored on the state (see ``TradingAgentsGraph.resolve_instrument_context``).
    Falls back to a ticker-only context — with no network lookup — when the
    state was constructed without it (bare programmatic states, tests), so a
    consumer is never forced to make a yfinance call mid-graph.
    """
    context = state.get("instrument_context")  # 【变量】运行开始时解析的标的上下文(可能缺失)
    if isinstance(context, str) and context.strip():
        return context
    return build_instrument_context(
        str(state["company_of_interest"]),
        state.get("asset_type", "stock"),
    )


# 【功能】创建"清空消息并加占位"的节点工厂(供 LangGraph 在阶段间裁剪上下文)。
# 【返回】delete_messages 节点函数。
def create_msg_delete():
    # 【功能】删除全部旧消息并插入一个"锚定到标的与日期"的占位消息。
    # 【参数】state: 图状态(含 messages / instrument_context / trade_date)。
    # 【返回】{"messages": [RemoveMessage...] + [占位 HumanMessage]}。
    # 【关键】占位不能是裸 "Continue":部分 OpenAI 兼容 provider 会把 Continue 当真任务而跑题(#888)。
    def delete_messages(state):
        """Clear messages and add a context-anchored placeholder.

        The placeholder must not be a bare ``"Continue"``: some
        OpenAI-compatible providers interpret that literally as the user task
        and produce output about the word "continue" instead of analysing the
        instrument (#888). Anchoring it to the resolved instrument context and
        date keeps the next analyst on-task even if the provider treats the
        placeholder as a standalone request.
        """
        messages = state["messages"]  # 【变量】当前状态的消息列表
        removal_operations = [RemoveMessage(id=m.id) for m in messages]  # 【调用函数】为每条旧消息构造删除操作(LangGraph 上下文清理)

        instrument_context = get_instrument_context_from_state(state)
        trade_date = state.get("trade_date", "the requested date")  # 【变量】分析日期(缺省为占位文案)
        placeholder = HumanMessage(  # 【调用函数】构造上下文锚定占位消息,防止模型把 'Continue' 当真任务
            content=(
                f"Proceed with your assigned analysis for this workflow. "
                f"{instrument_context} The analysis date is {trade_date}."
            )
        )
        return {"messages": removal_operations + [placeholder]}

    return delete_messages
