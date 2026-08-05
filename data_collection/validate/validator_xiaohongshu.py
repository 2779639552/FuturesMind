"""
小红书数据采集验证器
=====================
小红书在2025年10月升级了签名算法，需要 X-s / X-t / X-s-common 等签名参数。
本验证器提供三种方案：

方案A (推荐): 基于 Spider_XHS 开源项目
    - pip install 后调用其API
    - 提供了搜索和详情获取能力

方案B: Playwright浏览器方案
    - 打开浏览器 → 手动扫码登录 → 自动搜索采集
    - 优点：不需要逆向，稳定
    - 缺点：需要手动扫码，速度慢

方案C: JS逆向补环境（生产级）
    - Node.js执行 mnsv2() → 生成 X-s 签名
    - Python subprocess调用
    - 优点：纯后端，可规模化
    - 缺点：需要持续维护签名算法

使用方式:
    python validator_xiaohongshu.py --method playwright   # 浏览器方案（推荐首次验证）
    python validator_xiaohongshu.py --method spider_xhs   # 使用Spider_XHS（需要先安装）
"""

import json
import time
import random
import logging
import argparse
import sys
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field

import requests

from config import (
    SEARCH_KEYWORDS, REQUEST_HEADERS, USER_AGENTS,
    REQUEST_TIMEOUT, MIN_DELAY, MAX_DELAY,
    ENDPOINTS, ValidationCriteria, OUTPUT_DIR, LOG_FORMAT, LOG_LEVEL,
)

logger = logging.getLogger("xhs.validator")


@dataclass
class XHSValidationResult:
    platform: str
    accessible: bool
    method: str  # "playwright" | "spider_xhs" | "js_reverse"
    total_results: int
    relevant_results: int
    avg_response_time: float
    errors: list = field(default_factory=list)
    sample_posts: list = field(default_factory=list)

    @property
    def relevance_rate(self) -> float:
        if self.total_results == 0:
            return 0.0
        return self.relevant_results / self.total_results

    @property
    def passed(self) -> bool:
        return (
            self.accessible
            and self.relevant_results >= 5  # 小红书标准降低（获取难度大）
            and self.relevance_rate >= 0.5
        )

    def to_report(self) -> str:
        status = "✅ 通过" if self.passed else "❌ 未通过"
        lines = [
            f"\n{'='*60}",
            f"小红书 (xiaohongshu.com) 验证结果: {status}",
            f"{'='*60}",
            f"  采集方式:     {self.method}",
            f"  可接入性:     {'✅' if self.accessible else '❌'}",
            f"  总结果数:     {self.total_results}",
            f"  相关结果数:   {self.relevant_results}",
            f"  相关率:       {self.relevance_rate*100:.0f}%",
            f"  平均响应时间: {self.avg_response_time:.2f}s",
        ]
        if self.sample_posts:
            lines.append(f"\n  样本帖子:")
            for i, post in enumerate(self.sample_posts[:5], 1):
                title = post.get("title", post.get("text", ""))[:80]
                lines.append(f"  {i}. @{post.get('user_name', '?')} | {title}")
        if self.errors:
            lines.append(f"\n  错误:")
            for err in self.errors[:5]:
                lines.append(f"  - {err}")
        return '\n'.join(lines)


# ============================================================
# 方案B: Playwright 浏览器方案
# ============================================================

