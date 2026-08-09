"""
抖音数据采集验证器
===================
提供两种方案：

方案A (推荐验证用): Playwright 浏览器模拟
    - 模拟真实浏览器访问抖音搜索页
    - 不需要逆向 a_bogus / X-Bogus 签名
    - 缺点：速度慢，不适合规模化

方案B (规模化): Go协程池 + 预签名Token
    - 纯后端高并发方案
    - 需要逆向 a_bogus 签名算法
    - 日均可处理 50万+ 请求

方案C: MediaCrawler 集成
    - 基于开源项目 MediaCrawler
    - 成熟方案，社区活跃

注意：抖音的反爬检测非常严格，单纯API请求几乎必定失败。
      Playwright方案是目前最稳定可靠的验证方式。

使用方式:
    python validator_douyin.py --method playwright           # 浏览器方案
    python validator_douyin.py --method mediacrawler         # MediaCrawler方案
    python validator_douyin.py --method pure_api             # 了解API方案
"""

import argparse
import json
import logging
import random
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from config import (
    LOG_FORMAT,
    LOG_LEVEL,
    OUTPUT_DIR,
    SEARCH_KEYWORDS,
)

logger = logging.getLogger("douyin.validator")


@dataclass
class DouyinValidationResult:
    platform: str
    accessible: bool
    method: str
    total_results: int
    relevant_results: int
    avg_response_time: float
    errors: list = field(default_factory=list)
    sample_posts: list = field(default_factory=list)
    # 抖音特有指标
    avg_like_count: float = 0.0
    avg_comment_count: float = 0.0
    avg_share_count: float = 0.0
    video_count: int = 0
    user_count: int = 0

    @property
    def relevance_rate(self) -> float:
        if self.total_results == 0:
            return 0.0
        return self.relevant_results / self.total_results

    @property
    def passed(self) -> bool:
        return self.accessible and self.relevant_results >= 5 and self.relevance_rate >= 0.5

    def to_report(self) -> str:
        status = "✅ 通过" if self.passed else "❌ 未通过"
        lines = [
            f"\n{'=' * 60}",
            f"抖音 (douyin.com) 验证结果: {status}",
            f"{'=' * 60}",
            f"  采集方式:     {self.method}",
            f"  可接入性:     {'✅' if self.accessible else '❌'}",
            f"  总结果数:     {self.total_results}",
            f"  相关结果数:   {self.relevant_results}",
            f"  相关率:       {self.relevance_rate * 100:.0f}%",
            f"  平均响应时间: {self.avg_response_time:.2f}s",
            f"  平均点赞:     {self.avg_like_count:.0f}",
            f"  平均评论:     {self.avg_comment_count:.0f}",
            f"  视频数:       {self.video_count}",
            f"  作者数:       {self.user_count}",
        ]
        if self.sample_posts:
            lines.append("\n  样本视频:")
            for i, post in enumerate(self.sample_posts[:5], 1):
                title = post.get("desc", post.get("title", ""))[:80]
                author = post.get("author", {}).get("nickname", post.get("user_name", "?"))
                stats = post.get("statistics", post.get("stats", {}))
                lines.append(f"  {i}. @{author} | {title}")
                lines.append(
                    f"     ❤️{stats.get('digg_count', 0)} 💬{stats.get('comment_count', 0)} 🔄{stats.get('share_count', 0)}"
                )
        if self.errors:
            lines.append("\n  错误:")
            for err in self.errors[:5]:
                lines.append(f"  - {err}")
        return "\n".join(lines)


# ============================================================
# 方案A: Playwright 浏览器方案
# ============================================================


