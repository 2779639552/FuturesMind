"""
微博数据采集验证器
===================
使用 m.weibo.cn 移动端API验证：
1. 可接入性 — 接口是否可达
2. 内容覆盖 — 期货关键词搜索结果的相关性
3. 数据质量 — 字段完整度
4. 频率限制 — 连续请求的抗封禁能力
5. 时效性 — 最新数据的延迟

使用方式:
    python validator_weibo.py                          # 单独运行
    python validator_weibo.py --cookie "YOUR_SUB=..."  # 指定Cookie
"""

import json
import time
import random
import hashlib
import logging
import argparse
import sys
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
)

logger = logging.getLogger("weibo.validator")


# ============================================================
# 数据结构
# ============================================================

@dataclass
class WeiboPost:
    """微博帖子"""
    mid: str
    text: str
    created_at: Optional[str] = None
    user_name: Optional[str] = None
    user_id: Optional[str] = None
    reposts_count: int = 0
    comments_count: int = 0
    attitudes_count: int = 0
    source: Optional[str] = None
    pics: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)


@dataclass
class ValidationResult:
    """单个平台的验证结果"""
    platform: str
    accessible: bool
    total_requests: int
    successful_requests: int
    total_results: int
    relevant_results: int
    avg_response_time: float
    max_freshness_minutes: Optional[float]
    sustained_success_rate: float
    errors: list = field(default_factory=list)
    sample_posts: list = field(default_factory=list)

    @property
    def relevance_rate(self) -> float:
        if self.total_results == 0:
            return 0.0
        return self.relevant_results / self.total_results

    @property
    def passed(self) -> bool:
        """是否通过所有验证"""
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
            f"微博 (m.weibo.cn) 验证结果: {status}",
            f"{'='*60}",
            f"  可接入性:     {'✅' if self.accessible else '❌'}",
            f"  请求成功率:   {self.successful_requests}/{self.total_requests} ({self.successful_requests/self.total_requests*100:.0f}%)",
            f"  总结果数:     {self.total_results}",
            f"  相关结果数:   {self.relevant_results}",
            f"  相关率:       {self.relevance_rate*100:.0f}% (要求 ≥ {ValidationCriteria.MIN_RELEVANCE_RATE*100:.0f}%)",
            f"  平均响应时间: {self.avg_response_time:.2f}s",
            f"  数据新鲜度:   {self.max_freshness_minutes:.0f}min 前" if self.max_freshness_minutes else "  数据新鲜度:   N/A",
            f"  持续请求成功率: {self.sustained_success_rate*100:.0f}%",
        ]
        if self.sample_posts:
            lines.append(f"\n  样本帖子 (前5条):")
            for i, post in enumerate(self.sample_posts[:5], 1):
                text_preview = post.text[:80].replace('\n', ' ')
                lines.append(f"  {i}. [{post.user_name}] {text_preview}...")
                lines.append(f"     转发:{post.reposts_count} 评论:{post.comments_count} 赞:{post.attitudes_count}")
        if self.errors:
            lines.append(f"\n  错误列表:")
            for err in self.errors[:5]:
                lines.append(f"  - {err}")
        return '\n'.join(lines)


# ============================================================
# 期货相关性判断
# ============================================================

# 期货相关术语词典
FUTURES_TERMS = {
    # 品种
    "螺纹", "螺纹钢", "铁矿石", "铁矿", "热卷", "焦炭", "焦煤",
    "铜", "沪铜", "铝", "沪铝", "锌", "沪锌", "镍", "沪镍",
    "黄金", "沪金", "白银", "沪银",
    "原油", "PTA", "甲醇", "PVC", "PP", "塑料", "橡胶", "沥青",
    "豆粕", "豆油", "棕榈油", "菜粕", "白糖", "棉花", "玉米",
    "生猪", "鸡蛋",
    "股指期货", "国债期货", "IF", "IC", "IH", "IM",
    # 通用期货术语
    "期货", "期市", "商品期货", "金融期货",
    "多头", "空头", "做多", "做空", "开仓", "平仓", "持仓",
    "止损", "止盈", "套利", "对冲", "基差", "升水", "贴水",
    "主力合约", "远月合约", "交割", "移仓", "换月",
    "上期所", "大商所", "郑商所", "中金所", "上期能源",
    "CME", "LME", "COMEX", "CBOT", "ICE",
    "K线", "均线", "MACD", "布林带", "成交量", "持仓量",
    "逼仓", "爆仓", "穿仓", "强平",
    # 行情描述
    "涨", "跌", "震荡", "突破", "回调", "反弹", "跳水",
    "高开", "低开", "收涨", "收跌",
}