def validate_playwright(
    keywords: Optional[list[str]] = None,
    headless: bool = False,
) -> XHSValidationResult:
    """
    使用 Playwright 打开浏览器访问小红书搜索页面。
    需要用户手动扫码登录，然后自动进行搜索采集。
    """
    result = XHSValidationResult(
        platform="xiaohongshu",
        accessible=False,
        method="playwright",
        total_results=0,
        relevant_results=0,
        avg_response_time=0.0,
    )

    if keywords is None:
        keywords = SEARCH_KEYWORDS[:3]

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
    except ImportError:
        result.errors.append("playwright 未安装。请运行: pip install playwright && playwright install chromium")
        print(result.to_report())
        return result

    print("=" * 60)
    print("小红书数据采集验证 — Playwright 浏览器方案")
    print("=" * 60)
    print()
    print("⚠️  此方案将打开一个浏览器窗口。")
    print("请手动完成以下步骤：")
    print("1. 浏览器打开后，在小红书首页扫码登录")
    print("2. 登录成功后，程序将自动搜索期货关键词")
    print("3. 请勿关闭浏览器窗口")
    print()
    input("按 Enter 键开始...")

    with sync_playwright() as p:
        # 使用非无头模式以便用户扫码
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
            user_agent=random.choice(USER_AGENTS),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )

        # 尝试加载已保存的登录状态
        storage_path = Path(OUTPUT_DIR) / "xhs_storage_state.json"
        if storage_path.exists():
            try:
                with open(storage_path, "r") as f:
                    context.storage_state(loaded=json.load(f))
                print("✅ 已加载上次登录状态")
            except Exception:
                pass

        page = context.new_page()

        # 注入反检测脚本
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
            window.chrome = {runtime: {}};
        """)

        try:
            # 先访问首页，给用户时间登录
            print("\n正在打开小红书首页...")
            page.goto("https://www.xiaohongshu.com/explore", timeout=30000)

            # 检查是否需要登录
            print("请在浏览器中完成登录（如需要）...")
            print("等待登录完成（最多120秒）...")
            time.sleep(5)

            # 等待用户登录（检测搜索框是否出现）
            try:
                page.wait_for_selector(
                    'input[placeholder*="搜索"], .search-input, #search-input',
                    timeout=120000,
                )
                print("✅ 检测到搜索框，已登录！")
            except PwTimeout:
                print("⚠️ 未检测到搜索框，尝试继续...")

            # 保存登录状态
            storage_state = context.storage_state()
            storage_path.parent.mkdir(parents=True, exist_ok=True)
            with open(storage_path, "w") as f:
                json.dump(storage_state, f)
            print(f"✅ 登录状态已保存到: {storage_path}")

            # 开始搜索
            response_times = []
            all_posts = []

            for keyword in keywords:
                print(f"\n搜索关键词: '{keyword}'")
                time.sleep(random.uniform(2, 4))

                try:
                    start = time.time()

                    # 导航到搜索结果页
                    search_url = f"https://www.xiaohongshu.com/search_result?keyword={keyword}&type=51"
                    page.goto(search_url, timeout=15000)

                    # 等待搜索结果加载
                    page.wait_for_selector(
                        '.note-item, .search-result-item, [class*="note"]',
                        timeout=10000,
                    )

                    elapsed = time.time() - start
                    response_times.append(elapsed)

                    # 提取搜索结果
                    post_elements = page.query_selector_all(
                        '.note-item, .search-result-item, [class*="noteItem"]'
                    )
                    posts_count = len(post_elements)
                    result.total_results += posts_count

                    # 提取每个帖子的文本
                    for elem in post_elements[:10]:
                        try:
                            title_el = elem.query_selector(
                                '.title, [class*="title"], .note-title, span'
                            )
                            author_el = elem.query_selector(
                                '.author, [class*="author"], .name, .nickname'
                            )
                            like_el = elem.query_selector(
                                '.like-count, [class*="like"], [class*="count"]'
                            )

                            title_text = title_el.inner_text() if title_el else ""
                            author_text = author_el.inner_text() if author_el else ""
                            like_text = like_el.inner_text() if like_el else "0"

                            # 期货相关性判断
                            from validator_weibo import is_futures_related
                            is_related = is_futures_related(title_text)
                            if is_related:
                                result.relevant_results += 1

                            all_posts.append({
                                "title": title_text[:200],
                                "user_name": author_text,
                                "like_count": like_text,
                                "is_related": is_related,
                            })
                        except Exception:
                            pass

                    print(f"  ✅ 获取到 {posts_count} 条笔记 ({elapsed:.2f}s)")

                    result.accessible = True

                except PwTimeout:
                    print(f"  ⚠️ 加载超时")
                    result.errors.append(f"Timeout for keyword '{keyword}'")
                except Exception as e:
                    print(f"  ❌ 失败: {e}")
                    result.errors.append(f"'{keyword}': {str(e)[:100]}")

            # 保存样本
            related = [p for p in all_posts if p.get("is_related")]
            result.sample_posts = related[:10]

            if response_times:
                result.avg_response_time = sum(response_times) / len(response_times)

        except Exception as e:
            result.errors.append(f"Browser error: {str(e)[:200]}")
            logger.error(f"浏览器错误: {e}")

        finally:
            print("\n采集完成，关闭浏览器...")
            time.sleep(2)
            browser.close()

    print(result.to_report())
    return result


# ============================================================
# 方案A: Spider_XHS 开源项目方案
# ============================================================

def validate_spider_xhs(
    keywords: Optional[list[str]] = None,
) -> XHSValidationResult:
    """
    使用 Spider_XHS 开源项目进行采集。
    需要先安装 Spider_XHS 及其依赖。
    """
    result = XHSValidationResult(
        platform="xiaohongshu",
        accessible=False,
        method="spider_xhs",
        total_results=0,
        relevant_results=0,
        avg_response_time=0.0,
    )

    if keywords is None:
        keywords = SEARCH_KEYWORDS[:3]

    print("=" * 60)
    print("小红书数据采集验证 — Spider_XHS 方案")
    print("=" * 60)
    print()

    # 检查 Spider_XHS 是否已安装
    spider_xhs_path = Path("Spider_XHS")
    if not spider_xhs_path.exists():
        print("Spider_XHS 项目未找到。请执行以下步骤：")
        print()
        print("  git clone https://github.com/cv-cat/Spider_XHS.git")
        print("  cd Spider_XHS")
        print("  pip install -r requirements.txt")
        print()
        print("然后重新运行: python validator_xiaohongshu.py --method spider_xhs")
        result.errors.append("Spider_XHS not installed")
        print(result.to_report())
        return result

    # 尝试通过 subprocess 调用 Spider_XHS
    # Spider_XHS 通常提供 CLI 或 Python API
    try:
        sys.path.insert(0, str(spider_xhs_path))
        # 尝试导入 Spider_XHS 的主要模块
        # 具体导入名称取决于 Spider_XHS 的实际结构
        try:
            from spider_xhs import XHSClient  # type: ignore
        except ImportError:
            try:
                from xhs import Client as XHSClient  # type: ignore
            except ImportError:
                # 尝试通过命令行调用
                print("通过命令行调用 Spider_XHS...")
                response_times = []

                for keyword in keywords:
                    print(f"搜索: '{keyword}'")
                    start = time.time()

                    try:
                        proc = subprocess.run(
                            [
                                sys.executable, "-m", "spider_xhs",
                                "search", keyword,
                                "--count", "20",
                                "--format", "json",
                            ],
                            capture_output=True,
                            text=True,
                            timeout=30,
                            cwd=str(spider_xhs_path),
                        )

                        elapsed = time.time() - start
                        response_times.append(elapsed)

                        if proc.returncode == 0 and proc.stdout:
                            data = json.loads(proc.stdout)
                            posts = data if isinstance(data, list) else data.get("items", [])
                            result.total_results += len(posts)

                            from validator_weibo import is_futures_related
                            for post in posts:
                                title = post.get("title", post.get("note_title", ""))
                                if is_futures_related(title):
                                    result.relevant_results += 1

                            print(f"  ✅ {len(posts)}条 ({elapsed:.2f}s)")
                            result.accessible = True
                        else:
                            print(f"  ❌ 返回错误: {proc.stderr[:200]}")
                            result.errors.append(f"Spider_XHS error: {proc.stderr[:200]}")

                    except subprocess.TimeoutExpired:
                        print(f"  ❌ 超时")
                        result.errors.append(f"Timeout for '{keyword}'")
                    except json.JSONDecodeError:
                        print(f"  ❌ JSON解析失败")
                        result.errors.append(f"Invalid JSON from Spider_XHS")

                if response_times:
                    result.avg_response_time = sum(response_times) / len(response_times)

                print(result.to_report())
                return result

    except Exception as e:
        result.errors.append(f"Spider_XHS integration error: {str(e)}")
        print(f"Spider_XHS 调用失败: {e}")

    print(result.to_report())
    return result


# ============================================================
# 方案C: JS逆向补环境（文档 + 框架代码）
# ============================================================

XHS_JS_REVERSE_GUIDE = """
# 小红书 X-s 签名逆向工程指南

