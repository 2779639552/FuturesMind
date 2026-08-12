"""Symbol normalization and market-data error types for vendor calls.

Yahoo Finance (the default vendor) uses specific ticker conventions that
differ from the broker / TradingView / MT5 style symbols users often type:

    user types        Yahoo wants       why
    ---------------   ---------------   -----------------------------------
    XAUUSD, XAUUSD+   GC=F              gold has no forex pair on Yahoo;
                                        it is quoted as a COMEX future
    EURUSD            EURUSD=X          spot forex pairs take a ``=X`` suffix
    BTCUSD            BTC-USD           crypto pairs use a ``-`` separator
    SPX500, US500     ^GSPC             index CFDs map to Yahoo index symbols

Passing the raw broker symbol to Yahoo returns an empty result, which the
agents previously received as free text and could hallucinate a price
around (see issue #781). Centralizing the mapping here means every yfinance
entry point resolves symbols the same way, and new instruments are added by
appending a table row rather than editing call sites.
"""

from __future__ import annotations

import logging  # 【调用包】日志
import re  # 【调用包】正则校验 Yahoo 符号合法性

# NoMarketDataError lives in the vendor-error taxonomy (errors.py); re-exported
# here for the many call sites that import it alongside normalize_symbol.
from .errors import NoMarketDataError as NoMarketDataError  # 【调用包】无数据异常(从 vendor 错误体系重导出)

logger = logging.getLogger(__name__)


# ISO-4217 codes common enough to appear in retail forex pairs. A bare
# six-letter symbol whose halves are BOTH in this set is treated as a spot
# forex pair and given Yahoo's ``=X`` suffix.
# 【变量】常见 ISO-4217 币种码集合: 六字母符号若前后两半都在此集合, 视为外汇
#         现货对, 加 Yahoo 的 =X 后缀
_FOREX_CURRENCIES = frozenset(
    {
        "USD",
        "EUR",
        "GBP",
        "JPY",
        "CHF",
        "CAD",
        "AUD",
        "NZD",
        "CNY",
        "CNH",
        "HKD",
        "SGD",
        "SEK",
        "NOK",
        "DKK",
        "PLN",
        "MXN",
        "ZAR",
        "TRY",
        "INR",
        "KRW",
        "BRL",
        "RUB",
        "THB",
    }
)