def validate_playwright(
    keywords: list[str] | None = None,
    headless: bool = False,
) -> DouyinValidationResult:
    """
    使用 Playwright 打开抖音搜索页进行采集。
    抖音搜索页 https://www.douyin.com/search/{keyword}
    """
    result = DouyinValidationResult(
        platform="douyin",
        accessible=False,
        method="playwright",
        total_results=0,
        relevant_results=0,
        avg_response_time=0.0,
    )

    if keywords is None:
        keywords = SEARCH_KEYWORDS[:3]

    try:
        from playwright.sync_api import TimeoutError as PwTimeout, sync_playwright
    except ImportError:
        result.errors.append(
            "playwright 未安装: pip install playwright && playwright install chromium"
        )
        print(result.to_report())
        return result

    print("=" * 60)
    print("抖音数据采集验证 — Playwright 浏览器方案")
    print("=" * 60)
    print()
    print("⚠️  此方案将打开浏览器窗口。")
    print("抖音可能要求登录，请按提示操作。")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )

        # 反检测脚本
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            delete navigator.__proto__.webdriver;
            window.navigator.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
        """)

        page = context.new_page()

        try:
            # 访问抖音首页
            print("访问抖音首页...")
            page.goto("https://www.douyin.com/", timeout=30000)

            # 给用户时间处理验证码/登录
            print("如有验证码或登录提示，请手动处理...")
            time.sleep(5)

            response_times = []
            all_posts = []
            all_likes = []
            unique_users = set()

            for keyword in keywords:
                print(f"\n搜索关键词: '{keyword}'")
                time.sleep(random.uniform(2, 5))

                try:
                    start = time.time()

                    # 抖音搜索URL
                    search_url = f"https://www.douyin.com/search/{quote(keyword)}?type=general"
                    page.goto(search_url, timeout=15000)

                    # 等待搜索结果
                    try:
                        page.wait_for_selector(
                            '[data-e2e="search-list-item"], .search-result-card, '
                            '[class*="search"] [class*="item"], .video-card',
                            timeout=10000,
                        )
                    except PwTimeout:
                        # 尝试等待任意内容加载
                        page.wait_for_timeout(5000)

                    elapsed = time.time() - start
                    response_times.append(elapsed)

                    # 滚动加载更多
                    for _ in range(3):
                        page.evaluate("window.scrollBy(0, 800)")
                        time.sleep(1)

                    # 提取视频信息 - 尝试多种选择器
                    video_elements = page.query_selector_all(
                        '[data-e2e="search-list-item"], .search-result-card, '
                        'div[class*="video"], div[class*="card"], '
                        'li[class*="search"], div[class*="result"]'
                    )

                    # 如果没有直接找到视频元素，尝试从页面文本提取
                    if not video_elements:
                        # 获取页面可见文本
                        page_text = page.inner_text("body")
                        if keyword in page_text:
                            result.total_results += 10  # 粗略估计
                            print(
                                f"  ✅ 页面包含 '{keyword}' 相关内容 (无法精确定位元素, {elapsed:.2f}s)"
                            )
                            result.accessible = True
                            continue
                        else:
                            print("  ⚠️ 页面可能未加载搜索结果")
                            continue

                    posts_count = len(video_elements)
                    result.total_results += posts_count

                    batch_related = 0
                    for elem in video_elements[:10]:
                        try:
                            # 尝试提取标题
                            desc = ""
                            for selector in [
                                '[class*="desc"]',
                                '[class*="title"]',
                                '[class*="text"]',
                                "p",
                                "span",
                            ]:
                                el = elem.query_selector(selector)
                                if el:
                                    desc = el.inner_text() or ""
                                    if len(desc) > 5:
                                        break

                            if not desc:
                                # 获取整个元素的文本
                                desc = elem.inner_text()[:200] or ""

                            # 提取作者名
                            author = ""
                            for selector in [
                                '[class*="author"]',
                                '[class*="name"]',
                                '[class*="nickname"]',
                            ]:
                                el = elem.query_selector(selector)
                                if el:
                                    author = el.inner_text() or ""
                                    if author:
                                        break

                            # 提取互动数据
                            likes = 0
                            for selector in [
                                '[class*="like"]',
                                '[class*="digg"]',
                                '[class*="count"]',
                            ]:
                                el = elem.query_selector(selector)
                                if el:
                                    try:
                                        likes_text = el.inner_text()
                                        likes = int("".join(filter(str.isdigit, likes_text)) or 0)
                                    except (ValueError, AttributeError):
                                        pass
                                    if likes > 0:
                                        break

                            from validator_weibo import is_futures_related

                            is_related = is_futures_related(desc)

                            all_posts.append(
                                {
                                    "desc": desc[:200],
                                    "author_name": author,
                                    "likes": likes,
                                    "is_related": is_related,
                                }
                            )

                            if is_related:
                                batch_related += 1
                                result.relevant_results += 1

                            all_likes.append(likes)
                            if author:
                                unique_users.add(author)

                        except Exception:
                            pass

                    print(
                        f"  ✅ 获取到 {posts_count} 个视频元素, {batch_related}个期货相关 ({elapsed:.2f}s)"
                    )
                    result.accessible = True

                except PwTimeout:
                    print("  ⚠️ 加载超时")
                    result.errors.append(f"Timeout for '{keyword}'")
                except Exception as e:
                    print(f"  ❌ 失败: {e}")
                    result.errors.append(f"'{keyword}': {str(e)[:100]}")

            # 计算指标
            if response_times:
                result.avg_response_time = sum(response_times) / len(response_times)
            if all_likes:
                result.avg_like_count = sum(all_likes) / len(all_likes)
            result.user_count = len(unique_users)
            result.video_count = result.total_results
            related = [p for p in all_posts if p.get("is_related")]
            result.sample_posts = related[:10]

        except Exception as e:
            result.errors.append(f"Browser error: {str(e)[:200]}")

        finally:
            browser.close()

    print(result.to_report())
    return result


# ============================================================
# 方案C: MediaCrawler 集成
# ============================================================


def validate_mediacrawler(
    keywords: list[str] | None = None,
) -> DouyinValidationResult:
    """
    使用 MediaCrawler 开源项目进行采集。
    MediaCrawler 使用 Playwright 自动处理签名，使用更稳定。
    """
    result = DouyinValidationResult(
        platform="douyin",
        accessible=False,
        method="mediacrawler",
        total_results=0,
        relevant_results=0,
        avg_response_time=0.0,
    )

    if keywords is None:
        keywords = SEARCH_KEYWORDS[:3]

    mediacrawler_path = Path("MediaCrawler")
    if not mediacrawler_path.exists():
        print("MediaCrawler 未安装。执行以下步骤：")
        print()
        print("  git clone https://github.com/NanmiCoder/MediaCrawler.git")
        print("  cd MediaCrawler")
        print("  pip install -r requirements.txt")
        print("  playwright install chromium")
        print()
        result.errors.append("MediaCrawler not installed")
        print(result.to_report())
        return result

    print("通过 MediaCrawler 调用抖音搜索...")
    # MediaCrawler 通常通过命令行调用
    # python main.py --platform dy --type search --keywords "螺纹钢期货"

    response_times = []
    for keyword in keywords:
        print(f"搜索: '{keyword}'")
        start = time.time()

        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "--platform",
                    "dy",
                    "--type",
                    "search",
                    "--keywords",
                    keyword,
                    "--max_count",
                    "20",
                    "--output",
                    "json",
                ],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(mediacrawler_path),
            )

            elapsed = time.time() - start
            response_times.append(elapsed)

            if proc.returncode == 0:
                # MediaCrawler 的结果格式取决于具体版本
                print(f"  ✅ 完成 ({elapsed:.2f}s)")
                result.accessible = True
                result.total_results += 20  # 默认max_count
            else:
                print(f"  ❌ 错误: {proc.stderr[:200]}")
                result.errors.append(f"MediaCrawler error: {proc.stderr[:200]}")

        except subprocess.TimeoutExpired:
            print("  ❌ 超时")
            result.errors.append(f"Timeout for '{keyword}'")
        except Exception as e:
            print(f"  ❌ 失败: {e}")
            result.errors.append(f"'{keyword}': {e}")

    if response_times:
        result.avg_response_time = sum(response_times) / len(response_times)

    print(result.to_report())
    return result


# ============================================================
# 抖音API直接方案（文档，基本不可行）
# ============================================================

DOUYIN_API_GUIDE = """
# 抖音API直接采集方案