## 签名原理
X-s = "XYS_" + CustomBase64( JSON.stringify({x0, x1, x2, x3, x4}) )

其中:
  x0 = SDK版本 (如 "4.3.1")
  x1 = 应用标识 (如 "xhs-pc-web")
  x2 = 操作系统 (如 "Windows")
  x3 = mnsv2(f, c, d)   ← 核心签名值
  x4 = "object"

  f = url_path + json_body
  c = MD5(f)
  d = MD5(url_path)

## mnsv2 函数
- 定义在小红书页面的 window.mnsv2
- 深度绑定浏览器环境 (window, document, navigator)
- 依赖浏览器的JS引擎

## Python调用方案

### 1. 使用 execjs (PyExecJS)
```python
import execjs

with open('xhs_sign.js', 'r', encoding='utf-8') as f:
    js_code = f.read()

ctx = execjs.compile(js_code)
x_s = ctx.call('get_xs', url_path, json_body, cookie)
```

### 2. 使用 subprocess (Node.js)
```python
import subprocess
import json

def get_xhs_sign(url_path: str, data: str, cookie: str) -> dict:
    result = subprocess.run(
        ['node', 'xhs_sign.js', url_path, data, cookie],
        capture_output=True, text=True
    )
    return json.loads(result.stdout)
```

