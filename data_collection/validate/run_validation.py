#!/usr/bin/env python3
"""
期货社交媒体数据采集 — 可行性统一验证框架
============================================

一键运行所有平台的采集可行性验证，生成汇总报告。

使用方式:
    python run_validation.py                          # 全平台验证
    python run_validation.py --platform weibo         # 单平台
    python run_validation.py --platform weibo,xueqiu  # 多平台
    python run_validation.py --quick                  # 快速验证（减少关键词）
    python run_validation.py --output report.json     # 指定输出文件

依赖:
    pip install -r requirements.txt

运行前准备:
    微博:   需要Cookie (浏览器登录 m.weibo.cn 后获取 SUB 字段)
    雪球:   无需Cookie，公开访问
    小红书: 需要安装 Playwright 或 Spider_XHS
    抖音:   需要安装 Playwright 或 MediaCrawler
"""

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from config import (
    LOG_FORMAT,
    OUTPUT_DIR,
    SEARCH_KEYWORDS,
)

logger = logging.getLogger("futures.collector")


# ============================================================
# 汇总报告
# ============================================================


@dataclass
class FeasibilitySummary:
    """全平台可行性汇总"""

    timestamp: str
    platforms: dict  # platform_name -> result dict
    overall_score: float  # 0-100
    recommendation: str

    @classmethod
    def from_results(cls, results: dict) -> "FeasibilitySummary":
        platform_scores = {}
        passed_count = 0
        total_count = len(results)

        for name, result in results.items():
            if result is None:
                platform_scores[name] = {
                    "passed": False,
                    "score": 0,
                    "status": "not_tested",
                    "summary": "未测试",
                }
                continue

            passed = result.get("passed", False)
            accessible = result.get("accessible", False)

            # 计算平台分数
            score = 0
            if accessible:
                score += 30
                relevance_rate = result.get("metrics", {}).get("relevance_rate", 0)
                score += min(relevance_rate * 60, 60)  # 最多60分
                success_rate = result.get("metrics", {}).get("sustained_success_rate", 0)
                if success_rate:
                    score += min(success_rate * 10, 10)  # 最多10分

            if passed:
                passed_count += 1

            platform_scores[name] = {
                "passed": passed,
                "score": round(score, 1),
                "status": "passed" if passed else ("partial" if accessible else "failed"),
                "summary": result.get("metrics", {}),
            }

        overall = sum(p["score"] for p in platform_scores.values()) / max(total_count, 1)

        # 推荐
        if overall >= 70:
            recommendation = (
                "大规模采集条件成熟。建议以微博+雪球为双核心启动，"
                "小红书和抖音采用Playwright方案补充。"
            )
        elif overall >= 40:
            recommendation = (
                "部分平台可行，建议分阶段推进。优先开发微博+雪球，小红书/抖音需要更多逆向工程投入。"
            )
        else:
            recommendation = (
                "多数平台采集困难。建议先聚焦微博和雪球的深度优化，其他平台关注开源项目进展。"
            )

        return cls(
            timestamp=datetime.now().isoformat(),
            platforms=platform_scores,
            overall_score=round(overall, 1),
            recommendation=recommendation,
        )

    def to_report(self) -> str:
        lines = [
            "",
            "=" * 70,
            "  期货社交媒体数据采集 — 可行性验证汇总报告",
            "=" * 70,
            f"  时间: {self.timestamp}",
            f"  综合得分: {self.overall_score}/100",
            "",
            "  " + "-" * 66,
        ]

        # 结果表
        header = f"  {'平台':<12} {'状态':<8} {'得分':<8} {'关键指标'}"
        lines.append(header)
        lines.append("  " + "-" * 66)

        for name, info in self.platforms.items():
            status_icon = {
                "passed": "✅ 通过",
                "partial": "⚠️ 部分",
                "failed": "❌ 失败",
                "not_tested": "⬜ 未测",
            }.get(info["status"], "❓")

            summary = info.get("summary", {})
            key_metric = ""
            if summary:
                if "relevant_results" in summary:
                    key_metric = f"期货相关 {summary['relevant_results']}条"
                if "relevance_rate" in summary:
                    key_metric += f", 相关率 {summary['relevance_rate'] * 100:.0f}%"

            lines.append(f"  {name:<12} {status_icon:<8} {info['score']:<8.1f} {key_metric}")

        lines.append("  " + "-" * 66)
        lines.append("")
        lines.append(f"  📋 综合建议: {self.recommendation}")
        lines.append("")
        lines.append("  " + "=" * 66)
        lines.append("  下一步:")
        lines.append("  1. 对通过的平台，进入 Phase: 数据质量深度验证")
        lines.append("  2. 对部分通过的平台，投入逆向工程/浏览器方案优化")
        lines.append("  3. 对所有平台，构建统一的期货品种NER+情感分析pipeline")
        lines.append("=" * 70)
        lines.append("")

        return "\n".join(lines)


