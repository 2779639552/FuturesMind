"""
期货社交媒体 — 多平台批量采集
==============================
平台适配器模式: 通过 --platform 切换小红书/微博/知乎。
  - 自适应延时 (正常请求后自适应缩短, 检测到限流后自动加长)
  - 增量保存 (每个关键词完成后立即写盘, 中断不丢数据)
  - 失败重试 + 退避
  - 进度条 + ETA
  - NER + 情感分析 enrich

使用:
  python batch_collect.py                                      # 默认 xhs, 30条/关键词
  python batch_collect.py --platform weibo                     # 微博
  python batch_collect.py --per-kw 50 --max-detail 20          # 每词50条, 深挖20条
  python batch_collect.py --keywords "螺纹" "铁矿"              # 自定义关键词
  python batch_collect.py --safe-mode                          # 安全模式(更慢更安全)
  python batch_collect.py --turbo                              # 极速模式
  python batch_collect.py --no-detail                          # 不深挖(微博推荐,速度快)
"""

import argparse
import contextlib
import json
import logging
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# NER + 情感 (纯文本, 平台无关)
from ner import FuturesNER

# 平台适配器
from platforms import get_adapter, list_platforms
from platforms.base import CredentialError, PlatformAdapter
from sentiment import SentimentAnalyzer

logger = logging.getLogger("batch.collect")

# ============================================================
# 配置
# ============================================================

# 采集控制
DEFAULT_PER_KW = 30  # 每关键词采集条数
MIN_DELAY_MS = 300  # 最小请求间隔(ms) — 实测API耗时~750ms, 300ms间隔安全
MAX_DELAY_MS = 1000  # 最大请求间隔(ms)
SAFE_MODE_MULTIPLIER = 2.5  # 安全模式延时倍率 (300*2.5=750ms 起步)
TURBO_MIN_DELAY_MS = 120  # 极速模式最小间隔
TURBO_MAX_DELAY_MS = 500  # 极速模式最大间隔
RATE_LIMIT_COOLDOWN = 30  # 触发限流后冷却秒数
BATCH_COOLDOWN = 1  # 每批关键词间休息秒数
MAX_DETAIL_PER_KW = 10  # 每关键词最多深挖条数

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 平台默认关键词 (xhs 保留旧列表, 微博/知乎待 config.py 扩展)
DEFAULT_KEYWORDS_XHS = [
    # 黑色系
    "螺纹钢期货",
    "铁矿石期货",
    "焦炭期货",
    "焦煤期货",
    "热卷期货",
    "硅铁期货",
    "锰硅期货",
    # 有色金属
    "沪铜期货",
    "沪铝期货",
    "沪锌期货",
    "沪镍期货",
    "黄金期货分析",
    "白银期货分析",
    "碳酸锂期货",
    "工业硅期货",
    # 能源化工
    "原油期货分析",
    "PTA期货",
    "甲醇期货",
    "纯碱期货",
    "PVC期货",
    "玻璃期货",
    "尿素期货",
    "橡胶期货",
    "沥青期货",
    # 农产品
    "豆粕期货",
    "豆油期货",
    "棕榈油期货",
    "菜粕期货",
    "白糖期货",
    "棉花期货",
    "玉米期货",
    "生猪期货",
    "鸡蛋期货",
    "苹果期货",
    "红枣期货",
    "花生期货",
    # 金融
    "股指期货策略",
    "国债期货",
    # 通用
    "期货实盘",
    "期货技术分析",
    "期货基本面",
    "期货日内交易",
    "期货波段策略",
]

DEFAULT_KEYWORDS_WEIBO = [
    "螺纹钢",
    "铁矿石",
    "焦炭",
    "焦煤",
    "热卷",
    "沪铜",
    "沪铝",
    "沪锌",
    "沪镍",
    "黄金",
    "白银",
    "原油",
    "PTA",
    "甲醇",
    "纯碱",
    "PVC",
    "玻璃",
    "尿素",
    "橡胶",
    "豆粕",
    "豆油",
    "棕榈油",
    "菜粕",
    "白糖",
    "棉花",
    "玉米",
    "生猪",
    "股指期货",
    "国债期货",
    "期货实盘",
    "期货技术分析",
    "期货基本面",
]

DEFAULT_KEYWORDS_ZHIHU = [
    "期货",
    "商品期货",
    "金融期货",
    "螺纹钢",
    "铁矿石",
    "焦炭",
    "焦煤",
    "沪铜",
    "黄金",
    "白银",
    "原油",
    "豆粕",
    "棕榈油",
    "白糖",
    "股指期货",
    "国债期货",
    "期货交易策略",
    "期货技术分析",
]

