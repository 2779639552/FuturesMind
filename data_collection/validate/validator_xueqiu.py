"""
雪球数据采集验证器
===================
雪球是中国最大的投资者社区，期货讨论信息质量极高。
它的搜索API开放度好，反爬力度远低于娱乐社交平台。

核心接口:
- 搜索: https://xueqiu.com/statuses/search.json
- 热帖: https://xueqiu.com/statuses/hots.json

注意: 雪球需要先访问首页获取Cookie (xq_a_token, cookies)，否则API返回401。

使用方式:
    python validator_xueqiu.py
"""

import json
import time
import random
import logging
import argparse
import sys
import re
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import (
    SEARCH_KEYWORDS, REQUEST_HEADERS, USER_AGENTS,
    REQUEST_TIMEOUT, MIN_DELAY, MAX_DELAY, MAX_RETRIES, BACKOFF_FACTOR,
    ENDPOINTS, ValidationCriteria, OUTPUT_DIR, LOG_FORMAT, LOG_LEVEL,
    FUTURES_KEYWORDS,
)

logger = logging.getLogger("xueqiu.validator")


# ============================================================
# 数据结构
# ============================================================

@dataclass
class XueqiuPost:
    """雪球帖子"""
    post_id: str
    title: str
    text: str
    created_at: Optional[str] = None
    user_name: Optional[str] = None
    user_id: Optional[str] = None
    reply_count: int = 0
    retweet_count: int = 0
    like_count: int = 0
    view_count: int = 0
    tags: list = field(default_factory=list)
    stocks: list = field(default_factory=list)  # 关联的股票/期货
    raw: dict = field(default_factory=dict)


@dataclass
class XueqiuValidationResult:
    platform: str
    accessible: bool
    total_requests: int
    successful_requests: int
    total_results: int
    relevant_results: int
    avg_response_time: float
    max_freshness_minutes: Optional[float]
    sustained_success_rate: float
    # 雪球特有指标
    avg_like_count: float = 0.0
    avg_reply_count: float = 0.0
    unique_stocks: set = field(default_factory=set)
    errors: list = field(default_factory=list)
    sample_posts: list = field(default_factory=list)

    @property
    def relevance_rate(self) -> float:
        if self.total_results == 0:
            return 0.0
        return self.relevant_results / self.total_results

    @property
    def passed(self) -> bool:
        checks = [
            self.accessible,
            self.relevant_results >= ValidationCriteria.MIN_VALID_RESULTS,
            self.relevance_rate >= ValidationCriteria.MIN_RELEVANCE_RATE,
            self.sustained_success_rate >= ValidationCriteria.MIN_SUCCESS_RATE,
        ]
        return all(checks)

    def to_report(self) -> str:
        status = "✅ 通过" if self.passed else "❌ 未通过"
        lines = [
            f"\n{'='*60}",
            f"雪球 (xueqiu.com) 验证结果: {status}",
            f"{'='*60}",
            f"  可接入性:     {'✅' if self.accessible else '❌'}",
            f"  请求成功率:   {self.successful_requests}/{self.total_requests}",
            f"  总结果数:     {self.total_results}",
            f"  相关结果数:   {self.relevant_results}",
            f"  相关率:       {self.relevance_rate*100:.0f}%",
            f"  平均响应时间: {self.avg_response_time:.2f}s",
            f"  平均点赞数:   {self.avg_like_count:.0f}",
            f"  平均评论数:   {self.avg_reply_count:.0f}",
            f"  关联标的数:   {len(self.unique_stocks)}",
            f"  持续请求成功率: {self.sustained_success_rate*100:.0f}%",
        ]
        if self.sample_posts:
            lines.append(f"\n  样本帖子 (前5条):")
            for i, post in enumerate(self.sample_posts[:5], 1):
                title_preview = post["title"][:80]
                lines.append(f"  {i}. @{post['user_name']} | {title_preview}")
                lines.append(f"     💬{post['reply_count']} 🔄{post['retweet_count']} ❤️{post['like_count']} | {post['created_at']}")
                if post.get("stocks"):
                    lines.append(f"     🏷️ 关联: {', '.join(post['stocks'][:5])}")
        if self.errors:
            lines.append(f"\n  错误:")
            for err in self.errors[:5]:
                lines.append(f"  - {err}")
        return '\n'.join(lines)