# 噪声过滤词（含这些词但非期货相关的）
NOISE_TERMS = {
    "期货交易平台", "期货配资", "喊单", "带单", "入金", "出金",
    "期货骗局", "杀猪盘", "稳赚", "暴富", "日赚",
    "美女", "相亲", "恋爱",  # 垃圾营销
}


def is_futures_related(text: str) -> bool:
    """
    判断文本是否与期货相关。
    采用级联过滤：先匹配噪声词排除 → 再匹配期货术语
    """
    if not text:
        return False

    # 第一层：噪声排除
    text_lower = text.lower()
    for noise in NOISE_TERMS:
        if noise in text_lower:
            return False

    # 第二层：期货术语匹配
    match_count = 0
    for term in FUTURES_TERMS:
        if term in text:
            match_count += 1
            if match_count >= 2:  # 至少匹配2个期货术语
                return True

    # 如果只匹配1个术语，对于短文本（<50字）也可以接受
    if match_count == 1 and len(text) < 50 and any(t in text for t in [
        "螺纹", "铁矿石", "焦炭", "焦煤", "铜", "铝", "原油", "PTA",
        "期货", "多头", "空头", "做多", "做空", "开仓", "平仓", "持仓",
        "交割", "上期所", "大商所", "郑商所", "主力合约",
    ]):
        return True

    return False


# ============================================================
# HTTP 会话构建
# ============================================================

