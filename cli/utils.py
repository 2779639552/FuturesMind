import os  # 【调用包】环境变量读取(OLLAMA_BASE_URL / API key 等)
from pathlib import Path  # 【调用包】.env 文件路径定位

import questionary  # 【调用包】交互式输入(文本/下拉/多选/密码)
from dotenv import find_dotenv, set_key  # 【调用包】.env 文件定位与写入 API key
from rich.console import Console  # 【调用包】Rich 终端输出

from cli.models import AnalystType, AssetType  # 【调用包】CLI 数据模型(分析师/资产类型枚举)
from tradingagents.llm_clients.api_key_env import get_api_key_env  # 【调用包】提供商→API key 环境变量名映射
from tradingagents.llm_clients.model_catalog import get_model_options  # 【调用包】各提供商模型目录(下拉选项)

console = Console()  # 【变量】全局 Rich 控制台

TICKER_INPUT_EXAMPLES = "SPY, 0700.HK, BTC-USD"  # 【变量】ticker 输入提示示例
COMMODITY_INPUT_EXAMPLES = "RB (螺纹钢), I (铁矿石), M (豆粕)"  # 【变量】商品期货代码输入提示示例

ANALYST_ORDER = [  # 【变量】(显示名, 分析师枚举) 列表,决定普通分析师菜单顺序
    ("Market Analyst", AnalystType.MARKET),
    ("Sentiment Analyst", AnalystType.SOCIAL),
    ("News Analyst", AnalystType.NEWS),
    ("Fundamentals Analyst", AnalystType.FUNDAMENTALS),
]

COMMODITY_ANALYST_ORDER = [  # 【变量】(显示名, 分析师枚举) 列表,商品期货分析师菜单顺序
    ("Technical Analyst (技术面)", AnalystType.COMMODITY_TECHNICAL),
    ("Fundamental Analyst (基本面)", AnalystType.COMMODITY_FUNDAMENTAL),
    ("Macro/News Analyst (宏观面)", AnalystType.COMMODITY_MACRO),
    ("Sentiment Analyst (情绪面)", AnalystType.COMMODITY_SENTIMENT),
]

CRYPTO_SUFFIXES = ("-USD", "-USDT", "-USDC", "-BTC", "-ETH")  # 【变量】加密资产后缀集合,用于 detect_asset_type 判定

# Commodity variety codes (uppercase, 1-2 chars)
_COMMODITY_CODES = {  # 【变量】商品期货品种代码集合(大写,1-2 字母),用于识别商品代码输入
    "RB",
    "HC",
    "I",
    "JM",
    "J",
    "M",
    "TA",
    "MA",
    "FG",
    "SC",
    "RU",
    "CU",
    "AU",
    "AG",
    "SA",
    "UR",
    "PF",
    "CF",
    "SR",
    "OI",
    "RM",
    "AP",
    "CJ",
    "PK",
    "SM",
    "SF",
}


# 【功能】校验 ticker 输入是否可接受(字符集 + 长度);空输入允许(下游默认 SPY)。
# 【参数】value:用户输入的原始 ticker 字符串。
# 【返回】bool:True 表示合法。
# 【关键】允许 Yahoo 符号字符集,含 "=" 表示期货/外汇(如 GC=F、EURUSD=X),"^" 表示指数;长度上限 32。
def is_valid_ticker_input(value: str) -> bool:
    """Whether a ticker entry is acceptable (charset + length).

    Allows the characters Yahoo symbols use, including ``=`` for futures/forex
    like ``GC=F`` and ``EURUSD=X`` (#980), and ``^`` for indices. Empty input is
    allowed (it defaults to SPY downstream).
    """
    v = value.strip()
    return not v or (all(ch.isalnum() or ch in "._-^=" for ch in v) and len(v) <= 32)


# 【功能】交互式获取 ticker,保留交易所后缀;无输入时默认 SPY。
# 【参数】无
# 【返回】str:规范化后的 ticker(见 normalize_ticker_symbol)。
# 【关键】用 questionary.text 而非 typer.prompt,避免部分 shell 吞掉尾部点号后缀(如 000404.SH);
#         取消输入时退出程序。
def get_ticker() -> str:
    """Prompt the user to enter a ticker symbol, preserving exchange suffixes.

    Uses questionary.text (not typer.prompt, which strips trailing dot-suffixes
    like ``000404.SH`` on some shells) and validates the symbol charset so an
    obvious typo is caught before the run starts.
    """
    ticker = questionary.text(
        f"Enter ticker symbol (e.g. {TICKER_INPUT_EXAMPLES}):",
        validate=lambda x: (
            is_valid_ticker_input(x)
            or "Please enter a valid ticker symbol, e.g. AAPL, 000404.SZ, 0700.HK, GC=F."
        ),
        style=questionary.Style(
            [
                ("text", "fg:green"),
                ("highlighted", "noinherit"),
            ]
        ),
    ).ask()

    if ticker is None:
        console.print("\n[red]No ticker symbol provided. Exiting...[/red]")
        exit(1)

    return normalize_ticker_symbol(ticker) if ticker.strip() else "SPY"