### 3. 使用 JSRPC (浏览器RPC)
在真实浏览器中注入WebSocket服务器,
Python通过WebSocket客户端发送待签名内容,
浏览器执行 mnsv2() 并返回签名结果。
"""


def print_js_reverse_guide():
    """打印JS逆向指南"""
    print(XHS_JS_REVERSE_GUIDE)
    print()
    print("推荐的开源项目 (截至2026.04仍在活跃更新):")
    print("  - https://github.com/cv-cat/Spider_XHS (Python, 全覆盖)")
    print("  - https://github.com/aki66938/XHS_RS_TOOLS (Rust+Python, Docker)")
    print("  - https://github.com/submato/xhscrawl (Python+Node.js, 在线签名服务)")


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="小红书数据采集可行性验证",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python validator_xiaohongshu.py --method playwright     # 浏览器方案（推荐）
  python validator_xiaohongshu.py --method spider_xhs      # Spider_XHS方案
  python validator_xiaohongshu.py --method js_reverse      # 查看JS逆向指南
  python validator_xiaohongshu.py --method playwright --headless  # 无头浏览器
        """,
    )
    parser.add_argument(
        "--method", type=str, default="playwright",
        choices=["playwright", "spider_xhs", "js_reverse"],
        help="采集方案 (默认: playwright)",
    )
    parser.add_argument("--keyword", type=str, nargs="+", default=None,
                        help="测试关键词")
    parser.add_argument("--headless", action="store_true",
                        help="Playwright无头模式 (不需要手动扫码时需要已有登录状态)")
    parser.add_argument("--output", type=str, default=None,
                        help="JSON输出路径")

    args = parser.parse_args()

    keywords = args.keyword if args.keyword else SEARCH_KEYWORDS[:3]

    if args.method == "js_reverse":
        print_js_reverse_guide()
        return 0
    elif args.method == "spider_xhs":
        result = validate_spider_xhs(keywords=keywords)
    elif args.method == "playwright":
        result = validate_playwright(keywords=keywords, headless=args.headless)
    else:
        print(f"未知方法: {args.method}")
        return 1

    # JSON输出
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
        },
        "sample_posts": result.sample_posts,
        "errors": result.errors,
    }

    output_path = args.output or str(Path(OUTPUT_DIR) / "xiaohongshu_validation.json")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存到: {output_path}")

    return 0 if result.passed else 1


if __name__ == "__main__":
    logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
    sys.exit(main())
