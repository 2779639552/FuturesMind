# TradingAgents/graph/trading_graph.py

# =============================================================================
# 本文件在整个项目中的角色 —— 主编排器 (Orchestrator)
# -----------------------------------------------------------------------------
# 这是整个股票分析框架的"总指挥"类 TradingAgentsGraph 所在文件。它不做具体
# 的智能体工作, 而是把大量零件组装起来并驱动整条流水线运行:
#
#   1. 组装阶段 (__init__):
#      - 读配置 → 通过 LLM 工厂 (llm_clients/factory.py) 创建快/慢两套 LLM;
#      - 创建记忆日志 (TradingMemoryLog)、工具节点 (ToolNode)、条件路由
#        (ConditionalLogic)、图搭建器 (GraphSetup)、传播器 (Propagator)、
#        反思器 (Reflector)、信号处理器 (SignalProcessor);
#      - 最后调用 setup.py 的 setup_graph() 建图并 compile(), 得到可运行 graph。
#
#   2. 运行阶段 (propagate → _run_graph):
#      - 先解析本 ticker 之前"待定"的决策结果 (实现延迟反思);
#      - 初始化状态 (Propagator.create_initial_state) → 调用 graph.invoke()
#        或 graph.stream() 让整张 StateGraph 按节点顺序跑完;
#      - 把最终状态落盘 (JSON 日志 + 记忆日志), 提取最终交易信号。
#
#   与 LLM 工厂的分工: 本文件通过 create_llm_client() 拿到 LLM, 但完全不关心
#   底层到底是 OpenAI / Anthropic / Google 中的哪一家——换厂商只改配置,
#   不改本文件的任何业务代码, 这正是"LLM 多厂商适配"解耦的关键。
#
#   与商品/加密路径的关系: 本类是"股票路径" (asset_type="stock") 的主入口;
#   商品期货路径由根目录 commodity_demo.py 驱动, 不使用本类。
# =============================================================================

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yfinance as yf
from langgraph.prebuilt import ToolNode

# Import the abstract tool methods from agent_utils
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_global_news,
    get_income_statement,
    get_indicators,
    get_insider_transactions,
    get_macro_indicators,
    get_news,
    get_prediction_markets,
    get_stock_data,
    get_verified_market_snapshot,
    resolve_instrument_identity,
)
from tradingagents.agents.utils.memory import TradingMemoryLog
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.utils import safe_ticker_component
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.llm_clients import create_llm_client
from tradingagents.reporting import write_report_tree

from .checkpointer import checkpoint_step, clear_checkpoint, get_checkpointer, thread_id
from .conditional_logic import ConditionalLogic
from .propagation import Propagator
from .reflection import Reflector
from .setup import GraphSetup
from .signal_processing import SignalProcessor

logger = logging.getLogger(__name__)


def _coerce_max_retries(value):
    """Validate an ``llm_max_retries`` value to a non-negative int.

    Accepts an int or a numeric string (env vars arrive as strings). Rejects
    booleans and negatives loudly so a misconfiguration fails at startup rather
    than silently disabling retries.

    【中文说明】
    【功能】把配置里的 llm_max_retries 值"规范化"成合法的非负整数。
    【参数】value: 配置文件或环境变量传来的原始值, 可能是 int 或数字字符串
        (环境变量都是字符串, 例如 "2")。
    【返回】合法的非负 int。
    【关键逻辑】三段防御:
        1) 先拒绝 bool —— 因为 True/False 在 Python 里能当 0/1 用, 直接 int()
           不会报错, 但语义完全错误, 所以显式抛异常;
        2) 用 int(value) 把字符串/数字统一转成 int, 转不动就抛 ValueError;
        3) 负数 (重试次数不可能为负) 也抛异常, 让配置错误在启动时立刻暴露,
           而不是静默地让重试机制失效。
    """
    if isinstance(value, bool):
        raise ValueError(f"llm_max_retries must be an integer, not a boolean: {value!r}")
    try:
        n = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"llm_max_retries must be an integer, got {value!r}") from exc
    if n < 0:
        raise ValueError(f"llm_max_retries must be >= 0, got {n}")
    return n