# 【功能】把用户输入规整为规范 Yahoo 符号(单一事实来源)。
# 【参数】ticker:用户输入的原始符号。
# 【返回】str:规范化符号(如 BTCUSD → BTC-USD,XAUUSD → GC=F);数据层不可用时回退为大写原样。
# 【关键】委托数据层 normalize_symbol,保证 CLI 传给流水线的符号与数据路径实际定价的符号一致。
def normalize_ticker_symbol(ticker: str) -> str:
    """Resolve user input to its canonical Yahoo symbol (single source of truth).

    Delegates to the data layer's ``normalize_symbol`` so the symbol the CLI
    passes through the pipeline is exactly the one the data path will price
    (e.g. ``BTCUSD`` -> ``BTC-USD``, ``XAUUSD`` -> ``GC=F``). Falls back to the
    plain upper-case if the data layer is unavailable.
    """
    try:
        from tradingagents.dataflows.symbol_utils import normalize_symbol  # 【调用函数】数据层符号规范化(运行时导入,避免硬依赖)

        return normalize_symbol(ticker)
    except Exception:
        return ticker.strip().upper()


# 【功能】在规范符号上判定资产类型:加密 / 商品期货 / 股票。
# 【参数】ticker:用户输入的原始符号。
# 【返回】AssetType 枚举(STOCK / CRYPTO / COMMODITY_FUTURES)。
# 【关键】先规整再判断,使 BTCUSD 与 BTC-USDT 都判定为 CRYPTO(#981/#982);
#         商品期货按 1-2 位大写字母代码匹配 _COMMODITY_CODES。
def detect_asset_type(ticker: str) -> AssetType:
    """Classify on the canonical symbol so e.g. BTCUSD and BTC-USDT both read as
    crypto (#981/#982), matching what the data path will actually fetch."""
    canonical = normalize_ticker_symbol(ticker)
    if canonical.endswith(CRYPTO_SUFFIXES):
        return AssetType.CRYPTO
    # Detect commodity futures code: 1-2 uppercase letters
    stripped = ticker.strip().upper()
    if stripped in _COMMODITY_CODES:
        return AssetType.COMMODITY_FUTURES
    return AssetType.STOCK


# 【功能】按资产类型过滤可选分析师:加密资产剔除基本面分析师(其数据源对加密不可用)。
# 【参数】analysts:候选分析师列表;asset_type:资产类型。
# 【返回】过滤后的分析师列表;非加密资产原样返回。
def filter_analysts_for_asset_type(
    analysts: list[AnalystType], asset_type: AssetType
) -> list[AnalystType]:
    if asset_type != AssetType.CRYPTO:
        return analysts
    return [analyst for analyst in analysts if analyst != AnalystType.FUNDAMENTALS]


# 【功能】交互式获取 YYYY-MM-DD 格式的分析日期。
# 【参数】无
# 【返回】str:合法日期字符串;取消输入时退出程序。
# 【关键】内嵌 validate_date 做格式与真实日期校验,非法输入即时提示并重问。
def get_analysis_date() -> str:
    """Prompt the user to enter a date in YYYY-MM-DD format."""
    import re
    from datetime import datetime

    # 【功能】校验日期字符串:格式须匹配 YYYY-MM-DD 且能真实解析为日期。
    # 【参数】date_str:待校验字符串。
    # 【返回】bool:True 表示合法日期。
    def validate_date(date_str: str) -> bool:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
            return False
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
            return True
        except ValueError:
            return False

    date = questionary.text(
        "Enter the analysis date (YYYY-MM-DD):",
        validate=lambda x: (
            validate_date(x.strip()) or "Please enter a valid date in YYYY-MM-DD format."
        ),
        style=questionary.Style(
            [
                ("text", "fg:green"),
                ("highlighted", "noinherit"),
            ]
        ),
    ).ask()

    if not date:
        console.print("\n[red]No date provided. Exiting...[/red]")
        exit(1)

    return date.strip()


# 【功能】用交互式复选框让用户选择分析师团队。
# 【参数】asset_type:资产类型(默认股票),决定可选分析师范围(如加密剔除基本面)。
# 【返回】list[AnalystType]:用户选中的分析师;一个不选或取消时退出程序。
def select_analysts(asset_type: AssetType = AssetType.STOCK) -> list[AnalystType]:
    """Select analysts using an interactive checkbox."""
    available_analysts = filter_analysts_for_asset_type(
        [value for _, value in ANALYST_ORDER],
        asset_type,
    )
    choices = questionary.checkbox(
        "Select Your [Analysts Team]:",
        choices=[
            questionary.Choice(display, value=value)
            for display, value in ANALYST_ORDER
            if value in available_analysts
        ],
        instruction="\n- Press Space to select/unselect analysts\n- Press 'a' to select/unselect all\n- Press Enter when done",
        validate=lambda x: len(x) > 0 or "You must select at least one analyst.",
        style=questionary.Style(
            [
                ("checkbox-selected", "fg:green"),
                ("selected", "fg:green noinherit"),
                ("highlighted", "noinherit"),
                ("pointer", "noinherit"),
            ]
        ),
    ).ask()

    if not choices:
        console.print("\n[red]No analysts selected. Exiting...[/red]")
        exit(1)

    return choices