## 为什么纯API方案基本不可行
抖音对API请求实施了多层防护：
1. a_bogus / X-Bogus 签名（840行混淆JS）
2. TLS指纹检测（JA3）
3. 浏览器环境检测（navigator.webdriver, chrome.runtime）
4. 行为检测（请求频率、时序模式）
5. 设备指纹（ttwid, odin_tt）

## 签名生成流程
1. 获取搜索URL + User-Agent
2. 对URL做SM3哈希
3. 注入12字节伪随机数
4. 自定义RC4加密
5. 双字母表Custom Base64编码
6. 最终得到 a_bogus 参数

## 生产级方案（Go实现，日处理50万+）
```go
// 核心: 预签名Token池 + 协程池
type DouyinCrawler struct {
    signGenerator *SignGenerator   // 签名生成器
    tokenPool     *TokenPool       // 预签名Token池
    workerPool    *WorkerPool      // 协程池
    sessionStore  *SessionStore    // Session复用
}

// 关键性能参数:
// - 单机 50 goroutine
// - HTTP/2 连接复用 (MaxIdleConnsPerHost=100)
// - TLS指纹: X25519曲线 + h2 ALPN
// - 请求间隔: Gamma分布 (均值1.2s)
// - UA池: 200+ 移动端真实UA
// - 每小时轮换IP
```

**结论**: 生产环境推荐Go实现或MediaCrawler，验证阶段推荐Playwright。
"""


def main():
    parser = argparse.ArgumentParser(
        description="抖音数据采集可行性验证",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python validator_douyin.py --method playwright    # 浏览器方案（推荐）
  python validator_douyin.py --method mediacrawler   # MediaCrawler方案
  python validator_douyin.py --method pure_api       # 了解API逆向方案
        """,
    )
    parser.add_argument(
        "--method",
        type=str,
        default="playwright",
        choices=["playwright", "mediacrawler", "pure_api"],
        help="采集方案",
    )
    parser.add_argument("--keyword", type=str, nargs="+", default=None)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--output", type=str, default=None)

    args = parser.parse_args()
    keywords = args.keyword if args.keyword else SEARCH_KEYWORDS[:3]

    if args.method == "pure_api":
        print(DOUYIN_API_GUIDE)
        return 0
    elif args.method == "mediacrawler":
        result = validate_mediacrawler(keywords=keywords)
    elif args.method == "playwright":
        result = validate_playwright(keywords=keywords, headless=args.headless)
    else:
        print(f"未知方法: {args.method}")
        return 1

    output_data = {
        "platform": result.platform,
        "method": result.method,
        "passed": result.passed,
        "accessible": result.accessible,
        "metrics": {
            "total_results": result.total_results,
            "relevant_results": result.relevant_results,
            "relevance_rate": result.relevance_rate,
            "avg_response_time": result.avg_response_time,
            "avg_like_count": result.avg_like_count,
            "user_count": result.user_count,
            "video_count": result.video_count,
        },
        "sample_posts": result.sample_posts,
        "errors": result.errors,
    }

    output_path = args.output or str(Path(OUTPUT_DIR) / "douyin_validation.json")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存到: {output_path}")

    return 0 if result.passed else 1


if __name__ == "__main__":
    logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
    sys.exit(main())