DEFAULT_KEYWORDS_XUEQIU = [
    "螺纹钢",
    "铁矿石",
    "焦炭",
    "焦煤",
    "热卷",
    "沪铜",
    "沪铝",
    "沪锌",
    "沪镍",
    "黄金",
    "白银",
    "原油",
    "PTA",
    "甲醇",
    "纯碱",
    "PVC",
    "玻璃",
    "尿素",
    "橡胶",
    "豆粕",
    "豆油",
    "棕榈油",
    "菜粕",
    "白糖",
    "棉花",
    "玉米",
    "生猪",
    "股指期货",
    "国债期货",
    "期货实盘",
    "期货技术分析",
    "期货基本面",
]

DEFAULT_KEYWORDS = {
    "xhs": DEFAULT_KEYWORDS_XHS,
    "weibo": DEFAULT_KEYWORDS_WEIBO,
    "zhihu": DEFAULT_KEYWORDS_ZHIHU,
    "xueqiu": DEFAULT_KEYWORDS_XUEQIU,
}


# ============================================================
# 数据结构
# ============================================================


@dataclass
class CollectStats:
    """采集统计"""

    started: str = ""
    keywords_total: int = 0
    keywords_done: int = 0
    searched_total: int = 0
    detail_fetched: int = 0
    detail_failed: int = 0
    rate_limits_hit: int = 0
    errors: list = field(default_factory=list)


class RateLimiter:
    """自适应延时控制器 (平台无关)"""

    def __init__(self, safe_mode: bool = False, turbo_mode: bool = False):
        if turbo_mode:
            self.min_delay = TURBO_MIN_DELAY_MS / 1000
            self.max_delay = TURBO_MAX_DELAY_MS / 1000
            self.jitter_pct = 0.10
        elif safe_mode:
            self.min_delay = (MIN_DELAY_MS * SAFE_MODE_MULTIPLIER) / 1000
            self.max_delay = (MAX_DELAY_MS * SAFE_MODE_MULTIPLIER) / 1000
            self.jitter_pct = 0.25
        else:
            self.min_delay = MIN_DELAY_MS / 1000
            self.max_delay = MAX_DELAY_MS / 1000
            self.jitter_pct = 0.15
        self.current_delay = self.min_delay
        self.consecutive_failures = 0
        self.last_request_time = 0
        self.safe_mode = safe_mode
        self.turbo_mode = turbo_mode

    def wait(self):
        """等待适当的时间"""
        elapsed = time.time() - self.last_request_time
        wait_time = max(0, self.current_delay - elapsed)
        if wait_time > 0:
            jitter = random.uniform(-self.jitter_pct, self.jitter_pct) * wait_time
            time.sleep(wait_time + jitter)
        self.last_request_time = time.time()

    def report_success(self):
        """请求成功 → 逐渐恢复最小延时"""
        self.consecutive_failures = 0
        self.current_delay = max(self.min_delay, self.current_delay * 0.9)

    def report_failure(self, is_rate_limit: bool = False):
        """请求失败 → 增加延时"""
        self.consecutive_failures += 1
        if is_rate_limit:
            self.current_delay *= 2.0
            logger.warning(f"Rate limit detected! Delay increased to {self.current_delay:.1f}s")
            time.sleep(RATE_LIMIT_COOLDOWN)
        else:
            backoff = min(2.0**self.consecutive_failures, 5.0)
            self.current_delay = min(self.max_delay, self.current_delay * backoff)

    def cooldown(self, seconds: float = 10):
        """批次间冷却"""
        logger.info(f"Batch cooldown: {seconds}s...")
        time.sleep(seconds)


