import os  # 【调用包】环境变量读取与路径拼接

_TRADINGAGENTS_HOME = os.path.join(os.path.expanduser("~"), ".tradingagents")  # 【变量】用户主目录下的数据根目录(日志/缓存/记忆)

# Single source of truth for env-var → config-key overrides. To expose
# a new config key for environment-based override, add a row here — no
# entry-point script changes required. Coercion is driven by the type
# of the existing default, so users can keep writing plain strings in
# their .env file.
_ENV_OVERRIDES = {  # 【变量】环境变量名 → 配置键 的映射表(新增可覆盖键只需在此加一行)
    "TRADINGAGENTS_LLM_PROVIDER": "llm_provider",
    "TRADINGAGENTS_DEEP_THINK_LLM": "deep_think_llm",
    "TRADINGAGENTS_QUICK_THINK_LLM": "quick_think_llm",
    "TRADINGAGENTS_LLM_BACKEND_URL": "backend_url",
    "TRADINGAGENTS_OUTPUT_LANGUAGE": "output_language",
    "TRADINGAGENTS_MAX_DEBATE_ROUNDS": "max_debate_rounds",
    "TRADINGAGENTS_MAX_RISK_ROUNDS": "max_risk_discuss_rounds",
    "TRADINGAGENTS_CHECKPOINT_ENABLED": "checkpoint_enabled",
    "TRADINGAGENTS_BENCHMARK_TICKER": "benchmark_ticker",
    "TRADINGAGENTS_TEMPERATURE": "temperature",
    "TRADINGAGENTS_LLM_MAX_RETRIES": "llm_max_retries",
    # Provider-specific reasoning/thinking knobs (None = each provider's own
    # default). Settable here for non-interactive runs; the CLI also offers an
    # interactive choice, which is skipped when the matching var is set.
    "TRADINGAGENTS_GOOGLE_THINKING_LEVEL": "google_thinking_level",
    "TRADINGAGENTS_OPENAI_REASONING_EFFORT": "openai_reasoning_effort",
    "TRADINGAGENTS_ANTHROPIC_EFFORT": "anthropic_effort",
}


_BOOL_TRUE = ("true", "1", "yes", "on")  # 【变量】布尔环境变量判真集合
_BOOL_FALSE = ("false", "0", "no", "off")  # 【变量】布尔环境变量判假集合


# 【功能】把环境变量的字符串值按「参考默认值」的类型强制转换(bool / int / float / 原样字符串)。
# 【参数】value:环境变量的原始字符串;reference:配置中已有的默认值,决定转换目标类型。
# 【返回】转换后的值(bool / int / float / str)。
# 【关键】非法布尔/非数字会抛 ValueError 而不是静默回退,让配置错误在启动时暴露。
def _coerce(value: str, reference):
    """Coerce env-var string to the type of the existing default value.

    Invalid values raise ``ValueError`` rather than silently falling back to a
    default — a misspelled boolean (e.g. ``treu``) or non-numeric int should fail
    loudly at startup, not quietly misconfigure an unattended run.
    """
    if isinstance(reference, bool):
        normalized = value.strip().lower()
        if normalized in _BOOL_TRUE:
            return True
        if normalized in _BOOL_FALSE:
            return False
        raise ValueError(
            f"expected a boolean ({'/'.join(_BOOL_TRUE + _BOOL_FALSE)}), got {value!r}"
        )
    if isinstance(reference, int) and not isinstance(reference, bool):
        return int(value)
    if isinstance(reference, float):
        return float(value)
    return value


# 【功能】把全部 TRADINGAGENTS_* 环境变量就地覆盖到 config 字典上。
# 【参数】config:要覆盖的配置字典(会被就地修改)。
# 【返回】dict:覆盖完成后的同一个 config 对象。
# 【关键】空值(None / "")不覆盖;某个变量值非法时抛 ValueError 并指明是哪个变量。
def _apply_env_overrides(config: dict) -> dict:
    """Apply TRADINGAGENTS_* env vars to the config dict in-place."""
    for env_var, key in _ENV_OVERRIDES.items():
        raw = os.environ.get(env_var)  # 【变量】环境变量原始字符串
        if raw is None or raw == "":
            continue
        try:
            config[key] = _coerce(raw, config.get(key))
        except ValueError as exc:
            raise ValueError(f"Invalid value for {env_var}: {exc}") from exc
    return config