# 【功能】交互式选择研究深度(浅/中/深),对应不同的辩论与策略讨论轮数。
# 【参数】无
# 【返回】int:辩论/风控轮数(1 / 3 / 5);取消选择时退出程序。
def select_research_depth() -> int:
    """Select research depth using an interactive selection."""

    # Define research depth options with their corresponding values
    DEPTH_OPTIONS = [  # 【变量】(显示文本, 对应轮数) 研究深度选项表
        ("Shallow - Quick research, few debate and strategy discussion rounds", 1),
        ("Medium - Middle ground, moderate debate rounds and strategy discussion", 3),
        ("Deep - Comprehensive research, in depth debate and strategy discussion", 5),
    ]

    choice = questionary.select(
        "Select Your [Research Depth]:",
        choices=[questionary.Choice(display, value=value) for display, value in DEPTH_OPTIONS],
        instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
        style=questionary.Style(
            [
                ("selected", "fg:yellow noinherit"),
                ("highlighted", "fg:yellow noinherit"),
                ("pointer", "fg:yellow noinherit"),
            ]
        ),
    ).ask()

    if choice is None:
        console.print("\n[red]No research depth selected. Exiting...[/red]")
        exit(1)

    return choice


# Mainstream OpenRouter chat-LLM provider namespaces. We surface the newest
# models from these rather than the universal-newest, which is dominated by
# niche/experimental releases. These are the general-purpose chat providers;
# more enterprise/specialised namespaces (nvidia, cohere, amazon, ...) tend to
# ship research/safety variants as their newest, so they're left out of the
# shortlist. Provider names are stable (unlike model IDs), so this rarely needs
# touching; anything not here is still reachable via Custom ID.
_OPENROUTER_MAINSTREAM = {  # 【变量】OpenRouter 主流通用聊天提供商命名空间集合(决定前 5 短名单)
    "openai",
    "anthropic",
    "google",
    "deepseek",
    "qwen",
    "mistralai",
    "meta-llama",
    "x-ai",
    "z-ai",
    "minimax",
    "moonshotai",
}


# 【功能】从 OpenRouter API 拉取可用模型,按创建时间倒序返回(新→旧)。
# 【参数】无
# 【返回】list[(显示名, 模型ID)];请求失败时打印黄色提示并返回空列表。
# 【关键】外部 API 请求(超时 10 秒);显式按 created 倒序排序,确保"最新在前"的展示承诺成立。
def _fetch_openrouter_models() -> list[tuple[str, str]]:
    """Fetch available models from the OpenRouter API."""
    import requests

    try:
        resp = requests.get("https://openrouter.ai/api/v1/models", timeout=10)  # 【调用函数】外部 API 拉取 OpenRouter 模型目录
        resp.raise_for_status()
        models = resp.json().get("data", [])  # 【变量】模型原始条目列表
        # Newest first so the top-N shown really is the latest available — the
        # API currently returns this order, but sort explicitly so the prompt's
        # "latest available" label holds regardless of response ordering.
        models.sort(key=lambda m: m.get("created") or 0, reverse=True)
        return [(m.get("name") or m["id"], m["id"]) for m in models]
    except Exception as e:
        console.print(f"\n[yellow]Could not fetch OpenRouter models: {e}[/yellow]")
        return []


# 【功能】提示必填文本输入;用户取消(Ctrl-C/Esc)时干净退出程序。
# 【参数】message:提示语;hint:校验失败时展示的提示文本。
# 【返回】str:去空白后的输入。
# 【关键】questionary.text(...).ask() 在取消时返回 None,这里与其它必选项保持一致退出,
#         避免返回空模型/部署名导致下游失败。
def _require_text(message: str, hint: str) -> str:
    """Prompt for a required value; exit cleanly if the user cancels.

    ``questionary.text(...).ask()`` returns None on Ctrl-C/Esc; mirror the
    exit-on-cancel behavior of the other required selections so a cancelled
    prompt never returns an empty model/deployment that would fail downstream.
    """
    response = questionary.text(
        message,
        validate=lambda x: len(x.strip()) > 0 or hint,
    ).ask()
    if response is None:
        console.print("\n[red]Cancelled. Exiting...[/red]")
        exit(1)
    return response.strip()