# ============================================================
# 主流程
# ============================================================


def run_platform_validation(
    platform: str,
    cookie: str | None = None,
    quick: bool = False,
) -> dict | None:
    """运行单个平台的验证"""
    keywords = SEARCH_KEYWORDS[:3] if quick else SEARCH_KEYWORDS[:5]

    print(f"\n{'#' * 60}")
    print(f"#  开始验证: {platform}")
    print(f"{'#' * 60}")

    start = time.time()

    try:
        if platform == "weibo":
            from validator_weibo import validate

            if not cookie:
                print("⚠️ 微博需要Cookie才能验证，跳过。")
                print("  请使用 --cookie 参数或在运行前设置环境变量 WEIBO_COOKIE")
                return None
            result = validate(cookie=cookie, keywords=keywords, verbose=False)
            return {
                "platform": "weibo",
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
                "sample_count": len(result.sample_posts),
                "errors": result.errors[:5],
            }

        elif platform == "xueqiu":
            from validator_xueqiu import validate

            result = validate(keywords=keywords, verbose=False)
            return {
                "platform": "xueqiu",
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
                    "unique_stocks": len(result.unique_stocks),
                    "sustained_success_rate": result.sustained_success_rate,
                },
                "sample_count": len(result.sample_posts),
                "errors": result.errors[:5],
            }

        elif platform == "xiaohongshu":
            from validator_xiaohongshu import validate_playwright

            result = validate_playwright(keywords=keywords, headless=False)
            return {
                "platform": "xiaohongshu",
                "passed": result.passed,
                "accessible": result.accessible,
                "method": result.method,
                "metrics": {
                    "total_results": result.total_results,
                    "relevant_results": result.relevant_results,
                    "relevance_rate": result.relevance_rate,
                    "avg_response_time": result.avg_response_time,
                },
                "sample_count": len(result.sample_posts),
                "errors": result.errors[:5],
            }

        elif platform == "douyin":
            from validator_douyin import validate_playwright

            result = validate_playwright(keywords=keywords, headless=False)
            return {
                "platform": "douyin",
                "passed": result.passed,
                "accessible": result.accessible,
                "method": result.method,
                "metrics": {
                    "total_results": result.total_results,
                    "relevant_results": result.relevant_results,
                    "relevance_rate": result.relevance_rate,
                    "avg_response_time": result.avg_response_time,
                    "avg_like_count": result.avg_like_count,
                },
                "sample_count": len(result.sample_posts),
                "errors": result.errors[:5],
            }

        else:
            print(f"未知平台: {platform}")
            return None

    except ImportError as e:
        print(f"❌ 缺少依赖: {e}")
        return {
            "platform": platform,
            "passed": False,
            "accessible": False,
            "metrics": {},
            "errors": [f"ImportError: {e}"],
        }
    except Exception as e:
        print(f"❌ 验证异常: {e}")
        logger.exception(f"验证 {platform} 时发生异常")
        return {
            "platform": platform,
            "passed": False,
            "accessible": False,
            "metrics": {},
            "errors": [f"Exception: {e}"],
        }
    finally:
        elapsed = time.time() - start
        print(f"\n{platform} 验证耗时: {elapsed:.0f}s")