class MultiPlatformCollector:
    """多平台批量采集器 — 通过 adapter 注入解耦平台差异"""

    def __init__(
        self,
        platform: str = "xhs",
        safe_mode: bool = False,
        turbo_mode: bool = False,
    ):
        self.platform_name = platform
        self.adapter: PlatformAdapter = get_adapter(platform)
        self.limiter = RateLimiter(safe_mode=safe_mode, turbo_mode=turbo_mode)
        self.ner = FuturesNER()
        self.sentiment = SentimentAnalyzer()
        self.stats = CollectStats(started=datetime.now().isoformat())
        self.output_file: Path | None = None
        self.seen_ids: set = set()  # (platform, note_id) 跨关键词去重

    def init_api(self):
        """初始化平台适配器"""
        try:
            self.adapter.init()
        except CredentialError as e:
            print(f"\n{'=' * 60}")
            print(f"  ⚠️  {self.adapter.display_name} 登录凭证缺失或已过期！")
            print(f"{'=' * 60}")
            print(f"\n  {e}\n")
            sys.exit(1)

        logger.info(f"{self.adapter.display_name} adapter initialized.")

    def collect_one_keyword(self, keyword: str, count: int, max_detail: int) -> list[dict]:
        """
        采集一个关键词 (平台无关)。
        流程: search → [get_detail for each] → normalize → filter seen
        """
        notes = []
        logger.info(f"Searching: '{keyword}' (target {count})")

        # Step 1: 搜索
        self.limiter.wait()
        try:
            items = self.adapter.search(keyword, count)
        except Exception as e:
            logger.error(f"Search failed for '{keyword}': {e}")
            self.limiter.report_failure(
                is_rate_limit=(self.adapter.classify_error(e) == "rate_limit")
            )
            return []

        if not items:
            logger.warning(f"Search '{keyword}' returned no results")
            self.limiter.report_failure()
            return []

        self.limiter.report_success()
        logger.info(f"  Found {len(items)} items for '{keyword}'")

        # Step 2: 逐条获取详情 + 归一化
        detail_count = 0
        detail_limit = max_detail if self.adapter.needs_detail_fetch else len(items)

        for _item_idx, item in enumerate(items):
            if detail_count >= detail_limit:
                break

            nid = item.get("id", "") or item.get("mid", "")
            if not nid:
                continue

            # 详情获取
            detail = None
            fetch_ok = True
            if self.adapter.needs_detail_fetch:
                self.limiter.wait()
                try:
                    detail = self.adapter.get_detail(item)
                except Exception as e:
                    logger.warning(f"  Detail fetch failed for {str(nid)[:12]}...: {e}")
                    self.limiter.report_failure(
                        is_rate_limit=(self.adapter.classify_error(e) == "rate_limit")
                    )
                    self.stats.detail_failed += 1
                    continue

                if detail is None:
                    self.stats.detail_failed += 1
                    self.limiter.report_failure()
                    continue

                self.limiter.report_success()
                self.stats.detail_fetched += 1
                fetch_ok = True
            else:
                # 无需深挖 (如微博), 所有 item 都算成功
                fetch_ok = True

            if not fetch_ok:
                continue

            # 归一化为统一 Schema
            try:
                note_dict = self.adapter.normalize(item, detail, keyword)
            except Exception as e:
                logger.warning(f"  Normalize failed: {e}")
                continue

            if note_dict is None:
                continue

            # 去重
            pid = note_dict.get("platform", self.platform_name)
            note_id = note_dict.get("note_id", "")
            dedup_key = (pid, note_id)
            if dedup_key in self.seen_ids:
                logger.debug(f"  Duplicate skipped: {note_id[:20]}")
                continue
            self.seen_ids.add(dedup_key)

            detail_count += 1
            notes.append(note_dict)

            # 日志
            desc_len = len(note_dict.get("desc", "") or "")
            title_preview = (note_dict.get("title") or note_dict.get("desc") or "")[:40].replace(
                "\n", " "
            )
            try:
                print(
                    f"  [{detail_count}/{detail_limit}] {str(note_id)[:12]}... "
                    f"L{note_dict.get('like_count', 0)} C{note_dict.get('comment_count', 0)} "
                    f"| {desc_len}c | {title_preview}"
                )
            except UnicodeEncodeError:
                safe_title = title_preview.encode("ascii", errors="replace").decode("ascii")
                print(
                    f"  [{detail_count}/{detail_limit}] {str(note_id)[:12]}... "
                    f"L{note_dict.get('like_count', 0)} C{note_dict.get('comment_count', 0)} "
                    f"| {desc_len}c | {safe_title}"
                )

        self.stats.searched_total += len(items)
        return notes

    def _enrich_notes(self, notes: list[dict]) -> list[dict]:
        """NER + 情感分析 enrich (平台无关, 只吃文本)"""
        for note in notes:
            text = (note.get("title", "") + " " + note.get("desc", "")).strip()
            if not text:
                # 无文本 → 填默认值
                note.setdefault("varieties", [])
                note.setdefault("contracts", [])
                note.setdefault("variety_count", 0)
                note.setdefault("sentiment", "neutral")
                note.setdefault("sentiment_score", 0.0)
                note.setdefault("sentiment_confidence", 0.0)
                note.setdefault("variety_sentiments", [])
                continue

            # NER
            entities = self.ner.extract(text)
            note["varieties"] = entities["varieties"]
            note["contracts"] = entities["contracts"]
            note["variety_count"] = entities["variety_count"]

            # 整篇情感
            r = self.sentiment.analyze(text)
            note["sentiment"] = r["sentiment"]
            note["sentiment_score"] = r["score"]
            note["sentiment_confidence"] = r["confidence"]

            # 品种级情感
            var_sent = self.sentiment.analyze_aspects(text, entities["varieties"])
            note["variety_sentiments"] = var_sent

        return notes

    def run(
        self,
        keywords: list[str],
        per_kw: int = 30,
        max_detail: int = 10,
        no_enrich: bool = False,
        since: str | None = None,
    ) -> list[dict]:
        """主采集循环 (平台无关)。

        Args:
            since: 可选日期过滤 (YYYY-MM-DD)，只保留此日期及之后的帖子。
        """
        self.init_api()
        self.stats.keywords_total = len(keywords)

        # 输出文件: batch_{platform}_{ts}.jsonl
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_file = OUTPUT_DIR / f"batch_{self.platform_name}_{ts}.jsonl"

        # Since date filter
        since_date = None
        if since:
            try:
                since_date = datetime.strptime(since, "%Y-%m-%d")
                print(f"  Time filter: only keeping posts since {since}")
            except ValueError:
                print(f"  WARNING: Invalid --since date '{since}', ignoring filter")
                since_date = None

        total_notes = 0
        detail_text = "no detail" if not self.adapter.needs_detail_fetch else f"detail {max_detail}"
        mode_str = (
            "TURBO" if self.limiter.turbo_mode else "SAFE" if self.limiter.safe_mode else "FAST"
        )

        print(f"\n{'=' * 60}")
        print(f"BATCH COLLECTION — {self.adapter.display_name}")
        print(f"  Keywords: {len(keywords)}")
        print(f"  Per keyword: search {per_kw}, {detail_text}")
        print(f"  Mode: {mode_str}")
        print(f"  Output: {self.output_file}")
        print(f"{'=' * 60}\n")

        start_time = time.time()

        for kw_idx, kw in enumerate(keywords):
            print(f"\n--- [{kw_idx + 1}/{len(keywords)}] '{kw}' ---")

            try:
                notes = self.collect_one_keyword(kw, count=per_kw, max_detail=max_detail)
            except Exception as e:
                logger.error(f"Keyword '{kw}' failed: {e}")
                self.stats.errors.append(f"{kw}: {e}")
                notes = []

            # NER + 情感 enrich
            if notes and not no_enrich:
                self._enrich_notes(notes)

            # Time filter (since_date)
            if since_date and notes:
                filtered = []
                skipped = 0
                for note in notes:
                    pt = note.get("publish_time", "")
                    if pt:
                        try:
                            note_date = datetime.strptime(pt[:10], "%Y-%m-%d")
                            if note_date >= since_date:
                                filtered.append(note)
                            else:
                                skipped += 1
                        except ValueError:
                            filtered.append(note)  # Keep if unparseable
                    else:
                        filtered.append(note)  # Keep if no publish_time
                if skipped:
                    print(
                        f"  [filter] Kept {len(filtered)}/{len(notes)} notes (skipped {skipped} before {since})"
                    )
                notes = filtered

            # 增量写盘: 每个关键词完成后追加 (中断不丢数据)
            with open(self.output_file, "a", encoding="utf-8") as f:
                for note in notes:
                    f.write(json.dumps(note, ensure_ascii=False) + "\n")

            total_notes += len(notes)
            self.stats.keywords_done = kw_idx + 1

            # 进度
            elapsed = time.time() - start_time
            avg_time_per_kw = elapsed / (kw_idx + 1) if kw_idx > 0 else 0
            eta = avg_time_per_kw * (len(keywords) - kw_idx - 1)
            print(
                f"\n  '{kw}' done: {len(notes)} notes enriched. "
                f"Total: {total_notes}. ETA: {eta / 60:.0f}min"
            )

            # 批次间冷却 (无结果的 kw 跳过, 省时间)
            if kw_idx < len(keywords) - 1 and notes:
                self.limiter.cooldown(BATCH_COOLDOWN + random.uniform(0, 1))

        # 关闭平台资源
        with contextlib.suppress(Exception):
            self.adapter.close()

        # 汇总
        elapsed = time.time() - start_time
        print(f"\n{'=' * 60}")
        print(f"BATCH COLLECTION COMPLETE — {self.adapter.display_name}")
        print(f"  Time: {elapsed / 60:.0f}min")
        print(f"  Keywords: {len(keywords)} done")
        print(f"  Total notes: {total_notes} (unique: {len(self.seen_ids)})")
        print(f"  Detail success: {self.stats.detail_fetched}")
        print(f"  Detail failed: {self.stats.detail_failed}")
        print(f"  Rate limits hit: {self.stats.rate_limits_hit}")
        print(f"  Output: {self.output_file}")
        print(f"{'=' * 60}")

        return []