def build_session(cookie: Optional[str] = None) -> requests.Session:
    """构建带重试机制和Cookie的HTTP会话"""
    session = requests.Session()

    # 重试策略
    retry_strategy = Retry(
        total=MAX_RETRIES,
        backoff_factor=BACKOFF_FACTOR,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(
        max_retries=retry_strategy,
        pool_connections=5,
        pool_maxsize=5,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    # 默认请求头
    session.headers.update(REQUEST_HEADERS)

    # Cookie
    if cookie:
        session.headers["Cookie"] = cookie

    return session


# ============================================================
# 核心采集逻辑
# ============================================================

def search_weibo(
    session: requests.Session,
    keyword: str,
    page: int = 1,
) -> list[WeiboPost]:
    """
    搜索微博。
    使用 m.weibo.cn 移动端搜索接口。
    """
    params = {
        "containerid": f"100103type=1&q={keyword}",
        "page": page,
    }

    response = session.get(
        ENDPOINTS["weibo"]["search"],
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()

    if data.get("ok") != 1:
        logger.warning(f"搜索 '{keyword}' (page={page}) 返回ok!=1: {data}")
        return []

    posts = []
    cards = data.get("data", {}).get("cards", [])

    for card in cards:
        if card.get("card_type") != 9:  # card_type=9 是微博帖子
            continue

        mblog = card.get("mblog", {})
        if not mblog:
            continue

        # 提取文本（优先取长文本）
        text = mblog.get("text_raw", "")
        if not text:
            text = mblog.get("text", "")

        # 去除HTML标签
        import re
        text = re.sub(r'<[^>]+>', '', text)

        post = WeiboPost(
            mid=mblog.get("mid", ""),
            text=text,
            created_at=mblog.get("created_at"),
            user_name=mblog.get("user", {}).get("screen_name", "unknown"),
            user_id=str(mblog.get("user", {}).get("id", "")),
            reposts_count=mblog.get("reposts_count", 0),
            comments_count=mblog.get("comments_count", 0),
            attitudes_count=mblog.get("attitudes_count", 0),
            source=mblog.get("source", ""),
            pics=[p.get("url", "") for p in mblog.get("pics", [])],
            raw=mblog,
        )
        posts.append(post)

    return posts


def extract_posts(response_json: dict) -> list[dict]:
    """从 m.weibo.cn 搜索响应中提取帖子"""
    posts = []
    cards = response_json.get("data", {}).get("cards", [])

    for card in cards:
        if card.get("card_type") != 9:
            continue
        mblog = card.get("mblog", {})
        if mblog:
            text = mblog.get("text", "")
            import re
            text = re.sub(r'<[^>]+>', '', text)
            posts.append({
                "mid": mblog.get("mid", ""),
                "text": text,
                "created_at": mblog.get("created_at"),
                "user_name": mblog.get("user", {}).get("screen_name", ""),
                "reposts_count": mblog.get("reposts_count", 0),
                "comments_count": mblog.get("comments_count", 0),
                "attitudes_count": mblog.get("attitudes_count", 0),
            })
    return posts


def parse_created_at(created_at: str) -> Optional[datetime]:
    """解析微博时间格式：'Tue Jul 15 10:30:00 +0800 2026' 或 '2分钟前' 等"""
    if not created_at:
        return None

    now = datetime.now()

    # 相对时间
    if "分钟前" in created_at:
        minutes = int(created_at.replace("分钟前", "").strip())
        return now - timedelta(minutes=minutes)
    elif "小时前" in created_at:
        hours = int(created_at.replace("小时前", "").strip())
        return now - timedelta(hours=hours)
    elif "昨天" in created_at:
        return now - timedelta(days=1)
    elif "前天" in created_at:
        return now - timedelta(days=2)
    elif "秒前" in created_at:
        return now
    elif "刚刚" in created_at:
        return now

    # 绝对时间：Tue Jul 15 10:30:00 +0800 2026
    try:
        return datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
    except (ValueError, TypeError):
        pass

    # 绝对时间：2026-07-15 10:30:00
    try:
        return datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        pass

    return None


# ============================================================
# 验证主流程
# ============================================================

def validate(
    cookie: Optional[str] = None,
    keywords: Optional[list[str]] = None,
    verbose: bool = False,
) -> ValidationResult:
    """
    运行完整的微博数据采集可行性验证。
    """
    if keywords is None:
        keywords = SEARCH_KEYWORDS[:5]  # 用前5个关键词做验证

    if verbose:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    session = build_session(cookie)
    result = ValidationResult(
        platform="weibo",
        accessible=False,
        total_requests=0,
        successful_requests=0,
        total_results=0,
        relevant_results=0,
        avg_response_time=0.0,
        max_freshness_minutes=None,
        sustained_success_rate=0.0,
    )

    logger.info(f"开始微博数据采集验证... 关键词: {keywords}")

    # ========================================
    # Phase 1: 可接入性 + 内容覆盖
    # ========================================
    print(f"\n{'─'*50}")
    print("Phase 1: 可接入性与内容覆盖验证")
    print(f"{'─'*50}")

    response_times = []
    all_posts = []
    max_freshness = timedelta.max

    for keyword in keywords:
        logger.info(f"搜索关键词: '{keyword}'")
        time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

        try:
            start = time.time()
            posts = search_weibo(session, keyword, page=1)
            elapsed = time.time() - start

            response_times.append(elapsed)
            result.successful_requests += 1

            # 相关性判断
            for post in posts:
                is_related = is_futures_related(post.text)
                if is_related:
                    result.relevant_results += 1
                all_posts.append((post, is_related))

                # 时效性
                post_time = parse_created_at(post.created_at)
                if post_time:
                    fresh = datetime.now(post_time.tzinfo) - post_time if post_time.tzinfo else datetime.now() - post_time
                    if fresh < max_freshness:
                        max_freshness = fresh

            result.total_results += len(posts)

            if verbose:
                related_count = sum(1 for _, r in all_posts[-len(posts):] if r)
                print(f"  '{keyword}': {len(posts)}条结果, {related_count}条相关, {elapsed:.2f}s")
            else:
                print(f"  ✅ '{keyword}': {len(posts)}条结果, {sum(1 for _, r in all_posts[-len(posts):] if r)}条相关 ({elapsed:.2f}s)")

            result.accessible = True

        except requests.exceptions.RequestException as e:
            logger.error(f"  搜索 '{keyword}' 失败: {e}")
            result.errors.append(f"Keyword '{keyword}': {str(e)[:100]}")

        result.total_requests += 1

    # 计算指标
    if response_times:
        result.avg_response_time = sum(response_times) / len(response_times)
    if max_freshness != timedelta.max:
        result.max_freshness_minutes = max_freshness.total_seconds() / 60.0

    # 保存样本
    related_posts = [p for p, r in all_posts if r]
    result.sample_posts = related_posts[:10]

    # ========================================
    # Phase 2: 频率限制验证
    # ========================================
    print(f"\n{'─'*50}")
    print("Phase 2: 频率限制验证 (连续{0}次请求)")

    sustained = 0
    sustained_total = 0
    test_keyword = keywords[0]  # 用第一个关键词做压力测试

    for i in range(ValidationCriteria.SUSTAINED_REQUESTS):
        time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))
        try:
            posts = search_weibo(session, test_keyword, page=(i % 3) + 1)
            if posts:
                sustained += 1
            sustained_total += 1
            if verbose or i % 5 == 0:
                print(f"  请求 #{i+1}: ✅ ({len(posts)}条)")
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if hasattr(e, 'response') else '?'
            print(f"  请求 #{i+1}: ❌ HTTP {status}")
            result.errors.append(f"Sustained request #{i+1}: HTTP {status}")
            if status == 418 or status == 403:
                logger.warning(f"触发反爬，停止压力测试 (请求#{i+1})")
                break
        except Exception as e:
            print(f"  请求 #{i+1}: ❌ {str(e)[:50]}")
            result.errors.append(f"Sustained request #{i+1}: {str(e)[:50]}")

    if sustained_total > 0:
        result.sustained_success_rate = sustained / sustained_total
    print(f"  结果: {sustained}/{sustained_total} 成功 (成功率 {result.sustained_success_rate*100:.0f}%)")

    # ========================================
    # Phase 3: 结果展示
    # ========================================
    print(f"\n{'─'*50}")
    print("Phase 3: 样本数据展示")
    print(f"{'─'*50}")

    if result.sample_posts:
        print(f"\n期货相关的微博样本 ({len(result.sample_posts)}条):")
        for i, post in enumerate(result.sample_posts[:5], 1):
            text_preview = post.text[:100].replace('\n', ' ').replace('\r', '')
            print(f"\n  [{i}] @{post.user_name} ({post.created_at})")
            print(f"  📝 {text_preview}...")
            print(f"  📊 转发:{post.reposts_count} 评论:{post.comments_count} 赞:{post.attitudes_count}")
    else:
        print("\n  ⚠️ 未找到期货相关的微博（可能需要更新Cookie或关键词）")

    # 输出完整报告
    print(result.to_report())

    return result


# ============================================================
# CLI入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="微博数据采集可行性验证")
    parser.add_argument("--cookie", type=str, default=None,
                        help="微博Cookie (必须包含SUB字段)")
    parser.add_argument("--keyword", type=str, nargs="+", default=None,
                        help="测试关键词（默认使用配置中的前5个）")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="详细输出")
    parser.add_argument("--output", type=str, default=None,
                        help="结果输出JSON文件路径")

    args = parser.parse_args()

    if not args.cookie:
        print("=" * 60)
        print("⚠️  未提供Cookie！")
        print("=" * 60)
        print()
        print("微博的移动端搜索接口 (m.weibo.cn) 需要登录Cookie。")
        print()
        print("获取Cookie的方法：")
        print("1. 打开浏览器，访问 https://m.weibo.cn")
        print("2. 扫码或密码登录")
        print("3. 按 F12 打开开发者工具 → Application → Cookies → m.weibo.cn")
        print("4. 复制 SUB 的值（其他字段也会自动带上）")
        print()
        print("然后运行:")
        print('  python validator_weibo.py --cookie "SUB=你的SUB值; ..."')
        print()
        print("=" * 60)
        sys.exit(1)

    keywords = args.keyword if args.keyword else SEARCH_KEYWORDS[:5]

    result = validate(
        cookie=args.cookie,
        keywords=keywords,
        verbose=args.verbose,
    )

    # 输出JSON
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
            "data_freshness_minutes": result.max_freshness_minutes,
            "sustained_success_rate": result.sustained_success_rate,
        },
        "sample_posts": [
            {
                "mid": p.mid,
                "text": p.text[:200],
                "user_name": p.user_name,
                "created_at": p.created_at,
                "reposts": p.reposts_count,
                "comments": p.comments_count,
                "attitudes": p.attitudes_count,
            }
            for p in result.sample_posts[:10]
        ],
        "errors": result.errors[:10],
    }

    output_path = args.output or str(Path(OUTPUT_DIR) / "weibo_validation.json")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    print(f"\n验证结果已保存到: {output_path}")

    return 0 if result.passed else 1


if __name__ == "__main__":
    logging.basicConfig(
        level=LOG_LEVEL,
        format=LOG_FORMAT,
    )
    sys.exit(main())