DEFAULT_CONFIG = _apply_env_overrides(  # 【变量】默认运行配置:先写默认值,再用环境变量覆盖
    {
        "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),  # 【变量】项目根目录(本文件所在目录)
        "results_dir": os.getenv(
            "TRADINGAGENTS_RESULTS_DIR", os.path.join(_TRADINGAGENTS_HOME, "logs")
        ),  # 【变量】分析结果输出目录(可用 TRADINGAGENTS_RESULTS_DIR 覆盖)
        "data_cache_dir": os.getenv(
            "TRADINGAGENTS_CACHE_DIR", os.path.join(_TRADINGAGENTS_HOME, "cache")
        ),  # 【变量】数据缓存目录(可用 TRADINGAGENTS_CACHE_DIR 覆盖)
        "memory_log_path": os.getenv(
            "TRADINGAGENTS_MEMORY_LOG_PATH",
            os.path.join(_TRADINGAGENTS_HOME, "memory", "trading_memory.md"),
        ),  # 【变量】交易记忆日志文件路径
        # Optional cap on the number of resolved memory log entries. When set,
        # the oldest resolved entries are pruned once this limit is exceeded.
        # Pending entries are never pruned. None disables rotation entirely.
        "memory_log_max_entries": None,
        # LLM settings
        "llm_provider": "openai",  # 【变量】默认 LLM 提供商(openai / google / anthropic / ollama 等)
        "deep_think_llm": "gpt-5.5",  # 【变量】深度思考模型(复杂推理/最终报告)
        "quick_think_llm": "gpt-5.4-mini",  # 【变量】快速思考模型(轻量/日常任务)
        # When None, each provider's client falls back to its own default endpoint
        # (api.openai.com for OpenAI, generativelanguage.googleapis.com for Gemini, ...).
        # The CLI overrides this per provider when the user picks one. Keeping a
        # provider-specific URL here would leak (e.g. OpenAI's /v1 was previously
        # being forwarded to Gemini, producing malformed request URLs).
        "backend_url": None,
        # Provider-specific thinking configuration
        "google_thinking_level": None,  # "high", "minimal", etc.
        "openai_reasoning_effort": None,  # "medium", "high", "low"
        "anthropic_effort": None,  # "high", "medium", "low"
        # Sampling temperature, forwarded to every provider when set. None leaves
        # each provider at its own default. Lower values reduce run-to-run
        # variation on models that honor it; reasoning models largely ignore it
        # and no setting makes LLM output bit-identical across runs (see README).
        "temperature": None,
        # SDK retry budget forwarded to every provider chat client. None leaves each
        # provider/SDK at its own default (usually 2). Raise it to ride out bursty
        # 429 throttling on rate-limited deployments instead of aborting a run (#1091).
        "llm_max_retries": None,
        # Checkpoint/resume: when True, LangGraph saves state after each node
        # so a crashed run can resume from the last successful step.
        "checkpoint_enabled": False,
        # Output language for analyst reports and final decision
        # Internal agent debate stays in English for reasoning quality
        "output_language": "English",
        # Debate and discussion settings
        "max_debate_rounds": 1,  # 【变量】投研辩论最大轮数(默认 1)
        "max_risk_discuss_rounds": 1,  # 【变量】风控讨论最大轮数(默认 1)
        "max_recur_limit": 100,  # 【变量】递归/循环深度上限,防止死循环
        # News / data fetching parameters
        # Increase for longer lookback strategies or to broaden macro coverage;
        # decrease to reduce token usage in agent prompts.
        "news_article_limit": 20,  # max articles per ticker (ticker-news) 【变量】每只标的抓取的新闻条数上限
        "global_news_article_limit": 10,  # max articles for global/macro news 【变量】宏观新闻抓取条数上限
        "global_news_lookback_days": 7,  # macro news lookback window 【变量】宏观新闻回溯窗口(天)
        # Search queries used by get_global_news for macro headlines. Extend or
        # replace to broaden geographic / sector coverage.
        "global_news_queries": [
            "Federal Reserve interest rates inflation",
            "S&P 500 earnings GDP economic outlook",
            "geopolitical risk trade war sanctions",
            "ECB Bank of England BOJ central bank policy",
            "oil commodities supply chain energy",
        ],
        # Data vendor configuration
        # Category-level configuration (default for all tools in category).
        # The configured value is the exact vendor chain — requests are NOT silently
        # routed to vendors you didn't choose. For ordered fallback, list several,
        # e.g. "yfinance,alpha_vantage". "default" uses all available vendors.
        "data_vendors": {
            "core_stock_apis": "yfinance",  # Options: alpha_vantage, yfinance
            "technical_indicators": "yfinance",  # Options: alpha_vantage, yfinance
            "fundamental_data": "yfinance",  # Options: alpha_vantage, yfinance
            "news_data": "yfinance",  # Options: alpha_vantage, yfinance
            "macro_data": "fred",  # Options: fred (needs FRED_API_KEY)
            "prediction_markets": "polymarket",  # Options: polymarket (keyless)
            # Commodity futures data (via akshare)
            "futures_price": "commodity_futures",
            "futures_basis": "commodity_futures",
            "futures_inventory": "commodity_futures",
            "futures_news": "commodity_futures",
            "futures_macro": "commodity_futures",
            "futures_supply_demand": "commodity_futures",
            "futures_sentiment": "commodity_futures",
            "futures_verified": "commodity_futures",
        },
        # Tool-level configuration (takes precedence over category-level)
        "tool_vendors": {
            # Example: "get_stock_data": "alpha_vantage",  # Override category default
        },
        # Benchmark for alpha calculation in the reflection layer.
        # ``benchmark_ticker`` (when set) overrides the suffix map for all
        # tickers; leave it None to use ``benchmark_map`` for auto-detection
        # based on the ticker's exchange suffix. SPY remains the US default
        # so the reflection label keeps reading "Alpha vs SPY" for US tickers
        # while non-US tickers get their regional index automatically.
        "benchmark_ticker": None,
        "benchmark_map": {
            ".NS": "^NSEI",  # NSE India (Nifty 50)
            ".BO": "^BSESN",  # BSE India (Sensex)
            ".T": "^N225",  # Tokyo (Nikkei 225)
            ".HK": "^HSI",  # Hong Kong (Hang Seng)
            ".L": "^FTSE",  # London (FTSE 100)
            ".TO": "^GSPTSE",  # Toronto (TSX Composite)
            ".AX": "^AXJO",  # Australia (ASX 200)
            ".SS": "000001.SS",  # Shanghai (SSE Composite)
            ".SZ": "399001.SZ",  # Shenzhen (SZSE Component)
            "": "SPY",  # default for US-listed tickers (no suffix)
        },
    }
)
