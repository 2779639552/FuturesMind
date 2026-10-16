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

"渠道静默失效"治理 (2026-09-14)
--------------------------------
    症状: 多平台跑完只有一两个平台有数据, 其余"看起来正常"却 0 条, 且几天后
          才发现——因为旧实现**没有任何可事后复查的机器可读记录**。
    已定位的真实缺陷:
      1) 【致命】`--no-detail` 把需要详情深挖的平台(zhihu/xhs)的条目**整批丢掉**:
         `detail_limit = max_detail if needs_detail_fetch else len(items)` 在
         max_detail=0 时等于 0, 循环第一轮就 break → search 明明成功却写 0 条。
         知乎自 2026-08-26 起 100% 零条即由此而来。已修:max_detail<=0 只表示
         "不额外拉详情", 仍然用搜索返回的摘要入库。
      2) 退出码恒为 0: 凭证失效/整批被拦截也 exit 0, 调用方无法据此告警。
         现在按结论给退出码(EXIT_AUTH_FAILED / EXIT_BLOCKED / EXIT_OK ...)。
      3) 无运行留痕: 现在每次运行都写 output/collect_run_{platform}_{ts}.json,
         并在 stdout 打一行 `COLLECT_RUN: {...}` 供调用方直接解析。
         (文件名刻意不用 batch_ 前缀, 免得被聚合的 batch_*.jsonl 通配扫进去。)
"""

import argparse  # 【调用包】命令行参数解析 (--platform/--per-kw等)
import contextlib  # 【调用包】suppress异常 (关闭平台适配器时容错)
import json  # 【调用包】JSON序列化, 逐行写入JSONL结果文件
import logging  # 【调用包】日志记录 (采集过程状态)
import os  # 【调用包】原子替换(os.replace)写轮转游标状态文件
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

# ── 两道运行护栏 (2026-09-15 从抖音迁移包定向移植) ──────────────────────
# 【为什么需要】本文件原先没有任何"主动止损"机制:平台被限流时仍会挨个跑完
# 全部关键词,每次都在平台上再撞一次,既浪费时间又加重风控;子进程最终
# 只返回一句"采集到 0 条",调用方(调度器)无从判断是限流、失效还是真没内容。
# 抖音尤其明显:它的适配器要逐视频开页滚评论,单关键词可达数分钟,
# 一旦被限流而继续跑完 3~5 个关键词,足以撞上调度器 15 分钟的子进程超时。
MAX_RUN_MINUTES = 20  # 全局时间预算(分钟):超时在**关键词边界**优雅收尾,数据已逐词落盘
# 【2026-09-18 12 → 20】实测只有抖音会被这个预算卡住(443s/6 词 = 73.8s/词, 其余平台
#   都在预算内跑完全部关键词: 股吧 31/31 词 583s、知乎 20/20 词 103s、雪球 12/12 词 43s、
#   小红书 25/25 词 66s), 所以放预算几乎只影响抖音 —— 影响面干净。
# 【2026-09-24 30 → 20 回退】抖音改回 13 词/轮(06:00 早轮 + 08:00/18:00 管道轮), 13 词
#   × ~55s ≈ 12min, 20min 预算富余(曾试 25 词全量需 ~37min 会截断, 已回退)。
#   ⚠️ 这个默认值只作用于**定时链路**(调度器不传 --max-minutes); 网页「一键更新」链路
#   显式传 `--max-minutes 9`(配它自己的 subprocess timeout=600), 故不受此处影响。
#   ⚠️ 两者必须满足 `子进程 timeout > 本预算`: 预算比超时还长 = **必然被强杀**, 一旦强杀
#   连 collect_run 报告与退出码都写不出来(见 tests 里的跨模块不变量测试)。
MAX_CONSECUTIVE_EMPTY = 5  # 连续 N 个关键词 0 命中即熔断(平台级故障的强信号)


def keyword_rotation_offset(run_index: int, stride: int, total: int) -> int:
    """关键词轮转的起始偏移(纯函数,便于确定性单测)。

    【功能】给出第 run_index 轮应从关键词列表的第几个开始。
    【参数】
        run_index: 自年初起的第几轮(全局递增,由 日期序号×2 + 当天上/下午 推出)
        stride:    步长 —— 取"一轮能跑多少个词"(即 --max-keywords)
        total:     关键词总数
    【返回】偏移量,范围 [0, total)。
    【关键逻辑】步长必须等于窗口宽度,相邻两轮才是**互补切片**:
        12 词/轮 × 2 轮/天 对 23 个关键词 → offset 0 覆盖 0~11、offset 12 覆盖 12~22,
        一天即覆盖全部。若步长取 1,窗口每轮只挪一格,要滑 11 天才轮完一遍,等于没解决
        "排在后面的关键词被系统性饿死"这个问题。
    """
    if total <= 0:
        return 0
    return (run_index * max(1, stride)) % total


# ============================================================================
# 关键词轮转的【持久游标】 (2026-09-17)
# ============================================================================
# 【为什么要从"无状态派生"改成"落盘游标"】旧实现用 `日期序号×2 + 上/下午` 推 offset,
#   刻度是**半天**: 同一半天内无论跑几次, offset 完全相同 ⇒ 同一天上午的三次运行采的
#   是**同一批词**(实测 2026-09-17 抖音上午三轮都从 '沥青期货' 起跑, 内容高度重复)。
#   错峰的价值被吃掉, 还让同一个词在短时间内被反复请求, 给平台风控多送一次触发机会。
#   现在游标落在 output/.rotation_{platform}.json, 按"本轮实际跑完的词数"推进:
#   跑 6 个词就前进 6 格, 一天两轮即覆盖 12 个词, 与词池大小无关地持续推进。
# 【自愈性】推进量取 **keywords_done**(实际完成数)而非窗口宽度: 若某轮被风控熔断
#   只跑了 2 个词, 游标只前进 2 格, 没跑到的词下一轮**优先补上**, 不会漏采。
# 【兼容】状态文件缺失/损坏 → 回退到旧的半天派生法(行为与升级前逐字一致),
#   绝不因为读不到状态就抛错、也不会突然从第 1 个词重来。
ROTATION_STATE_TEMPLATE = ".rotation_{platform}.json"  # 【常量】轮转游标文件名(点开头, 不会被 batch_*.jsonl 通配扫到)


def rotation_state_path(platform: str, output_dir: Path | None = None) -> Path:
    """轮转游标文件路径(默认落在采集输出目录 output/)。"""
    return (output_dir or OUTPUT_DIR) / ROTATION_STATE_TEMPLATE.format(platform=platform)


def next_rotation_offset(offset: int, done: int, total: int) -> int:
    """按"本轮实际跑完的关键词数"推进游标(纯函数, 便于确定性单测)。

    【参数】offset: 本轮起点偏移; done: 本轮实际完成的关键词数; total: 词池总数
    【返回】下一轮起点偏移, 范围 [0, total)
    【关键逻辑】done=0(本轮一个词都没跑成)时返回原偏移 —— 失败不推进,
        下一轮从同一个词重来, 属于"自愈"而不是"跳过"。
    """
    if total <= 0:
        return 0
    return (max(0, int(offset)) + max(0, int(done))) % total


def read_rotation_state(path: Path) -> dict | None:
    """读轮转游标; 文件缺失/损坏/字段异常一律返回 None(调用方回退到派生法)。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        return {
            "offset": int(data.get("offset") or 0),
            "run_seq": int(data.get("run_seq") or 0),
            "total": int(data.get("total") or 0),
            "last_done": int(data.get("last_done") or 0),
            "updated_at": str(data.get("updated_at") or ""),
        }
    except (OSError, ValueError, TypeError):
        return None