# 【功能】从 OpenRouter 最新模型中选一个,或输入自定义模型 ID。
# 【参数】mode:"quick" / "deep",用于区分两次连续选择的提示文案。
# 【返回】str:模型 ID;取消选择时退出程序。
# 【关键】优先展示主流提供商的最近模型,避免被小众/实验模型挤占前 5;跳过 "~" 开头的变体/别名路由。
def select_openrouter_model(mode: str) -> str:
    """Select an OpenRouter model from the newest available, or enter a custom ID.

    ``mode`` ("quick"/"deep") labels the prompt so the two consecutive
    OpenRouter selections are distinguishable, like the other providers (#1000).
    """
    models = _fetch_openrouter_models()  # newest first
    # Prefer the newest from mainstream providers so the shortlist isn't crowded
    # out by niche/experimental releases; fall back to all if none match.
    mainstream = [  # 【变量】主流提供商且非 "~" 变体路由的模型列表
        (name, mid)
        for name, mid in models
        if not mid.startswith("~")  # skip variant/alias duplicate routes
        and mid.split("/", 1)[0] in _OPENROUTER_MAINSTREAM
    ]
    top = (mainstream or models)[:5]  # 【变量】下拉展示的最近 5 个模型(主流优先,否则取全部最新)

    choices = [questionary.Choice(name, value=mid) for name, mid in top]
    choices.append(questionary.Choice("Custom model ID", value="custom"))

    choice = questionary.select(
        f"Select Your [{mode.title()}-Thinking] OpenRouter Model (latest available):",
        choices=choices,
        instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
        style=questionary.Style(
            [
                ("selected", "fg:magenta noinherit"),
                ("highlighted", "fg:magenta noinherit"),
                ("pointer", "fg:magenta noinherit"),
            ]
        ),
    ).ask()

    if choice is None:
        console.print("\n[red]No model selected. Exiting...[/red]")
        exit(1)
    if choice == "custom":
        return _require_text(
            "Enter OpenRouter model ID (e.g. google/gemma-4-26b-a4b-it):",
            "Please enter a model ID.",
        )
    return choice


# 【功能】提示用户输入自定义模型 ID。
# 【参数】无
# 【返回】str:去空白后的模型 ID。
def _prompt_custom_model_id() -> str:
    """Prompt user to type a custom model ID."""
    return _require_text("Enter model ID:", "Please enter a model ID.")


# 【功能】按提供商与模式(quick/deep)交互式选择思考模型。
# 【参数】provider:LLM 提供商名;mode:"quick" 或 "deep",用于提示文案。
# 【返回】str:模型 ID(Azure 为部署名);取消时退出。
# 【关键】openrouter 走 select_openrouter_model;azure 走部署名文本输入;其余走 get_model_options 下拉,选中 custom 再追问。
def _select_model(provider: str, mode: str) -> str:
    """Select a model for the given provider and mode (quick/deep)."""
    if provider.lower() == "openrouter":
        return select_openrouter_model(mode)

    if provider.lower() == "azure":
        return _require_text(
            f"Enter Azure deployment name ({mode}-thinking):",
            "Please enter a deployment name.",
        )

    choice = questionary.select(
        f"Select Your [{mode.title()}-Thinking LLM Engine]:",
        choices=[
            questionary.Choice(display, value=value)
            for display, value in get_model_options(provider, mode)
        ],
        instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
        style=questionary.Style(
            [
                ("selected", "fg:magenta noinherit"),
                ("highlighted", "fg:magenta noinherit"),
                ("pointer", "fg:magenta noinherit"),
            ]
        ),
    ).ask()

    if choice is None:
        console.print(f"\n[red]No {mode} thinking llm engine selected. Exiting...[/red]")
        exit(1)

    if choice == "custom":
        return _prompt_custom_model_id()

    return choice


# 【功能】选择快速思考模型(浅层/日常任务)。
# 【参数】provider:LLM 提供商名。
# 【返回】str:模型 ID。
def select_shallow_thinking_agent(provider) -> str:
    """Select shallow thinking llm engine using an interactive selection."""
    return _select_model(provider, "quick")


# 【功能】选择深度思考模型(深层/复杂推理)。
# 【参数】provider:LLM 提供商名。
# 【返回】str:模型 ID。
def select_deep_thinking_agent(provider) -> str:
    """Select deep thinking llm engine using an interactive selection."""
    return _select_model(provider, "deep")


