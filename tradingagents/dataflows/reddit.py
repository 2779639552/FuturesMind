"""Reddit search fetcher for ticker-specific discussion posts.

Default path is Reddit's public Atom/RSS search feed
(``reddit.com/r/{sub}/search.rss``). The richer JSON search endpoint
(``/search.json``) is reliably WAF-blocked (``HTTP 403``) for public clients
(issue #862), and probing it on every call only doubled our request volume
against Reddit's per-IP rate limit — tripping ``429`` on the RSS fallback — so
it is kept (``_fetch_subreddit_json``) but not used by default. On a 429 we back
off once (honouring ``Retry-After``). RSS lacks score / comment counts, so those
posts are marked and the formatter omits the metrics rather than printing fake
zeros.

No API key required. Returns formatted plaintext blocks ready for prompt
injection and degrades gracefully — returns a placeholder string rather than
raising, so callers never special-case missing data.
"""

from __future__ import annotations

import html  # 【调用包】反转义 HTML 实体
import http.client  # 【调用包】捕获 chunked 传输错误(IncompleteRead/BadStatusLine)
import json  # 【调用包】解析 JSON 搜索接口响应
import logging  # 【调用包】日志
import re  # 【调用包】剥离 HTML 标签
import time  # 【调用包】429 退避睡眠与时间戳格式化
import xml.etree.ElementTree as ET  # 【调用包】解析 RSS/Atom XML 源
from collections.abc import Iterable  # 【调用包】类型标注(可迭代子版块集合)
from datetime import datetime  # 【调用包】解析 ISO 时间戳
from urllib.error import HTTPError  # 【调用包】捕获 HTTP 错误(尤其 429)
from urllib.parse import urlencode  # 【调用包】构造查询字符串
from urllib.request import Request, urlopen  # 【调用包】发起 HTTP 请求(无第三方依赖)

from .symbol_utils import crypto_base  # 【调用包】从 Yahoo 加密货币对提取 base 符号(BTC-USD -> BTC)

logger = logging.getLogger(__name__)

# 【变量】Reddit JSON 搜索接口模板(已被 WAF 403 屏蔽, 保留不用)
_API = "https://www.reddit.com/r/{sub}/search.json?{qs}"
# 【变量】Reddit RSS/Atom 搜索源模板(默认路径)
_RSS = "https://www.reddit.com/r/{sub}/search.rss?{qs}"
# A descriptive, identified User-Agent (per Reddit's API etiquette). Reddit
# blocks generic/anonymous tokens like bare "Mozilla/5.0" or "curl/…" but
# serves this one on both endpoints; the RSS feed accepts it even when the
# JSON search endpoint 403s, so no browser-spoofing is needed.
# 【变量】自标识的 User-Agent(遵循 Reddit API 礼仪)
_UA = "tradingagents/0.2 (+https://github.com/TauricResearch/TradingAgents)"
# 【变量】Atom 命名空间映射, 供 ElementTree 查询 atom: 标签
_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

# Default subreddits ordered roughly by signal density for ticker-specific
# discussion. wallstreetbets has the most volume but most noise; stocks /
# investing trend more measured. Caller can override.
# 【变量】默认搜索的子版块, 大致按"针对个股讨论的信号密度"排序
DEFAULT_SUBREDDITS = ("wallstreetbets", "stocks", "investing")


# 【功能】构造 Reddit 搜索接口的查询字符串参数。
# 【参数】ticker: 搜索关键词(标的); limit: 返回条数上限。
# 【返回】URL 编码后的查询字符串。
# 【关键】t="week" 限定最近 7 天, sort="new" 按最新排序, restrict_sr=on 限当前版块。
def _search_qs(ticker: str, limit: int) -> str:
    return urlencode(  # 【调用函数】URL 编码查询参数
        {
            "q": ticker,
            "restrict_sr": "on",
            "sort": "new",
            "t": "week",  # last 7 days
            "limit": limit,
        }
    )