def write_rotation_state(
    path: Path, *, offset: int, run_seq: int, total: int, last_done: int
) -> bool:
    """原子写轮转游标(临时文件 + os.replace), 避免并发读取到半截 JSON。

    【返回】True=已落盘; False=写失败(只影响轮转精度, 绝不影响已采集的数据)。
    """
    payload = {
        "offset": int(offset),
        "run_seq": int(run_seq),
        "total": int(total),
        "last_done": int(last_done),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()
        return False


def budget_stop_decision(
    now: float, start_time: float, deadline: float, kw_idx: int
) -> tuple[bool, float]:
    """时间预算的**前瞻**判断:是否该在下一个关键词开始前收尾。

    【功能】返回 (是否收尾, 已跑关键词的平均耗时)。抽成纯函数是为了能被确定性单测
            (不含 sleep/时钟依赖)。
    【参数】
        now:        当前时刻(time.time())
        start_time: 本轮起点
        deadline:   预算截止时刻
        kw_idx:     已处理的关键词数(用于算平均耗时;0 表示还没跑过)
    【返回】(should_stop, eta_next) —— eta_next 为预计下一个关键词所需秒数。
    【关键逻辑·2026-09-17】只判"是否已经超时"是不够的:单个关键词可能耗时数分钟,
       于是在 11:50 启动的那个关键词会一路跑到预算上限,正好撞上调度器的
       subprocess timeout(现为 1500s, 见 MAX_RUN_MINUTES 注释)被 **强杀** —— 一旦被强杀,
       连 collect_run 报告与退出码
       都写不出来,表现为"每轮 error、却查不到任何报告",还把 health_warning 刷成噪音
       (雪球每轮都是这个形态: 明明采到 369KB 数据却被记成失败)。
       因此改成用**已完成关键词的平均耗时**前瞻一步:若现在启动下一个关键词、预计完成
       时刻会越过截止时间,就提前收尾,保证进程能在子进程硬超时之前正常退出并写报告。
    """
    if now > deadline:
        return True, 0.0
    eta_next = (now - start_time) / kw_idx if kw_idx else 0.0
    return (bool(eta_next) and now + eta_next > deadline), eta_next

OUTPUT_DIR = Path(__file__).parent / "output"  # 【变量】结果输出目录 (validate/output/)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)  # 【调用函数】确保输出目录存在

# 平台默认关键词 (xhs 保留旧列表, 微博/知乎待 config.py 扩展)
# 【品种池】2026-09-09 起全项目只保留活跃品种(ACTIVE_VARIETIES),关键词
# 列表收缩为"池内品种词 + 通用主题词";池外品种(螺纹钢/黄金/股指等)不再采集。
# 【2026-09-30】池 20 → 21,六平台各补入 P 棕榈油(见各列表内注释)。
DEFAULT_KEYWORDS_XHS = [  # 【变量】小红书平台默认关键词列表 (21 品种池 + 通用主题词)
    # 能化 13
    "原油期货分析",
    "PTA期货",
    "对二甲苯期货",
    "乙二醇期货",
    "燃料油期货",
    "低硫燃料油期货",
    "LPG期货",
    "橡胶期货",
    "20号胶期货",
    "丁二烯橡胶期货",
    "沥青期货",
    "纯苯期货",
    "苯乙烯期货",
    # 农产品 5
    "豆粕期货",
    "棉花期货",
    "红枣期货",
    "生猪期货",
    "棕榈油期货",
    # 有色(新能源) 3
    "碳酸锂期货",
    "多晶硅期货",
    "工业硅期货",
    # 通用
    "期货实盘",
    "期货技术分析",
    "期货基本面",
    "期货日内交易",
    "期货波段策略",
]

DEFAULT_KEYWORDS_WEIBO = [  # 【变量】微博平台默认关键词列表 (品种级短词, 21 品种池)
    "原油",
    "PTA",
    "对二甲苯",
    "乙二醇",
    "燃料油",
    "低硫燃料油",
    "LPG",
    "液化石油气",
    "橡胶",
    "20号胶",
    "丁二烯橡胶",
    "沥青",
    "纯苯",
    "苯乙烯",
    "豆粕",
    "棉花",
    "红枣",
    "生猪",
    "棕榈油",
    "碳酸锂",
    "多晶硅",
    "工业硅",
    # 通用
    "期货实盘",
    "期货技术分析",
    "期货基本面",
]

DEFAULT_KEYWORDS_ZHIHU = [  # 【变量】知乎平台默认关键词列表 (偏问答/讨论向, 21 品种池)
    "期货",
    "商品期货",
    "原油",
    "沥青",
    "橡胶",
    "20号胶",
    "燃料油",
    "低硫燃料油",
    "苯乙烯",
    "乙二醇",
    "对二甲苯",
    "豆粕",
    "棉花",
    "红枣",
    "生猪",
    "棕榈油",
    "碳酸锂",
    "多晶硅",
    "工业硅",
    "期货交易策略",
    "期货技术分析",
]

DEFAULT_KEYWORDS_XUEQIU = [  # 【变量】雪球平台默认关键词列表 (21 品种池)
    "原油",
    "PTA",
    "对二甲苯",
    "乙二醇",
    "燃料油",
    "低硫燃料油",
    "LPG",
    "橡胶",
    "20号胶",
    "丁二烯橡胶",
    "沥青",
    "纯苯",
    "苯乙烯",
    "豆粕",
    "棉花",
    "红枣",
    "生猪",
    "棕榈油",
    "碳酸锂",
    "多晶硅",
    "工业硅",
    # 通用
    "期货实盘",
    "期货技术分析",
    "期货基本面",
]

DEFAULT_KEYWORDS_EASTMONEY_GUBA = [  # 【变量】东财股吧平台默认关键词列表(带"期货"后缀, 21 品种池+池内品种长尾)
    # 能化 13
    "原油期货",
    "PTA期货",
    "对二甲苯期货",
    "乙二醇期货",
    "燃料油期货",
    "低硫燃料油期货",
    "LPG期货",
    "橡胶期货",
    "20号胶期货",
    "丁二烯橡胶期货",
    "沥青期货",
    "纯苯期货",
    "苯乙烯期货",
    # 农产品 5
    "豆粕期货",
    "棉花期货",
    "红枣期货",
    "生猪期货",
    "棕榈油期货",
    # 有色(新能源) 3
    "碳酸锂期货",
    "多晶硅期货",
    "工业硅期货",
    # 池内品种长尾扩充(股吧帖子长尾词; 不用裸"红枣"避免撞水果)
    "生猪价格",
    "能繁母猪",
    "红枣库存",
    "棕榈油库存",
    "碳酸锂库存",
    "硅料价格",
    # 主题词
    "期货实盘",
    "期货技术分析",
    "期货基本面",
    "商品期货",
    "期货实战",
    "期货交易心得",
]

# 抖音(2026-09-07 试点接入): 浏览器模拟逐视频采评论,速度慢,关键词精简为活跃品种核心词
# 【品种池】2026-09-09 收缩:试点词换成池内品种(甲醇/纯碱已出池→沥青/苯乙烯)
# 【2026-09-17 扩容 5 词 → 25 词】旧池只有 3 个品种词 + 2 个通用词, 是"跑通即扩"的
#   试点规模, 但上线后没人回来扩 —— 实测历史累计分布里, 池中的 碳酸锂期货/期货交易/
#   期货实盘 三个词**一次都没被采到过**(从 09-07 建池起), 当天视角则表现为"抖音只有
#   沥青及其相关品种"。两个结构性原因叠加:
#     ① 词池本身只有 5 词(其它平台是 20~30 词), 覆盖度天生就窄;
#     ② 单词语成本 240s × 预算 12min ⇒ 一轮最多 3 词, 而轮转刻度是"半天"
#        (同一半天内所有运行 offset 相同) ⇒ 每轮都从头 3 个词起跑, 后 2 词被饿死。
#   本次配合: 成本闸从 240s 降到 ~100s(见 platforms/douyin_adapter.py)、
#   轮转游标细化到"每次运行"(见 next_rotation_offset), 一轮可跑 6 词,
#   25 词约 4 轮(2 天)覆盖全部, 而不是永远在头两个词上打转。
DEFAULT_KEYWORDS_DOUYIN = [
    # 能化 13
    "原油期货", "PTA期货", "对二甲苯期货", "乙二醇期货", "燃料油期货",
    "低硫燃料油期货", "LPG期货", "橡胶期货", "20号胶期货", "丁二烯橡胶期货",
    "沥青期货", "纯苯期货", "苯乙烯期货",
    # 农产品 5
    "豆粕期货", "棉花期货", "红枣期货", "生猪期货", "棕榈油期货",
    # 有色(新能源) 3
    "碳酸锂期货", "多晶硅期货", "工业硅期货",
    # 通用
    "期货交易", "期货实盘", "期货技术分析", "期货基本面", "期货行情分析",
]

DEFAULT_KEYWORDS = {  # 【变量】平台名→默认关键词列表映射 (按--platform选择)
    "xhs": DEFAULT_KEYWORDS_XHS,
    "weibo": DEFAULT_KEYWORDS_WEIBO,
    "zhihu": DEFAULT_KEYWORDS_ZHIHU,
    "xueqiu": DEFAULT_KEYWORDS_XUEQIU,
    "eastmoney_guba": DEFAULT_KEYWORDS_EASTMONEY_GUBA,
    "douyin": DEFAULT_KEYWORDS_DOUYIN,
}

# 凭证失效时的重登录指引 (2026-09-01): 采集失败诊断块按平台输出下一步操作。
AUTH_GUIDANCE = {  # 【变量】平台名→重登录指引 (无对应登录脚本的平台给手动指引)
    "_default": "查看该平台登录方式并重新登录后再采集",
    "zhihu": "运行 python zhihu_login.py 重新登录(扫码/验证码)",
    "weibo": "运行 python weibo_login.py 重新登录更新 Cookie",
    "xueqiu": "更新 credentials/xueqiu_cookie.txt(需手动从浏览器复制新 Cookie)",
    "eastmoney_guba": "重新登录东方财富股吧并更新会话凭证",
    "xhs": "检查小红书登录状态并重新登录",
    "douyin": "运行 python douyin_login_profile.py 扫码重新登录(持久 profile: credentials/douyin_profile)",
}

# ── 退出码约定 (2026-09-14 新增) ───────────────────────────────────────
# 【为什么要退出码】旧实现无论采到 0 条还是一路报错都 exit 0, 调用方(调度器/Web)
# 只能靠人读 stdout 猜, 于是"某渠道静默失效"可以连续几周不被发现。现在把
# "渠道失效"与"平台确实没内容"用退出码分开, 调用方可以据此告警/拦截。
EXIT_OK = 0  # 正常(含 empty: 搜索请求成功但平台确实没有相关内容)
EXIT_AUTH_FAILED = 3  # 凭证缺失/失效 → 需要重新登录
EXIT_BLOCKED = 4  # 全部关键词搜索都失败(接口变更/反爬拦截/网络), 非凭证类
EXIT_ERROR = 5  # 其他致命错误(初始化异常、平台未注册等)

# 机器可读的单行汇总前缀: 调用方(Web/调度器)用一行 JSON 就能拿到结论,
# 不必再去猜 stdout 的中文措辞。形如:
#   COLLECT_RUN: {"platform": "zhihu", "status": "empty", "notes": 0, ...}
RUN_LINE_PREFIX = "COLLECT_RUN:"


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
      search_failures: 搜索阶段抛异常/null 的关键词数(非凭证类)。
      with_data_keywords: 至少拿到 1 条搜索结果的词数 —— 用来区分
          "整平台被拦截"(该值恒 0) 与 "平台确实没内容"(该值 > 0)。
      per_keyword: 逐关键词诊断明细(机器可读), 会上报进运行报告。
    【关键逻辑】供 run() 结束时的汇总打印与中断提示使用。
    """

    started: str = ""
    keywords_total: int = 0
    keywords_done: int = 0
    searched_total: int = 0
    detail_fetched: int = 0
    detail_failed: int = 0
    rate_limits_hit: int = 0
    auth_failures: int = 0  # 【变量】凭证类失败次数(登录态失效/过期), 用于结尾诊断提示
    search_failures: int = 0  # 【变量】搜索阶段失败词数(非凭证类: 接口/反爬/网络)
    with_data_keywords: int = 0  # 【变量】拿到过搜索结果的关键词数(区分"被拦截"与"没内容")
    errors: list = field(default_factory=list)
    per_keyword: list = field(default_factory=list)  # 【变量】逐关键词诊断明细([{keyword,items,notes,error,error_class}])


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
        self.total_notes = 0  # 【变量】本次写入 JSONL 的总条数(供运行报告使用)
        self.exit_code = EXIT_OK  # 【变量】进程退出码(由 _decide_status 决定), 见 EXIT_* 常量
        self._last_kw: dict = {}  # 【变量】最近一次 collect_one_keyword 的诊断明细(供 run() 汇总)

    # ═══════════════════════════════════════════════════════════
    # 运行报告 (机器可读留痕) —— 2026-09-14
    # ═══════════════════════════════════════════════════════════

    def write_report(self, status: str, diagnosis: str = "", fix_hint: str = "") -> Path | None:
        """把本次采集的**机器可读**运行报告落盘, 并打印单行 JSON 供调用方解析。

        【功能】在 output/collect_run_{platform}_{时间戳}.json 写出本次运行的完整结论,
                同时在 stdout 打印一行 `COLLECT_RUN: {...}`。
        【参数】status: ok|empty|auth_failed|blocked|error; diagnosis: 人话结论;
                fix_hint: 下一步怎么修。
        【返回】报告文件 Path;写盘失败返回 None(绝不影响采集本身)。
        【关键逻辑】
          - 为什么必须落盘: 旧实现只有 stdout 中文文本, 子进程输出被父进程读走即丢,
            事后完全无法复盘"某个渠道今天到底有没有跑、为什么 0 条"。
          - 文件名用 collect_run_ 前缀(不是 batch_), 避免被 trend_aggregator /
            platform_summary 的 batch_*.jsonl 通配扫进去污染聚合。
          - 写盘失败只 warning: 报告是"锦上添花", 不能反过来把采集搞崩。
          - 【2026-09-18】新增 degraded / degradations: 采集**没有失败但已在降级**
            时(如微博游客态只能取第 1 页)必须一并落盘。此前这类故障只进 logger,
            报告里 status=ok、auth_failures=0, 页面完全看不出异常 —— 实测微博
            游客态因此连续几十轮无人发现。degraded 与 status **正交**:
            status 表达"跑没跑成", degraded 表达"跑成了但少采了多少"。
        """
        s = self.stats
        # 【变量】degradations: 适配器本次登记的降级(如登录态失效→只能取第 1 页)。
        # 读取失败一律降级为空列表 —— 报告落盘绝不能被降级读取拖垮。
        try:
            _degs = list(getattr(self.adapter, "degradations", None) or [])
        except Exception:  # noqa: BLE001
            _degs = []
        rec = {
            "platform": self.platform_name,
            "display_name": getattr(self.adapter, "display_name", self.platform_name),
            "status": status,
            "diagnosis": diagnosis,
            "fix_hint": fix_hint,
            "degraded": bool(_degs),
            "degradations": _degs,
            "degradation_codes": [d.get("code") for d in _degs],
            "started_at": (s.started or "")[:19].replace("T", " "),
            "ended_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "keywords_total": s.keywords_total,
            "keywords_done": s.keywords_done,
            "keywords_with_data": s.with_data_keywords,
            "searched_total": s.searched_total,
            "notes": self.total_notes,
            "detail_fetched": s.detail_fetched,
            "detail_failed": s.detail_failed,
            "auth_failures": s.auth_failures,
            "search_failures": s.search_failures,
            "error_count": len(s.errors),
            "errors": s.errors[:20],
            "per_keyword": s.per_keyword,
            "output_file": str(self.output_file) if self.output_file else "",
        }
        path = None
        try:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            path = OUTPUT_DIR / (
                f"collect_run_{self.platform_name}_{datetime.now():%Y%m%d_%H%M%S}.json"
            )
            with open(path, "w", encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False, indent=2)  # 【调用函数】运行报告落盘(机器可读)
        except Exception:  # 报告写盘失败不能反过来搞崩采集
            logger.warning("Write run report failed", exc_info=True)
            path = None
        try:
            print(f"{RUN_LINE_PREFIX} {json.dumps(rec, ensure_ascii=False)}", flush=True)
        except Exception:
            pass
        return path

    def _decide_status(self) -> tuple[str, str, str, int]:
        """根据本次采集统计推出 (status, diagnosis, fix_hint, exit_code)。

        【关键逻辑】把"渠道失效"与"平台确实没内容"分开 —— 这是本文件此前最大的盲区:
          - auth_failures > 0        → auth_failed(凭证失效, 必须重新登录)
          - 0 条 & 从没拿到搜索结果  → blocked(整平台被拦截/接口变更)
          - 0 条 但搜索请求是成功的  → empty(平台确实没内容, 不是故障)
          - 其余                     → ok
        """
        s = self.stats
        hint = AUTH_GUIDANCE.get(self.platform_name, AUTH_GUIDANCE["_default"])
        if s.auth_failures:
            return (
                "auth_failed",
                f"{s.auth_failures}/{s.keywords_done or s.keywords_total} 个关键词被判定为凭证失效",
                hint,
                EXIT_AUTH_FAILED,
            )
        if self.total_notes == 0 and s.with_data_keywords == 0:
            # 【2026-09-15】把"第一手原因"透出来。此前 blocked 只给一句笼统的
            # "接口变更/反爬拦截或网络"，使用者拿不到平台自己的话；而各平台被拦的
            # 真实形态差别很大（抖音是 status_code:0 + data:[] 的假成功空数据、
            # 会话失效是 ok=-100 + passport 跳转……），把首条异常摘要附上才可处置。
            first_err = ""
            for kw in s.per_keyword or []:
                if kw.get("error"):
                    first_err = str(kw["error"])[:300]
                    break
            return (
                "blocked",
                f"{s.search_failures} 个关键词搜索全部失败/无返回(非凭证类: 接口变更、反爬拦截或网络)"
                + (f" | 首条原因: {first_err}" if first_err else ""),
                "先确认网络与账号是否被限流; 若平台接口已改版, 需更新该平台适配器的 "
                "search 端点/签名逻辑(见 platforms/*_adapter.py)",
                EXIT_BLOCKED,
            )
        if self.total_notes == 0:
            return (
                "empty",
                "搜索请求本身成功, 但没有任何条目通过过滤(平台在目标关键词下可能确实无内容)",
                "换关键词或放宽 --since; 若长期为空请核对关键词是否匹配该平台语料",
                EXIT_OK,
            )
        return ("ok", f"采集到 {self.total_notes} 条", "", EXIT_OK)

    def init_api(self):
        """【功能】初始化平台适配器 (登录/建立会话等), 凭证缺失时给出提示并退出。
        【参数】无。 【返回】无。
        【关键逻辑】adapter.init() 若抛出 CredentialError (凭证缺失/过期),
        打印友好提示、**写一份 auth_failed 运行报告**后以 EXIT_AUTH_FAILED(=3) 退出。
        2026-09-14 前这里直接 sys.exit(1) 且不落任何报告 → 该渠道当天"零痕迹"消失。"""
        try:
            self.adapter.init()  # 【调用函数】平台适配器初始化 (登录/建立会话)
        except CredentialError as e:
            print(f"\n{'=' * 60}")
            print(f"  ⚠️  {self.adapter.display_name} 登录凭证缺失或已过期！")
            print(f"{'=' * 60}")
            print(f"\n  {e}\n")
            self.write_report(
                "auth_failed",
                f"平台初始化失败(凭证缺失/失效): {str(e)[:300]}",
                AUTH_GUIDANCE.get(self.platform_name, AUTH_GUIDANCE["_default"]),
            )
            sys.exit(EXIT_AUTH_FAILED)

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
        # 逐关键词诊断(机器可读): 在 run() 里补齐 notes/耗时后写入 stats.per_keyword
        self._last_kw = {
            "keyword": keyword,
            "items": 0,
            "notes": 0,
            "error": "",
            "error_class": "",
        }

        # Step 1: 搜索 (发请求前先 wait 遵守延时)
        self.limiter.wait()
        try:
            items = self.adapter.search(keyword, count)  # 【调用函数】平台适配器搜索接口 (发请求前已wait遵守延时)
        except Exception as e:
            logger.error(f"Search failed for '{keyword}': {e}")
            # classify_error 把异常归类为 rate_limit/auth/other, 决定退避策略与结尾诊断
            _cat = self.adapter.classify_error(e)  # 【变量】异常分类(rate_limit/auth/other)
            self.limiter.report_failure(is_rate_limit=(_cat == "rate_limit"))
            self._last_kw["error"] = str(e)[:200]
            self._last_kw["error_class"] = _cat or "other"
            if _cat == "auth":
                self.stats.auth_failures += 1  # 【变量】凭证类失败计数(结尾提示重新登录)
            else:
                self.stats.search_failures += 1  # 【变量】非凭证类搜索失败计数(接口/反爬/网络)
            return []

        if not items:
            logger.warning(f"Search '{keyword}' returned no results")
            self.limiter.report_failure()
            self._last_kw["error"] = "search returned no results"
            self._last_kw["error_class"] = "empty"
            return []

        self.limiter.report_success()
        logger.info(f"  Found {len(items)} items for '{keyword}'")
        self._last_kw["items"] = len(items)
        self.stats.with_data_keywords += 1  # 【变量】拿到过搜索结果 → 证明"通道是通的"

        # Step 2: 逐条获取详情 + 归一化
        detail_count = 0  # 已成功采集的条数 (用作进度计数)
        # 【关键逻辑·2026-09-14 修复】详情深挖上限:
        #   - 平台本身不需要详情(weibo/xueqiu/eastmoney/douyin) → 全部 items 都算成功;
        #   - 平台需要详情(zhihu/xhs)且 max_detail > 0 → 只深挖前 max_detail 条;
        #   - 平台需要详情 但 max_detail <= 0(即调用方传了 --no-detail) → **不再丢弃条目**,
        #     而是"跳过详情请求", 直接用搜索返回的摘要/正文归一化入库。
        #   旧写法 `max_detail if needs_detail_fetch else len(items)` 在 --no-detail
        #   时把 detail_limit 算成 0, 下面的 for 循环第一轮就 break → 整批条目被丢光,
        #   而 search 其实是成功的: 表现为"知乎/小红书 永远 0 条且退出码 0"的静默失效。
        _want_detail = bool(self.adapter.needs_detail_fetch) and max_detail > 0
        detail_limit = max_detail if _want_detail else len(items)  # 【变量】详情深挖上限(<=0 时退化为"不深挖但照样入库")

        for _item_idx, item in enumerate(items):
            if detail_count >= detail_limit:
                break

            # 从平台原始条目里取出唯一 ID (不同平台字段名不同: id / mid)
            nid = item.get("id", "") or item.get("mid", "")
            if not nid:
                continue

            # 详情获取 (仅对需要详情深挖的平台生效, 如 xhs/zhihu)
            detail = None
            fetch_ok = True
            if _want_detail:
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
                # 无需深挖: 平台本身不需要(微博/雪球/东财/抖音), 或调用方传了 --no-detail
                # (此时用搜索返回的摘要直接入库, 而不是丢弃条目)
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
        self._last_kw["notes"] = len(notes)
        # 【关键】搜索有结果却一条都没入库 → 必须留痕(否则就是静默失效)
        if items and not notes:
            self._last_kw["error"] = f"search 返回 {len(items)} 条但 normalize/去重后 0 条入库"
            self._last_kw["error_class"] = "no_notes"
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
        max_minutes: float = MAX_RUN_MINUTES,
    ) -> list[dict]:
        """
        【功能】主采集循环 (平台无关): 逐关键词采集 → enrich → 时间过滤 → 增量写盘。
        【参数】
          keywords: list[str], 本次要采集的关键词列表。
          per_kw: int, 每个关键词搜索多少条。
          max_detail: int, 每个关键词最多深挖详情多少条。
          no_enrich: bool, 为 True 时跳过 NER + 情感 enrich (提速)。
          since: str | None, 形如 "YYYY-MM-DD", 只保留此日期及之后的帖子。
          max_minutes: float, 全局时间预算(分钟)。超时在**关键词边界**优雅收尾。
        【返回】list[dict] (当前实现总是返回空列表; 数据直接落盘到 JSONL)。
        【关键逻辑】
          - 输出文件: output/batch_{platform}_{时间戳}.jsonl (逐关键词追加)。
          - 每个关键词: collect_one_keyword → _enrich_notes → since 时间过滤
            → 追加写盘, 因此中途中断也不会丢已完成的批次。
          - 批次间用 limiter.cooldown 冷却, 无结果的关键词不等待省时间。
          - 两道护栏(2026-09-15): ① 时间预算到点收尾; ② 连续 0 命中熔断。
            数据逐关键词落盘, 两种收尾都**不会丢已完成批次**。
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
        deadline = start_time + max_minutes * 60  # 【变量】全局时间预算硬截止
        consecutive_empty = 0  # 【变量】连续 0 命中关键词计数(熔断用)
        _session_healed = False  # 【变量】本轮是否已重建过一次会话(自愈只做一次,防死循环)

        # ===== 逐关键词主循环 =====
        for kw_idx, kw in enumerate(keywords):
            # 护栏 1: 时间预算 —— 到点、或"来不及跑完下一个关键词"时,在关键词边界收尾
            # (数据已逐关键词落盘,两种收尾都不会丢已完成批次)。
            # 判断规则见 budget_stop_decision() 的文档:必须**前瞻**一步,否则会启动一个
            # 跑不完的关键词,撞上调度器 subprocess timeout(1500s) 被强杀,连报告都写不出来。
            _stop, _eta_next = budget_stop_decision(
                time.time(), start_time, deadline, kw_idx
            )
            if _stop:
                _why = (
                    "reached"
                    if _eta_next == 0.0
                    else f"next keyword needs ~{_eta_next:.0f}s"
                )
                print(f"\n  [budget] Time budget {max_minutes:.0f}min {_why}, "
                      f"stopping early at keyword {kw_idx + 1}/{len(keywords)}")
                break

            # 护栏 2: 连续空结果熔断 —— 平台级故障(限流/登录态失效)提前放弃。
            # 意义: 被限流时每个关键词都要等完整冷却才返回 0, 跑完剩余关键词既
            # 浪费时间又在平台上继续加重风控; 连续 0 命中是"通道已断"的强信号。
            if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                # 【2026-09-17 自愈一次】实测"整轮瞬时全空"多半是**会话被打死**,不是平台
                # 真没内容: 知乎 09-16 18:16、小红书 09-16 08:24 都是这个形态 —— 每个
                # 关键词 1.5~2.5s 空手返回(远快于正常的 3.5s), 而平台本身随后实测可用。
                # 旧行为是直接熔断, 于是把剩下 15~20 个关键词整批丢掉(那一轮 = 白跑)。
                # 现在先重建一次会话(close+init: 知乎/雪球会重启浏览器、小红书会重读
                # Cookie、抖音会重开 profile)再继续; 只有重建后仍然连续空才真正放弃。
                # 代价最多是跳过 1 个关键词, 换来整轮保命。
                # ⚠️ 凭证类失败(auth_failures>0)不重建 —— Cookie 过期重启会话救不回来,
                # 那样只会浪费一轮时间并掩盖"请重新登录"这个真正结论。
                if not _session_healed and not self.stats.auth_failures:
                    _session_healed = True
                    print(f"\n  [circuit-breaker] {consecutive_empty} consecutive empty keywords; "
                          f"restarting adapter session and retrying once (self-heal)")
                    try:
                        self.adapter.close()  # 【调用函数】关掉旧会话(浏览器/Cookie 上下文)
                        self.adapter.init()  # 【调用函数】重建会话(拿一个干净通道)
                        consecutive_empty = 0
                        continue
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"Session restart failed, giving up: {e}")
                print(f"\n  [circuit-breaker] {consecutive_empty} consecutive keywords "
                      f"returned 0 notes (rate-limit or auth issue), stopping early")
                break

            print(f"\n--- [{kw_idx + 1}/{len(keywords)}] '{kw}' ---")

            _kw_t0 = time.time()  # 【变量】单词耗时起点(写进 per_keyword 诊断)
            # 采集单个关键词; 整体失败时记录错误并继续下一个关键词
            try:
                notes = self.collect_one_keyword(kw, count=per_kw, max_detail=max_detail)  # 【调用函数】采集单个关键词 (搜索→详情→归一化→去重)
            except Exception as e:
                logger.error(f"Keyword '{kw}' failed: {e}")
                self.stats.errors.append(f"{kw}: {e}")
                notes = []
                self._last_kw = {
                    "keyword": kw,
                    "items": 0,
                    "notes": 0,
                    "error": str(e)[:200],
                    "error_class": "exception",
                }
                self.stats.search_failures += 1

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
            # 熔断计数: 本关键词有命中即清零, 否则累加(供下一轮循环开头的护栏 2 判断)
            consecutive_empty = 0 if notes else consecutive_empty + 1

            # 逐关键词诊断留痕(机器可读): 关键词 / 搜索命中 / 实际入库 / 错误原因 / 耗时
            _diag = dict(self._last_kw)
            _diag["keyword"] = kw
            _diag["notes"] = len(notes)
            _diag["seconds"] = round(time.time() - _kw_t0, 1)
            if not _diag.get("error") and not notes:
                _diag["error"] = "time filter 后无条目"
                _diag["error_class"] = _diag.get("error_class") or "filtered"
            self.stats.per_keyword.append(_diag)

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
        self.total_notes = total_notes  # 【变量】供运行报告读取
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

        # ══ 采集失败诊断与引导 (2026-09-01): 不静默跳过 ══
        # 凭证失效或整平台零数据时, 给出原因与下一步, 便于用户自助修复;
        # 诊断行以 ⚠️ 开头, web 端一键更新会据此透传到前端。
        if self.stats.auth_failures:
            print(f"\n⚠️  采集失败: 登录凭证失效/过期 ({self.adapter.display_name})")
            print(f"    {self.stats.auth_failures} 个关键词请求被判定为凭证问题。")
            print(f"    处理: {AUTH_GUIDANCE.get(self.platform_name, AUTH_GUIDANCE['_default'])}")
        elif total_notes == 0:
            print(f"\n⚠️  未采集到任何数据 ({self.adapter.display_name})")
            print("    可能原因:")
            print("    1) 登录凭证过期/失效 → 重新登录后重试")
            print("    2) 网络/反爬限制 → 稍后重试或更换网络")
            print("    3) 关键词在本平台确实无内容 → 换关键词试试")

        # ══ 运行报告 + 退出码 (2026-09-14): 让"渠道失效"无法再静默 ══
        # 【为什么】旧实现无论 0 条还是一路报错都 exit 0 且不留任何机器可读痕迹,
        # 于是"某个渠道连续几周采不到"只能靠人翻 output 目录才发现(知乎自
        # 2026-08-26 起 100% 零条, 直到 2026-09-14 才被定位)。现在:
        #   - 写 output/collect_run_{platform}_{ts}.json(完整结论 + 逐关键词明细);
        #   - stdout 打一行 COLLECT_RUN: {...} 供调用方直接解析;
        #   - 用退出码区分 auth_failed / blocked / empty / ok。
        _status, _diag_msg, _fix, self.exit_code = self._decide_status()
        self.write_report(_status, _diag_msg, _fix)
        print(f"  Run status: {_status} | {_diag_msg}")
        if _fix:
            print(f"  处理建议: {_fix}")

        # ══ 降级行 (2026-09-18): "跑成了但少采了" 也必须被人看见 ══
        # 【为什么】降级此前只进 logger.warning, 而子进程日志会被父进程的输出淹没;
        # 采集"成功"时更是没人会去翻日志。这里明确打印到 stdout, 与 Run status 同屏,
        # 让"本轮跑成但少采"无法再伪装成一切正常。
        try:
            _degs = list(getattr(self.adapter, "degradations", None) or [])
        except Exception:  # noqa: BLE001
            _degs = []
        for _d in _degs:
            _n = int(_d.get("count") or 1)
            print(f"  ⚠️  降级[{_d.get('code')}]: {_d.get('message', '')}"
                  + (f"（共 {_n} 次）" if _n > 1 else ""))
            if _d.get("impact"):
                print(f"      影响: {_d['impact']}")
            if _d.get("fix_hint"):
                print(f"      处理: {_d['fix_hint']}")

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
    parser.add_argument(
        "--rotate-keywords",
        action="store_true",
        help=(
            "每轮从关键词列表的不同位置开始(起点取自 output/.rotation_{平台}.json 持久游标, "
            "按上轮实际完成的词数推进; 游标缺失时回退到按日期/轮次派生, 无状态可复现)。"
            "与 --max-keywords 搭配使用: 每轮只跑得起前 N 个关键词的平台(雪球受"
            "请求配额约束就是如此), 若每轮都从第 1 个开始, 后面的关键词永远采不到;"
            "轮转后一天两轮各覆盖一段, 与词池大小无关地持续推进。"
        ),
    )
    parser.add_argument(
        "--max-keywords",
        type=int,
        default=None,
        help=(
            "只取预设关键词列表的前 N 个 (默认不限制)。"
            "用于对风控敏感的平台降载: 抖音软限流(HTTP 200 + data:[])"
            "触发后冷却 45min 不恢复, 减关键词比加大间隔更有效。"
            "显式传 --keywords 时本参数不生效。"
        ),
    )
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
    parser.add_argument(
        "--max-minutes", type=float, default=MAX_RUN_MINUTES,
        help=(
            f"全局时间预算分钟数, 超时在关键词边界收尾 (默认 {MAX_RUN_MINUTES})。"
            "抖音这类逐视频开页的慢平台必用: 单关键词可达数分钟, 不加预算会撞上"
            "调度器 15 分钟的子进程超时。"
        ),
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
    # --rotate-keywords: 每轮从关键词列表的**不同位置**开始(配合 --max-keywords 降载)。
    # 【为什么需要·2026-09-17】受配额/限流约束的平台每轮只能跑下前 N 个关键词, 若每轮
    # 都从第 1 个开始, 排在后面的关键词将**永远采不到**(系统性饿死, 表现为"看着有数据
    # 但总感觉不全")。实测雪球就是这种平台: fast 节奏跑到第 12 个关键词就挂死, safe 节奏
    # 能跑到第 21 个 —— 阈值随请求数走, 23 个词一轮跑不完。解法不是硬撑, 而是让每轮
    # 跑不同的切片: 偏移量优先读 output/.rotation_{platform}.json 的持久游标(每次运行
    # 按实际完成的词数推进), 读不到才回退到"日期序号×2 + 当天上/下午"的无状态派生法。
    _rotation_state_file: Path | None = None  # 【变量】游标文件路径(None=本轮未启用轮转, 收尾不推进)
    _rotation_total = 0  # 【变量】词池总数(游标回绕基准)
    _rotation_offset = 0  # 【变量】本轮起点偏移
    _rotation_run_seq = 0  # 【变量】已执行轮数(留痕/排查)
    if getattr(args, "rotate_keywords", False) and keywords:
        # 【游标落盘 2026-09-17】优先用状态文件里的持久游标; 读不到(首次运行/被清理/
        # 损坏)才回退到"半天派生"旧算法 —— 保证升级那一刻行为与旧版一致, 之后逐轮推进。
        _rotation_total = len(keywords)  # 【变量】词池总数(游标回绕基准)
        _stride = args.max_keywords if args.max_keywords and args.max_keywords > 0 else 1
        _rotation_state_file = rotation_state_path(args.platform)  # 【调用函数】定位该平台的游标文件
        _rotation_state = read_rotation_state(_rotation_state_file)  # 【调用函数】读持久游标(缺失/损坏→None)
        if _rotation_state is not None:
            _rotation_offset = _rotation_state["offset"] % _rotation_total  # 【变量】本轮起点偏移
            _rotation_run_seq = _rotation_state["run_seq"]  # 【变量】已执行轮数(留痕/排查)
            _source = f"cursor file, run #{_rotation_run_seq}"
        else:
            # 旧算法兜底: 日期序号×2 + 当天上/下午(刻度为半天)
            _slot = 1 if datetime.now().hour >= 12 else 0  # 【变量】当天轮次(上午 0 / 下午 1)
            _run_index = datetime.now().timetuple().tm_yday * 2 + _slot  # 【变量】自年初起的第几轮
            _rotation_offset = keyword_rotation_offset(_run_index, _stride, _rotation_total)
            _rotation_run_seq = 0
            _source = "derived (day×2+slot; 游标文件缺失时的回退)"
        # 步长取"一个窗口宽度"(= --max-keywords): 这样相邻两轮跑的是**互补切片**,
        # 同一天的上、下午两轮合起来刚好覆盖整个关键词列表。
        # 若步长取 1,窗口每次只挪一格,窗口要滑很多天才轮完一遍 —— 等于没解决饿死。
        keywords = keywords[_rotation_offset:] + keywords[:_rotation_offset]
        print(f"  Keyword rotation: offset {_rotation_offset}/{_rotation_total} "
              f"({_source}), starting at '{keywords[0]}'")
    # --max-keywords: 只取预设列表前 N 个(显式 --keywords 时不生效)。
    # 用途: 风控敏感平台降载(见 --max-keywords 帮助文本)。
    if args.max_keywords and args.max_keywords > 0 and not args.keywords:
        _before = len(keywords)
        keywords = keywords[: args.max_keywords]
        print(f"  Keyword cap: {len(keywords)}/{_before} (--max-keywords {args.max_keywords})")
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
            max_minutes=args.max_minutes,
        )
    except KeyboardInterrupt:
        print("\n\nInterrupted! Partial results saved to:")
        print(f"  {collector.output_file}")
        print(f"  Collected: {collector.stats.keywords_done}/{len(keywords)} keywords")
        print(f"  Total notes so far: ~{collector.stats.detail_fetched}")
        collector.exit_code = EXIT_OK  # 用户主动中断不算故障
    except Exception as e:
        logger.error(f"Fatal: {e}", exc_info=args.verbose)
        if collector.output_file:
            print(f"\nPartial results saved to: {collector.output_file}")
        # 致命异常也要留一份报告, 否则调用方只看到"非零退出码"却不知道原因
        collector.exit_code = EXIT_ERROR
        with contextlib.suppress(Exception):
            collector.write_report("error", f"致命异常: {str(e)[:300]}", "")

    # 【游标推进 2026-09-17】按**实际跑完的词数**前进(不是窗口宽度): 被风控熔断只跑了
    # 2 个词就只走 2 格, 没跑到的词下一轮优先补上, 不会漏采; 落盘失败只影响轮转精度,
    # 不影响本轮已采集的数据, 因此只打日志不抛错。
    if _rotation_state_file is not None:
        _done = int(getattr(collector.stats, "keywords_done", 0) or 0)
        _next_offset = next_rotation_offset(_rotation_offset, _done, _rotation_total)
        _state_ok = write_rotation_state(
            _rotation_state_file,
            offset=_next_offset,
            run_seq=_rotation_run_seq + 1,
            total=_rotation_total,
            last_done=_done,
        )
        print(f"  Keyword rotation: cursor {_rotation_offset} → {_next_offset} "
              f"(done {_done}/{_rotation_total}"
              f"{'' if _state_ok else ', 游标落盘失败(仅影响下轮起点)'})")

    # 【关键】把"渠道是否失效"通过退出码交给调用方(调度器据此写 collection_log
    # 与告警, Web 一键更新据此在平台卡片上标红)。注意 SystemExit(init_api 里
    # 的 sys.exit)不是 Exception, 会直接穿透到这里之外, 自然带出 EXIT_AUTH_FAILED。
    sys.exit(collector.exit_code)


if __name__ == "__main__":
    main()
