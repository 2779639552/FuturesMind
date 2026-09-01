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


本文件在"情绪数据生产链"中的角色
--------------------------------
    这是多平台【批量采集入口】, 也是 scheduler(定时任务)调用的核心脚本。
    它把"平台差异"抽象成适配器(adapter), 统一完成:
      搜索(search) → 详情(get_detail) → 归一化(normalize) → 去重(dedup)
      → NER品种识别 + 情感分析(enrich) → 增量写盘(batch_*.jsonl)

    与 hybrid_pipeline.py 的关系:
      - hybrid_pipeline 是"小红书专用"的两层(Playwright+API拦截)采集器。
      - 本文件是"多平台通用"采集器, 通过 platforms 模块的 get_adapter()
        按 --platform 参数获取对应平台适配器 (xhs/weibo/zhihu/xueqiu/eastmoney_guba)。
      下游: 采集出的 JSONL 交给 analyze.py / trend_aggregator.py 做分析聚合。
"""

import argparse  # 【调用包】命令行参数解析 (--platform/--per-kw等)
import contextlib  # 【调用包】suppress异常 (关闭平台适配器时容错)
import json  # 【调用包】JSON序列化, 逐行写入JSONL结果文件
import logging  # 【调用包】日志记录 (采集过程状态)
import random  # 【调用包】随机抖动延时/冷却时长, 降低反爬识别
import sys  # 【调用包】sys.exit退出 (凭证缺失时)
import time  # 【调用包】请求间隔计时/ETA预估
from dataclasses import dataclass, field  # 【调用包】CollectStats统计容器
from datetime import datetime  # 【调用包】输出文件时间戳/--since日期解析
from pathlib import Path  # 【调用包】路径操作 (输出目录)

# NER + 情感 (纯文本, 平台无关)
from ner import FuturesNER  # 【调用包】期货品种NER识别 (品种/合约/交易所)

# 平台适配器
from platforms import (  # 【调用包】平台适配器工厂: 按平台名获取采集适配器
    get_adapter,
    list_platforms,
)
from platforms.base import (  # 【调用包】平台适配器基类接口 + 凭证异常
    CredentialError,
    PlatformAdapter,
)
from sentiment import SentimentAnalyzer  # 【调用包】规则情感分析器 (7级分类)

logger = logging.getLogger("batch.collect")

# Windows GBK 控制台/管道下, emoji(如 ⚠️/🟢)会触发 UnicodeEncodeError 崩溃,
# 掩盖真实错误(如适配器凭证缺失)。errors="replace" 把不可编码字符降级为 '?'
# 而非崩溃——这是调度器子进程(GBK 捕获 stdout)的安全兜底, 不改变正常中文输出。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(errors="replace")

# ============================================================
# 配置
# ============================================================

# ============ 采集控制参数 (限流/延时相关) ============
# 这些数值共同决定"多快"地访问平台, 是避免被封号的核心调参点。
DEFAULT_PER_KW = 30  # 每关键词采集条数
MIN_DELAY_MS = 300  # 最小请求间隔(ms) — 实测API耗时~750ms, 300ms间隔安全
MAX_DELAY_MS = 1000  # 最大请求间隔(ms)
SAFE_MODE_MULTIPLIER = 2.5  # 安全模式延时倍率 (300*2.5=750ms 起步)
TURBO_MIN_DELAY_MS = 120  # 极速模式最小间隔
TURBO_MAX_DELAY_MS = 500  # 极速模式最大间隔
RATE_LIMIT_COOLDOWN = 30  # 触发限流后冷却秒数
BATCH_COOLDOWN = 1  # 每批关键词间休息秒数
MAX_DETAIL_PER_KW = 10  # 每关键词最多深挖条数

OUTPUT_DIR = Path(__file__).parent / "output"  # 【变量】结果输出目录 (validate/output/)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)  # 【调用函数】确保输出目录存在

# 平台默认关键词 (xhs 保留旧列表, 微博/知乎待 config.py 扩展)
DEFAULT_KEYWORDS_XHS = [  # 【变量】小红书平台默认关键词列表 (按板块分组, 覆盖全品种)
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
    "燃料油期货",
    "低硫燃料油期货",
    "20号胶期货",
    "苯乙烯期货",
    "乙二醇期货",
    "塑料期货",
    "PP期货",
    "对二甲苯期货",
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

DEFAULT_KEYWORDS_WEIBO = [  # 【变量】微博平台默认关键词列表 (品种级短词)
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
    "沥青",
    "燃料油",
    "低硫燃料油",
    "20号胶",
    "苯乙烯",
    "乙二醇",
    "聚丙烯",
    "对二甲苯",
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

DEFAULT_KEYWORDS_ZHIHU = [  # 【变量】知乎平台默认关键词列表 (偏问答/讨论向)
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
    "沥青",
    "橡胶",
    "燃料油",
    "低硫燃料油",
    "20号胶",
    "苯乙烯",
    "乙二醇",
    "对二甲苯",
    "豆粕",
    "棕榈油",
    "白糖",
    "股指期货",
    "国债期货",
    "期货交易策略",
    "期货技术分析",
]

DEFAULT_KEYWORDS_XUEQIU = [  # 【变量】雪球平台默认关键词列表
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
    "沥青",
    "燃料油",
    "低硫燃料油",
    "20号胶",
    "苯乙烯",
    "乙二醇",
    "聚丙烯",
    "对二甲苯",
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

DEFAULT_KEYWORDS_EASTMONEY_GUBA = [  # 【变量】东财股吧平台默认关键词列表(带"期货"后缀, 覆盖33品种池+稀疏品种长尾扩充)
    # 黑色系
    "螺纹钢期货",
    "铁矿石期货",
    "焦炭期货",
    "焦煤期货",
    "热卷期货",
    "硅铁期货",
    "锰硅期货",
    # 有色
    "沪铜期货",
    "沪铝期货",
    "沪锌期货",
    "沪镍期货",
    "黄金期货",
    "白银期货",
    "碳酸锂期货",
    "工业硅期货",
    # 能化 (覆盖 33 品种池; 2026-09-01 扩能化整组 12 品种)
    "原油期货",
    "PTA期货",
    "甲醇期货",
    "纯碱期货",
    "PVC期货",
    "玻璃期货",
    "尿素期货",
    "橡胶期货",
    "沥青期货",
    "燃料油期货",
    "低硫燃料油期货",
    "20号胶期货",
    "苯乙烯期货",
    "乙二醇期货",
    "塑料期货",
    "PP期货",
    "对二甲苯期货",
    "短纤期货",
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
    # 稀疏品种长尾扩充(股吧帖子长尾词, 补 AP/CJ/PK/FG/UR/PF 等帖子量; 不用裸"苹果"避免撞Apple)
    "苹果冷库",
    "苹果套袋",
    "玻璃库存",
    "浮法玻璃",
    "尿素出口",
    "涤纶短纤",
    "短纤报价",
    "红枣库存",
    "花生库存",
    # 主题词
    "期货实盘",
    "期货技术分析",
    "期货基本面",
    "商品期货",
    "期货实战",
    "期货交易心得",
]

DEFAULT_KEYWORDS = {  # 【变量】平台名→默认关键词列表映射 (按--platform选择)
    "xhs": DEFAULT_KEYWORDS_XHS,
    "weibo": DEFAULT_KEYWORDS_WEIBO,
    "zhihu": DEFAULT_KEYWORDS_ZHIHU,
    "xueqiu": DEFAULT_KEYWORDS_XUEQIU,
    "eastmoney_guba": DEFAULT_KEYWORDS_EASTMONEY_GUBA,
}


# ============================================================
# 数据结构
# ============================================================


@dataclass
class CollectStats:
    """
    【功能】采集过程统计信息的容器 (数据类)。
    【字段说明】
      started: 开始时间 (ISO 字符串)。
      keywords_total / keywords_done: 关键词总数 / 已完成数。
      searched_total: 搜索到的条目总数。
      detail_fetched / detail_failed: 详情成功获取数 / 失败数。
      rate_limits_hit: 触发限流次数 (预留给统计, 当前未在此处累加)。
      errors: 采集过程中的错误信息列表。
    【关键逻辑】供 run() 结束时的汇总打印与中断提示使用。
    """

    started: str = ""
    keywords_total: int = 0
    keywords_done: int = 0
    searched_total: int = 0
    detail_fetched: int = 0
    detail_failed: int = 0
    rate_limits_hit: int = 0
    errors: list = field(default_factory=list)


class RateLimiter:
    """
    【功能】自适应请求延时控制器 (平台无关)。
            根据请求成功/失败动态调整每次请求前的等待时间,
            降低触发平台反爬/限流的概率。
    【关键逻辑】
      - 三档模式: TURBO(极速)/SAFE(安全)/FAST(默认)。
      - 成功: 逐渐缩短延时 (report_success)。
      - 失败: 指数退避延长延时; 若是限流则翻倍并进入冷却 (report_failure)。
    """

    def __init__(self, safe_mode: bool = False, turbo_mode: bool = False):
        """【功能】初始化延时区间与抖动幅度。
        【参数】safe_mode: 是否安全模式; turbo_mode: 是否极速模式。
        【返回】无。"""
        if turbo_mode:
            self.min_delay = TURBO_MIN_DELAY_MS / 1000  # 【变量】极速模式: 120~500ms间隔 (Cookie新鲜时使用)
            self.max_delay = TURBO_MAX_DELAY_MS / 1000
            self.jitter_pct = 0.10  # 【变量】极速模式抖动幅度10%
        elif safe_mode:
            self.min_delay = (MIN_DELAY_MS * SAFE_MODE_MULTIPLIER) / 1000  # 【变量】安全模式: 延时×2.5倍 (300→750ms起步)
            self.max_delay = (MAX_DELAY_MS * SAFE_MODE_MULTIPLIER) / 1000
            self.jitter_pct = 0.25  # 【变量】安全模式抖动幅度25%
        else:
            self.min_delay = MIN_DELAY_MS / 1000  # 【变量】默认FAST模式: 300~1000ms间隔
            self.max_delay = MAX_DELAY_MS / 1000
            self.jitter_pct = 0.15  # 【变量】默认模式抖动幅度15%
        self.current_delay = self.min_delay  # 【变量】当前目标延时 (成功递减/失败递增)
        self.consecutive_failures = 0  # 【变量】连续失败次数 (用于指数退避)
        self.last_request_time = 0  # 【变量】上次请求时间戳 (计算实际间隔)
        self.safe_mode = safe_mode
        self.turbo_mode = turbo_mode

    def wait(self):
        """【功能】在每次发请求前调用, 若距上次请求时间不足"目标延时"则睡眠补齐。
        【参数】无。 【返回】无。
        【关键逻辑】在基础延时上加随机抖动(jitter), 让请求间隔不像机器一样均匀。"""
        elapsed = time.time() - self.last_request_time
        wait_time = max(0, self.current_delay - elapsed)
        if wait_time > 0:
            jitter = random.uniform(-self.jitter_pct, self.jitter_pct) * wait_time
            time.sleep(wait_time + jitter)
        self.last_request_time = time.time()

    def report_success(self):
        """【功能】请求成功后调用: 逐步把延时恢复回最小值 (乘以 0.9 逼近 min_delay)。
        【参数】无。 【返回】无。"""
        self.consecutive_failures = 0
        self.current_delay = max(self.min_delay, self.current_delay * 0.9)

    def report_failure(self, is_rate_limit: bool = False):
        """【功能】请求失败后调用: 提高延时, 限流时加倍并进入冷却休眠。
        【参数】is_rate_limit: 是否被判定为限流。
        【返回】无。
        【关键逻辑】
          - 限流: current_delay 翻倍 + 睡 RATE_LIMIT_COOLDOWN 秒。
          - 普通失败: 按连续失败次数指数退避 (2^n 封顶 5 倍), 且不超过 max_delay。"""
        self.consecutive_failures += 1
        if is_rate_limit:
            self.current_delay *= 2.0
            logger.warning(f"Rate limit detected! Delay increased to {self.current_delay:.1f}s")
            time.sleep(RATE_LIMIT_COOLDOWN)
        else:
            backoff = min(2.0**self.consecutive_failures, 5.0)
            self.current_delay = min(self.max_delay, self.current_delay * backoff)

    def cooldown(self, seconds: float = 10):
        """【功能】批次之间主动休息 (默认 10 秒), 降低连续大批请求的暴露风险。
        【参数】seconds: 休息秒数。 【返回】无。"""
        logger.info(f"Batch cooldown: {seconds}s...")
        time.sleep(seconds)


class MultiPlatformCollector:
    """
    【功能】多平台批量采集器。核心思路是"适配器(adapter)注入":
            平台差异被封装进 platforms 模块的 PlatformAdapter 接口,
            本类只面向接口编程, 换平台只需换 adapter, 采集逻辑完全复用。
    【关键逻辑】
      - adapter.search / adapter.get_detail / adapter.normalize 完成平台对接。
      - self.ner (FuturesNER) 与 self.sentiment (SentimentAnalyzer) 是
        平台无关的纯文本处理模块, 用于采集后的 enrich (NER + 情感)。
      - self.seen_ids 用 (platform, note_id) 元组做跨关键词全局去重。
    """

    def __init__(
        self,
        platform: str = "xhs",
        safe_mode: bool = False,
        turbo_mode: bool = False,
    ):
        # 平台名 + 通过工厂函数 get_adapter 拿到对应适配器实例
        self.platform_name = platform
        self.adapter: PlatformAdapter = get_adapter(platform)  # 【调用函数】工厂函数获取平台适配器实例 (xhs/weibo/zhihu/xueqiu/eastmoney_guba)
        # 自适应延时控制器 (三档模式在构造时决定)
        self.limiter = RateLimiter(safe_mode=safe_mode, turbo_mode=turbo_mode)
        # NER 与情感分析器 (纯文本, 平台无关)
        self.ner = FuturesNER()
        self.sentiment = SentimentAnalyzer()
        self.stats = CollectStats(started=datetime.now().isoformat())  # 【变量】采集统计容器, 记录开始时间供汇总打印
        self.output_file: Path | None = None
        self.seen_ids: set = set()  # (platform, note_id) 跨关键词去重

    def init_api(self):
        """【功能】初始化平台适配器 (登录/建立会话等), 凭证缺失时给出提示并退出。
        【参数】无。 【返回】无。
        【关键逻辑】adapter.init() 若抛出 CredentialError (凭证缺失/过期),
        打印友好提示后 sys.exit(1), 避免带着坏会话继续空跑。"""
        try:
            self.adapter.init()  # 【调用函数】平台适配器初始化 (登录/建立会话)
        except CredentialError as e:
            print(f"\n{'=' * 60}")
            print(f"  ⚠️  {self.adapter.display_name} 登录凭证缺失或已过期！")
            print(f"{'=' * 60}")
            print(f"\n  {e}\n")
            sys.exit(1)

        logger.info(f"{self.adapter.display_name} adapter initialized.")

    def collect_one_keyword(self, keyword: str, count: int, max_detail: int) -> list[dict]:
        """
        【功能】采集单个关键词的全流程 (平台无关)。
        【参数】
          keyword: str, 本次要搜索的关键词。
          count: int, 搜索时目标返回条数。
          max_detail: int, 最多对其中多少条做详情深挖。
        【返回】list[dict]: 通过去重、归一化后的笔记字典列表 (未做 NER/情感)。
        【关键逻辑】
          流程: search(搜索) → get_detail(逐条详情) → normalize(归一化) → 去重。
          每一步的限流/失败都会通过 self.limiter 反馈给延时控制器。
        """
        notes = []
        logger.info(f"Searching: '{keyword}' (target {count})")

        # Step 1: 搜索 (发请求前先 wait 遵守延时)
        self.limiter.wait()
        try:
            items = self.adapter.search(keyword, count)  # 【调用函数】平台适配器搜索接口 (发请求前已wait遵守延时)
        except Exception as e:
            logger.error(f"Search failed for '{keyword}': {e}")
            # classify_error 把异常归类为 rate_limit 或其他, 决定退避策略
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
        detail_count = 0  # 已成功采集的条数 (用作进度计数)
        # 需要详情深挖的平台按 max_detail 限制; 否则(如微博)全部 items 都算成功
        detail_limit = max_detail if self.adapter.needs_detail_fetch else len(items)  # 【变量】详情深挖上限: 需深挖平台按max_detail, 否则全部items算成功

        for _item_idx, item in enumerate(items):
            if detail_count >= detail_limit:
                break

            # 从平台原始条目里取出唯一 ID (不同平台字段名不同: id / mid)
            nid = item.get("id", "") or item.get("mid", "")
            if not nid:
                continue

            # 详情获取 (仅对需要详情深挖的平台生效, 如 xhs)
            detail = None
            fetch_ok = True
            if self.adapter.needs_detail_fetch:
                self.limiter.wait()
                try:
                    detail = self.adapter.get_detail(item)  # 【调用函数】平台适配器详情接口 (获取完整正文/互动数据)
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

            # 归一化为统一 Schema:
            # 各平台原始字段千差万别, normalize() 统一成 note_id/title/desc/... 标准字段
            try:
                note_dict = self.adapter.normalize(item, detail, keyword)  # 【调用函数】平台适配器归一化: 各平台字段统一为note_id/title/desc标准Schema
            except Exception as e:
                logger.warning(f"  Normalize failed: {e}")
                continue

            if note_dict is None:
                continue

            # 去重: 用 (platform, note_id) 作为全局唯一键,
            # 同一个帖子即使被多个关键词搜到, 也只保留第一份
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
        """
        【功能】对一批笔记做"文本增强": NER 品种识别 + 情感分析 (平台无关)。
        【参数】notes: list[dict], 归一化后的笔记列表。
        【返回】list[dict]: 原列表, 但每个 dict 被原地追加 NER/情感字段。
        【关键逻辑】
          - 把 title 和 desc 拼起来作为分析文本 (标题通常信息密度高)。
          - 无文本的笔记直接填默认值 (neutral, 空品种)。
          - 情感分三层: 整篇情感 (sentiment_*) + 品种级情感 (variety_sentiments)。
        """
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

            # NER: 从文本中提取品种/合约, 得到 varieties(品种列表) 等字段
            entities = self.ner.extract(text)  # 【调用函数】跨模块(ner): 提取品种/合约/交易所等实体
            note["varieties"] = entities["varieties"]
            note["contracts"] = entities["contracts"]
            note["variety_count"] = entities["variety_count"]

            # 整篇情感: 规则引擎对全文打分, 得到 7 级情感 + 分数 + 置信度
            r = self.sentiment.analyze(text)  # 【调用函数】跨模块(sentiment): 对全文做整篇情感打分
            note["sentiment"] = r["sentiment"]
            note["sentiment_score"] = r["score"]
            note["sentiment_confidence"] = r["confidence"]

            # 品种级情感: 对每个提到的品种, 截取其上下文分别打情感分
            var_sent = self.sentiment.analyze_aspects(text, entities["varieties"])  # 【调用函数】跨模块(sentiment): 逐品种截取上下文做细粒度情感
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
        """
        【功能】主采集循环 (平台无关): 逐关键词采集 → enrich → 时间过滤 → 增量写盘。
        【参数】
          keywords: list[str], 本次要采集的关键词列表。
          per_kw: int, 每个关键词搜索多少条。
          max_detail: int, 每个关键词最多深挖详情多少条。
          no_enrich: bool, 为 True 时跳过 NER + 情感 enrich (提速)。
          since: str | None, 形如 "YYYY-MM-DD", 只保留此日期及之后的帖子。
        【返回】list[dict] (当前实现总是返回空列表; 数据直接落盘到 JSONL)。
        【关键逻辑】
          - 输出文件: output/batch_{platform}_{时间戳}.jsonl (逐关键词追加)。
          - 每个关键词: collect_one_keyword → _enrich_notes → since 时间过滤
            → 追加写盘, 因此中途中断也不会丢已完成的批次。
          - 批次间用 limiter.cooldown 冷却, 无结果的关键词不等待省时间。
        """
        self.init_api()
        self.stats.keywords_total = len(keywords)

        # 输出文件: batch_{platform}_{ts}.jsonl
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_file = OUTPUT_DIR / f"batch_{self.platform_name}_{ts}.jsonl"  # 【变量】输出文件路径: batch_{平台}_{时间戳}.jsonl

        # Since date filter
        since_date = None
        if since:
            try:
                since_date = datetime.strptime(since, "%Y-%m-%d")  # 【调用函数】解析--since日期字符串为datetime对象
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

        # ===== 逐关键词主循环 =====
        for kw_idx, kw in enumerate(keywords):
            print(f"\n--- [{kw_idx + 1}/{len(keywords)}] '{kw}' ---")

            # 采集单个关键词; 整体失败时记录错误并继续下一个关键词
            try:
                notes = self.collect_one_keyword(kw, count=per_kw, max_detail=max_detail)  # 【调用函数】采集单个关键词 (搜索→详情→归一化→去重)
            except Exception as e:
                logger.error(f"Keyword '{kw}' failed: {e}")
                self.stats.errors.append(f"{kw}: {e}")
                notes = []

            # NER + 情感 enrich: 给每条笔记补充品种/合约/情感字段
            if notes and not no_enrich:
                self._enrich_notes(notes)  # 【调用函数】NER+情感enrich, 给每条笔记补充分析字段

            # Time filter (since_date):
            # 若指定了 --since, 只保留 publish_time >= since_date 的帖子
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

            # 增量写盘: 每个关键词完成后立即追加到 JSONL (逐行 JSON),
            # 好处: 即使程序中途被 Ctrl+C / 报错打断, 已完成的关键词数据不丢失
            with open(self.output_file, "a", encoding="utf-8") as f:
                for note in notes:
                    f.write(json.dumps(note, ensure_ascii=False) + "\n")  # 【调用函数】JSONL增量写盘 (每关键词立即落盘, 中断不丢)

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
                self.limiter.cooldown(BATCH_COOLDOWN + random.uniform(0, 1))  # 【调用函数】批次间主动冷却 (随机1~2秒, 降低连续大批请求暴露)

        # 关闭平台资源
        with contextlib.suppress(Exception):
            self.adapter.close()  # 【调用函数】关闭平台适配器资源 (会话/连接)

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
    """
    【功能】命令行入口: 解析参数 → 打印采集计划 → 构造采集器 → run()。
    【关键逻辑】
      - 关键词优先级: 命令行 --keywords 参数 > 平台预设关键词列表。
      - --no-detail 会把 max_detail 置为 0 (微博等平台本就无需详情)。
      - 打印预估详情 API 调用次数, 便于用户判断请求量。
      - KeyboardInterrupt / 异常时都提示"部分结果已保存到 output 文件"。
    """
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

    args = parser.parse_args()  # 【调用函数】解析命令行参数
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
    max_detail = 0 if args.no_detail else args.max_detail  # 【变量】--no-detail时深挖数置0 (微博等平台无需详情)
    platform_name = args.platform

    from platforms import ADAPTER_DISPLAY_NAMES  # 【调用包】平台显示名映射 (用于打印友好名称)

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