# 【功能】把 Atom 的 published 时间戳解析为 UTC 纪元秒。
# 【参数】iso_str: ISO 8601 时间字符串, 可能以 Z 结尾。
# 【返回】float 纪元秒; 解析失败或为空时返回 None。
def _iso_to_timestamp(iso_str: str | None) -> float | None:
    """Parse an Atom ``published`` timestamp to a UTC epoch, or None."""
    if not iso_str:
        return None
    try:
        normalized = iso_str[:-1] + "+00:00" if iso_str.endswith("Z") else iso_str  # 【变量】把 Z 结尾统一为 +00:00 时区
        return datetime.fromisoformat(normalized).timestamp()  # 【调用函数】ISO 字符串转纪元秒
    except (ValueError, TypeError):
        return None


# 【功能】把 Reddit 在 Atom 条目里嵌的 HTML 正文精简为纯文本。
# 【关键】正文夹在 <!-- SC_OFF --> 与 <!-- SC_ON --> 标记之间, 先截取中间段,
#         再剥掉所有 HTML 标签, 反转义实体并压缩空白。
def _strip_html(content: str) -> str:
    """Reduce the HTML body Reddit embeds in an Atom entry to plain text."""
    if not content:
        return ""
    # Reddit wraps the real selftext between SC_OFF / SC_ON markers.
    if "<!-- SC_OFF -->" in content and "<!-- SC_ON -->" in content:
        content = content.split("<!-- SC_OFF -->")[1].split("<!-- SC_ON -->")[0]  # 【变量】截取 SC_OFF/SC_ON 之间的真实正文
    text = re.sub(r"<[^>]+>", " ", content)  # 【调用函数】正则剥离 HTML 标签
    return " ".join(html.unescape(text).split())  # 【调用函数】反转义实体并压缩空白


# 【功能】从 429 响应的 Retry-After 头读取等待秒数, 上限 30 秒。
# 【参数】exc: HTTPError(429)。
# 【返回】float 秒数; 无该头或解析失败时返回 None。
def _retry_after_seconds(exc: HTTPError) -> float | None:
    """Seconds to wait from a 429's ``Retry-After`` header, capped at 30s."""
    try:
        val = exc.headers.get("Retry-After") if getattr(exc, "headers", None) else None
        return min(float(val), 30.0) if val else None
    except (ValueError, TypeError, AttributeError):
        return None


# 【功能】默认路径: 解析某版块的公共 Atom 搜索源, 返回规范化帖子列表。
# 【参数】ticker: 搜索关键词; sub: 版块名; limit: 条数上限; timeout: 请求超时;
#         _retry: 是否允许对 429 退避重试一次(内部递归时置 False)。
# 【返回】list[dict], 每个 dict 含 title/score/num_comments/created_utc/selftext/source。
# 【关键】RSS 不带 score/评论数, 故这些字段置 None、标记 source="rss" 诚实展示;
#         429 时按 Retry-After 退避一次再给, 避免瞬时突发把整页刷空。
def _fetch_subreddit_rss(
    ticker: str,
    sub: str,
    limit: int,
    timeout: float,
    _retry: bool = True,
) -> list[dict]:
    """Default path: parse the public Atom search feed for a subreddit.

    Carries no score / comment counts, so those fields are left None and the
    post is tagged ``source="rss"`` for honest display. On a 429 (Reddit's
    per-IP rate limit) we back off once — honouring ``Retry-After`` when
    present — before giving up, so a transient burst doesn't blank the feed.
    """
    url = _RSS.format(sub=sub, qs=_search_qs(ticker, limit))  # 【变量】拼出 RSS 源 URL
    req = Request(url, headers={"User-Agent": _UA})  # 【调用函数】构造带 UA 的请求
    try:
        with urlopen(req, timeout=timeout) as resp:  # 【调用函数】发起外部 HTTP 请求
            root = ET.fromstring(resp.read())  # 【调用函数】解析 XML 响应
    except HTTPError as exc:
        if exc.code == 429 and _retry:
            wait = _retry_after_seconds(exc) or 5.0  # 【变量】退避秒数(Retry-After 缺省 5s)
            logger.warning(
                "Reddit RSS 429 for r/%s · %s — backing off %.1fs then retrying once",
                sub,
                ticker,
                wait,
            )
            time.sleep(wait)
            return _fetch_subreddit_rss(ticker, sub, limit, timeout, _retry=False)  # 【调用函数】退避后递归重试一次
        logger.warning("Reddit RSS fetch failed for r/%s · %s: %s", sub, ticker, exc)
        return []
    except (OSError, http.client.HTTPException, ET.ParseError) as exc:
        # OSError covers URLError/TimeoutError/connection resets; HTTPException
        # covers chunked-transfer errors (IncompleteRead/BadStatusLine, #1024).
        logger.warning("Reddit RSS fetch failed for r/%s · %s: %s", sub, ticker, exc)
        return []

    posts = []  # 【变量】规范化后的帖子列表
    for entry in root.findall("atom:entry", _ATOM_NS)[:limit]:  # 【调用函数】按命名空间遍历 atom:entry
        title_el = entry.find("atom:title", _ATOM_NS)
        published_el = entry.find("atom:published", _ATOM_NS)
        content_el = entry.find("atom:content", _ATOM_NS)
        posts.append(
            {
                "title": (title_el.text if title_el is not None else "") or "",
                "score": None,  # 【变量】RSS 无点赞数, 置 None 以便格式化时省略
                "num_comments": None,  # 【变量】RSS 无评论数, 置 None
                "created_utc": _iso_to_timestamp(
                    published_el.text if published_el is not None else None
                ),
                "selftext": _strip_html(content_el.text if content_el is not None else ""),
                "source": "rss",
            }
        )
    return posts


