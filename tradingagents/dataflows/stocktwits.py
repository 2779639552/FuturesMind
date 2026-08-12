"""StockTwits public symbol-stream fetcher.

StockTwits exposes a per-symbol message stream at
``api.stocktwits.com/api/2/streams/symbol/{ticker}.json`` that requires no
API key, no OAuth, and no registration. Each message includes a
user-labeled sentiment field (``Bullish``/``Bearish``/null), the message
body, timestamp, and posting user.

The function is deliberately self-contained: short timeout, graceful
degradation on any HTTP or parse failure, and a string return type so
the calling agent gets a uniform interface regardless of whether the
network call succeeded.
"""

from __future__ import annotations  # 【调用包】启用延迟求值的类型注解

import http.client  # 【调用包】捕获 chunked 传输错误(IncompleteRead/BadStatusLine)
import json  # 【调用包】解析 JSON 响应
import logging  # 【调用包】日志
from urllib.request import Request, urlopen  # 【调用包】发起 HTTP 请求(无第三方依赖)

from .symbol_utils import crypto_base  # 【调用包】提取加密 base(BTC-USD -> BTC)

logger = logging.getLogger(__name__)

# 【变量】StockTwits 单符号消息流接口模板(无需 Key/OAuth)
_API = "https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
# 【变量】自标识的 User-Agent
_UA = "tradingagents/0.2 (+https://github.com/TauricResearch/TradingAgents)"


# 【功能】把标的符号映射为 StockTwits 约定(加密对 -> <BASE>.X)。
# 【关键】StockTwits 用 BTC.X 形式(Yahoo 的 BTC-USD 会 404), 故加密符号解析为
#         base 加 .X; 其他符号原样大写直通。
def _stocktwits_symbol(ticker: str) -> str:
    """Map a crypto pair to StockTwits' ``<BASE>.X`` convention.

    StockTwits lists crypto as ``BTC.X`` (Yahoo's ``BTC-USD`` form 404s), so any
    crypto symbol resolves to its base plus ``.X``; other symbols pass through
    upper-cased.
    """
    base = crypto_base(ticker)  # 【调用函数】提取加密 base
    return f"{base}.X" if base else ticker.strip().upper()


# 【功能】拉取某标的最近的 StockTwits 消息, 返回格式化纯文本块(可注入 prompt)。
# 【参数】ticker: 标的; limit: 返回消息条数; timeout: 请求超时。
# 【返回】纯文本报告(含多空统计摘要 + 每帖一行); 端点不可达/无消息/响应形状
#         异常时返回占位字符串(调用方无需特判 None 或异常)。
# 【关键】接口无需 Key/OAuth; 任何 HTTP/解析失败都优雅降级。
def fetch_stocktwits_messages(ticker: str, limit: int = 30, timeout: float = 10.0) -> str:
    """Fetch recent StockTwits messages for ``ticker`` and return them as a
    formatted plaintext block ready for prompt injection.

    Returns a placeholder string when the endpoint is unreachable, the
    symbol has no messages, or the response shape is unexpected — the
    caller never has to special-case None or exceptions.
    """
    url = _API.format(ticker=_stocktwits_symbol(ticker))  # 【变量】拼出接口 URL
    req = Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})  # 【调用函数】构造带 UA 的请求
    try:
        with urlopen(req, timeout=timeout) as resp:  # 【调用函数】发起外部 HTTP 请求
            data = json.loads(resp.read())  # 【调用函数】解析 JSON 响应
    except (OSError, http.client.HTTPException, json.JSONDecodeError) as exc:
        # OSError covers URLError/TimeoutError/connection resets; HTTPException
        # covers chunked-transfer errors (IncompleteRead/BadStatusLine, #1024).
        logger.warning("StockTwits fetch failed for %s: %s", ticker, exc)
        return f"<stocktwits unavailable: {type(exc).__name__}>"  # 【调用函数】失败时返回占位字符串

    messages = data.get("messages", []) if isinstance(data, dict) else []  # 【变量】消息列表
    if not messages:
        return f"<no StockTwits messages found for ${ticker.upper()}>"

    lines = []  # 【变量】每帖一行的文本行
    bullish = bearish = unlabeled = 0  # 【变量】多/空/无标签消息计数
    for m in messages[:limit]:
        created = m.get("created_at", "")
        user = (m.get("user") or {}).get("username", "?")
        entities = m.get("entities") or {}
        sentiment_obj = entities.get("sentiment") or {}
        sentiment = sentiment_obj.get("basic") if isinstance(sentiment_obj, dict) else None  # 【变量】用户标注的多空情绪
        body = (m.get("body") or "").replace("\n", " ").strip()  # 【变量】正文(压缩换行)
        if len(body) > 280:
            body = body[:280] + "…"

        if sentiment == "Bullish":
            bullish += 1
            tag = "Bullish"
        elif sentiment == "Bearish":
            bearish += 1
            tag = "Bearish"
        else:
            unlabeled += 1
            tag = "no-label"  # 【变量】无标签帖的展示标记
        lines.append(f"[{created} · @{user} · {tag}] {body}")

    total = bullish + bearish + unlabeled  # 【变量】统计的消息总数
    bull_pct = round(100 * bullish / total) if total else 0  # 【变量】多头占比(%)
    bear_pct = round(100 * bearish / total) if total else 0  # 【变量】空头占比(%)
    summary = (
        f"Bullish: {bullish} ({bull_pct}%) · "
        f"Bearish: {bearish} ({bear_pct}%) · "
        f"Unlabeled: {unlabeled} · "
        f"Total: {total} most-recent messages"
    )
    return summary + "\n\n" + "\n".join(lines)