class TradingAgentsGraph:
    """Main class that orchestrates the trading agents framework.

    【中文说明】
    【功能】整个股票分析框架的"主编排器": 在 __init__ 中把所有组件 (LLM、工具、
    图搭建器、传播器、反思器、信号处理器) 组装好并编译出可运行的 LangGraph;
    在 propagate()/_run_graph() 中初始化状态、按节点顺序驱动整张图跑完。
    【关键逻辑】理解本类只要抓住两条主线:
        1. 组装: LLM 工厂创建快/慢两套 LLM → GraphSetup 建图 → workflow.compile();
        2. 运行: Propagator.create_initial_state() 初始化状态 →
           graph.invoke()/stream() 按图顺序运行 → 落盘 + 提取信号;
        3. 记忆闭环: 每次运行把最终决策存入 TradingMemoryLog, 下次运行该 ticker
           时先 _resolve_pending_entries() 结算上一次的收益并让 Reflector 反思。
    """

    def __init__(
        self,
        selected_analysts=("market", "social", "news", "fundamentals"),
        debug=False,
        config: dict[str, Any] = None,
        callbacks: list | None = None,
    ):
        """Initialize the trading agents graph and components.

        Args:
            selected_analysts: List of analyst types to include
            debug: Whether to run in debug mode
            config: Configuration dictionary. If None, uses default config
            callbacks: Optional list of callback handlers (e.g., for tracking LLM/tool stats)

        【中文说明】
        【功能】初始化主编排器的全部内部状态与组件, 并把图编译成可运行对象。
        【参数】
            selected_analysts: 本次要启用的分析师类型, 会传给 setup_graph 决定
                图中注册哪些分析师节点 (默认四种全开)。
            debug: True 时用 graph.stream() 逐节点流式输出, 便于调试。
            config: 配置字典 (含 llm_provider / 模型名 / 轮数上限等); 为 None
                时用默认配置 DEFAULT_CONFIG。
            callbacks: 可选回调列表, 透传给 LLM 构造器, 用于统计 LLM/工具调用。
        【关键逻辑】
            1) set_config() 把配置同步到 dataflows 模块 (数据采集层共用同一份配置);
            2) create_llm_client() 走 LLM 工厂拿到"提供商无关"的客户端, 再
               .get_llm() 得到真正可调用的 LLM 对象——deep 用于深度思考节点,
               quick 用于快速响应节点;
            3) GraphSetup.setup_graph() 建图后立刻 compile(), 得到 self.graph。
               注意 compile() 一次的结果被反复复用; 只有在开启断点续跑
               (checkpoint_enabled) 时才会在 propagate() 里用 checkpointer 重编译。
        """
        self.debug = debug
        self.config = config or DEFAULT_CONFIG
        self.callbacks = callbacks or []

        # Update the interface's config
        # 【中文说明】把配置写入 dataflows 模块的全局单例, 确保数据采集层
        # (行情、财报等工具) 与主流程使用同一份配置 (如 ticker 后缀、缓存目录)。
        set_config(self.config)

        # Create necessary directories
        # 【中文说明】确保数据缓存目录和结果目录存在, 避免后续写盘时 FileNotFoundError。
        os.makedirs(self.config["data_cache_dir"], exist_ok=True)
        os.makedirs(self.config["results_dir"], exist_ok=True)

        # Initialize LLMs with provider-specific thinking configuration
        # 【中文说明】_get_provider_kwargs() 按当前提供商 (google/openai/anthropic)
        # 收集各自的思考参数 (如 reasoning_effort), 以及跨厂商通用的温度与重试次数。
        llm_kwargs = self._get_provider_kwargs()

        # Add callbacks to kwargs if provided (passed to LLM constructor)
        if self.callbacks:
            llm_kwargs["callbacks"] = self.callbacks

        # 【关键逻辑】通过 LLM 工厂创建两个客户端。注意两个调用都只传
        # provider/model/base_url, 具体是哪家 SDK 由 factory.py 根据 provider
        # 分发——这就是"换厂商不改业务代码"的实现点。
        deep_client = create_llm_client(
            provider=self.config["llm_provider"],
            model=self.config["deep_think_llm"],
            base_url=self.config.get("backend_url"),
            **llm_kwargs,
        )
        quick_client = create_llm_client(
            provider=self.config["llm_provider"],
            model=self.config["quick_think_llm"],
            base_url=self.config.get("backend_url"),
            **llm_kwargs,
        )

        # 【中文说明】get_llm() 返回真正可调用的底层 LLM 对象 (LangChain 兼容)。
        # deep_thinking_llm 给研究经理/组合经理等"要深思熟虑"的节点,
        # quick_thinking_llm 给分析师/研究员/交易员等"要快速响应"的节点。
        self.deep_thinking_llm = deep_client.get_llm()
        self.quick_thinking_llm = quick_client.get_llm()

        self.memory_log = TradingMemoryLog(self.config)

        # Create tool nodes
        # 【中文说明】_create_tool_nodes() 返回 {分析师类型: ToolNode} 的字典,
        # 每个 ToolNode 里装着该分析师可调用的真实数据工具函数 (行情/新闻/财报等)。
        self.tool_nodes = self._create_tool_nodes()

        # Initialize components
        # 【中文说明】ConditionalLogic 提供"条件路由"所需的全部判断函数
        # (should_continue_*), 参数指定多空辩论与风险辩论的最大轮数。
        self.conditional_logic = ConditionalLogic(
            max_debate_rounds=self.config["max_debate_rounds"],
            max_risk_discuss_rounds=self.config["max_risk_discuss_rounds"],
        )
        # 【中文说明】GraphSetup 负责"建图" (来自 setup.py)。这里把快/慢两套
        # LLM、工具节点、条件路由都交给它, 稍后调用它的 setup_graph() 建图。
        self.graph_setup = GraphSetup(
            self.quick_thinking_llm,
            self.deep_thinking_llm,
            self.tool_nodes,
            self.conditional_logic,
        )

        # 【中文说明】Propagator: 负责"初始化状态 + 提供图运行参数"。
        # Reflector: 负责对一次交易决策的"事后反思" (延迟到下次运行该 ticker 时做)。
        # SignalProcessor: 负责把最终决策字符串加工成结构化信号。
        self.propagator = Propagator(
            max_recur_limit=self.config.get("max_recur_limit", 100),
        )
        self.reflector = Reflector(self.quick_thinking_llm)
        self.signal_processor = SignalProcessor(self.quick_thinking_llm)

        # State tracking
        # 【中文说明】记录最近一次运行的状态/股票名; log_states_dict 用"日期"作
        # 键缓存完整状态字典, 最终由 _log_state() 落盘成 JSON。
        self.curr_state = None
        self.ticker = None
        self.log_states_dict = {}  # date to full state dict

        # Graph-shape-affecting run choices, kept for the checkpoint signature.
        # 【中文说明】把分析师选择固化成元组保存, 供断点续跑时生成"运行签名"
        # (见 _run_signature), 用于判断断点缓存是否仍然有效。
        self.selected_analysts = tuple(selected_analysts)

        # Set up the graph: keep the workflow for recompilation with a checkpointer.
        # 【关键逻辑】self.workflow = 未编译的图 (可反复 compile); self.graph =
        # 已编译可运行对象。保留 workflow 是为了在开启断点续跑时用 checkpointer
        # 重新编译 (见 propagate()), 而不需要重新搭建整张图。
        self.workflow = self.graph_setup.setup_graph(selected_analysts)
        self.graph = self.workflow.compile()
        self._checkpointer_ctx = None

    def _get_provider_kwargs(self) -> dict[str, Any]:
        """Get provider-specific kwargs for LLM client creation.

        【中文说明】
        【功能】按当前 LLM 提供商收集"思考相关"参数, 并统一处理跨厂商通用参数
        (温度、重试次数), 最后返回给 create_llm_client 作为 **kwargs。
        【参数】无 (读 self.config)。
        【返回】kwargs 字典; 没有匹配到任何提供商时会返回空字典或只含通用参数。
        【关键逻辑】
            1) 先把 llm_provider 转小写再按提供商分支:
               google → thinking_level; openai → reasoning_effort;
               anthropic → effort。这些是各 SDK 特有的"思考档位"参数,
               不能混用, 所以必须按厂商分别取;
            2) temperature 是跨厂商通用的, 只要配置了就以 float 转发——
               float() 保证环境变量字符串 "0.2" 和程序里传入的 0.2 行为一致;
            3) llm_max_retries 同样跨厂商通用, 只显式配置时才转发, 这样未配置时
               每家 SDK 保持自己的默认重试次数 (通常是 2) (issue #1091);
            4) llm_max_retries 先经 _coerce_max_retries 规范化, 防止脏配置。
        """
        kwargs = {}
        provider = self.config.get("llm_provider", "").lower()

        if provider == "google":
            thinking_level = self.config.get("google_thinking_level")
            if thinking_level:
                kwargs["thinking_level"] = thinking_level

        elif provider == "openai":
            reasoning_effort = self.config.get("openai_reasoning_effort")
            if reasoning_effort:
                kwargs["reasoning_effort"] = reasoning_effort

        elif provider == "anthropic":
            effort = self.config.get("anthropic_effort")
            if effort:
                kwargs["effort"] = effort

        # Sampling temperature is cross-provider: forward it whenever set.
        # float() here so a value coming from a TRADINGAGENTS_TEMPERATURE env
        # string ("0.2") works the same as a programmatic float.
        temperature = self.config.get("temperature")
        if temperature is not None and temperature != "":
            kwargs["temperature"] = float(temperature)

        # SDK retry budget is cross-provider. Forward it only when explicitly set
        # so each provider keeps its own default (usually 2) otherwise (#1091).
        max_retries = self.config.get("llm_max_retries")
        if max_retries is not None and max_retries != "":
            kwargs["max_retries"] = _coerce_max_retries(max_retries)

        return kwargs

    def _create_tool_nodes(self) -> dict[str, ToolNode]:
        """Create tool nodes for different data sources using abstract methods.

        【中文说明】
        【功能】为每位分析师类型准备一个 LangGraph 的 ToolNode (工具节点)。
        【参数】无 (只读 self.config, 实际这里未使用)。
        【返回】字典 {分析师类型: ToolNode}, 键与 GraphSetup 里 analyst_factories
            的键一一对应 (market/social/news/fundamentals)。
        【关键逻辑】ToolNode 是 LangGraph 预置节点: 当分析师 (LLM) 在回复里请求
            调用工具时, 图会自动转到这里执行真实函数并把结果回传给 LLM。每个
            分析师只拿到与自身职责相关的工具子集 (如 fundamentals 只给财报类工具),
            避免 LLM 乱调不相关的工具。
        """
        return {
            "market": ToolNode(
                [
                    # Core stock data tools
                    get_stock_data,
                    # Technical indicators
                    get_indicators,
                    # Deterministic verification snapshot (bound to the analyst
                    # LLM and required by its prompt; must be executable here or
                    # the call fails and the model reports it "unavailable").
                    get_verified_market_snapshot,
                ]
            ),
            "social": ToolNode(
                [
                    # News tools for social media analysis
                    get_news,
                ]
            ),
            "news": ToolNode(
                [
                    # News and insider information
                    get_news,
                    get_global_news,
                    get_insider_transactions,
                    get_macro_indicators,
                    get_prediction_markets,
                ]
            ),
            "fundamentals": ToolNode(
                [
                    # Fundamental analysis tools
                    get_fundamentals,
                    get_balance_sheet,
                    get_cashflow,
                    get_income_statement,
                ]
            ),
        }

    def _resolve_benchmark(self, ticker: str) -> str:
        """Pick the benchmark ticker for alpha calculation against ``ticker``.

        ``config["benchmark_ticker"]`` overrides everything when set; otherwise
        the suffix map matches the ticker's exchange suffix (e.g. ``.T`` for
        Tokyo). US-listed tickers without a dotted suffix fall through to the
        empty-suffix entry (SPY by default). Unrecognised suffixes (including
        US tickers with dots like ``BRK.B``) also fall back to the empty-suffix
        entry, which is the right default because the alpha calculation works
        in USD.

        【中文说明】
        【功能】为给定的股票 ticker 挑选一个"基准指数 ticker", 用于计算超额
        收益 (alpha = 个股收益 − 基准收益)。
        【参数】ticker: 个股代码, 如 "AAPL" 或 "7203.T"。
        【返回】基准 ticker 字符串 (默认 SPY)。
        【关键逻辑】优先级从高到低:
            1) config["benchmark_ticker"] 显式指定则一票否决;
            2) 否则按 config["benchmark_map"] 里的"后缀 → 基准"映射匹配 ticker
               的市场后缀 (如 .T → 东京市场对应基准), 用来给非美股选当地指数;
            3) 都不匹配就回落 benchmark_map[""] 的空后缀条目 (默认 SPY)。之所以
               空后缀默认 SPY 是对的, 是因为 alpha 计算以美元计价。
        """
        explicit = self.config.get("benchmark_ticker")
        if explicit:
            return explicit
        benchmark_map = self.config.get("benchmark_map", {})
        ticker_upper = ticker.upper()
        for suffix, benchmark in benchmark_map.items():
            if suffix and ticker_upper.endswith(suffix.upper()):
                return benchmark
        return benchmark_map.get("", "SPY")

    def _fetch_returns(
        self,
        ticker: str,
        trade_date: str,
        holding_days: int = 5,
        benchmark: str = "SPY",
    ) -> tuple[float | None, float | None, int | None]:
        """Fetch raw and alpha return for ticker over holding_days from trade_date.

        ``benchmark`` is the index used as the alpha baseline (resolved by the
        caller via ``_resolve_benchmark``). Returns ``(raw_return, alpha_return,
        actual_holding_days)`` or ``(None, None, None)`` if price data is
        unavailable (too recent, delisted, or network error).

        【中文说明】
        【功能】从 yfinance 拉取个股与基准指数在 [trade_date, trade_date+持有期]
        的日线数据, 计算持有期内的【原始收益】与相对基准的【超额收益 (alpha)】。
        【参数】
            ticker: 个股代码; trade_date: 买入日 (YYYY-MM-DD);
            holding_days: 名义持有天数 (默认 5); benchmark: 基准指数代码。
        【返回】三元组 (raw_return, alpha_return, actual_holding_days);
            若数据不足或拉取失败返回 (None, None, None) (调用方会跳过, 下次再试)。
        【关键逻辑】
            1) 结束日比开始日多留 7 天"缓冲", 吸收周末/节假日导致的停盘;
            2) normalize_symbol() 把代码归一化到与分析时同一标的 (如
               XAUUSD → GC=F), 保证"事后结算"查到的价格与分析时一致 (issue #984);
            3) actual_days = min(holding_days, 个股长度-1, 基准长度-1), 防止越界;
            4) 收益用 (结束价−起始价)/起始价; alpha = 个股收益 − 基准收益;
            5) 任何异常都只打 warning 日志并返回 (None,None,None), 不中断主流程。
        """
        from tradingagents.dataflows.symbol_utils import normalize_symbol

        try:
            start = datetime.strptime(trade_date, "%Y-%m-%d")
            end = start + timedelta(days=holding_days + 7)  # buffer for weekends/holidays
            end_str = end.strftime("%Y-%m-%d")

            # Normalize so the realized-return lookup hits the same instrument
            # the analysis priced (e.g. XAUUSD -> GC=F) (#984). The benchmark is
            # already a canonical Yahoo symbol from ``_resolve_benchmark``.
            stock = yf.Ticker(normalize_symbol(ticker)).history(start=trade_date, end=end_str)
            bench = yf.Ticker(benchmark).history(start=trade_date, end=end_str)

            if len(stock) < 2 or len(bench) < 2:
                return None, None, None

            actual_days = min(holding_days, len(stock) - 1, len(bench) - 1)
            raw = float(
                (stock["Close"].iloc[actual_days] - stock["Close"].iloc[0]) / stock["Close"].iloc[0]
            )
            bench_ret = float(
                (bench["Close"].iloc[actual_days] - bench["Close"].iloc[0]) / bench["Close"].iloc[0]
            )
            alpha = raw - bench_ret
            return raw, alpha, actual_days
        except Exception as e:
            logger.warning(
                "Could not resolve outcome for %s on %s vs %s (will retry next run): %s",
                ticker,
                trade_date,
                benchmark,
                e,
            )
            return None, None, None

    def _resolve_pending_entries(self, ticker: str) -> None:
        """Resolve pending log entries for ticker at the start of a new run.

        Fetches returns for each same-ticker pending entry, generates reflections,
        then writes all updates in a single atomic batch write to avoid redundant I/O.
        Skips entries whose price data is not yet available (too recent or delisted).

        Trade-off: only same-ticker entries are resolved per run.  Entries for
        other tickers accumulate until that ticker is run again.

        【中文说明】
        【功能】在新一次运行开始前, 结算上一次"待定"的决策结果 (实现延迟反思)。
        【参数】ticker: 本次要运行的股票代码; 只结算该 ticker 的待定条目。
        【返回】无 (结果直接写回记忆日志)。
        【关键逻辑】
            1) 从记忆日志取出所有"待定"条目, 过滤出与本次 ticker 相同的;
            2) 对每条: 用 _fetch_returns 拉实际收益 → 没有价格数据就跳过
               (太新/退市/网络错, 下次再试) → 有数据就让 Reflector 反思这次决策;
            3) 全部攒在 updates 列表里, 最后一次性批量写回 (batch_update_
               with_outcomes), 避免逐条 I/O;
            4) 设计取舍: 每次运行只结算"同 ticker"的待定条目, 其他 ticker 的
               会一直积累到那个 ticker 被再次运行时才结算。
        """
        pending = [e for e in self.memory_log.get_pending_entries() if e["ticker"] == ticker]
        if not pending:
            return

        benchmark = self._resolve_benchmark(ticker)
        updates = []
        for entry in pending:
            raw, alpha, days = self._fetch_returns(
                ticker,
                entry["date"],
                benchmark=benchmark,
            )
            if raw is None:
                continue  # price not available yet — try again next run
            reflection = self.reflector.reflect_on_final_decision(
                final_decision=entry.get("decision", ""),
                raw_return=raw,
                alpha_return=alpha,
                benchmark_name=benchmark,
            )
            updates.append(
                {
                    "ticker": ticker,
                    "trade_date": entry["date"],
                    "raw_return": raw,
                    "alpha_return": alpha,
                    "holding_days": days,
                    "reflection": reflection,
                }
            )

        if updates:
            self.memory_log.batch_update_with_outcomes(updates)

    def resolve_instrument_context(self, ticker: str, asset_type: str = "stock") -> str:
        """Resolve ticker identity once and return the full instrument context.

        Deterministic yfinance lookup (cached, fail-open) injected into a
        context string so every agent anchors to the real company instead of
        hallucinating one from the price chart (#814). Both the propagate()
        path and the CLI call this so the resolved identity reaches the whole
        graph regardless of entry point.

        【中文说明】
        【功能】在运行开始时"一次性地"解析 ticker 的真实公司身份, 并拼装成一段
        上下文文本返回。
        【参数】
            ticker: 股票代码; asset_type: 资产类型 (默认 "stock")。
        【返回】一段包含真实公司信息 (名称/行业等) 的上下文字符串。
        【关键逻辑】
            1) resolve_instrument_identity(): 确定的 yfinance 查询, 带缓存、
               查不到也不抛错 (fail-open);
            2) 把解析结果注入 context 字符串, 让每个 agent 都锚定"真实公司",
               而不是只凭价格图瞎猜公司是谁 (issue #814);
            3) propagate() 与 CLI 都调用它, 保证无论从哪个入口进图, 解析到的
               身份都能覆盖整张图的所有 agent。
        """
        identity = resolve_instrument_identity(ticker)
        return build_instrument_context(ticker, asset_type, identity)

    def _run_signature(self, asset_type: str) -> str:
        """Graph-shape inputs that must invalidate a checkpoint if changed.

        Keyed into the checkpoint thread ID so a resume under a different analyst
        selection, debate/risk depth, or asset mode starts fresh instead of
        silently continuing the previous graph (#1089).

        【中文说明】
        【功能】生成"图形状签名"字符串——凡是会改变图结构的输入都拼进来。
        【参数】asset_type: 资产类型 ("stock"/"crypto")。
        【返回】一个用 | 连接的字符串, 例如:
            "analysts=market,social,news,fundamentals|debate=3|risk=2|asset=stock"
        【关键逻辑】该签名被写进断点 (checkpoint) 的 thread_id。这样如果用户换
            了分析师组合、辩论/风险轮数或资产类型, 断点 key 就不同, 续跑时会被
            当作"新的运行"而重新开始, 而不会静默地接着上一张不同形状的图跑
            (issue #1089)。
        """
        return "|".join(
            [
                "analysts=" + ",".join(self.selected_analysts),
                f"debate={self.config['max_debate_rounds']}",
                f"risk={self.config['max_risk_discuss_rounds']}",
                f"asset={asset_type}",
            ]
        )

    def propagate(self, company_name, trade_date, asset_type: str = "stock"):
        """Run the trading agents graph for a company on a specific date.

        ``asset_type`` selects between the stock pipeline (default) and the
        crypto pipeline (``"crypto"``) shipped in #567 — the CLI auto-detects
        from the ticker; programmatic callers pass it explicitly. When
        ``checkpoint_enabled`` is set in config, the graph is recompiled with
        a per-ticker SqliteSaver so a crashed run can resume from the last
        successful node on a subsequent invocation with the same ticker+date.

        【中文说明】
        【功能】一次股票分析的"总入口": 先处理记忆结算与断点续跑, 然后调用
            _run_graph() 初始化状态并真正驱动整张图跑完。
        【参数】
            company_name: 股票代码 (内部用 self.ticker 保存);
            trade_date: 分析日期 (YYYY-MM-DD);
            asset_type: "stock"(默认) 或 "crypto"。CLI 会从 ticker 自动判断,
                编程调用方需显式传入。
        【返回】_run_graph() 的返回值: (final_state, 处理后的交易信号)。
        【关键逻辑】
            1) 每次运行前先 _resolve_pending_entries(): 结算该 ticker 上一次
               待定决策的收益并做延迟反思 (记忆闭环);
            2) 若 config["checkpoint_enabled"] 开启: 用 get_checkpointer() 建一个
               按 ticker 隔离的 SqliteSaver, 用 workflow 重新 compile(checkpointer=
               saver), 这样图每执行完一个节点就把状态存进 SQLite——崩溃后下次用
               同一 ticker+date 再跑就能从"最后一个成功节点"续起;
            3) checkpoint_step() 返回上次跑到第几步 (None 表示全新运行);
            4) finally 块保证无论成功失败都退出 checkpointer 上下文, 并把 graph
               恢复成"无断点"的普通编译版本, 避免污染后续运行。
        """
        self.ticker = company_name

        # Resolve any pending memory-log entries for this ticker before the pipeline runs.
        # 【中文说明】在正式跑图前, 先把上一轮"待定"决策结算掉 (见
        # _resolve_pending_entries 的中文说明)。这样本轮的反思上下文是最新的。
        self._resolve_pending_entries(company_name)

        # Recompile with a checkpointer if the user opted in.
        # 【中文说明】只有配置里显式开启断点续跑时才做这一步; 否则直接用 __init__
        # 里编译好的 self.graph 跑 (见 try 块)。
        if self.config.get("checkpoint_enabled"):
            self._checkpointer_ctx = get_checkpointer(self.config["data_cache_dir"], company_name)
            saver = self._checkpointer_ctx.__enter__()
            self.graph = self.workflow.compile(checkpointer=saver)

            # 【关键逻辑】checkpoint_step() 查询上次中断在哪个节点: 返回非 None
            # 表示可续跑 (日志提示), None 表示没有历史或签名变了, 从零开始。
            step = checkpoint_step(
                self.config["data_cache_dir"],
                company_name,
                str(trade_date),
                self._run_signature(asset_type),
            )
            if step is not None:
                logger.info("Resuming from step %d for %s on %s", step, company_name, trade_date)
            else:
                logger.info("Starting fresh for %s on %s", company_name, trade_date)

        try:
            return self._run_graph(company_name, trade_date, asset_type=asset_type)
        finally:
            # 【关键逻辑】清理断点上下文: 退出 SqliteSaver 上下文并关闭连接,
            # 然后把 graph 还原为"无断点"版本, 保证下一次普通运行不受影响。
            if self._checkpointer_ctx is not None:
                self._checkpointer_ctx.__exit__(None, None, None)
                self._checkpointer_ctx = None
                self.graph = self.workflow.compile()

    def save_reports(self, final_state, ticker, save_path=None) -> Path:
        """Write the markdown report tree for a completed run, like the CLI does.

        Programmatic callers get the same on-disk reports the CLI produces. Pass
        an explicit ``save_path`` or let it default under ``results_dir``.

        【中文说明】
        【功能】把一次完整运行得到的最终状态, 渲染成一棵 Markdown 报告目录树写盘,
            让编程调用方拿到与 CLI 完全一致的报告文件。
        【参数】
            final_state: 图运行结束后返回的完整状态字典;
            ticker: 股票代码 (用于给目录/文件名命名);
            save_path: 显式指定的保存路径; 为 None 时自动生成
                <results_dir>/reports/<safe_ticker>_<时间戳>。
        【返回】实际写入的目录 Path 对象。
        【关键逻辑】目录/文件名里的 ticker 会经过 safe_ticker_component() 清洗,
            防止把含路径分隔符的非法字符串拼进路径导致目录逃逸。
        """
        if save_path is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            save_path = (
                Path(self.config["results_dir"])
                / "reports"
                / f"{safe_ticker_component(ticker)}_{stamp}"
            )
        return write_report_tree(final_state, ticker, save_path)

    def _run_graph(self, company_name, trade_date, asset_type: str = "stock"):
        """Execute the graph and write the resulting state to disk and memory log.

        【中文说明】
        【功能】真正驱动整张 LangGraph 图运行, 并把最终状态落盘 + 存入记忆日志。
        【参数】
            company_name: 股票代码; trade_date: 分析日期;
            asset_type: "stock" 或 "crypto"。
        【返回】(final_state, 交易信号): 前者是完整最终状态字典, 后者是
            process_signal(final_trade_decision) 提取出的核心决策信号。
        【关键逻辑】三步:
            1) 初始化状态 (见下方中文说明): 用 Propagator 创建图运行的初始状态,
               注入记忆上下文与确定性公司身份;
            2) 运行图: debug 模式用 graph.stream() 逐节点流式输出并合并成最终
               状态; 普通模式用 graph.invoke() 一次跑完返回最终状态;
            3) 收尾: 存最终状态 → 存决策到记忆日志 (供下次延迟反思) → 成功则
               清掉断点缓存。
        """
        # Initialize state — inject memory log context for PM and the
        # deterministically resolved instrument identity for all agents.
        # 【中文说明】初始化状态三要素:
        #   1) past_context: 记忆日志给出的"同 ticker 历史决策 + 跨 ticker 经验",
        #      让组合经理等节点能参考过往;
        #   2) instrument_context: 确定性解析出的公司身份文本 (见
        #      resolve_instrument_context), 防止 agent 凭价格图瞎猜公司;
        #   3) create_initial_state(): 由 Propagator 组装出包含上述信息以及
        #      空的多空/风险辩论状态、各报告字段初始值等的完整初始状态字典。
        past_context = self.memory_log.get_past_context(company_name)
        instrument_context = self.resolve_instrument_context(company_name, asset_type)
        init_agent_state = self.propagator.create_initial_state(
            company_name,
            trade_date,
            asset_type=asset_type,
            past_context=past_context,
            instrument_context=instrument_context,
        )
        # 【中文说明】get_graph_args() 返回图运行所需参数: 主要是
        # recursion_limit (递归上限, 防止图死循环) 与 stream_mode 等。
        args = self.propagator.get_graph_args()

        # Inject thread_id so same ticker+date+graph-shape resumes; a different
        # date or graph shape starts fresh (#1089).
        # 【中文说明】把 thread_id 写进运行 config。LangGraph 的 checkpointer 按
        # thread_id 归档节点快照: 相同的 ticker+date+图形状 → 相同 thread_id → 可
        # 续跑; 日期或图形状变了 → thread_id 变 → 当作全新运行 (issue #1089)。
        if self.config.get("checkpoint_enabled"):
            tid = thread_id(company_name, str(trade_date), self._run_signature(asset_type))
            args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = tid

        if self.debug:
            # 【中文说明】debug 模式: 用 graph.stream() 让图"一步一个节点"地吐出
            # 状态增量 (chunk), 便于观察中间过程。
            trace = []
            last_printed = None
            for chunk in self.graph.stream(init_agent_state, **args):
                if chunk["messages"]:
                    msg = chunk["messages"][-1]
                    # Nodes after the trader don't append to messages, so the
                    # same trailing message repeats across chunks. Print it only
                    # when it changes (#1027); the trace/state merge is unchanged.
                    # 【中文说明】交易员之后的节点不再往 messages 追加内容, 导致
                    # 同样一条消息会在多个 chunk 里重复出现。这里只在消息变化时
                    # 才打印, 避免刷屏 (issue #1027)。
                    signature = (type(msg).__name__, getattr(msg, "content", None))
                    if signature != last_printed:
                        msg.pretty_print()
                        last_printed = signature
                    trace.append(chunk)
            # Streamed chunks are per-node deltas. Merge them so the returned
            # state matches what graph.invoke() yields in the non-debug path.
            # 【关键逻辑】stream() 吐的是"每个节点产生的增量", 把它们逐个 update
            # 合并起来, 得到与普通路径 graph.invoke() 返回一致的完整最终状态。
            final_state = {}
            for chunk in trace:
                final_state.update(chunk)
        else:
            # 【关键逻辑】非 debug 模式: graph.invoke() 一次调用就把整张图跑完,
            # 返回合并后的最终状态 (中途不输出)。
            final_state = self.graph.invoke(init_agent_state, **args)

        # Store current state for reflection.
        self.curr_state = final_state

        # Log state to disk.
        # 【中文说明】把完整最终状态按日期写成 JSON 文件 (便于人工审阅)。
        self._log_state(trade_date, final_state)

        # Store decision for deferred reflection on the next same-ticker run.
        # 【中文说明】把最终交易决策存入记忆日志——下回再运行同一 ticker 时,
        # _resolve_pending_entries() 会用它 + 实际收益做延迟反思。
        self.memory_log.store_decision(
            ticker=company_name,
            trade_date=trade_date,
            final_trade_decision=final_state["final_trade_decision"],
        )

        # Clear checkpoint on successful completion to avoid stale state.
        # 【中文说明】本次运行已成功完成, 主动清掉断点缓存, 避免下次运行
        # 从过期的旧快照续跑。
        if self.config.get("checkpoint_enabled"):
            clear_checkpoint(
                self.config["data_cache_dir"],
                company_name,
                str(trade_date),
                self._run_signature(asset_type),
            )

        # 【返回】最终状态 + 处理后的交易信号 (二选一: 返回给 CLI/前端/编程调用方)。
        return final_state, self.process_signal(final_state["final_trade_decision"])

    def _log_state(self, trade_date, final_state):
        """Log the final state to a JSON file.

        【中文说明】
        【功能】把一次运行的完整最终状态, 按日期整理成字典并写成 JSON 文件。
        【参数】
            trade_date: 分析日期 (用作键和文件名的一部分);
            final_state: 图运行结束后的完整状态字典。
        【返回】无。
        【关键逻辑】
            1) 只挑选可 JSON 序列化的字段 (报告、辩论历史、决策等) 存进
               log_states_dict["<日期>"], 并逐层拆开辩论状态的内部结构;
            2) 写盘前用 safe_ticker_component() 清洗 ticker, 防止把含
               路径分隔符/非法字符的 ticker 拼进路径导致目录逃逸;
            3) 路径为 <results_dir>/<safe_ticker>/TradingAgentsStrategy_logs/
               full_states_log_<trade_date>.json, 自动创建父目录。
        """
        self.log_states_dict[str(trade_date)] = {
            "company_of_interest": final_state["company_of_interest"],
            "trade_date": final_state["trade_date"],
            "market_report": final_state["market_report"],
            "sentiment_report": final_state["sentiment_report"],
            "news_report": final_state["news_report"],
            "fundamentals_report": final_state["fundamentals_report"],
            "investment_debate_state": {
                "bull_history": final_state["investment_debate_state"]["bull_history"],
                "bear_history": final_state["investment_debate_state"]["bear_history"],
                "history": final_state["investment_debate_state"]["history"],
                "current_response": final_state["investment_debate_state"]["current_response"],
                "judge_decision": final_state["investment_debate_state"]["judge_decision"],
            },
            "trader_investment_decision": final_state["trader_investment_plan"],
            "risk_debate_state": {
                "aggressive_history": final_state["risk_debate_state"]["aggressive_history"],
                "conservative_history": final_state["risk_debate_state"]["conservative_history"],
                "neutral_history": final_state["risk_debate_state"]["neutral_history"],
                "history": final_state["risk_debate_state"]["history"],
                "judge_decision": final_state["risk_debate_state"]["judge_decision"],
            },
            "investment_plan": final_state["investment_plan"],
            "final_trade_decision": final_state["final_trade_decision"],
        }

        # Save to file. Reject ticker values that would escape the
        # results directory when joined as a path component.
        # 【中文说明】safe_ticker_component() 会剔除 ticker 中不安全的路径字符,
        # 防止把 "../" 之类的值拼进路径造成目录逃逸。
        safe_ticker = safe_ticker_component(self.ticker)
        directory = Path(self.config["results_dir"]) / safe_ticker / "TradingAgentsStrategy_logs"
        directory.mkdir(parents=True, exist_ok=True)

        log_path = directory / f"full_states_log_{trade_date}.json"
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(self.log_states_dict[str(trade_date)], f, indent=4)

    def process_signal(self, full_signal):
        """Process a signal to extract the core decision.

        【中文说明】
        【功能】把图最终产出的完整决策文本, 交给 SignalProcessor 提取成核心信号。
        【参数】full_signal: 最终决策的完整字符串 (如投资计划/风险结论全文)。
        【返回】结构化/精简后的交易信号 (具体格式由 SignalProcessor 定义)。
        【关键逻辑】这只是对 self.signal_processor.process_signal() 的一层薄封装,
            让外部调用方 (CLI/前端) 不需要直接持有 SignalProcessor 实例。
        """
        return self.signal_processor.process_signal(full_signal)