# 【功能】更丰富的 JSON 搜索路径(带 score/评论数), 但目前不被默认使用。
# 【参数】ticker: 搜索关键词; sub: 版块名; limit: 条数上限; timeout: 请求超时。
# 【返回】list[dict](原始 data 结构); 失败时回退到 RSS 路径。
# 【关键】Reddit WAF 对非 OAuth 客户端稳定返回 403 Blocked(#862), 故默认不调用,
#         以免对限流雪上加霜; 保留以便 WAF 放松或接入 OAuth 后启用。
def _fetch_subreddit_json(
    ticker: str,
    sub: str,
    limit: int,
    timeout: float,
) -> list[dict]:
    """Richer JSON search path (carries score / comment counts).

    Reddit's WAF currently returns ``403 Blocked`` on this endpoint for
    non-OAuth clients (issue #862), so it is NOT used by default — calling it on
    every request only doubled our volume against the per-IP rate limit and
    triggered 429s on the RSS fallback. Kept for the day the WAF relaxes or an
    OAuth token is wired in; degrades to RSS on failure.
    """
    url = _API.format(sub=sub, qs=_search_qs(ticker, limit))  # 【变量】拼出 JSON 接口 URL
    req = Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})  # 【调用函数】构造带 UA 的 JSON 请求
    try:
        with urlopen(req, timeout=timeout) as resp:  # 【调用函数】发起外部 HTTP 请求
            payload = json.loads(resp.read())  # 【调用函数】解析 JSON 响应
        children = (payload.get("data") or {}).get("children") or []  # 【变量】帖子子节点列表
        return [c.get("data", {}) for c in children if isinstance(c, dict)]
    except (OSError, http.client.HTTPException, json.JSONDecodeError) as exc:
        logger.warning(
            "Reddit JSON fetch failed for r/%s · %s: %s — falling back to RSS feed.",
            sub,
            ticker,
            exc,
        )
        return _fetch_subreddit_rss(ticker, sub, limit, timeout)  # 【调用函数】失败回退到 RSS 路径