# Crypto bases that brokers quote against USD without a separator.
# 【变量】券商无分隔符报价的加密货币 base 集合(用于识别 BTCUSD 等)
_CRYPTO_BASES = frozenset(
    {"BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "LTC", "BCH", "DOT", "AVAX", "LINK"}
)

# Explicit aliases for instruments whose broker symbol does not map to a
# Yahoo symbol by rule. Metals/energy resolve to their front-month future;
# index CFD names resolve to the underlying Yahoo index symbol. Extend by
# adding rows — no call site changes required.
# 【变量】无法靠规则映射的显式别名表: 金属/能源解析为近月期货, 指数 CFD 解析为
#         对应 Yahoo 指数符号; 加行即可扩展, 无需改调用点
_ALIASES = {
    # Precious metals (spot names -> COMEX/NYMEX futures)
    "XAUUSD": "GC=F",
    "XAU": "GC=F",
    "GOLD": "GC=F",
    "XAGUSD": "SI=F",
    "XAG": "SI=F",
    "SILVER": "SI=F",
    "XPTUSD": "PL=F",
    "XPDUSD": "PA=F",
    # Energy
    "WTICOUSD": "CL=F",
    "USOIL": "CL=F",
    "WTI": "CL=F",
    "BCOUSD": "BZ=F",
    "UKOIL": "BZ=F",
    "BRENT": "BZ=F",
    "NATGAS": "NG=F",
    "XNGUSD": "NG=F",
    "COPPER": "HG=F",
    "XCUUSD": "HG=F",
    # Index CFDs -> Yahoo index symbols
    "SPX500": "^GSPC",
    "US500": "^GSPC",
    "SPX": "^GSPC",
    "NAS100": "^NDX",
    "US100": "^NDX",
    "USTEC": "^NDX",
    "US30": "^DJI",
    "DJI30": "^DJI",
    "WS30": "^DJI",
    "GER40": "^GDAXI",
    "GER30": "^GDAXI",
    "DE40": "^GDAXI",
    "UK100": "^FTSE",
    "JP225": "^N225",
    "JPN225": "^N225",
    "FRA40": "^FCHI",
    "EU50": "^STOXX50E",
    "HK50": "^HSI",
}

# Yahoo symbols may contain letters, digits, and these structural characters.
# 【变量】合法 Yahoo 符号字符集正则(字母/数字/._-^=)
_YAHOO_SAFE = re.compile(r"^[A-Za-z0-9._\-\^=]+$")


# Crypto quote currencies that all map to Yahoo's USD pair. Yahoo lists only
# ``<BASE>-USD`` (not the USDT/USDC stablecoin pairs), so a broker symbol quoted
# in any of these resolves to ``-USD`` (#982). Longest first so ``USDT``/``USDC``
# match before the ``USD`` substring.
# 【变量】加密货币计价货币集合: 均映射到 Yahoo 的 USD 对(Yahoo 只有 <BASE>-USD,
#         无 USDT/USDC 稳定币对)。最长的排前面, 使 USDT/USDC 先于 USD 子串匹配。
_CRYPTO_QUOTES = ("USDT", "USDC", "USD")


# 【功能】提取已知加密符号的 base(如 BTC-USD / BTCUSD / BTC-USDT -> "BTC")。
# 【返回】base 字符串; 非加密符号返回 None。
# 【关键】纯语法判断, 不做网络调用; 处理管线中各种形式(带 -/不带 -/带 +)。
def crypto_base(raw: str) -> str | None:
    """Return the crypto base (e.g. ``BTC``) for a known USD/USDT/USDC-quoted
    crypto symbol in any form the pipeline may hold — ``BTC-USD``, ``BTCUSD``,
    ``BTC-USDT`` — or None for non-crypto symbols. Purely syntactic.
    """
    if not isinstance(raw, str):
        return None
    compact = raw.strip().upper().rstrip("+").replace("-", "")  # 【变量】去掉空白/大写的 + 后缀/连字符
    for quote in _CRYPTO_QUOTES:
        if compact.endswith(quote):
            base = compact[: -len(quote)]  # 【变量】去掉计价货币后的 base
            return base if base in _CRYPTO_BASES else None
    return None


# 【功能】把已知加密符号归一化为 ``<BASE>-USD`` 形式; 非加密返回 None。
def _normalize_crypto(s: str) -> str | None:
    """Return ``<BASE>-USD`` for a known USD/USDT/USDC-quoted crypto, else None."""
    base = crypto_base(s)  # 【调用函数】提取 base
    return f"{base}-USD" if base else None


# 【功能】把用户/券商符号映射为 Yahoo Finance 规范符号。
# 【参数】raw: 用户输入符号(可为任意形式)。
# 【返回】规范 Yahoo 符号字符串。
# 【关键】解析顺序(首个命中即返回): ① 显式别名表; ② 加密规则(已知 base + USD/
#         USDT/USDC 计价, 带不带连字符) -> BASE-USD; ③ 外汇规则(六字母且两半都是
#         币种码) -> PAIR=X; ④ 其余原样大写(普通股票/ETF/Yahoo 原生符号如 GC=F、
#         ^GSPC)。尾部 + (券商 CFD 标记)先剥除。纯语法、无网络调用, 可安全地
#         应用到每个请求。
def normalize_symbol(raw: str) -> str:
    """Map a user/broker symbol to its canonical Yahoo Finance symbol.

    Resolution order (first match wins):
      1. Explicit alias table (metals, energy, index CFDs).
      2. Crypto rule: a known crypto base quoted in USD/USDT/USDC (dashed or
         not) -> ``BASE-USD``.
      3. Forex rule: six letters that are two ISO currency codes -> ``PAIR=X``.
      4. Otherwise the upper-cased symbol is returned unchanged (plain
         equities, ETFs, Yahoo-native symbols like ``GC=F`` or ``^GSPC``).

    A trailing ``+`` (broker CFD marker, e.g. ``XAUUSD+``) is stripped before
    matching. The function is purely syntactic — it performs no network
    calls — so it is safe to apply on every request.
    """
    if not isinstance(raw, str) or not raw.strip():
        return raw

    s = raw.strip().upper()  # 【变量】去空白并大写
    # Broker CFD/qualifier suffixes Yahoo never uses.
    s = s.rstrip("+")  # 【变量】剥掉尾部 + (券商 CFD 标记)

    crypto = _normalize_crypto(s)  # 【调用函数】加密规则解析
    if s in _ALIASES:
        canonical = _ALIASES[s]  # 【变量】命中显式别名表
    elif crypto is not None:
        canonical = crypto  # 【变量】命中加密规则 -> BASE-USD
    elif len(s) == 6 and s[:3] in _FOREX_CURRENCIES and s[3:] in _FOREX_CURRENCIES:
        canonical = f"{s}=X"  # 【变量】命中外汇规则 -> PAIR=X
    else:
        canonical = s  # 【变量】其余原样大写返回

    if canonical != raw.strip().upper():
        logger.info("Resolved symbol %r to Yahoo symbol %r", raw, canonical)
    return canonical


# 【功能】判断符号是否只含 Yahoo 符号使用的字符。
# 【返回】布尔值。
def is_yahoo_safe(symbol: str) -> bool:
    """True when ``symbol`` only contains characters Yahoo symbols use."""
    return bool(symbol) and _YAHOO_SAFE.fullmatch(symbol) is not None  # 【调用函数】正则全匹配校验