def main():
    parser = argparse.ArgumentParser(
        description="期货社交媒体数据采集 — 可行性统一验证框架",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python run_validation.py                                    # 全平台验证
  python run_validation.py --platform weibo,xueqiu            # 指定平台
  python run_validation.py --quick                            # 快速模式
  python run_validation.py --cookie "SUB=..." --platform weibo  # 带Cookie
  python run_validation.py --output ./reports/summary.json    # 指定输出
        """,
    )

    parser.add_argument(
        "--platform",
        type=str,
        default="all",
        help="目标平台: all, weibo, xueqiu, xiaohongshu, douyin (逗号分隔多个)",
    )
    parser.add_argument(
        "--cookie",
        type=str,
        default=None,
        help="微博Cookie (WEIBO_COOKIE环境变量也可)",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="快速模式 (减少关键词和请求次数)",
    )
    parser.add_argument(
        "--skip-playwright",
        action="store_true",
        help="跳过需要Playwright浏览器的平台 (小红书/抖音)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="汇总报告JSON输出路径",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="详细日志",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format=LOG_FORMAT)
    else:
        logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)

    # 确定平台列表
    all_platforms = ["weibo", "xueqiu", "xiaohongshu", "douyin"]
    if args.platform == "all":
        platforms = all_platforms
    else:
        platforms = [p.strip() for p in args.platform.split(",")]
        # 验证平台名
        for p in platforms:
            if p not in all_platforms:
                print(f"未知平台: {p}。可选: {', '.join(all_platforms)}")
                return 1

    if args.skip_playwright:
        platforms = [p for p in platforms if p not in ("xiaohongshu", "douyin")]
        print(f"跳过Playwright平台。剩余: {', '.join(platforms)}")

    # Cookie 来源: 参数 > 环境变量
    cookie = args.cookie
    if not cookie:
        import os

        cookie = os.environ.get("WEIBO_COOKIE")

    # 前置检查
    if "weibo" in platforms and not cookie:
        print("\n" + "=" * 60)
        print("⚠️  微博验证需要Cookie！")
        print("=" * 60)
        print()
        print("你有两个选择：")
        print()
        print("选项1: 提供Cookie参数")
        print("  1. 浏览器打开 https://m.weibo.cn 并登录")
        print("  2. F12 → Application → Cookies → 复制完整的Cookie")
        print('  3. python run_validation.py --cookie "你的Cookie"')
        print()
        print("选项2: 跳过微博，只验证不需要Cookie的平台")
        print("  python run_validation.py --platform xueqiu,xiaohongshu,douyin")
        print()
        print("=" * 60)

        response = input("\n是否跳过微博继续？(y/n): ").strip().lower()
        if response == "y":
            platforms = [p for p in platforms if p != "weibo"]
        else:
            print("已取消。请准备好Cookie后再运行。")
            return 1

    print("\n期货社交媒体数据采集 — 可行性验证框架")
    print(f"目标平台: {', '.join(platforms)}")
    print(f"模式: {'快速' if args.quick else '完整'}")
    print()

    # 运行验证
    results = {}
    for platform in platforms:
        result = run_platform_validation(
            platform=platform,
            cookie=cookie,
            quick=args.quick,
        )
        results[platform] = result

    # 生成汇总报告
    summary = FeasibilitySummary.from_results(results)

    # 打印报告
    print(summary.to_report())

    # 保存JSON报告
    output_path = args.output or str(Path(OUTPUT_DIR) / "feasibility_summary.json")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    output_data = {
        "timestamp": summary.timestamp,
        "overall_score": summary.overall_score,
        "recommendation": summary.recommendation,
        "platforms": summary.platforms,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    print(f"汇总报告已保存到: {output_path}")

    # 如果有平台未测试，给出提示
    untested = [p for p in platforms if results.get(p) is None]
    if untested:
        print(f"\n⚠️ 以下平台未完成测试: {', '.join(untested)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