# 【功能】拉取单个版块, RSS 优先的统一入口。
# 【关键】JSON 搜索接口对公共客户端稳定被 WAF 403, 故直接走 RSS 源(能可靠
#         服务自标识 UA), 从而把对 Reddit 每 IP 限流的请求量减半。
def _fetch_subreddit(
    ticker: str,
    sub: str,
    limit: int,
    timeout: float,
) -> list[dict]:
    """Fetch one subreddit, RSS-first.

    The JSON search endpoint is reliably WAF-blocked (403) for public clients,
    so we go straight to the RSS feed — which serves our identified User-Agent
    reliably — halving our request volume against Reddit's per-IP rate limit.
    """
    return _fetch_subreddit_rss(ticker, sub, limit, timeout)  # 【调用函数】委托 RSS 路径


# 【功能】跨多个金融版块拉取提及某标的的近期 Reddit 帖子, 返回格式化纯文本块。
# 【参数】ticker: 标的; subreddits: 要搜索的版块集合; limit_per_sub: 每版块条数;
#         timeout: 单请求超时; inter_request_delay: 版块间请求间隔(限流节拍)。
# 【返回】纯文本报告(可直接注入 prompt); 无帖时返回占位说明(绝不抛异常)。
# 【关键】crypto 以 Yahoo 对(BTC-USD)形式到达, 先取 base("BTC")再搜, 否则几乎
#         搜不到讨论; inter_request_delay 节拍 + RSS 优先使 429 极少出现。
def fetch_reddit_posts(
    ticker: str,
    subreddits: Iterable[str] = DEFAULT_SUBREDDITS,
    limit_per_sub: int = 5,
    timeout: float = 10.0,
    inter_request_delay: float = 1.0,
) -> str:
    """Fetch recent Reddit posts mentioning ``ticker`` across finance
    subreddits and return them as a formatted plaintext block.

    ``inter_request_delay`` paces the (now RSS-only) per-subreddit requests to
    stay under Reddit's public per-IP rate limit; combined with the RSS-first
    path it makes 429s rare even when several analyses run back-to-back.
    """
    # Crypto reaches us as a Yahoo pair (BTC-USD); search Reddit for the base
    # ("BTC") so the query actually matches discussion instead of near-nothing.
    ticker = crypto_base(ticker) or ticker  # 【调用函数】加密货币对提取 base 符号
    blocks = []  # 【变量】各版块的文本块集合
    total_posts = 0
    for i, sub in enumerate(subreddits):
        if i > 0:
            time.sleep(inter_request_delay)  # 【调用函数】版块间限流节拍
        posts = _fetch_subreddit(ticker, sub, limit_per_sub, timeout)  # 【调用函数】拉取单版块帖子
        total_posts += len(posts)
        if not posts:
            blocks.append(
                f"r/{sub}: <no posts found mentioning {ticker.upper()} in the past 7 days>"
            )
            continue

        via_rss = any(p.get("source") == "rss" for p in posts)  # 【变量】是否全部走 RSS(即无点赞/评论数据)
        header = f"r/{sub} — {len(posts)} recent posts mentioning {ticker.upper()}"
        header += " (via RSS feed; scores/comments unavailable):" if via_rss else ":"
        lines = [header]
        for p in posts:
            title = (p.get("title") or "").replace("\n", " ").strip()
            score = p.get("score")
            comments = p.get("num_comments")
            created = p.get("created_utc")
            created_str = time.strftime("%Y-%m-%d", time.gmtime(created)) if created else "?"
            # Score / comment counts are absent on the RSS fallback path —
            # show them only when present rather than printing fake zeros.
            meta = created_str  # 【变量】每帖的元信息前缀(日期 [+ 点赞 + 评论])
            if score is not None and comments is not None:
                meta += f" · {score:>4}↑ · {comments:>3}c"
            selftext = (p.get("selftext") or "").replace("\n", " ").strip()  # 【变量】正文摘录(压缩换行)
            if len(selftext) > 240:
                selftext = selftext[:240] + "…"
            lines.append(
                f"  [{meta}] {title}" + (f"\n    body excerpt: {selftext}" if selftext else "")
            )
        blocks.append("\n".join(lines))

    if total_posts == 0:
        return (
            f"<no Reddit posts found mentioning {ticker.upper()} across "
            f"{', '.join(f'r/{s}' for s in subreddits)} in the past 7 days>"
        )
    return "\n\n".join(blocks)