# ============================================================
# 雪球期货相关性判断（更精准，利用雪球的标签和关联标的）
# ============================================================

# 期货品种在雪球上的代码前缀
XUEQIU_FUTURES_PREFIXES = {
    "RB", "I", "HC", "J", "JM", "SF", "SM", "WR",     # 黑色系
    "CU", "AL", "ZN", "PB", "NI", "SN", "AU", "AG",   # 有色
    "SC", "TA", "MA", "V", "PP", "L", "RU", "BU",     # 能化
    "UR", "SA", "FG", "EG", "EB", "PF", "PG",
    "M", "Y", "P", "RM", "OI", "SR", "CF", "C", "CS", # 农产品
    "JD", "LH", "AP", "CJ", "PK",
    "IF", "IC", "IH", "IM", "T", "TF", "TS", "TL",    # 金融
}


def is_xueqiu_futures_related(post: XueqiuPost) -> bool:
    """雪球专用：结合标签和关联标的判断"""
    # 如果有明确的关联期货标的
    if post.stocks:
        for stock in post.stocks:
            code = stock.upper().strip()
            # 期货代码模式：字母+数字 (如 SHFE:RB2501, DCE:I2505)
            if any(code.startswith(p) for p in XUEQIU_FUTURES_PREFIXES):
                return True
            # 检查是否在品种知识库中
            for variety, aliases in FUTURES_KEYWORDS.items():
                for alias in aliases:
                    if alias.upper() in code:
                        return True

    # 检查标签
    futures_tags = {"期货", "商品期货", "黑色系", "有色金属", "农产品", "能化", "股指期货"}
    if any(tag in post.tags for tag in futures_tags):
        return True

    # 文本检查
    text = post.title + " " + post.text
    from validator_weibo import is_futures_related
    return is_futures_related(text)


# ============================================================
# 雪球Session初始化（关键：需要先获取Cookie）
# ============================================================