# ============================================================
# CLI
# ============================================================


def main():
    parser = argparse.ArgumentParser(
        description="多平台期货社交媒体数据批量采集",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python batch_collect.py                                         # 默认 xhs, 40关键词
  python batch_collect.py --platform weibo                        # 微博采集
  python batch_collect.py --platform xhs --per-kw 30              # 小红书, 每词30条
  python batch_collect.py --keywords 铁矿石 螺纹钢 原油           # 自定义关键词
  python batch_collect.py --no-detail                             # 不深挖 (微博推荐)
  python batch_collect.py --safe-mode                             # 安全模式
  python batch_collect.py --turbo                                 # 极速模式
        """,
    )
    parser.add_argument(
        "--platform", type=str, default="xhs", choices=list_platforms(), help="目标平台 (默认 xhs)"
    )
    parser.add_argument("--keywords", nargs="+", default=None, help="采集关键词 (默认使用预设列表)")
    parser.add_argument("--per-kw", type=int, default=30, help="每关键词搜索条数 (默认30)")
    parser.add_argument(
        "--max-detail", type=int, default=10, help="每关键词深挖条数 (默认10, 微博自动忽略)"
    )
    parser.add_argument(
        "--safe-mode", action="store_true", help="安全模式: 更长的请求间隔 (750ms起步)"
    )
    parser.add_argument(
        "--turbo", action="store_true", help="极速模式: 最小延时(120ms), Cookie新鲜时使用"
    )
    parser.add_argument(
        "--no-detail", action="store_true", help="不获取详情 (速度快, 但可能无正文)"
    )
    parser.add_argument("--no-enrich", action="store_true", help="跳过NER+情感分析")
    parser.add_argument(
        "--since", type=str, default=None, help="只保留此日期之后的帖子 (YYYY-MM-DD)"
    )
    parser.add_argument("--output", type=str, default=None, help="输出文件名 (默认自动生成)")
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # 关键词
    keywords = (
        args.keywords
        if args.keywords
        else DEFAULT_KEYWORDS.get(args.platform, DEFAULT_KEYWORDS_XHS)
    )
    max_detail = 0 if args.no_detail else args.max_detail
    platform_name = args.platform

    from platforms import ADAPTER_DISPLAY_NAMES

    display = ADAPTER_DISPLAY_NAMES.get(platform_name, platform_name)

    mode_str = "TURBO" if args.turbo else "SAFE" if args.safe_mode else "FAST"
    print("=" * 60)
    print(f"  Multi-Platform Futures Data Collection — {display}")
    print("=" * 60)
    print(f"  Platform:    {display}")
    print(f"  Keywords:    {len(keywords)}")
    print(f"  Per keyword: search {args.per_kw}, detail {max_detail}")
    print(f"  Mode:        {mode_str}")
    print(f"  NER+Sentiment: {'OFF' if args.no_enrich else 'ON'}")
    if not args.no_detail:
        deep_fetch = (
            max_detail if getattr(get_adapter(platform_name), "needs_detail_fetch", True) else 0
        )
        est_calls = len(keywords) * deep_fetch if deep_fetch else len(keywords)
        print(f"  Est. detail API calls: ~{est_calls}")
    print()

    collector = MultiPlatformCollector(
        platform=platform_name,
        safe_mode=args.safe_mode,
        turbo_mode=args.turbo,
    )

    try:
        collector.run(
            keywords=keywords,
            per_kw=args.per_kw,
            max_detail=max_detail,
            no_enrich=args.no_enrich,
            since=args.since,
        )
    except KeyboardInterrupt:
        print("\n\nInterrupted! Partial results saved to:")
        print(f"  {collector.output_file}")
        print(f"  Collected: {collector.stats.keywords_done}/{len(keywords)} keywords")
        print(f"  Total notes so far: ~{collector.stats.detail_fetched}")
    except Exception as e:
        logger.error(f"Fatal: {e}", exc_info=args.verbose)
        if collector.output_file:
            print(f"\nPartial results saved to: {collector.output_file}")


if __name__ == "__main__":
    main()