# 【功能】返回所有支持提供商的可选表 [(显示名, 提供商键, 默认 base_url)]。
# 【参数】无
# 【返回】list[(display_name, provider_key, base_url)];base_url 为 None 表示由客户端决定默认端点。
# 【关键】交互选择与环境变量驱动共用此表,保证 env 选出的提供商与菜单默认端点一致;
#         Ollama 读 OLLAMA_BASE_URL,未设置时回退 localhost 默认。
def _llm_provider_table() -> list[tuple[str, str, str | None]]:
    """(display_name, provider_key, base_url) for every supported provider.

    Shared by the interactive picker and by env-driven configuration so an
    env-set provider resolves to the same default endpoint the menu uses.
    Ollama users can point at a remote ollama-serve via OLLAMA_BASE_URL
    (convention from the broader Ollama ecosystem); falls back to the
    localhost default when unset.
    """
    ollama_url = os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434/v1"  # 【变量】Ollama 端点(环境可覆盖,默认本地)
    return [
        ("OpenAI", "openai", "https://api.openai.com/v1"),
        ("Google", "google", None),
        ("Anthropic", "anthropic", "https://api.anthropic.com/"),
        ("xAI", "xai", "https://api.x.ai/v1"),
        ("DeepSeek", "deepseek", "https://api.deepseek.com"),
        ("Qwen", "qwen", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
        ("GLM", "glm", "https://open.bigmodel.cn/api/paas/v4/"),
        ("MiniMax", "minimax", "https://api.minimax.io/v1"),
        ("OpenRouter", "openrouter", "https://openrouter.ai/api/v1"),
        ("Mistral", "mistral", "https://api.mistral.ai/v1"),
        ("Kimi (Moonshot)", "kimi", "https://api.moonshot.ai/v1"),
        ("Groq", "groq", "https://api.groq.com/openai/v1"),
        ("NVIDIA NIM", "nvidia", "https://integrate.api.nvidia.com/v1"),
        ("Azure OpenAI", "azure", None),
        ("Amazon Bedrock", "bedrock", None),
        ("Ollama", "ollama", ollama_url),
        ("OpenAI-compatible (vLLM, LM Studio, llama.cpp, custom relay)", "openai_compatible", None),
    ]


# 【功能】返回某提供商的默认后端地址;未知提供商返回 None。
# 【参数】provider_key:提供商键(大小写不敏感)。
# 【返回】str | None:默认 base_url。
def provider_default_url(provider_key: str) -> str | None:
    """Return the default backend URL for a provider key, or None if unknown."""
    key = provider_key.lower()
    for _, pk, url in _llm_provider_table():
        if pk == key:
            return url
    return None


# 【功能】按优先级解析后端地址:env_url(环境覆盖) > menu_url(菜单/区域选择) > 提供商默认。
# 【参数】provider:提供商键;menu_url:菜单或区域选择的地址;env_url:TRADINGAGENTS_LLM_BACKEND_URL 对应值。
# 【返回】str | None。
# 【关键】显式环境变量永远优先(#978),避免交互选择覆盖用户 env 配置。
def resolve_backend_url(
    provider: str, menu_url: str | None = None, env_url: str | None = None
) -> str | None:
    """Resolve the backend URL with the correct precedence.

    An explicit env override (``env_url``, from ``TRADINGAGENTS_LLM_BACKEND_URL``
    via ``DEFAULT_CONFIG['backend_url']``) is honored regardless of how the
    provider was chosen — interactively or from the environment (#978).
    Otherwise the menu/region URL, then the provider's default.
    """
    return env_url or menu_url or provider_default_url(provider)


# 【功能】提示输入 OpenAI 兼容端点的 base URL(必须以 http:// 或 https:// 开头)。
# 【参数】无
# 【返回】str:去空白后的 URL;取消输入时退出程序。
def prompt_openai_compatible_url() -> str:
    """Prompt for a custom OpenAI-compatible endpoint base URL."""
    url = questionary.text(
        "Enter the OpenAI-compatible base URL "
        "(e.g. http://localhost:8000/v1 for vLLM, http://localhost:1234/v1 for LM Studio):",
        validate=lambda x: (
            x.strip().startswith(("http://", "https://"))
            or "Enter a URL starting with http:// or https://"
        ),
    ).ask()
    if not url:
        console.print("\n[red]No endpoint URL provided. Exiting...[/red]")
        exit(1)
    return url.strip()


# 【功能】交互式选择 LLM 提供商及其端点。
# 【参数】无
# 【返回】(provider_key, url):提供商键与端点地址;取消选择时退出程序。
def select_llm_provider() -> tuple[str, str | None]:
    """Select the LLM provider and its API endpoint."""
    PROVIDERS = _llm_provider_table()  # 【变量】提供商候选表(选项值携带 provider_key 与 url)

    choice = questionary.select(
        "Select your LLM Provider:",
        choices=[
            questionary.Choice(display, value=(provider_key, url))
            for display, provider_key, url in PROVIDERS
        ],
        instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
        style=questionary.Style(
            [
                ("selected", "fg:magenta noinherit"),
                ("highlighted", "fg:magenta noinherit"),
                ("pointer", "fg:magenta noinherit"),
            ]
        ),
    ).ask()

    if choice is None:
        console.print("\n[red]No LLM provider selected. Exiting...[/red]")
        exit(1)

    provider, url = choice
    return provider, url


# 【功能】询问 OpenAI 推理强度(medium / high / low)。
# 【参数】无
# 【返回】str:选择值;取消时返回 None(由调用方兜底为默认)。
def ask_openai_reasoning_effort() -> str:
    """Ask for OpenAI reasoning effort level."""
    choices = [
        questionary.Choice("Medium (Default)", "medium"),
        questionary.Choice("High (More thorough)", "high"),
        questionary.Choice("Low (Faster)", "low"),
    ]
    return questionary.select(
        "Select Reasoning Effort:",
        choices=choices,
        style=questionary.Style(
            [
                ("selected", "fg:cyan noinherit"),
                ("highlighted", "fg:cyan noinherit"),
                ("pointer", "fg:cyan noinherit"),
            ]
        ),
    ).ask()


# 【功能】询问 Anthropic effort 等级(high / medium / low),控制 token 用量与回复详尽度。
# 【参数】无
# 【返回】str | None:选择值;取消时返回 None。
# 【关键】API 也接受 "max",此处只暴露 low/medium/high 常见区间。
def ask_anthropic_effort() -> str | None:
    """Ask for Anthropic effort level.

    Controls token usage and response thoroughness on Claude 4.5 / 4.6 / 4.7
    models. The API also accepts "max"; we expose low/medium/high as the
    common selection range.
    """
    return questionary.select(
        "Select Effort Level:",
        choices=[
            questionary.Choice("High (recommended)", "high"),
            questionary.Choice("Medium (balanced)", "medium"),
            questionary.Choice("Low (faster, cheaper)", "low"),
        ],
        style=questionary.Style(
            [
                ("selected", "fg:cyan noinherit"),
                ("highlighted", "fg:cyan noinherit"),
                ("pointer", "fg:cyan noinherit"),
            ]
        ),
    ).ask()


# 【功能】询问 Gemini 思考模式("high" / "minimal")。
# 【参数】无
# 【返回】str | None:thinking_level;客户端按模型系列映射到对应 API 参数。
def ask_gemini_thinking_config() -> str | None:
    """Ask for Gemini thinking configuration.

    Returns thinking_level: "high" or "minimal".
    Client maps to appropriate API param based on model series.
    """
    return questionary.select(
        "Select Thinking Mode:",
        choices=[
            questionary.Choice("Enable Thinking (recommended)", "high"),
            questionary.Choice("Minimal/Disable Thinking", "minimal"),
        ],
        style=questionary.Style(
            [
                ("selected", "fg:green noinherit"),
                ("highlighted", "fg:green noinherit"),
                ("pointer", "fg:green noinherit"),
            ]
        ),
    ).ask()


# 【功能】询问 GLM 平台(Z.AI 国际 vs BigModel 中国),两套账号密钥不可互换。
# 【参数】无
# 【返回】(provider_key, backend_url):("glm", Z.AI 地址) 或 ("glm-cn", BigModel 地址)。
def ask_glm_region() -> tuple[str, str]:
    """Ask which GLM platform (Z.AI international vs BigModel China) to use.

    Zhipu serves the same GLM models under two brands with separate
    accounts; keys aren't interchangeable. Returns (provider_key, backend_url).
    """
    return questionary.select(
        "Select GLM platform:",
        choices=[
            questionary.Choice(
                "Z.AI — api.z.ai (international, uses ZHIPU_API_KEY)",
                value=("glm", "https://api.z.ai/api/paas/v4/"),
            ),
            questionary.Choice(
                "BigModel — open.bigmodel.cn (China, uses ZHIPU_CN_API_KEY)",
                value=("glm-cn", "https://open.bigmodel.cn/api/paas/v4/"),
            ),
        ],
        style=questionary.Style(
            [
                ("selected", "fg:cyan noinherit"),
                ("highlighted", "fg:cyan noinherit"),
                ("pointer", "fg:cyan noinherit"),
            ]
        ),
    ).ask()


# 【功能】询问 Qwen 区域(国际 vs 中国),两套 DashScope 端点账号不可互换。
# 【参数】无
# 【返回】(provider_key, backend_url):("qwen", 国际地址) 或 ("qwen-cn", 中国地址)。
# 【关键】一个区域的 key 无法用于另一个区域(#758)。
def ask_qwen_region() -> tuple[str, str]:
    """Ask which Qwen region (international vs China) to use.

    Alibaba DashScope exposes two endpoints with separate accounts —
    a key from one region does NOT authenticate against the other
    (fixes #758). Returns (provider_key, backend_url).
    """
    return questionary.select(
        "Select Qwen region:",
        choices=[
            questionary.Choice(
                "International — dashscope-intl.aliyuncs.com (uses DASHSCOPE_API_KEY)",
                value=("qwen", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
            ),
            questionary.Choice(
                "China — dashscope.aliyuncs.com (uses DASHSCOPE_CN_API_KEY)",
                value=("qwen-cn", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            ),
        ],
        style=questionary.Style(
            [
                ("selected", "fg:cyan noinherit"),
                ("highlighted", "fg:cyan noinherit"),
                ("pointer", "fg:cyan noinherit"),
            ]
        ),
    ).ask()


# 【功能】询问 MiniMax 区域(Global vs China),两套端点账号不可互换。
# 【参数】无
# 【返回】(provider_key, backend_url):("minimax", Global 地址) 或 ("minimax-cn", 中国地址)。
def ask_minimax_region() -> tuple[str, str]:
    """Ask which MiniMax region (global vs China) to use.

    MiniMax exposes two endpoints with separate accounts — a key from
    one region does NOT authenticate against the other. Returns
    (provider_key, backend_url).
    """
    return questionary.select(
        "Select MiniMax region:",
        choices=[
            questionary.Choice(
                "Global — api.minimax.io (uses MINIMAX_API_KEY)",
                value=("minimax", "https://api.minimax.io/v1"),
            ),
            questionary.Choice(
                "China — api.minimaxi.com (uses MINIMAX_CN_API_KEY)",
                value=("minimax-cn", "https://api.minimaxi.com/v1"),
            ),
        ],
        style=questionary.Style(
            [
                ("selected", "fg:cyan noinherit"),
                ("highlighted", "fg:cyan noinherit"),
                ("pointer", "fg:cyan noinherit"),
            ]
        ),
    ).ask()


# 【功能】在选模型前展示解析出的 Ollama 端点,并给出缺少 scheme / 非 11434 端口的软提示。
# 【参数】url:已解析的 Ollama 端点地址。
# 【返回】无
# 【关键】提示仅 advisory,不拒绝异常输入(用户可能刻意使用反向代理路径等非常规配置)。
def confirm_ollama_endpoint(url: str) -> None:
    """Show the resolved Ollama endpoint after provider selection.

    Surfaces three things the user benefits from seeing before model
    selection: which URL we'll actually hit, where it came from
    (`OLLAMA_BASE_URL` vs default), and a soft warning if the URL is
    missing the scheme/port that ollama-serve expects. The warning is
    advisory only — we don't reject malformed input, since the user may
    be doing something deliberately unusual (e.g. a reverse-proxy path).
    """
    from_env = os.environ.get("OLLAMA_BASE_URL")  # 【变量】环境变量中的 Ollama 地址(可能为 None)
    origin = " (from OLLAMA_BASE_URL)" if from_env and from_env == url else ""  # 【变量】地址来源后缀(用于展示)
    console.print(f"[green]✓ Using Ollama at {url}{origin}[/green]")

    if not url.startswith(("http://", "https://")):
        console.print(
            f"[yellow]Note: {url!r} is missing a scheme. "
            f"Ollama-serve typically expects a URL like "
            f"http://<host>:11434/v1.[/yellow]"
        )
    elif ":11434" not in url and "://localhost" not in url and "://127.0.0.1" not in url:
        # Soft hint when the port differs from the ollama-serve default
        # and the host isn't local (where users sometimes proxy on :80).
        console.print(
            f"[yellow]Note: {url!r} doesn't include port 11434. "
            f"Make sure your remote ollama-serve listens on the port "
            f"shown above.[/yellow]"
        )


# 【功能】确保提供商的 API key 在环境变量中可用;缺失时交互输入并持久化到项目 .env。
# 【参数】provider:提供商键(如 "openai" / "ollama")。
# 【返回】str | None:key 值;无需 key 的提供商(ollama / 未知)或用户跳过时返回 None。
# 【关键】key 可选提供商(通用 OpenAI 兼容 / 本地服务)只读不强制弹窗;
#         写 .env 用 python-dotenv set_key,并同步到 os.environ 供当前进程使用。
def ensure_api_key(provider: str) -> str | None:
    """Make sure the API key for `provider` is available in the environment.

    If the env var is already set, returns its value untouched. Otherwise
    interactively prompts the user, persists the value to the project's
    .env file via python-dotenv's set_key (creating .env if needed), and
    exports it into os.environ so the current process picks it up.

    Returns None for providers that do not require a key (e.g. ollama)
    and for providers not found in the canonical mapping.
    """
    env_var = get_api_key_env(provider)  # 【变量】该提供商对应的 API key 环境变量名
    if env_var is None:
        return None  # ollama / unknown — no key check possible

    # Key-optional providers (generic OpenAI-compatible / local servers) read the
    # key when present but must never force an interactive prompt.
    from tradingagents.llm_clients.openai_client import OPENAI_COMPATIBLE_PROVIDERS  # 【调用函数】运行时导入:key 可选提供商白名单

    spec = OPENAI_COMPATIBLE_PROVIDERS.get(provider.lower())  # 【变量】provider 的 key 可选配置(若在白名单中)
    if spec is not None and spec.key_optional:
        return os.environ.get(env_var)

    existing = os.environ.get(env_var)  # 【变量】已存在的 key(有则直接返回)
    if existing:
        return existing

    console.print(f"\n[yellow]{env_var} is not set in your environment.[/yellow]")
    key = questionary.password(
        f"Paste your {env_var} (will be saved to .env):",
        style=questionary.Style(
            [
                ("text", "fg:cyan"),
                ("highlighted", "noinherit"),
            ]
        ),
    ).ask()
    if not key:
        console.print(f"[red]Skipped. API calls will fail until {env_var} is set.[/red]")
        return None

    env_path = find_dotenv(usecwd=True) or str(Path.cwd() / ".env")  # 【变量】目标 .env 文件路径(找不到则用项目根 .env)
    Path(env_path).touch(exist_ok=True)  # 【调用函数】确保 .env 存在(不存在则创建空文件)
    set_key(env_path, env_var, key)  # 【调用函数】python-dotenv 把 key 持久化写入 .env
    os.environ[env_var] = key  # 【调用函数】同步到当前进程环境变量,免重启生效
    console.print(f"[green]Saved {env_var} to {env_path}[/green]")
    return key


# 【功能】询问报告输出语言;取消时回退英语,自定义语言为空时也回退英语。
# 【参数】无
# 【返回】str:语言名(如 "English" / "Chinese" / 自定义语言名)。
# 【关键】输出语言有合理默认值,取消不退出程序(与必填的模型/提供商提示不同)。
def ask_output_language() -> str:
    """Ask for report output language."""
    choice = questionary.select(
        "Select Output Language:",
        choices=[
            questionary.Choice("English (default)", "English"),
            questionary.Choice("Chinese (中文)", "Chinese"),
            questionary.Choice("Japanese (日本語)", "Japanese"),
            questionary.Choice("Korean (한국어)", "Korean"),
            questionary.Choice("Hindi (हिन्दी)", "Hindi"),
            questionary.Choice("Spanish (Español)", "Spanish"),
            questionary.Choice("Portuguese (Português)", "Portuguese"),
            questionary.Choice("French (Français)", "French"),
            questionary.Choice("German (Deutsch)", "German"),
            questionary.Choice("Arabic (العربية)", "Arabic"),
            questionary.Choice("Russian (Русский)", "Russian"),
            questionary.Choice("Custom language", "custom"),
        ],
        style=questionary.Style(
            [
                ("selected", "fg:yellow noinherit"),
                ("highlighted", "fg:yellow noinherit"),
                ("pointer", "fg:yellow noinherit"),
            ]
        ),
    ).ask()

    # Output language has a sensible default, so a cancel falls back to English
    # rather than exiting the run (unlike the required model/provider prompts).
    if choice is None:
        return "English"
    if choice == "custom":
        return (
            questionary.text(
                "Enter language name (e.g. Turkish, Vietnamese, Thai, Indonesian):",
                validate=lambda x: len(x.strip()) > 0 or "Please enter a language name.",
            ).ask()
            or ""
        ).strip() or "English"

    return choice


# ---------------------------------------------------------------------------
# Commodity futures helpers
# ---------------------------------------------------------------------------


# 【功能】判断 ticker 是否为商品期货品种代码。
# 【参数】value:原始输入(大小写不敏感)。
# 【返回】bool:True 表示命中 _COMMODITY_CODES。
def is_commodity_ticker(value: str) -> bool:
    """Check if a ticker looks like a commodity variety code."""
    return value.strip().upper() in _COMMODITY_CODES


# 【功能】交互式获取商品期货品种代码(校验须在 _COMMODITY_CODES 内)。
# 【参数】无
# 【返回】str:大写品种代码(如 "RB");取消输入时退出程序。
def get_commodity_ticker() -> str:
    """Prompt for a commodity futures variety code."""
    ticker = questionary.text(
        f"Enter variety code (e.g. {COMMODITY_INPUT_EXAMPLES}):",
        validate=lambda x: (
            is_commodity_ticker(x)
            or f"Please enter a valid variety code. Supported: {', '.join(sorted(_COMMODITY_CODES))}"
        ),
        style=questionary.Style(
            [
                ("text", "fg:green"),
                ("highlighted", "noinherit"),
            ]
        ),
    ).ask()

    if ticker is None:
        console.print("\n[red]No variety code provided. Exiting...[/red]")
        exit(1)

    return ticker.strip().upper()


# 【功能】交互式多选商品期货分析师(技术面 / 基本面 / 宏观面 / 情绪面)。
# 【参数】无
# 【返回】list[AnalystType]:选中的分析师;一个不选或取消时退出程序。
def select_commodity_analysts() -> list[AnalystType]:
    """Select commodity analysts using an interactive checkbox."""
    choices = questionary.checkbox(
        "Select Your [Commodity Analysts Team]:",
        choices=[
            questionary.Choice(display, value=value) for display, value in COMMODITY_ANALYST_ORDER
        ],
        instruction="\n- Press Space to select/unselect\n- Press 'a' to select/unselect all\n- Press Enter when done",
        validate=lambda x: len(x) > 0 or "You must select at least one analyst.",
        style=questionary.Style(
            [
                ("checkbox-selected", "fg:green"),
                ("selected", "fg:green noinherit"),
                ("highlighted", "noinherit"),
                ("pointer", "noinherit"),
            ]
        ),
    ).ask()

    if not choices:
        console.print("\n[red]No analysts selected. Exiting...[/red]")
        exit(1)

    return choices