def init_xueqiu_session() -> requests.Session:
    """
    初始化雪球Session。
    雪球需要先访问首页获取Cookie，否则API直接返回401。
    """
    session = requests.Session()

    retry_strategy = Retry(
        total=MAX_RETRIES,
        backoff_factor=BACKOFF_FACTOR,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    session.headers.update({
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://xueqiu.com/",
    })

    # 第一步：访问首页获取Cookie
    logger.info("初始化雪球Session: 访问首页获取Cookie...")
    try:
        resp = session.get("https://xueqiu.com/", timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        logger.info(f"首页访问成功 (status={resp.status_code}, cookies={len(session.cookies)})")
    except Exception as e:
        logger.warning(f"首页访问失败: {e}，尝试直接调用API")

    return session


# ============================================================
# 核心搜索逻辑
# ============================================================

def search_xueqiu(
    session: requests.Session,
    keyword: str,
    page: int = 1,
    count: int = 20,
) -> list[XueqiuPost]:
    """搜索雪球帖子"""
    params = {
        "q": keyword,
        "count": count,
        "page": page,
        "type": "status",  # 帖子类型
        "sort": "time",    # 按时间排序
    }

    response = session.get(
        ENDPOINTS["xueqiu"]["search"],
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()

    if "list" not in data:
        logger.warning(f"搜索 '{keyword}' 返回格式异常: {list(data.keys())[:5]}")
        return []

    posts = []
    for item in data.get("list", []):
        post = XueqiuPost(
            post_id=str(item.get("id", "")),
            title=item.get("title", ""),
            text=item.get("text", item.get("description", "")),
            created_at=datetime.fromtimestamp(
                item.get("created_at", 0) / 1000
            ).strftime("%Y-%m-%d %H:%M:%S") if item.get("created_at") else None,
            user_name=item.get("user", {}).get("screen_name", "unknown"),
            user_id=str(item.get("user", {}).get("id", "")),
            reply_count=item.get("reply_count", 0),
            retweet_count=item.get("retweet_count", 0),
            like_count=item.get("like_count", 0),
            view_count=item.get("view_count", 0),
            tags=[t.get("name", "") for t in item.get("tags", [])],
            stocks=[s.get("symbol", "") for s in item.get("stocks", [])],
            raw=item,
        )
        posts.append(post)

    return posts


def search_xueqiu_with_stock_filter(
    session: requests.Session,
    keyword: str,
    stock_codes: Optional[list[str]] = None,
    page: int = 1,
) -> list[XueqiuPost]:
    """
    带期货标的过滤的搜索。
    如果指定 stock_codes，则只返回关联了这些标的的帖子。

    期货相关股票代码示例:
      "SHFE:RB2501" - 上期所螺纹钢2501
      "DCE:I2505"   - 大商所铁矿石2505
      "CZCE:TA501"  - 郑商所PTA501
    """
    all_posts = search_xueqiu(session, keyword, page=page)
    if not stock_codes:
        return all_posts

    # 过滤
    filtered = []
    for post in all_posts:
        post_stocks_upper = [s.upper() for s in post.stocks]
        if any(any(sc.upper() in ps for sc in stock_codes) for ps in post_stocks_upper):
            filtered.append(post)
    return filtered


# ============================================================
# 验证主流程
# ============================================================

def validate(
    keywords: Optional[list[str]] = None,
    verbose: bool = False,
) -> XueqiuValidationResult:
    """运行雪球数据采集可行性验证"""
    if keywords is None:
        keywords = SEARCH_KEYWORDS[:5]

    if verbose:
        logger.setLevel(logging.DEBUG)

    session = init_xueqiu_session()
    result = XueqiuValidationResult(
        platform="xueqiu",
        accessible=False,
        total_requests=0,
        successful_requests=0,
        total_results=0,
        relevant_results=0,
        avg_response_time=0.0,
        max_freshness_minutes=None,
        sustained_success_rate=0.0,
    )

    logger.info(f"开始雪球数据采集验证... 关键词: {keywords}")

    # ========================================
    # Phase 1: 可接入性 + 内容覆盖
    # ========================================
    print(f"\n{'─'*50}")
    print("Phase 1: 可接入性与内容覆盖验证")
    print(f"{'─'*50}")

    response_times = []
    all_posts = []
    all_likes = []
    all_replies = []
    max_freshness = timedelta.max

    for keyword in keywords:
        logger.info(f"搜索关键词: '{keyword}'")
        time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

        try:
            start = time.time()
            posts = search_xueqiu(session, keyword, page=1)
            elapsed = time.time() - start

            response_times.append(elapsed)
            result.successful_requests += 1

            for post in posts:
                is_related = is_xueqiu_futures_related(post)
                if is_related:
                    result.relevant_results += 1
                all_posts.append((post, is_related))

                all_likes.append(post.like_count)
                all_replies.append(post.reply_count)
                result.unique_stocks.update(post.stocks)

                # 时效性
                if post.created_at:
                    try:
                        post_time = datetime.strptime(post.created_at, "%Y-%m-%d %H:%M:%S")
                        fresh = datetime.now() - post_time
                        if fresh < max_freshness:
                            max_freshness = fresh
                    except ValueError:
                        pass

            result.total_results += len(posts)
            related_in_batch = sum(1 for _, r in all_posts[-len(posts):] if r)
            print(f"  ✅ '{keyword}': {len(posts)}条, {related_in_batch}条期货相关 ({elapsed:.2f}s)")

            result.accessible = True

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if hasattr(e, 'response') else '?'
            logger.error(f"  搜索 '{keyword}' 失败: HTTP {status}")
            result.errors.append(f"Keyword '{keyword}': HTTP {status}")
        except Exception as e:
            logger.error(f"  搜索 '{keyword}' 失败: {e}")
            result.errors.append(f"Keyword '{keyword}': {str(e)[:100]}")

        result.total_requests += 1

    # 计算指标
    if response_times:
        result.avg_response_time = sum(response_times) / len(response_times)
    if max_freshness != timedelta.max:
        result.max_freshness_minutes = max_freshness.total_seconds() / 60.0
    if all_likes:
        result.avg_like_count = sum(all_likes) / len(all_likes)
    if all_replies:
        result.avg_reply_count = sum(all_replies) / len(all_replies)

    # 保存样本
    related_posts = [p for p, r in all_posts if r]
    result.sample_posts = [
        {
            "post_id": p.post_id,
            "title": p.title,
            "text": p.text[:200],
            "user_name": p.user_name,
            "created_at": p.created_at,
            "reply_count": p.reply_count,
            "retweet_count": p.retweet_count,
            "like_count": p.like_count,
            "tags": p.tags,
            "stocks": p.stocks[:10],
        }
        for p in related_posts[:10]
    ]

    # ========================================
    # Phase 2: 频率限制验证
    # ========================================
    print(f"\n{'─'*50}")
    print("Phase 2: 频率限制验证")

    sustained = 0
    sustained_total = 0
    test_keyword = keywords[0]

    for i in range(ValidationCriteria.SUSTAINED_REQUESTS):
        time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))
        try:
            posts = search_xueqiu(session, test_keyword, page=(i % 5) + 1)
            if posts:
                sustained += 1
            sustained_total += 1
            if verbose or i % 5 == 0:
                print(f"  请求 #{i+1}: ✅ ({len(posts)}条)")
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if hasattr(e, 'response') else '?'
            print(f"  请求 #{i+1}: ❌ HTTP {status}")
            result.errors.append(f"Sustained #{i+1}: HTTP {status}")
            if status in (403, 429):
                logger.warning(f"触发频率限制，停止测试 (请求#{i+1})")
                break
        except Exception as e:
            print(f"  请求 #{i+1}: ❌ {str(e)[:50]}")

    if sustained_total > 0:
        result.sustained_success_rate = sustained / sustained_total
    print(f"  结果: {sustained}/{sustained_total} 成功 ({result.sustained_success_rate*100:.0f}%)")

    # ========================================
    # Phase 3: 样本展示
    # ========================================
    print(f"\n{'─'*50}")
    print("Phase 3: 样本数据展示")
    print(f"{'─'*50}")

    if result.sample_posts:
        print(f"\n期货相关的雪球帖子 ({len(result.sample_posts)}条):")
        for i, post in enumerate(result.sample_posts[:5], 1):
            title = post["title"][:100]
            print(f"\n  [{i}] @{post['user_name']} ({post['created_at']})")
            print(f"  📝 {title}")
            print(f"  📊 💬{post['reply_count']} 🔄{post['retweet_count']} ❤️{post['like_count']}")
            if post.get("stocks"):
                print(f"  🏷️ 标的: {', '.join(post['stocks'][:5])}")
            if post.get("tags"):
                print(f"  🔖 标签: {', '.join(post['tags'][:5])}")
    else:
        print("\n  ⚠️ 未找到期货相关的雪球帖子")

    print(result.to_report())
    return result


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="雪球数据采集可行性验证")
    parser.add_argument("--keyword", type=str, nargs="+", default=None,
                        help="测试关键词")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="详细输出")
    parser.add_argument("--output", type=str, default=None,
                        help="JSON输出路径")

    args = parser.parse_args()

    keywords = args.keyword if args.keyword else SEARCH_KEYWORDS[:5]

    print("雪球数据采集验证（无需Cookie，公开访问）")
    print(f"测试关键词: {keywords}\n")

    result = validate(keywords=keywords, verbose=args.verbose)

    # JSON输出
    output_data = {
        "platform": result.platform,
        "passed": result.passed,
        "accessible": result.accessible,
        "metrics": {
            "total_requests": result.total_requests,
            "successful_requests": result.successful_requests,
            "total_results": result.total_results,
            "relevant_results": result.relevant_results,
            "relevance_rate": result.relevance_rate,
            "avg_response_time": result.avg_response_time,
            "avg_like_count": result.avg_like_count,
            "avg_reply_count": result.avg_reply_count,
            "unique_stocks_count": len(result.unique_stocks),
            "data_freshness_minutes": result.max_freshness_minutes,
            "sustained_success_rate": result.sustained_success_rate,
        },
        "sample_posts": result.sample_posts,
        "errors": result.errors[:10],
    }

    output_path = args.output or str(Path(OUTPUT_DIR) / "xueqiu_validation.json")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存到: {output_path}")

    return 0 if result.passed else 1


if __name__ == "__main__":
    logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
    sys.exit(main())
