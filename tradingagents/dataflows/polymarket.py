"""Polymarket prediction-market vendor.

Surfaces live, market-implied probabilities for forward-looking events (Fed
decisions, recession, elections, geopolitics, crypto) to the news analyst, as a
complement to news (what happened) and FRED macro data (where things stand):
what the crowd actually prices to happen next.

Uses Polymarket's public Gamma API (https://gamma-api.polymarket.com) — no key,
no auth. Each market's ``outcomePrices`` are the implied probabilities of its
outcomes (a "Yes" at 0.76 means the market prices a 76% chance).
"""

import json  # 【调用包】解析 outcomes/outcomePrices 的 JSON 字符串数组
import logging  # 【调用包】日志
from datetime import datetime, timezone  # 【调用包】时间戳与时区(比较 endDate)

import requests  # 【调用包】发起 Gamma API 的 HTTP GET 请求

logger = logging.getLogger(__name__)

# 【变量】Polymarket 公开 Gamma API 基地址(无需 Key/Auth)
GAMMA_BASE = "https://gamma-api.polymarket.com"

# Network timeout (seconds), consistent with the other vendors.
# 【变量】网络超时(秒), 与其他厂商一致
REQUEST_TIMEOUT = 30

# Default number of markets to return, ranked by traded volume.
# 【变量】默认返回市场数(按成交量排序取前 N)
DEFAULT_LIMIT = 6


# 【功能】对 Gamma API 发起 GET 请求并返回解析后的 JSON。
# 【参数】path: 接口路径; params: 查询参数。
# 【返回】dict(JSON 响应)。
# 【异常】requests.RequestException: 网络错误由调用方处理。
def _request(path: str, params: dict) -> dict:
    response = requests.get(f"{GAMMA_BASE}/{path}", params=params, timeout=REQUEST_TIMEOUT)  # 【调用函数】外部 HTTP GET
    response.raise_for_status()  # 【调用函数】HTTP 错误上抛
    return response.json()  # 【调用函数】解析 JSON 响应


# 【功能】把 Gamma 的 JSON 字符串数组解析为 list(兼容已是 list 的情况)。
# 【关键】Gamma 把 outcomes/outcomePrices 编码为 JSON 字符串数组。
def _parse_json_list(value) -> list:
    """Gamma encodes ``outcomes``/``outcomePrices`` as JSON-string arrays."""
    if isinstance(value, list):
        return value
    try:
        return json.loads(value)  # 【调用函数】解析 JSON 字符串
    except (json.JSONDecodeError, TypeError):
        return []  # 【调用函数】解析失败返回空列表


# 【功能】筛选"仍开放且在未来结算"的远期市场。
# 【参数】market: 单个市场 dict; now: 当前 UTC 时间。
# 【返回】布尔值。
# 【关键】closed 才是可靠已结算标志(active 对已结算市场仍为 True); endDate 在过去
#         说明事件已结算——任一情况都不是远期信号。
def _is_forward_looking(market: dict, now: datetime) -> bool:
    """Keep only open markets that resolve in the future.

    ``closed`` is the reliable resolved flag (``active`` stays True even for
    settled markets), and a past ``endDate`` means the event already resolved —
    either way it is not a forward-looking signal.
    """
    if market.get("closed"):
        return False
    end_date = market.get("endDate")  # 【变量】结算日期
    if end_date:
        try:
            if datetime.fromisoformat(end_date.replace("Z", "+00:00")) < now:  # 【调用函数】endDate 已过则非远期
                return False
        except ValueError:
            pass
    return bool(_parse_json_list(market.get("outcomePrices"))) and bool(
        _parse_json_list(market.get("outcomes"))
    )


# 【功能】按事件主题返回实时的预测市场概率(即人群中为事件定价的概率)。
# 【参数】topic: 事件关键词(如 "Fed rate cut"/"recession 2026"/"US election");
#         limit: 返回市场数上限(None 用 DEFAULT_LIMIT)。
# 【返回】markdown 报告: 与该主题匹配、成交量最高且仍在开放的市场, 每个含隐含概率、
#         成交量、结算日期与近 1 周变动。
# 【关键】作为新闻(发生了什么)/FRED 宏观(现状)之外的补充: 市场如何定价"接下来会发生"。
def get_prediction_markets(topic: str, limit: int | None = None) -> str:
    """Return live prediction-market probabilities for an event topic.

    Args:
        topic: Event keyword(s), e.g. "Fed rate cut", "recession 2026",
            "US election", or a sector/company event.
        limit: Max markets to return (ranked by traded volume); ``None`` uses
            DEFAULT_LIMIT.

    Returns:
        A markdown report of the most-traded open markets matching the topic,
        each with its implied probability, traded volume, resolution date, and
        recent (1-week) move.
    """
    if limit is None:
        limit = DEFAULT_LIMIT

    try:
        data = _request("public-search", {"q": topic, "limit_per_type": 20})  # 【调用函数】Gamma 公开搜索接口
    except requests.RequestException as e:
        logger.warning("Polymarket search failed for %r: %s", topic, e)
        return (
            f"Polymarket data is currently unavailable (network error: {e}). "
            f"Proceed without prediction-market signal for '{topic}'."
        )

    now = datetime.now(timezone.utc)  # 【变量】当前 UTC 时间
    candidates = [  # 【变量】所有远期市场(从 events 下展开 markets 并过滤)
        m
        for event in data.get("events", [])
        for m in event.get("markets", [])
        if _is_forward_looking(m, now)
    ]
    candidates.sort(key=lambda m: m.get("volumeNum") or 0, reverse=True)  # 【调用函数】按成交量降序

    header = (
        f'## Polymarket prediction markets: "{topic}"\n'
        f"Live, market-implied probabilities (higher traded volume = deeper, "
        f"more reliable). A probability is the crowd's priced odds of the event, "
        f"not a forecast you should take as certain.\n\n"
    )

    if not candidates:
        return header + (
            f"No open prediction markets matched '{topic}'. Polymarket coverage "
            f"is concentrated in macro, political, geopolitical, and crypto "
            f"events; a specific equity may have none."
        )

    lines = []  # 【变量】每市场一行
    for m in candidates[:limit]:
        prices = _parse_json_list(m.get("outcomePrices"))  # 【调用函数】解析隐含概率数组
        outcomes = _parse_json_list(m.get("outcomes"))  # 【调用函数】解析结果标签数组
        try:
            prob = float(prices[0])  # 【变量】第一个结果的隐含概率
        except (ValueError, IndexError):
            continue
        label = outcomes[0] if outcomes else "Yes"  # 【变量】结果标签(缺省 Yes)
        volume = m.get("volumeNum") or 0  # 【变量】成交量
        end_date = (m.get("endDate") or "")[:10]  # 【变量】结算日期(yyyy-mm-dd)
        wk = m.get("oneWeekPriceChange")  # 【变量】近 1 周价格变动
        wk_str = f", 1-week {wk * 100:+.1f}pp" if isinstance(wk, (int, float)) and wk else ""
        lines.append(
            f"- **{m.get('question')}** — {label} {prob:.0%} "
            f"(${volume:,.0f} volume, resolves {end_date}{wk_str})"
        )

    return header + "\n".join(lines) + "\n"
