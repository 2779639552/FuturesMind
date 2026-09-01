# =====================================================================
# web_app.py —— 本项目 Flask Web 看板的后端入口
#
# 【整体角色】
#   · 这是一个 Flask Web 服务器(生产环境用 Waitress 托管),默认监听
#     http://localhost:5000 ,浏览器访问 5000 端口即可打开看板前端。
#   · 以脚本运行时(__main__ 分支)会自动启动:
#       1) 后台调度器 scheduler.start_scheduler() —— 每天 08:00 / 18:00
#          执行定时采集 / 聚合等任务;
#       2) 价格缓存后台线程 _start_price_cache_updater() —— 交易时段每
#          5 分钟、非交易时段每 30 分钟刷新一次实时行情缓存。
#
# 【路由分组】(按文件中出现顺序)
#   1) 实时行情:   /api/live-prices、/api/price/update、/api/price/<品种>
#   2) 情绪数据:   /api/sentiment/<品种>、/api/sentiment_posts、/api/overlay/<品种>
#   3) 数据看板:   /api/dashboard/<品种>、/api/dashboard/sector/<板块>(仓单/库存/价格/基差 + 关联分析)
#   4) 主页面:     / 、/test
#   5) 分析工具:   /api/run_analysis(SSE 流式) + /api/progress 轮询 +
#                  /api/pause /api/resume /api/stop /api/feedback,
#                  /api/analysis_results、/api/backtest、/api/history、/api/compare
#   6) 报告导出:   /api/report/<文件>/pdf、/api/report/<文件>/md
#   7) 配置:       /api/config
#   8) 数据更新:   /api/update_data(SSE 流水线)
#   9) 数据库 / 调度器 / 鉴权: /api/db/*、/api/scheduler/*、/api/auth/*
#   10) 分析接口:  /api/analysis/*(异常、背离、领先滞后、作者、事件、排名、跨平台)
#   11) 自选:      /api/watchlist
#   12) 模拟交易:  /api/trading/*(20+ 条策略路由,含风控与多策略对比)
#   13) Agent 验证: /api/batch_backtest/*、/api/agent_validation/*
#
# 【与其它模块的协同】
#   · signal_analyzer.py —— 情绪/价格信号与各类模拟交易策略的实现,被本文件直接调用。
#   · commodity_demo.py  —— build_commodity_graph() 构建 LangGraph 多分析师图,
#                           本文件用它在后台线程跑完整分析流水线。
#   · web_template.html  —— get_page_template() 每次请求都重新读取该文件,
#                           前端页面就是它的内容(便于不改代码就改模板)。
#   · database.py        —— 封装 SQLite,提供自选 / 交易信号 / 告警 / 用户等存取。
#   · scheduler.py       —— APScheduler 封装,__main__ 里用它启动每日定时任务。
# =====================================================================

"""FuturesMind Web Dashboard — Flask + SSE streaming analysis.

Enhanced v2.5: ProgressTracker (thread-safe), pause/resume/stop,
PDF+MD export, LLM config panel, real-time token stats, dynamic paths.
"""

import glob  # 【调用包】批量路径匹配(如 batch_*.jsonl 文件列举)
import io  # 【调用包】内存字节流(BytesIO,PDF 下载响应)
import json  # 【调用包】JSON 序列化/反序列化(配置、行情缓存、批次文件)
import logging  # 【调用包】日志记录(报告落盘异常等)
import os  # 【调用包】路径/环境变量操作
import re  # 【调用包】正则提取报告章节与评级字段
import secrets  # 【调用包】生成安全 token 与 secret_key
import sys  # 【调用包】模块搜索路径调整、解释器路径获取
import threading  # 【调用包】后台线程与进度锁
import time  # 【调用包】耗时统计与轮询间隔
from collections import defaultdict  # 【调用包】字典计数(缺失键自动给默认值,非标数据聚合用)
from datetime import datetime, timedelta  # 【调用包】日期解析与时间窗计算
from functools import wraps  # 【调用包】保留被装饰函数元数据(鉴权装饰器)
from pathlib import Path  # 【调用包】路径对象操作(目录扫描/文件拼接)
from urllib.parse import urlparse  # 【调用包】URL 域名解析(platform 缺失时按 url 推断平台)

from dotenv import load_dotenv  # 【调用包】加载 .env 环境变量
from flask import (
    Flask,  # 【调用包】Web 框架:应用实例与路由
    Response,  # 【调用包】SSE 流式响应
    jsonify,  # 【调用包】JSON 响应封装
    make_response,  # 【调用包】构造带 Cookie/响应头的对象
    render_template_string,  # 【调用包】Jinja2 渲染模板字符串
    request,  # 【调用包】读取请求参数/JSON 体/请求头
    send_file,  # 【调用包】文件下载响应(PDF/MD)
    stream_with_context,  # 【调用包】让生成器在请求上下文里产出 SSE
)

load_dotenv()  # 【调用函数】读取 .env 中的环境变量(API key、LLM 配置等)

# 把本文件所在目录加入模块搜索路径,使同目录下的 commodity_demo / database /
# signal_analyzer / tradingagents 等模块无需安装即可被 import。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import contextlib  # noqa: E402  # 【调用包】上下文管理(异常抑制 suppress)

from langchain_core.messages import (  # noqa: E402  # 【调用包】LangChain 消息类型,构造 Agent 图初始对话
    HumanMessage,
)

from commodity_demo import (  # noqa: E402  # 【调用包】构建 LangGraph 多分析师图
    build_commodity_graph,
)

# New imports for v2.6
from database import get_db  # noqa: E402  # 【调用包】SQLite 存取(自选/交易信号/告警/用户)
from path_utils import resolve_think2_dir  # noqa: E402  # 【调用包】思路2项目目录自动探测
from signal_analyzer import (  # noqa: E402  # 【调用包】情绪/价格信号与全部模拟交易策略回测函数
    analyze_cross_platform,
    analyze_lead_lag,
    compare_varieties,
    compute_divergence,
    detect_anomalies,
    extract_events,
    get_all_variety_scores,
    get_top_authors,
    latest_trading_signal,
    run_adaptive_sentiment,
    run_atr_sent_strategy,
    run_atr_strategy,
    run_bollinger_sent_strategy,
    run_bollinger_strategy,
    run_contrarian_sentiment,
    run_donchian_strategy,
    run_ma_cross_sent_strategy,
    run_ma_cross_strategy,
    run_macd_sent_strategy,
    run_macd_strategy,
    run_momentum_adaptive,
    run_momentum_strategy,
    run_rsi_sent_strategy,
    run_rsi_strategy,
    run_simulated_trading,
    run_strategy_comparison,
    run_trailing_strategy,
    run_turtle_sent_strategy,
    run_turtle_strategy,
)
from tradingagents.dataflows.commodity_futures import (  # noqa: E402  # 【调用包】品种元数据与 AKShare 行情获取
    VARIETY_METADATA,
    get_futures_basis,
    get_futures_inventory,
    get_futures_price,
)
from tradingagents.dataflows.config import (  # noqa: E402  # 【调用包】把配置同步到全局(供 Agent 图/LLM 读取)
    set_config,
)
from tradingagents.dataflows.evolution_memory import (  # noqa: E402  # 【调用包】读取进化记忆(历史学习上下文)
    get_evolution_context,
)
from tradingagents.dataflows.sentiment_data import (  # noqa: E402  # 【调用包】质量感知判定:自身质量合格或板块复合可用才启用情绪分析师
    should_include_sentiment,
)
from tradingagents.default_config import (  # noqa: E402  # 【调用包】默认配置(LLM provider/模型名)
    DEFAULT_CONFIG,
)
from tradingagents.llm_clients import (  # noqa: E402  # 【调用包】创建 LLM 客户端(辩论接口用)
    create_llm_client,
)

# ------------------------------------------------------------------
# Flask 应用初始化
# · secret_key 优先取环境变量 FLASK_SECRET_KEY,否则随机生成(重启即失效)。
# · config 从 DEFAULT_CONFIG 复制一份,并同步给 tradingagents.dataflows.config,
#   供后续构建 Agent 图 / 调用 LLM 时读取统一配置。
# ------------------------------------------------------------------
app = Flask(__name__)  # 【变量】Flask 应用实例(注册全部路由)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))  # 【变量】会话签名密钥:优先环境变量,否则随机生成(重启即失效)
config = DEFAULT_CONFIG.copy()  # 【变量】运行时配置副本(前端 / Agent 图 / LLM 调用共用)
set_config(config)  # 【调用函数】把配置同步到 tradingagents.dataflows,供构建图与 LLM 调用读取

# ── Dynamic paths ────────────────────────────────────────────────────────────

# 【动态路径解析】
# · 情绪数据目录优先用真实采集目录 ~/.tradingagents/external_data;
#   若其中还没有任何 *_sentiment.json,则退回仓库自带的样例数据
#   data/external_data —— 让刚克隆的新环境也能正常浏览页面。
# User sentiment dir (real collected data). Falls back to bundled repo samples
# (data/external_data) when no user data exists yet — lets a fresh clone browse.
_USER_SENTIMENT_DIR = Path(os.path.expanduser("~/.tradingagents/external_data"))
_REPO_SENTIMENT_DIR = Path(__file__).parent / "data" / "external_data"
SENTIMENT_DIR = (
    _USER_SENTIMENT_DIR
    if any(_USER_SENTIMENT_DIR.glob("*_sentiment.json"))
    else _REPO_SENTIMENT_DIR
)

# 思路2(think2)项目目录的自动探测:优先环境变量 $THINK2_DIR,再查常见本地位置,
# 最后退回仓库自带样例(data/think2_validate),保证没有本地思路2工程也能渲染页面。
# Auto-detect 思路2 project directory: $THINK2_DIR override, then common local
# locations, then the bundled repo sample (data/think2_validate) so a fresh
# clone without the local 思路2 project can still render.
THINK2_DIR = resolve_think2_dir()

THINK2_OUTPUT = THINK2_DIR / "output" if THINK2_DIR else None  # 【变量】思路2采集输出目录(batch_*.jsonl 所在)
THINK2_TRENDS = THINK2_OUTPUT / "trends" if THINK2_OUTPUT else None  # 【变量】思路2回测趋势目录(_weights.json 所在)

LOG_DIR = Path(os.path.expanduser("~/.tradingagents/logs"))  # 【变量】日志目录(用户主目录下,跨项目共享)
REPORT_DIR = LOG_DIR  # 【变量】分析报告保存目录(与日志同目录)

logger = logging.getLogger(__name__)  # 【变量】模块级日志器(报告落盘/章节解析异常用)

# Template path (reload on every request for live editing)
TEMPLATE_PATH = Path(__file__).parent / "web_template.html"  # 【变量】前端模板路径(每次请求重读,支持热改)


# 【功能】读取前端模板 web_template.html 的完整内容。
# 【关键】每次调用都重新读盘,配合"每次请求都读模板"的设计实现模板热改。
def get_page_template():
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        return f.read()


# ── Sector mapping (dynamic from VARIETY_METADATA) ────────────────────────────


# 【功能】根据品种代码从 VARIETY_METADATA 取中文板块名(如"黑色系")。
# 【参数】code: 品种代码(如 "RB")。
# 【返回】板块中文名;查不到时返回 "其他"。
# 【关键】与 build_sector_to_varieties 同口径:剥括号子板块("有色(贵金属)"→"有色"),
#   保证 /api/dashboard/sector/<sector> 的板块 key 与前端 meta.sector 一致。
def _get_sector(code: str) -> str:
    """Get sector name dynamically from VARIETY_METADATA."""
    meta = VARIETY_METADATA.get(code, {})
    sector_cn = (meta.get("sector_cn") or "").strip()
    if not sector_cn:
        return "其他"
    return re.sub(r"[（(].*?[)）]", "", sector_cn).strip()


# ── Progress Tracker (thread-safe, adapted from astock-ref) ────────────────────

# 分析流水线的 10 个阶段定义。id 供前后端对位, name 为中文显示名, icon 为图标。
# 这些阶段与 commodity_demo 构建的 Agent 图节点一一对应:
# 技术 → 基本面 → 宏观 → 情绪 → 多方立论 → 空方反驳 → 多方反驳 → 辩论裁决 → 综合研判 → 情景分析。
PIPELINE_STAGES = [  # 【变量】分析流水线的 10 个阶段(与 Agent 图节点一一对应)
    {"id": "technical", "name": "技术分析", "icon": "📊"},
    {"id": "fundamental", "name": "基本面", "icon": "📋"},
    {"id": "macro", "name": "宏观/新闻", "icon": "🌍"},
    {"id": "sentiment", "name": "情绪分析", "icon": "💬"},
    {"id": "bull_opening", "name": "多方立论", "icon": "🐂"},
    {"id": "bear_refute", "name": "空方反驳", "icon": "🐻"},
    {"id": "bull_rebuttal", "name": "多方反驳", "icon": "🔄"},
    {"id": "moderator", "name": "辩论裁决", "icon": "⚖️"},
    {"id": "synthesis", "name": "综合研判", "icon": "🎯"},
    {"id": "scenario", "name": "情景分析", "icon": "📈"},
]


# ──────────────────────────────────────────────────────────────────
# ProgressTracker —— 分析进度跟踪器(线程安全)
# 【设计目的】分析在后台线程中异步运行,前端通过 /api/progress 轮询进度,
#   因此所有状态字段都用 RLock 保护,避免后台线程与请求线程读写冲突。
# 【关键机制】
#   · _pause_event 是 threading.Event:置位(set)= 继续,清空(clear)= 暂停。
#     分析主循环在每一步调用 wait_if_paused(),暂停期间线程会阻塞在这里。
#   · stop_requested 置位后,主循环会在下一个节点前退出,实现"停止"。
# ──────────────────────────────────────────────────────────────────
class ProgressTracker:
    """Thread-safe mutable state container for analysis progress."""

    # 【功能】初始化跟踪器,记录本次分析的品种、交易日、阶段列表与统计计数。
    # 【参数】symbol: 品种代码; trade_date: 交易日; stages: 阶段列表
    #   (缺省用 PIPELINE_STAGES;若本次不含情绪分析,会传入过滤掉 sentiment 的列表)。
    # 【关键】_pause_event 初始置位 = 默认"不暂停"。
    def __init__(self, symbol="", trade_date="", stages=None):
        self.symbol = symbol
        self.trade_date = trade_date
        self.stages = stages if stages is not None else PIPELINE_STAGES
        self.start_time = time.time()

        self.is_running = False
        self.is_complete = False
        self.is_paused = False
        self.stop_requested = False
        self.error = None

        self.current_stage = ""
        self.completed_stages: list[str] = []
        self.stage_reports: dict[str, str] = {}

        self.final_state: dict = {}
        self.rating = None

        self.llm_calls = 0
        self.tool_calls = 0
        self.tokens_in = 0
        self.tokens_out = 0

        self._lock = threading.RLock()
        self._pause_event = threading.Event()
        self._pause_event.set()

    # 【功能】请求暂停。仅当"正在运行 且 未完成/未出错/未暂停/未请求停止"时才生效。
    # 【返回】成功 True;失败 False(调用方据此返回 400)。
    # 【关键】清空 _pause_event → wait_if_paused() 阻塞 → 分析线程停在当前节点之后。
    def pause(self) -> bool:
        with self._lock:
            if (
                not self.is_running
                or self.is_complete
                or self.error
                or self.is_paused
                or self.stop_requested
            ):
                return False
            self.is_paused = True
            self._pause_event.clear()
            return True

    # 【功能】请求继续。仅当"已暂停 且 未请求停止"时才生效。
    # 【关键】重新置位 _pause_event,被阻塞的分析线程随即继续执行。
    def resume(self) -> bool:
        with self._lock:
            if not self.is_paused or self.stop_requested:
                return False
            self.is_paused = False
            self._pause_event.set()
            return True

    # 【功能】请求停止。置 stop_requested=True 并复位暂停状态、置位事件,
    #   让可能被暂停阻塞的线程先醒过来;主循环在下一个节点前检查 stop_requested 退出。
    def request_stop(self) -> bool:
        with self._lock:
            if not self.is_running or self.is_complete or self.error or self.stop_requested:
                return False
            self.stop_requested = True
            self.is_paused = False
            self._pause_event.set()
            return True

    # 【功能】暂停阻塞点。分析主循环每步调用;处于暂停时线程在此等待直到 resume/stop。
    def wait_if_paused(self):
        self._pause_event.wait()

    # 【功能】标记某阶段为"进行中"。若已请求停止则忽略(不覆盖 current_stage)。
    def mark_stage_active(self, stage_id: str):
        with self._lock:
            if self.stop_requested:
                return
            self.current_stage = stage_id

    # 【功能】标记某阶段完成,并把该阶段的报告摘要存到 stage_reports(截断到 3000 字符)。
    #   · 同一阶段多次完成不会重复加入 completed_stages。
    def mark_stage_done(self, stage_id: str, report=""):
        with self._lock:
            if self.stop_requested:
                return
            if stage_id not in self.completed_stages:
                self.completed_stages.append(stage_id)
            if report:
                self.stage_reports[stage_id] = report[:3000]
            self.current_stage = ""

    # 【功能】标记整个分析完成,保存最终状态 final_state 与评级 rating,
    #   复位运行/暂停/停止标记并唤醒可能被暂停的线程。
    def mark_complete(self, final_state: dict, rating=None):
        with self._lock:
            self.final_state = final_state
            self.rating = rating
            self.is_running = False
            self.is_complete = True
            self.is_paused = False
            self.stop_requested = False
            self._pause_event.set()

    # 【功能】记录错误并终止运行状态(不抛异常,前端通过 error 字段获知失败原因)。
    def mark_error(self, err: str):
        with self._lock:
            self.error = err
            self.is_running = False
            self.is_paused = False
            self.stop_requested = False
            self._pause_event.set()

    # 【功能】更新 LLM 调用次数、工具调用次数、Token 用量统计(供前端实时显示)。
    def update_stats(self, llm=0, tool=0, tok_in=0, tok_out=0):
        with self._lock:
            if self.stop_requested:
                return
            self.llm_calls = llm
            self.tool_calls = tool
            self.tokens_in = tok_in
            self.tokens_out = tok_out

    # 【属性】返回自 start_time 起已耗时的秒数。
    @property
    def elapsed(self) -> float:
        return time.time() - self.start_time

    # 【功能】返回某阶段状态: done(已完成)/ active(进行中)/ pending(待执行)。
    def stage_status(self, stage_id: str) -> str:
        with self._lock:
            if stage_id in self.completed_stages:
                return "done"
            if stage_id == self.current_stage:
                return "active"
            return "pending"

    # 【功能】把跟踪器整体导出为 dict,供 /api/progress 直接 jsonify 返回。
    # 【关键】stages 中每个阶段携带 status(done/active/pending),前端据此渲染进度条。
    def to_dict(self) -> dict:
        with self._lock:
            return {
                "symbol": self.symbol,
                "trade_date": self.trade_date,
                "elapsed": f"{self.elapsed:.0f}s",
                "is_running": self.is_running,
                "is_complete": self.is_complete,
                "is_paused": self.is_paused,
                "stop_requested": self.stop_requested,
                "error": self.error,
                "current_stage": self.current_stage,
                "completed_stages": self.completed_stages,
                "stages": [
                    {
                        "id": s["id"],
                        "name": s["name"],
                        "icon": s["icon"],
                        "status": self.stage_status(s["id"]),
                    }
                    for s in self.stages
                ],
                "rating": self.rating,
                "llm_calls": self.llm_calls,
                "tool_calls": self.tool_calls,
                "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out,
            }


# 全局唯一的分析进度跟踪器(同一时间只允许一次分析)。
# 由 /api/run_analysis 创建,后台线程与 /api/progress 等接口共享读写。
# Global tracker instance (one analysis at a time)
_tracker: ProgressTracker | None = None  # 【变量】全局唯一的分析进度跟踪器(同一时间只允许一次分析)

# ── Config persistence ──────────────────────────────────────────────────────

CONFIG_PATH = Path(os.path.expanduser("~/.tradingagents/web_config.json"))  # 【变量】前端配置持久化文件路径


# 【功能】读取前端界面配置(主题 / LLM 模型等)。文件不存在时返回空 dict。
# 【关键】配置持久化在用户主目录 ~/.tradingagents/web_config.json,重启不丢失。
def _load_web_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)  # 【调用函数】读取已保存的配置 JSON
    return {}


# 【功能】把前端界面配置写回 ~/.tradingagents/web_config.json(自动建目录、UTF-8 缩进)。
def _save_web_config(cfg: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)  # 【调用函数】配置落盘(UTF-8、缩进,可读性好)


# ── Routes ─────────────────────────────────────────────────────────────────

# ── Live Price ────────────────────────────────────────────────────────────────

PRICE_CACHE_FILE = Path(__file__).parent / "live_prices_cache.json"  # 【变量】实时行情缓存文件(warm_cache.py 写入)


# 【功能】启动一个后台守护线程,周期性调用 warm_cache.py 刷新实时行情缓存文件。
# 【关键逻辑】
#   · 交易时段判定(北京时间): 周一~周五 且 (8~15点 或 21~23点)。
#   · 交易时段每 300 秒(5 分钟)刷新,非交易时段每 1800 秒(30 分钟)。
#   · 子进程执行超时 120 秒;任何异常都吞掉,避免后台线程崩溃。
def _start_price_cache_updater():
    """Background thread: refresh price cache every 5 min during trading hours."""
    import subprocess as _sp  # 【调用包】子进程调用(刷新行情缓存)
    import sys as _sys  # 【调用包】当前 Python 解释器路径
    import threading as _th  # 【调用包】后台线程(刷新循环)
    import time as _time  # 【调用包】时间判断与间隔休眠

    # 【功能】循环刷新行情缓存:按交易时段计算间隔,周期性调用 warm_cache.py 子进程。
    def _refresh():
        while True:
            try:
                now = _time.localtime()
                # Trading hours: Mon-Fri 8:30-15:30, 21:00-23:30 (Beijing time)
                is_weekday = now.tm_wday < 5
                hour = now.tm_hour
                is_trading = is_weekday and ((8 <= hour <= 15) or (21 <= hour <= 23))
                interval = 300 if is_trading else 1800  # 5min during trading, 30min otherwise  # 【变量】刷新间隔:交易时段 300s,非交易 1800s

                _sp.run(
                    [_sys.executable, str(Path(__file__).parent / "warm_cache.py")],  # 【调用函数】子进程调用 warm_cache.py 刷新行情缓存
                    capture_output=True,
                    timeout=120,  # 【变量】子进程超时上限 120 秒
                )
            except Exception:
                pass
            _time.sleep(interval)

    t = _th.Thread(target=_refresh, daemon=True)
    t.start()


_start_price_cache_updater()


# 【功能】读取磁盘上的实时行情缓存(live_prices_cache.json,由 warm_cache.py 更新)。
# 【参数】查询串 ?varieties=rb,au(逗号分隔,可选): 只返回指定品种;缺省返回全部。
# 【返回】JSON 对象 {品种代码: 行情数据};缓存文件不存在时返回 {}。
@app.route("/api/live-prices")
def api_price_live():
    """Get real-time futures prices from disk cache (updated by warm_cache.py)."""
    varieties = request.args.get("varieties", "")
    vlist = (
        [v.strip() for v in varieties.split(",") if v.strip()]
        if varieties and varieties.strip()
        else None
    )
    try:
        if not PRICE_CACHE_FILE.exists():
            return jsonify({})
        with open(str(PRICE_CACHE_FILE), encoding="utf-8") as f:
            data = json.load(f)
        if vlist:
            data = {k: v for k, v in data.items() if k in vlist}
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


# 【功能】手动触发价格更新:调用 price_fetcher.update_price_files() 从 AKShare 拉最新行情。
# 【请求体】{"varieties": ["rb", ...]}(可选,缺省更新全部)。
# 【返回】{"updated": 更新数量, "details": 明细};失败返回 500。
@app.route("/api/price/update", methods=["POST"])
def api_price_update():
    """Update price JSON files with latest data from AKShare."""
    data = request.json or {}
    varieties = data.get("varieties")
    try:
        from price_fetcher import update_price_files  # 【调用包】AKShare 行情更新接口

        result = update_price_files(varieties)  # 【调用函数】跨模块调用:从 AKShare 拉取最新行情并写入价格文件
        return jsonify({"updated": len(result), "details": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# 【功能】渲染看板主页面。get_page_template() 读 web_template.html,再经
#   render_template_string 注入(模板里的 Jinja2 变量会被替换为实际数据)。
# 【关键】显式禁用浏览器缓存(no-cache),保证每次刷新都拿到最新模板与数据。
@app.route("/")
def index():
    resp = make_response(render_template_string(get_page_template()))  # 【调用函数】读模板并用 Jinja2 渲染(注入动态变量)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


# 【功能】调试用测试页:渲染 test_trading.html(模拟交易前端调试页面)。
@app.route("/test")
def test_page():
    path = Path(__file__).parent / "test_trading.html"
    return render_template_string(path.read_text(encoding="utf-8"))


# 【功能】列出全部支持的品种及其元数据(代码 / 名称 / 交易所 / 板块 / 情绪帖子数)。
# 【返回】[{code, name, exchange, sector, sentiment_posts}, ...]。
# 【关键】sentiment_posts 从 SENTIMENT_DIR 下 "<品种>_sentiment.json" 读取;
#   文件不存在或解析失败时记为 0。
@app.route("/api/varieties")
def api_varieties():
    """List all supported varieties with metadata."""
    result = []
    for code, meta in sorted(VARIETY_METADATA.items()):
        sent_path = SENTIMENT_DIR / f"{code}_sentiment.json"
        has_sentiment = sent_path.exists()
        if has_sentiment:
            try:
                with open(sent_path, encoding="utf-8") as f:
                    sd = json.load(f)
                posts = sd["data"]["social_sentiment"]["total_posts_analyzed"]
            except Exception:
                posts = 0
        else:
            posts = 0
        result.append(
            {
                "code": code,
                "name": meta.get("name", code),
                "exchange": meta.get("exchange_cn", ""),
                "sector": _get_sector(code),
                "sentiment_posts": posts,
            }
        )
    return jsonify(result)


# 【功能】获取某品种的情绪数据(含时间范围元信息)。
# 【参数】URL 路径 <variety>: 品种代码(如 rb)。
# 【返回】原始情绪 JSON,并额外注入 _meta: {data_start, data_end, data_days, file_updated};
#   文件不存在时返回 404 {"error": "No sentiment data"}。
@app.route("/api/sentiment/<variety>")
def api_sentiment(variety):
    """Get sentiment data for a variety with time range metadata."""
    path = SENTIMENT_DIR / f"{variety}_sentiment.json"
    if not path.exists():
        return jsonify({"error": "No sentiment data"}), 404
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    # 计算情绪数据的时间跨度并注入 _meta,供前端显示数据覆盖范围。
    # Compute time range
    series = data.get("data", {}).get("daily_series", [])
    if series:
        dates = sorted([s["date"] for s in series])
        data["_meta"] = {
            "data_start": dates[0],
            "data_end": dates[-1],
            "data_days": len(dates),
            "file_updated": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
        }
    return jsonify(data)


# 【功能】返回最近采集到的社交帖子(全平台、抽样)。
# 【参数】days=最近 N 天(按发布时间过滤,可选);since=YYYY-MM-DD 截止日(可选)。
# 【返回】{"_meta": {posts_start, posts_end, total_posts, filter_since},
#           "posts": 最多 200 条}。
# 【关键逻辑】遍历 THINK2_OUTPUT 下 batch_*.jsonl 逐行解析;按 note_id 去重;
#   platform 为 "?" 的脏数据跳过;最后按发布时间倒序排列。
@app.route("/api/sentiment_posts")
def api_sentiment_posts():
    """Get recent sentiment posts from collected data (all platforms, sampled).
    Query params: days (filter to last N days), since (YYYY-MM-DD filter).
    """
    if not THINK2_OUTPUT or not THINK2_OUTPUT.exists():
        return jsonify({"_meta": {}, "posts": []})

    days = request.args.get("days", 0, type=int)
    since = request.args.get("since", "")

    # Calculate cutoff date
    cutoff = None
    if since:
        with contextlib.suppress(ValueError):
            cutoff = datetime.strptime(since, "%Y-%m-%d")
    elif days > 0:
        cutoff = datetime.now() - timedelta(days=days)

    jsonl_files = sorted(glob.glob(str(THINK2_OUTPUT / "batch_*.jsonl")))
    posts = []
    seen = set()
    all_times = []
    for fpath in jsonl_files:
        try:
            with open(fpath, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    d = json.loads(line)
                    nid = d.get("note_id", "")
                    if nid in seen:
                        continue
                    seen.add(nid)
                    if d.get("platform") == "?":
                        continue
                    t = (d.get("publish_time", "") or "")[:16]
                    if t:
                        all_times.append(t[:10])
                        # Apply cutoff filter
                        if cutoff:
                            try:
                                post_date = datetime.strptime(t[:10], "%Y-%m-%d")
                                if post_date < cutoff:
                                    continue
                            except ValueError:
                                pass
                    posts.append(
                        {
                            "platform": d.get("platform", "?"),
                            "author": (d.get("author_name", "") or "")[:20],
                            "fans": d.get("author_fans", 0),
                            "title": (d.get("title", "") or d.get("desc", ""))[:120],
                            "sentiment": d.get("sentiment", "neutral"),
                            "score": d.get("sentiment_score", 0),
                            "varieties": [v["name"] for v in d.get("varieties", [])[:3]],
                            "likes": d.get("like_count", 0),
                            "time": t,
                            "url": d.get("url", ""),
                            "note_id": d.get("note_id", ""),
                        }
                    )
        except Exception:
            pass
    posts.sort(key=lambda p: p["time"], reverse=True)

    # Time range metadata
    meta = {}
    if all_times:
        meta = {
            "posts_start": min(all_times),
            "posts_end": max(all_times),
            "total_posts": len(posts),
        }
    if cutoff:
        meta["filter_since"] = cutoff.strftime("%Y-%m-%d")

    return jsonify({"_meta": meta, "posts": posts[:200]})


# ═══════════════════════════════════════════════════════════════════
# 非标数据可视化聚合(batch JSONL → 关系图 / 桑基图数据)
# 纯函数可单测;输入为 batch_*.jsonl 的逐条记录 dict。
# ═══════════════════════════════════════════════════════════════════
def _iter_batch_records():
    """逐条 yield 全部 batch_*.jsonl 的记录(note_id 去重,跳过 platform 为 '?')。

    【功能】把 api_sentiment_posts 里的"glob + 去重 + 过滤"读取逻辑收敛为公共
    生成器,供关系图/桑基等批量聚合 API 复用,避免各自重复读文件。
    【返回】逐条 dict;目录缺失或单文件异常时静默跳过。
    """
    if not THINK2_OUTPUT or not THINK2_OUTPUT.exists():
        return
    seen = set()  # 【变量】note_id 去重集合(同一帖在多个批次文件里只算一次)
    for fpath in sorted(glob.glob(str(THINK2_OUTPUT / "batch_*.jsonl"))):  # 【调用包】glob:批量匹配批次文件
        try:
            f = open(fpath, encoding="utf-8")  # noqa: SIM115  # 单独 open 以便跳过打不开的文件; with f: 已保证关闭
        except Exception:
            continue
        with f:
            for line in f:
                try:  # 【变量】坏行只跳过该行,不丢弃整个文件后续记录
                    if not line.strip():
                        continue
                    d = json.loads(line)
                    nid = d.get("note_id", "")
                    if not nid or nid in seen:
                        continue
                    seen.add(nid)
                    if d.get("platform") in (None, "", "?"):
                        continue
                    yield d
                except Exception:
                    continue


def _build_variety_platform_graph(records, top_varieties=25):
    """品种 ↔ 平台 ↔ 板块 三层关系图数据(帖子 NER 品种 × 采集平台 × 板块归属)。

    【功能】把批量帖子聚合成 ECharts graph 可渲染的三类节点与两类边:
      节点: platform(采集平台) / variety(帖子 NER 品种) / sector(板块归属)。
      边:   variety↔platform 共现帖数;variety→sector 归属(值为品种帖数)。
      品种只保留帖子数 Top N(防图过密);节点 value = 关联帖数(前端映射节点大小)。
    【参数】records: _iter_batch_records() 产出或等价 dict 列表;top_varieties: 品种上限。
    【返回】{"nodes": [{id,name,type,value}...], "links": [{source,target,value}...]}。
    """
    variety_posts = defaultdict(int)  # 【变量】品种 → 帖数
    platform_posts = defaultdict(int)  # 【变量】平台 → 帖数
    variety_sector = {}  # 【变量】品种 → 板块名(通常各帖一致,取最后一次)
    variety_platform = defaultdict(int)  # 【变量】(品种, 平台) → 共现帖数

    for d in records:
        pl = d.get("platform", "?")
        platform_posts[pl] += 1
        for v in (d.get("varieties") or []):  # 【变量】varieties 为 null/缺失都按空列表处理(否则 for None 崩)
            if not isinstance(v, dict):
                continue
            name = v.get("name", "")
            if not name:
                continue
            variety_posts[name] += 1
            variety_sector[name] = v.get("sector") or "其他"  # 【变量】板块名(null/缺失兜底,避免产出 None 节点)
            variety_platform[(name, pl)] += 1

    top = {
        name for name, _ in sorted(variety_posts.items(), key=lambda x: -x[1])[:top_varieties]
    }  # 【变量】帖子数 Top N 品种集合
    sector_posts = defaultdict(int)  # 【变量】板块 → 归属该板块的 top 品种帖数合计
    for name in top:
        sector_posts[variety_sector.get(name, "其他")] += variety_posts[name]

    nodes = []  # 【变量】节点列表
    node_ids = set()  # 【变量】已注册节点 id(防重)

    def _add_node(nid, ntype, label, value):
        if nid in node_ids:
            return
        node_ids.add(nid)
        nodes.append({"id": nid, "name": label, "type": ntype, "value": value})

    for pl, n in sorted(platform_posts.items(), key=lambda x: -x[1]):
        _add_node(pl, "platform", pl, n)
    for name, n in sorted(variety_posts.items(), key=lambda x: -x[1]):
        if name in top:
            _add_node(name, "variety", name, n)
    for sec, n in sector_posts.items():
        _add_node(sec, "sector", sec, n)

    links = []  # 【变量】边列表(source/target 为节点 id)
    for (name, pl), n in variety_platform.items():
        if name in top:
            links.append({"source": name, "target": pl, "value": n})
    for name in top:
        sec = variety_sector.get(name, "其他")
        links.append({"source": name, "target": sec, "value": variety_posts[name]})

    return {"nodes": nodes, "links": links}


def _sent_dir(score):
    """情绪得分 → 方向标签(与前端 renderPostCards 阈值一致)。"""
    try:
        score = float(score)  # 【变量】数值化:字符串/其他类型统一转 float,失败视为中性
    except (TypeError, ValueError):
        score = 0.0
    if score > 0.1:
        return "看多"
    if score < -0.1:
        return "看空"
    return "中性"


def _build_sentiment_sankey(records, top_varieties=15):
    """平台 → 品种 → 多/空 三层桑基流量数据。

    【功能】把批量帖子按"平台-品种-情绪方向"聚合成计数链,供 ECharts sankey 渲染。
    方向判定优先用品种级 variety_sentiments[].score(比整帖 score 更贴近该品种);
    无品种级情感时退回整帖 sentiment_score 挂到 varieties 首个品种。
    品种只保留 Top N;links 的 source/target 为节点序号(匹配 ECharts sankey)。
    【参数】records: _iter_batch_records() 产出或等价 dict 列表;top_varieties: 品种上限。
    【返回】{"nodes": [{"name"}...], "links": [{"source","target","value"}...]}。
    """
    flow = defaultdict(int)  # 【变量】(平台, 品种, 方向) → 计数
    variety_total = defaultdict(int)  # 【变量】品种 → 帖数(取 Top N 用)

    for d in records:
        pl = d.get("platform", "?")
        vs_list = d.get("variety_sentiments") or []
        if not vs_list:
            score = d.get("sentiment_score") or 0  # 【变量】整帖情感(null 兜底为中性)
            for v in (d.get("varieties") or [])[:1]:  # 【变量】varieties 为 null 时按空列表处理
                if not isinstance(v, dict):
                    continue
                name = v.get("name", "")
                if name:
                    flow[(pl, name, _sent_dir(score))] += 1
                    variety_total[name] += 1
            continue
        for vs in vs_list:
            name = vs.get("variety", "")
            if not name:
                continue
            score = vs.get("score") or 0  # 【变量】品种级情感(null 兜底为中性)
            flow[(pl, name, _sent_dir(score))] += 1
            variety_total[name] += 1

    top = {
        name for name, _ in sorted(variety_total.items(), key=lambda x: -x[1])[:top_varieties]
    }  # 【变量】帖子数 Top N 品种集合
    platforms = sorted({pl for pl, _, _ in flow})  # 【变量】有流量的平台
    directions = ["看多", "看空", "中性"]  # 【变量】方向层节点(固定三态)
    nodes = (
        [{"name": p} for p in platforms]
        + [{"name": n} for n in sorted(top)]
        + [{"name": d} for d in directions]
    )
    name_index = {nd["name"]: i for i, nd in enumerate(nodes)}  # 【变量】节点名 → 序号

    links = []  # 【变量】边列表(平台→品种、品种→方向 两段)
    for (pl, name, direction), n in flow.items():
        if name in top:
            links.append({"source": name_index[pl], "target": name_index[name], "value": n})
            links.append({"source": name_index[name], "target": name_index[direction], "value": n})
    return {"nodes": nodes, "links": links}


# 【功能】把实时主连 CSV 就地做后复权,消除换月跳空(与 price_fetcher 文件口径一致)。
# 【关键】get_futures_price() 返回的原始主连(如 RB0)是简单拼接、未复权,换月点有假跳空;
#   这里解析出 OHLC 后用同一套 _backward_adjust(最近 bar 因子=1)复权,最近 bar 因子=1。
#   换月点优先用真实日历(_load_rollover_calendar 按品种名查证),品种不在日历时回退 8% 启发式。
#   这样前端价格图与后端回测(读复权 *_price.json)口径一致,图上不再有伪缺口。
# 【返回】(points, roll_dates):points=[{date, close}] 复权后收盘序列;roll_dates=检测到的换月日期。
def _adjusted_price_points(result: str, variety_name: str | None = None) -> tuple[list[dict], list[str], str]:
    from price_fetcher import (  # 【调用包】延迟导入(避免 web_app 顶部重依赖)
        NAME_TO_CODE,
        _backward_adjust,
        _load_rollover_calendar,
    )

    raw = []
    for line in result.strip().split("\n"):
        if not line or line.startswith("#") or not line[0].isdigit():
            continue
        parts = line.split(",")
        if len(parts) >= 5:
            raw.append({
                "date": parts[0].strip(),
                "open": float(parts[1]),
                "high": float(parts[2]),
                "low": float(parts[3]),
                "close": float(parts[4]),
            })
    if len(raw) < 2:
        return raw, [], "heuristic"
    # 真实日历优先:该品种查证的换月日集合;无日历/品种不在日历时为 None → 回退 8% 启发式
    cal_dates = None
    if variety_name:
        cal = _load_rollover_calendar()  # 【调用函数】加载真实换月日历
        # 日历 key 是价格文件名(混用代码 PP/PTA/PVC 与中文名 螺纹钢/热卷),而前端传的是代码(如 "HC")。
        # 候选 key 依次尝试:①代码→中文名翻译(螺纹钢→RB) ②日历 main_contract 反查(HC0→热卷,兜底命名不一致)
        # ③代码本身(PP→PP)。取第一个能在日历里命中的。
        code_to_name = {v: k for k, v in NAME_TO_CODE.items()}  # 【变量】代码→中文名反向映射
        main_index = {
            (entry.get("main_contract") or "").rstrip("0"): key  # 【变量】主连代码(去尾部0)→日历 key,如 HC0→热卷
            for key, entry in (cal or {}).items()
            if entry and (entry.get("main_contract") or "").endswith("0")
        }
        cands = []  # 【变量】候选日历 key(保序去重)
        for c in (code_to_name.get(variety_name, variety_name), main_index.get(variety_name), variety_name):
            if c and c not in cands:
                cands.append(c)
        cal_key = next((c for c in cands if c in cal), None)  # 【变量】命中的日历 key
        if cal_key:
            ro = (cal.get(cal_key, {}) or {}).get("rollover_dates", []) or []  # 【变量】该品种换月日
            if ro:
                cal_dates = {r["date"] for r in ro}  # 【变量】日历换月日集合
    method = "calendar" if cal_dates else "heuristic"  # 【变量】换月来源(calendar=真实日历 / heuristic=8%启发式)
    adj, roll_idx = _backward_adjust(raw, calendar_dates=cal_dates)  # 【调用函数】后复权(原地修改 raw 的 OHLC)
    roll_dates = [adj[i]["date"] for i in roll_idx]  # 【变量】换月日期清单
    return [{"date": p["date"], "close": p["close"]} for p in adj], roll_dates, method


# 【功能】解析 get_futures_inventory() 返回的仓单库存 CSV 文本 → [{date, inventory, change}]。
# 【关键】CSV 三列 date,inventory,change(东财 futures_inventory_em 输出);行首可能带 '#' 的趋势/合并注释,
#   与英文表头逐行跳过;inventory 列非数值的行丢弃。合并后的 Hybrid 结果若列数更多,只取前三列。
# 【返回】解析出的仓单序列(可能为空列表)。
def _inventory_points(csv_text: str) -> list[dict]:
    points = []
    for line in csv_text.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3 or parts[0].lower() == "date":  # 跳过英文表头(Column: date,inventory,change)
            continue
        try:
            inventory = float(parts[1])
        except ValueError:
            continue  # 【关键】数值列非法 → 跳过该行(合并文本/说明行)
        points.append({
            "date": parts[0],
            "inventory": inventory,
            "change": parts[2],
        })
    return points


# 【功能】解析 get_futures_basis() 返回的基差 CSV 文本 → [{date, spot_price, near_basis, near_basis_rate}]。
# 【关键】CSV 表头是英文列名(akshare futures_spot_price_daily 经 col_map 重命名后 to_csv),列集随品种变化
#   (主力/近月列在品种无对应合约时缺失);按表头列名定位,缺列与空值统一为 None,避免列序漂移。
# 【返回】解析出的基差序列(可能为空列表)。
def _basis_points(csv_text: str) -> list[dict]:
    lines = [ln for ln in csv_text.strip().split("\n") if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        return []
    header = [h.strip() for h in lines[0].split(",")]
    idx = {name: i for i, name in enumerate(header)}

    def _col(row, name):  # 【变量】取列值(缺列/越界/空 → None)
        i = idx.get(name)
        if i is None or i >= len(row):
            return None
        v = row[i].strip()
        return v or None

    points = []
    for ln in lines[1:]:
        row = [c.strip() for c in ln.split(",")]
        date = _col(row, "date")
        if not date:
            continue
        point = {"date": date}
        for key in ("spot_price", "near_basis", "near_basis_rate"):
            v = _col(row, key)
            try:
                point[key] = float(v) if v is not None else None
            except ValueError:
                point[key] = None
        points.append(point)
    return points


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """【功能】两等长序列的 Pearson 相关系数(长度<2 或方差为 0 时返回 None)。"""
    n = len(xs)
    if n < 2 or len(ys) != n:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return None
    return cov / (vx * vy) ** 0.5


def _pct(a: float, b: float) -> float | None:
    """【功能】百分比变化(b 为基准;基准为 0 或 None 时返回 None)。"""
    if b is None or b == 0:
        return None
    return (a - b) / b * 100


# 【功能】数据看板关联分析(纯函数):价格-库存 R / 库存趋势 / 基差 / 近 5 日背离检测。
# 【参数】price=[{date, close}],inventory=[{date, inventory}],basis=[{date, near_basis, near_basis_rate}](均按日期升序)。
# 【返回】dict:has_* 数据可用标记;price_inventory_r(日期内连接后的 Pearson R)+ret_inventory_r(变化率 R);
#   inventory_trend(BUILDING/DRAINING/STABLE)+pct;basis_latest/basis_rate_latest;
#   divergence={label, desc, price_chg_pct, inventory_chg_pct}。
def _dashboard_relationships(
    price: list[dict],
    inventory: list[dict],
    basis: list[dict],
) -> dict:
    result = {
        "has_price": bool(price),
        "has_inventory": bool(inventory),
        "has_basis": bool(basis),
    }

    # 1) 价格-库存 Pearson R(按日期内连接;另给变化率 R 以去量纲)
    result["price_inventory_r"] = None
    result["price_inventory_n"] = 0
    result["ret_inventory_r"] = None
    if price and inventory:
        inv_by_date = {p["date"]: p["inventory"] for p in inventory}
        px, iv = [], []
        for p in price:
            inv = inv_by_date.get(p["date"])
            if inv is not None:
                px.append(p["close"])
                iv.append(inv)
        if len(px) >= 5:
            result["price_inventory_r"] = round(_pearson(px, iv), 4)
            result["price_inventory_n"] = len(px)
        if len(px) >= 6:  # 【关键】变化率 R:close 日环比 vs 库存日环比,去掉量纲差异
            px_chg = [_pct(a, b) for a, b in zip(px[1:], px[:-1], strict=True)]
            iv_chg = [_pct(a, b) for a, b in zip(iv[1:], iv[:-1], strict=True)]
            valid = [(a, b) for a, b in zip(px_chg, iv_chg, strict=True) if a is not None and b is not None]
            if len(valid) >= 5:
                result["ret_inventory_r"] = round(_pearson([a for a, _ in valid], [b for _, b in valid]), 4)

    # 2) 库存趋势:近 5 日均 vs 更早 5 日均 → BUILDING/DRAINING/STABLE + %
    result["inventory_trend"] = None
    result["inventory_trend_pct"] = None
    result["inventory_recent_avg"] = None
    result["inventory_earlier_avg"] = None
    if len(inventory) >= 10:
        vals = [p["inventory"] for p in inventory]
        recent = sum(vals[-5:]) / 5  # 【变量】近 5 日库存均值
        earlier = sum(vals[-10:-5]) / 5  # 【变量】更早 5 日库存均值
        if earlier > 0:
            pct = _pct(recent, earlier)
            trend = "BUILDING" if pct > 3 else ("DRAINING" if pct < -3 else "STABLE")
            result["inventory_trend"] = trend
            result["inventory_trend_pct"] = round(pct, 2)
            result["inventory_recent_avg"] = round(recent, 0)
            result["inventory_earlier_avg"] = round(earlier, 0)

    # 3) 基差:最新基差率/基差;基差-价格 R(近 60 交易日,按日期内连接)
    result["basis_latest"] = None
    result["basis_rate_latest"] = None
    result["basis_price_r"] = None
    if basis:
        last = basis[-1]
        result["basis_latest"] = last.get("near_basis")
        result["basis_rate_latest"] = last.get("near_basis_rate")
        px_by_date = {p["date"]: p["close"] for p in price}
        pairs = []
        for b in basis[-60:]:
            px = px_by_date.get(b["date"])
            bs = b.get("near_basis")
            if px is not None and bs is not None:
                pairs.append((px, bs))
        if len(pairs) >= 5:
            result["basis_price_r"] = round(_pearson([a for a, _ in pairs], [b for _, b in pairs]), 4)

    # 4) 近 5 交易日背离检测:价格方向 vs 库存方向
    result["divergence"] = None
    if len(price) >= 6 and len(inventory) >= 6:
        px0, px1 = price[-5]["close"], price[-1]["close"]
        iv0, iv1 = inventory[-5]["inventory"], inventory[-1]["inventory"]
        price_up = px1 >= px0  # 【变量】近 5 日价格是否上涨
        inv_up = iv1 >= iv0  # 【变量】近 5 日库存是否累加
        if price_up and not inv_up:
            label, desc = "健康上涨", "去库 + 上涨:供需偏紧,涨势有基本面支撑,持仓可继续持有。"
        elif inv_up and not price_up:
            label, desc = "健康下跌", "累库 + 下跌:供过于求,跌势与累库相互印证,空头逻辑成立。"
        elif price_up and inv_up:
            label, desc = "背离-虚涨", "累库 + 上涨:库存上升而价格不跌,警惕反弹的可持续性(虚涨),宜减仓观察。"
        else:
            label, desc = "背离-超跌", "去库 + 下跌:库存下降而价格走弱,或已超跌,注意抄底与需求崩塌的分野。"
        result["divergence"] = {
            "label": label,
            "desc": desc,
            "price_chg_pct": round(_pct(px1, px0) or 0.0, 2),
            "inventory_chg_pct": round(_pct(iv1, iv0) or 0.0, 2),
        }
    return result


# 【功能】获取品种日线价格,供前端画折线 / K 线。
# 【参数】days=回看天数(默认 180,强制限制在 30~730)。
# 【返回】{"_meta": {price_start, price_end, data_points, adjusted, rollover_dates}, "prices": [{date, close}, ...]}。
# 【关键】get_futures_price() 返回 CSV 文本;逐行解析 OHLC 后做后复权(_adjusted_price_points),
#   消除主力连续换月假跳空;只返回最后 max(days,120) 个点。_meta.adjusted=True 表示已是复权价。
@app.route("/api/price/<variety>")
def api_price(variety):
    """Get price data for charts. Query param: days (default 180)."""
    days = request.args.get("days", 180, type=int)
    days = max(30, min(days, 730))  # Clamp 30-730  # 【变量】回看天数强制钳制在 30~730 天
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    result = get_futures_price(variety, start_date, end_date)  # 【调用函数】跨模块获取行情 CSV 文本
    data, roll_dates, rollover_method = _adjusted_price_points(result, variety)  # 【调用函数】实时主连 → 后复权序列(消除换月跳空)
    # Return with meta
    meta = {}
    if data:
        meta = {
            "price_start": data[0]["date"],
            "price_end": data[-1]["date"],
            "data_points": len(data),
            "adjusted": True,  # 【变量】已后复权(消除主力连续换月假跳空)
            "adjust_method": "backward",  # 【变量】复权方式:后复权(最近 bar 因子=1)
            "rollover_dates": roll_dates,  # 【变量】换月日期(真实日历查证 or 8% 启发式)
            "rollover_method": rollover_method,  # 【变量】换月来源(calendar=真实日历 / heuristic=8%启发式)
        }
    return jsonify({"_meta": meta, "prices": data[-max(days, 120) :]})


# 【功能】把价格与情绪按日期对齐,供双轴叠加图使用。
# 【参数】days=回看天数(默认 180,范围 30~730)。
# 【返回】{"_meta": {...}, "overlay": [{date, close?, avg_score?, bullish_ratio?, ...}, ...]}。
# 【关键】以"价格与情绪的日期并集"为坐标轴;某天只有价格或只有情绪时,对应键缺失,
#   前端需自行容错;随后按 start_date 过滤到请求的时间窗内。
@app.route("/api/overlay/<variety>")
def api_overlay(variety):
    """Get price + sentiment data aligned by date for dual-axis overlay chart.
    Query param: days (default 180, range 30-730).
    """
    days = request.args.get("days", 180, type=int)
    days = max(30, min(days, 730))

    # Price data (后复权:消除主力连续换月假跳空,与回测口径一致)
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    price_result = get_futures_price(variety, start_date, end_date)  # 【调用函数】跨模块获取行情 CSV 文本
    price_points, overlay_roll_dates, overlay_method = _adjusted_price_points(price_result, variety)  # 【调用函数】实时主连 → 后复权序列
    price_map = {p["date"]: p["close"] for p in price_points}  # 【变量】后复权收盘价映射(日期 → 收盘价)

    # Sentiment data
    sent_path = SENTIMENT_DIR / f"{variety}_sentiment.json"
    sentiment_map = {}
    sent_meta = {}
    if sent_path.exists():
        with open(sent_path, encoding="utf-8") as f:
            sd = json.load(f)
        for d in sd["data"]["daily_series"]:
            sentiment_map[d["date"]] = {
                "avg_score": d.get("avg_score", 0),
                "simple_avg": d.get("simple_avg", 0),
                "bullish_ratio": d.get("bullish_ratio", 0),
                "bearish_ratio": d.get("bearish_ratio", 0),
                "total_notes": d.get("note_count", d.get("total_notes", 0)),
            }
        series_dates = sorted(sentiment_map.keys())
        if series_dates:
            sent_meta = {"sentiment_start": series_dates[0], "sentiment_end": series_dates[-1]}

    # Merge by date, then filter to the requested window
    all_dates = sorted(set(list(price_map.keys()) + list(sentiment_map.keys())))
    overlay = []
    for date in all_dates:
        point = {"date": date}
        if date in price_map:
            point["close"] = price_map[date]
        if date in sentiment_map:
            point.update(sentiment_map[date])
        # Apply days filter: only include dates within the lookback window
        if date >= start_date:
            overlay.append(point)

    price_dates = sorted(price_map.keys())
    meta = {
        "variety": variety,
        "price_start": price_dates[0] if price_dates else None,
        "price_end": price_dates[-1] if price_dates else None,
        "data_points": len(overlay),
        "filter_start": start_date,
        "adjusted": True,  # 【变量】价格轴已后复权(消除换月假跳空)
        "adjust_method": "backward",  # 【变量】复权方式:后复权
        "rollover_dates": overlay_roll_dates,  # 【变量】换月日期(真实日历查证 or 8% 启发式)
        "rollover_method": overlay_method,  # 【变量】换月来源(calendar=真实日历 / heuristic=8%启发式)
        **sent_meta,
    }

    return jsonify({"_meta": meta, "overlay": overlay})


# 【功能】数据看板:一次返回品种的价格/仓单库存/基差 + 关联分析,供 tab-dashboard 渲染。
# 【参数】days=回看天数(默认 180,范围 30~365)。
# 【返回】{"_meta": {variety, name, sector, price_points, inventory_available, basis_available, ...},
#           "price": [{date, close}], "inventory": {available, points, note},
#           "basis": {available, points, note}, "analysis": {关联分析结果}}。
# 【关键】价格走 _adjusted_price_points 后复权(与 /api/price、/api/overlay 口径一致);
#   仓单库存/基差分别经 _inventory_points/_basis_points 解析,数据源不可用(DATA_*/NO_DATA_*)时优雅降级为
#   available=false + note,不抛错。无仓单品种(SH/WR)、无基差品种(AO/CS/LC/SI)由此自然呈现空态。
@app.route("/api/dashboard/<variety>")
def api_dashboard(variety):
    """Return price / inventory / basis series + relationship analysis for the dashboard tab."""
    code = variety.upper()
    days = request.args.get("days", 180, type=int)
    days = max(30, min(days, 365))  # 【变量】回看天数钳制 30~365
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    # 价格:后复权收盘序列(换月口径与 /api/price 一致)
    price_result = get_futures_price(code, start_date, end_date)  # 【调用函数】跨模块获取行情 CSV 文本
    price, _, _ = _adjusted_price_points(price_result, code)  # 【调用函数】实时主连 → 后复权序列

    # 仓单库存(东财 futures_inventory_em;SH/WR 等品种无数据 → 优雅降级)
    inv = {"available": False, "points": [], "note": ""}
    try:
        inv_result = get_futures_inventory(code)  # 【调用函数】跨模块获取仓单库存 CSV 文本
        if inv_result.startswith(("DATA_ERROR", "DATA_UNAVAILABLE", "NO_DATA_AVAILABLE")):
            inv["note"] = inv_result
        else:
            inv_points = _inventory_points(inv_result)  # 【调用函数】解析仓单 CSV → 序列
            if inv_points:
                inv["available"] = True
                inv["points"] = inv_points
            else:
                inv["note"] = "NO_DATA_AVAILABLE: 仓单库存无数据(该品种此接口不覆盖)"
    except Exception as e:  # 【关键】网络/数据源异常不拖垮看板
        logger.warning("dashboard inventory %s: %s", code, e)
        inv["note"] = f"DATA_ERROR: {e}"

    # 基差(akshare futures_spot_price_daily;AO/CS/LC/SI 等品种无数据 → 优雅降级)
    basis = {"available": False, "points": [], "note": ""}
    try:
        basis_result = get_futures_basis(code, start_date, end_date)  # 【调用函数】跨模块获取基差 CSV 文本
        if basis_result.startswith(("DATA_ERROR", "DATA_UNAVAILABLE", "NO_DATA_AVAILABLE")):
            basis["note"] = basis_result
        else:
            basis_points = _basis_points(basis_result)  # 【调用函数】解析基差 CSV → 序列
            if basis_points:
                basis["available"] = True
                basis["points"] = basis_points
            else:
                basis["note"] = "NO_DATA_AVAILABLE: 基差无数据(该品种此接口不覆盖)"
    except Exception as e:
        logger.warning("dashboard basis %s: %s", code, e)
        basis["note"] = f"DATA_ERROR: {e}"

    analysis = _dashboard_relationships(  # 【调用函数】纯函数关联分析(价格-库存 R/趋势/基差/背离)
        price,
        inv["points"] if inv["available"] else [],
        basis["points"] if basis["available"] else [],
    )
    meta = {
        "variety": code,
        "name": VARIETY_METADATA.get(code, {}).get("name", code),
        "sector": _get_sector(code),  # 【调用函数】板块归并(剥括号子板块)
        "days": days,
        "price_points": len(price),
        "inventory_available": inv["available"],
        "basis_available": basis["available"],
        "inventory_note": inv["note"],
        "basis_note": basis["note"],
    }
    return jsonify({"_meta": meta, "price": price, "inventory": inv, "basis": basis, "analysis": analysis})


# 【功能】板块相关性总览:板块内各品种的价格-库存 Pearson R 汇总(前端按钮触发,不自动加载)。
# 【返回】{"sector": 板块名, "count": 有效品种数, "rows": [{code, name, r, n, trend}, ...]}。
# 【关键】循环板块内品种逐次拉价格+仓单库存(各 1 次网络请求),单品种数据不足 5 个点或仓库接口不可用时跳过;
#   耗时与板块规模成正比(能化 19 品种 ≈ 38 次请求),前端需给加载态。
@app.route("/api/dashboard/sector/<sector>")
def api_dashboard_sector(sector):
    from tradingagents.dataflows.sentiment_data import (
        build_sector_to_varieties,  # 【调用包】板块→品种反向映射(延迟导入,避免循环依赖)
    )

    sector_map = build_sector_to_varieties()  # 【调用函数】构建板块归并映射
    codes = sector_map.get(sector, [])
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
    rows = []
    for code in codes:
        try:
            pr = get_futures_price(code, start_date, end_date)  # 【调用函数】获取行情 CSV
            price, _, _ = _adjusted_price_points(pr, code)  # 【调用函数】后复权序列
            ir = get_futures_inventory(code)  # 【调用函数】获取仓单库存 CSV
            if ir.startswith(("DATA_ERROR", "DATA_UNAVAILABLE", "NO_DATA_AVAILABLE")):
                continue  # 【关键】无仓库数据的品种跳过(不记入汇总)
            inv_points = _inventory_points(ir)  # 【调用函数】解析仓单序列
            if len(inv_points) < 5:
                continue  # 【关键】数据点不足 5 个 → R 无意义,跳过
            rel = _dashboard_relationships(price, inv_points, [])  # 【调用函数】只取价格-库存 R 与趋势
            r = rel.get("price_inventory_r")
            if r is not None:
                rows.append({
                    "code": code,
                    "name": VARIETY_METADATA.get(code, {}).get("name", code),
                    "r": r,
                    "n": rel["price_inventory_n"],
                    "trend": rel.get("inventory_trend"),
                })
        except Exception as e:  # 【关键】单品种失败不拖垮整个板块
            logger.warning("dashboard sector %s / %s: %s", sector, code, e)
            continue
    rows.sort(key=lambda row: row["r"] or 0)  # 【变量】按 R 升序(负相关在前)
    return jsonify({"sector": sector, "count": len(rows), "rows": rows})


# 【功能】读取思路2项目生成的回测结果(全局权重 + 各品种方向准确率)。
# 【返回】{"platforms": 平台权重, "signal_comparison": 信号对比,
#           "varieties": {品种: {accuracy, pearson_r, n}}, "weight_source": 来源}。
# 【关键】只收录 data_points>0 的品种;文件名以 "_" 开头的临时文件跳过。
@app.route("/api/backtest")
def api_backtest():
    """Get backtest results."""
    if not THINK2_TRENDS or not THINK2_TRENDS.exists():
        return jsonify({"platforms": {}, "signal_comparison": {}, "varieties": {}})
    gw_path = THINK2_TRENDS / "_global_weights.json"
    result = {"platforms": {}, "signal_comparison": {}, "varieties": {}}
    if gw_path.exists():
        with open(gw_path, encoding="utf-8") as f:
            gw = json.load(f)
        result["platforms"] = gw.get("weights", {})
        result["signal_comparison"] = gw.get("signal_comparison", {})
        result["weight_source"] = gw.get("weight_source", "")
        variety_backtests = {}
        for vf in sorted(THINK2_TRENDS.glob("*_weights.json")):
            vname = vf.stem.replace("_weights", "")
            if vname.startswith("_"):
                continue
            with open(vf, encoding="utf-8") as f:
                vd = json.load(f)
            cm = vd.get("combined_metrics", {})
            if cm.get("data_points", 0) > 0:
                variety_backtests[vname] = {
                    "accuracy": cm.get("direction_accuracy", 0),
                    "pearson_r": cm.get("pearson_r", 0),
                    "n": cm.get("data_points", 0),
                }
        result["varieties"] = variety_backtests
    return jsonify(result)


# 【功能】把一次完整分析的结果落盘为历史报告文件 commodity_{symbol}_{ts}.md,
#          格式与 CLI 入口 commodity_demo.py 完全一致(标题/日期/耗时 + 各章节)。
# 【关键】此前只有 CLI 会写盘,Web 分析(run_analysis)从不落盘,导致 /api/history
#         只能列出 CLI 时代的旧文件(2026-07-21 之后停更)。本函数补上 Web 侧落盘。
# 【参数】symbol: 品种代码;trade_date: 交易日;final_state: 图最终状态;
#         elapsed: 本次分析耗时秒数。
# 【返回】写入成功的文件路径;目录不存在会自动创建。
def _persist_analysis_report(symbol, trade_date, final_state, elapsed):
    """Save a finished analysis to REPORT_DIR in CLI-compatible markdown format."""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")  # 时间戳用于文件名唯一化
    fpath = REPORT_DIR / f"commodity_{symbol}_{timestamp}.md"
    # 各阶段产物,顺序与分析流程一致(分析师 -> 辩论 -> 研判 -> 情景)
    reports = [  # 【变量】reports:各阶段报告(标题,内容)列表,标题与 CLI 一致
        ("Technical Analysis", final_state.get("technical_report", "")),
        ("Fundamental Analysis", final_state.get("fundamental_report", "")),
        ("Macro/News Analysis", final_state.get("macro_report", "")),
        ("Sentiment Analysis", final_state.get("sentiment_report", "")),
        ("Debate Moderator Summary", final_state.get("discussion_summary", "")),
        ("Synthesis & Recommendation", final_state.get("investment_plan", "")),
        ("Scenario Analysis", final_state.get("scenario_analysis", "")),
    ]
    with open(fpath, "w", encoding="utf-8") as f:  # 以 UTF-8 写入(跳过空内容段)
        f.write(f"# Commodity Futures Analysis: {symbol}\n\n")
        f.write(f"**Date**: {trade_date}\n")
        f.write(f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"**Elapsed**: {elapsed:.0f}s\n\n")
        f.write("---\n\n")
        for title, content in reports:
            if content:
                f.write(f"## {title}\n\n{content}\n\n---\n\n")
    logger.info("Report saved: %s", fpath)
    return fpath


# 【功能】按 url 域名推断平台代码(防漏)。早期批次文件部分记录缺 platform 字段,
#          在统计/列表处用此函数兜底,避免显示为 "?" 或数据被跳过。
# 【参数】url: 帖子 url(如 https://www.xiaohongshu.com/explore/...)。
# 【返回】平台代码 weibo/xhs/zhihu/xueqiu/eastmoney_guba;推断不出返回 "?"。
def _infer_platform(url):
    """Infer platform code from a post URL (fallback when `platform` field missing)."""
    dom = urlparse(url or "").netloc.lower()
    for kw, plat in (
        ("xiaohongshu", "xhs"),
        ("weibo", "weibo"),
        ("zhihu", "zhihu"),
        ("xueqiu", "xueqiu"),
        ("eastmoney", "eastmoney_guba"),  # 2026-08-26 补:东财股吧 URL 域名 guba.eastmoney.com
    ):
        if kw in dom:
            return plat
    return "?"


# 【功能】列出最近 20 份已保存的分析报告(commodity_*.md)。
# 【返回】[{symbol, filename, size, time, path}, ...]。
@app.route("/api/history")
def api_history():
    """Get past analysis reports."""
    reports = []
    if REPORT_DIR.exists():
        # 过滤 *_comparison.md(CLI 附带产物,非独立报告,避免占用历史列表)。
        # 按"修改时间"降序排序(而非文件名):文件名是 commodity_{品种}_{时间戳},
        # 若按文件名排序,会退化成按品种字母排序(如 AP 永远排在 RB/TA 之后),
        # 导致新落盘报告被挤到列表底部、看似"历史报告停更"。
        for f in sorted(
            (p for p in REPORT_DIR.glob("commodity_*.md") if not p.name.endswith("_comparison.md")),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:20]:
            stat = f.stat()
            name = f.stem.replace("commodity_", "")
            parts = name.split("_", 1)
            sym = parts[0] if parts else "?"
            reports.append(
                {
                    "symbol": sym,
                    "filename": f.name,
                    "size": stat.st_size,
                    "time": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "path": str(f),
                }
            )
    return jsonify(reports)


# 【功能】取某品种在指定日期之后约 30 天的价格 + 情绪,用于"预测 vs 实际"对比图。
# 【参数】date 必须为 YYYY-MM-DD,非法返回 400。
# 【返回】{prices, sentiment, start_close, end_close, pct_change, actual_direction,
#           trading_days}。
# 【关键】actual_direction 依收盘价涨跌幅判定: >0.3% 为 UP,<-0.3% 为 DOWN,否则 FLAT;
#   价格不足 2 条时记作 N/A。
@app.route("/api/compare/<variety>/<date>")
def api_compare(variety, date):
    """Get price data + sentiment for comparison chart (prediction vs actual)."""
    try:
        target = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "Invalid date"}), 400
    end_dt = target + timedelta(days=30)
    price_result = get_futures_price(variety, date, end_dt.strftime("%Y-%m-%d"))  # 【调用函数】跨模块获取行情 CSV 文本
    prices = []
    for line in price_result.strip().split("\n"):
        if not line or line.startswith("#") or not line[0].isdigit():
            continue
        parts = line.split(",")
        if len(parts) >= 5:
            prices.append({"date": parts[0].strip(), "close": float(parts[4])})

    sent_path = SENTIMENT_DIR / f"{variety}_sentiment.json"
    sentiment_series = []
    if sent_path.exists():
        with open(sent_path, encoding="utf-8") as f:
            sd = json.load(f)
        for d in sd["data"]["daily_series"]:
            if d["date"] >= date:
                sentiment_series.append(
                    {"date": d["date"], "score": d["avg_score"], "simple": d.get("simple_avg", 0)}
                )

    if len(prices) >= 2:
        start_close = prices[0]["close"]
        end_close = prices[-1]["close"]
        pct = (end_close - start_close) / start_close * 100 if start_close else 0
        actual_dir = "UP" if pct > 0.3 else ("DOWN" if pct < -0.3 else "FLAT")
    else:
        pct, actual_dir = 0, "N/A"

    return jsonify(
        {
            "prices": prices,
            "sentiment": sentiment_series,
            "start_close": prices[0]["close"] if prices else 0,
            "end_close": prices[-1]["close"] if prices else 0,
            "pct_change": round(pct, 2),
            "actual_direction": actual_dir,
            "trading_days": len(prices),
        }
    )


# 【功能】读取一份已保存报告,并解析其中的章节与评级。
# 【安全】os.path.basename 只取文件名,防止路径穿越攻击。
# 【返回】{content(前 5 万字), sections: {章节名: 正文}, rating: {rating, confidence, score},
#           filename}。
# 【关键】章节用正则 "## <章节名>" 切分;评级用 "RATING: ... | CONFIDENCE: ... | SCORE: N" 提取。
@app.route("/api/report/<path:filename>")
def api_report(filename):
    """Read a saved report with section parsing."""
    # Safety: prevent path traversal
    safe_name = os.path.basename(filename)
    fpath = REPORT_DIR / safe_name
    if not fpath.exists():
        return jsonify({"error": "Not found"}), 404

    with open(fpath, encoding="utf-8") as f:
        content = f.read()

    sections = {}
    for sec in [
        "Technical Analysis",
        "Fundamental Analysis",
        "Macro/News Analysis",
        "Sentiment Analysis",
        "Debate Moderator",
        "Synthesis",
        "Scenario",
    ]:
        # 正则放宽:允许标题带后缀(如 CLI 的 "Debate Moderator Summary"/"Synthesis & Recommendation"),
        # 用 ^## <sec>[^\n]* 匹配同一章标题任意结尾,保证新旧报告都能解析到章节。
        m = re.search(
            rf"^## {re.escape(sec)}[^\n]*\n(.*?)(?=\n## |\n---\n|\Z)",
            content,
            re.DOTALL | re.MULTILINE,
        )
        if m:
            sections[sec] = m.group(1).strip()[:5000]

    rating_match = re.search(
        r"RATING:\s*(.+?)\s*\|\s*CONFIDENCE:\s*(.+?)\s*\|\s*SCORE:\s*(\d+)", content
    )
    rating = None
    if rating_match:
        rating = {
            "rating": rating_match.group(1).strip(),
            "confidence": rating_match.group(2).strip(),
            "score": int(rating_match.group(3)),
        }

    return jsonify(
        {"content": content[:50000], "sections": sections, "rating": rating, "filename": safe_name}
    )


# ── Enhanced SSE analysis endpoint ──────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════
# 分析流控(重点路由组)
#   /api/run_analysis —— 启动完整分析(后台线程)
#   /api/progress     —— 前端轮询进度(0.5~1 秒一次)
#   /api/pause /api/resume /api/stop —— 暂停 / 继续 / 停止
#   /api/feedback     —— 用户与 Agent 辩论
#   /api/analysis_results —— 分析完成后取最终结果
# 说明:本组端点不是真 SSE 推送,而是"后台线程 + 前端轮询"模型;
#   /api/update_data 才是真正的 SSE 流式端点。
# ═══════════════════════════════════════════════════════════════════


# 【功能】启动一次完整的多分析师分析(技术/基本面/宏观/情绪 + 多方辩论 + 综合研判 + 情景分析)。
# 【请求体】{"symbol": "RB", "date": "2026-07-14",
#             "include_sentiment": "auto" | "include" | "exclude"}
#   · include_sentiment=include: 必定带情绪分析师; exclude: 不带;
#     auto(默认)= 有该品种情绪数据就带,否则退化为 3 分析师(技术/基本面/宏观)。
# 【返回】立即返回 {"status": "started", "include_sentiment": ..., "stages": [...]};
#   真正的分析在后台线程异步执行,前端通过 /api/progress 轮询进度。
# 【关键逻辑】
#   · 阶段列表随 include_sentiment 变化:不带情绪时过滤掉 "sentiment" 阶段。
#   · 后端把每个 Agent 节点名映射为阶段 id(stage_map),节点跑完就标记该阶段完成。
#   · 分析流会检查 _tracker.stop_requested / wait_if_paused(),实现前端"停止/暂停"。
@app.route("/api/run_analysis", methods=["POST"])
def api_run_analysis():
    """Run full analysis with SSE streaming — reports, debate, synthesis, rating."""
    global _tracker
    data = request.json or {}
    symbol = data.get("symbol", "RB").upper()
    trade_date = data.get("date", "2026-07-14")

    # 决定本次是否运行情绪分析师:
    # · "include" → 强制包含; "exclude" → 强制排除;
    # · "auto"(默认) → 该品种存在情绪数据(含仓库样例)则包含,否则退化为 3 分析师。
    # Resolve whether the Sentiment analyst runs this time. "auto" degrades
    # to the 3-analyst flow (Technical/Fundamental/Macro) when no sentiment
    # data is actually available for this variety (incl. repo-sample fallback).
    inc_choice = str(data.get("include_sentiment", "auto")).strip().lower()
    if inc_choice == "include":
        include_sentiment = True
    elif inc_choice == "exclude":
        include_sentiment = False
    else:  # "auto" (default)
        include_sentiment = should_include_sentiment(symbol)  # 【调用函数】质量感知判定(数据不足但有板块复合也算含)

    # 阶段列表:不带情绪分析时,过滤掉 "sentiment" 阶段,进度条也随之少一段。
    stages = (
        PIPELINE_STAGES
        if include_sentiment
        else [s for s in PIPELINE_STAGES if s["id"] != "sentiment"]
    )

    _tracker = ProgressTracker(symbol=symbol, trade_date=trade_date, stages=stages)
    _tracker.is_running = True

    # Agent 节点名 → 前端阶段 id 的映射,用于把图节点执行进度映射为进度条阶段。
    stage_map = {  # 【变量】Agent 节点名 → 前端阶段 id 的映射,用于把图执行进度映射为进度条阶段
        "technical_analyst": "technical",
        "fundamental_analyst": "fundamental",
        "macro_analyst": "macro",
        "sentiment_analyst": "sentiment",
        "bull_opening": "bull_opening",
        "bear_refute": "bear_refute",
        "bull_rebuttal": "bull_rebuttal",
        "debate_moderator": "moderator",
        "synthesis": "synthesis",
        "scenario_analysis": "scenario",
    }
    # Store final reports for retrieval
    _tracker._stage_reports = {}
    _tracker._final_rating = None

    # 后台线程实际执行的分析主体:构建图 → 流式驱动 → 汇总报告 → 事后校验。
    # 【功能】后台线程主体:构建图、流式驱动、汇总报告、事后校验(预测背离时写进化记忆)。
    def run_analysis():
        global _tracker
        try:
            # 构建 LangGraph 多分析师图;enable_feedback=False 表示本轮不要求用户反馈。
            app_graph, _ = build_commodity_graph(
                config, enable_feedback=False, include_sentiment=include_sentiment  # 【调用函数】构建 LangGraph 多分析师图
            )
            evo_ctx = get_evolution_context(symbol)  # 【调用函数】读取该品种历史进化记忆
            initial_state = {  # 【变量】LangGraph 初始状态(分析流水线的输入骨架,含会话与各报告槽位)
                "messages": [HumanMessage(content=f"Analyze {symbol} as of {trade_date}.")],
                "company_of_interest": symbol,
                "asset_type": "commodity_futures",
                "trade_date": trade_date,
                "past_context": evo_ctx,
                "technical_report": "",
                "fundamental_report": "",
                "macro_report": "",
                "sentiment_report": "",
                "discussion_summary": "",
                "user_feedback_summary": "",
                "investment_plan": "",
                "final_trade_decision": "",
                "scenario_analysis": "",
                "debate_state": {
                    "bull_history": "",
                    "bear_history": "",
                    "bull_last": "",
                    "bear_last": "",
                    "round": 0,
                },
            }
            final_state = {}
            # 以 "updates" 模式逐步驱动图,每步产出 {节点名: 该节点输出}。
            # 每步之前检查停止标记与暂停事件,使前端"停止/暂停"能即时生效。
            for chunk in app_graph.stream(initial_state, stream_mode="updates"):  # 【调用函数】以 updates 模式逐步驱动 Agent 图
                if _tracker.stop_requested:
                    break
                _tracker.wait_if_paused()
                for node_name, node_data in chunk.items():
                    if not node_data:
                        continue
                    if isinstance(node_data, dict):
                        final_state.update(node_data)
                    sid = stage_map.get(node_name)
                    if sid and sid not in _tracker.completed_stages:
                        _tracker.mark_stage_done(sid)
                    # Store reports
                    if node_name in (
                        "technical_analyst",
                        "fundamental_analyst",
                        "macro_analyst",
                        "sentiment_analyst",
                    ):
                        key = node_name.replace("_analyst", "") + "_report"
                        _tracker._stage_reports[node_name] = node_data.get(key, "")[:5000]
                    elif node_name == "debate_moderator":
                        _tracker._stage_reports[node_name] = node_data.get(
                            "discussion_summary", ""
                        )[:5000]
                    elif node_name == "synthesis":
                        syn = node_data.get("investment_plan", "")
                        _tracker._stage_reports[node_name] = syn[:5000]
                        m = re.search(
                            r"RATING:\s*(.+?)\s*\|\s*CONFIDENCE:\s*(.+?)\s*\|\s*SCORE:\s*(\d+)", syn
                        )
                        if m:
                            _tracker._final_rating = {
                                "rating": m.group(1).strip(),
                                "confidence": m.group(2).strip(),
                                "score": int(m.group(3)),
                            }
                    elif node_name == "scenario_analysis":
                        _tracker._stage_reports[node_name] = node_data.get("scenario_analysis", "")[
                            :5000
                        ]
                    elif node_name in ("bull_opening", "bear_refute", "bull_rebuttal"):
                        _tracker._stage_reports[node_name] = node_data.get("debate_state", {}).get(
                            "bull_last" if "bull" in node_name else "bear_last", ""
                        )[:3000]
                    _tracker.update_stats(llm=_tracker.llm_calls + 1)
            _tracker._stage_reports["_final_state"] = final_state
            _tracker.mark_complete(final_state)

            # 把本次分析落盘为历史报告(与 CLI 相同格式),供 /api/history 与
            # /api/report/<file> 读取。此前 Web 分析从不写盘,历史报告因此停更。
            # 落盘失败只记日志,不阻断分析完成状态。
            try:
                _persist_analysis_report(symbol, trade_date, final_state, elapsed=_tracker.elapsed)
            except Exception:
                logger.exception("Failed to persist analysis report for %s", symbol)

            # 事后校验:把 Agent 预测方向与真实行情走势对比;若背离则把该案例
            # 存入进化记忆(store_prediction),供后续轮次学习。失败不影响分析完成。
            # Post-mortem: check if prediction diverged from actual outcome
            try:
                outcome = _get_actual_outcome(symbol, trade_date, horizon_days=5)
                if outcome and _tracker._final_rating:
                    agent_dir = _tracker._final_rating.get("rating", "")
                    actual_dir = outcome.get("direction", "")
                    # Map Chinese rating to BULL/BEAR
                    if "看多" in agent_dir:
                        agent_dir = "BULL"
                    elif "看空" in agent_dir:
                        agent_dir = "BEAR"
                    else:
                        agent_dir = "HOLD"
                    diverged = (
                        (agent_dir != actual_dir) and agent_dir != "HOLD" and actual_dir != "HOLD"
                    )
                    if diverged:
                        # Store divergence for learning
                        from tradingagents.dataflows.evolution_memory import (  # 【调用包】进化记忆存储(背离学习)
                            store_prediction,
                        )

                        store_prediction(  # 【调用函数】把背离案例写入进化记忆,供后续轮次学习
                            symbol,
                            trade_date,
                            _tracker._final_rating.get("rating", "?"),
                            _tracker._final_rating.get("confidence", "?"),
                            _tracker._final_rating.get("score", 5),
                        )
                        # Flag the divergence
                        _tracker._stage_reports["_divergence"] = {
                            "agent_direction": agent_dir,
                            "actual_direction": actual_dir,
                            "actual_pct": outcome.get("pct_change", 0),
                            "note": f"Agent predicted {agent_dir} but market moved {actual_dir} ({outcome.get('pct_change', 0):+.2f}%). This case has been saved for learning.",
                        }
            except Exception:
                pass  # Non-critical, don't block analysis completion
        except Exception as e:
            # 任何异常都转成 mark_error,避免后台线程静默死亡。
            _tracker.mark_error(str(e))

    # 启动后台线程执行分析(daemon=True,主进程退出时随之终止),接口立即返回。
    t = threading.Thread(target=run_analysis, daemon=True)
    t.start()

    return jsonify(
        {
            "status": "started",
            "include_sentiment": include_sentiment,
            "stages": [s["id"] for s in stages],
        }
    )


# ── Pause / Resume / Stop endpoints ───────────────────────────────────────


# 【功能】暂停正在运行的分析。
# 【返回】成功: 200 {"status": "paused", "progress": 最新进度};
#   无任务可暂停时: 400 {"status": "not_running"}。
@app.route("/api/pause", methods=["POST"])
def api_pause():
    global _tracker
    if _tracker and _tracker.pause():
        return jsonify({"status": "paused", "progress": _tracker.to_dict()})
    return jsonify({"status": "not_running"}), 400


# 【功能】继续被暂停的分析。
# 【返回】成功: 200 {"status": "resumed", "progress": 最新进度};
#   当前未暂停时: 400 {"status": "not_paused"}。
@app.route("/api/resume", methods=["POST"])
def api_resume():
    global _tracker
    if _tracker and _tracker.resume():
        return jsonify({"status": "resumed", "progress": _tracker.to_dict()})
    return jsonify({"status": "not_paused"}), 400


# 【功能】请求停止分析(设置停止标记,分析线程会在下一个节点退出)。
# 【返回】成功: 200 {"status": "stopping", "progress": 最新进度};
#   无运行任务时: 400 {"status": "not_running"}。
@app.route("/api/stop", methods=["POST"])
def api_stop():
    global _tracker
    if _tracker and _tracker.request_stop():
        return jsonify({"status": "stopping", "progress": _tracker.to_dict()})
    return jsonify({"status": "not_running"}), 400


# 【功能】轮询当前分析进度(前端每 0.5~1 秒调用一次)。
# 【返回】ProgressTracker.to_dict() 的完整结构,并附加 "rating"(最终评级,未出则为 None);
#   从未启动过分析时返回 {"is_running": False, "is_complete": False}。
@app.route("/api/progress")
def api_progress():
    """Get current analysis progress."""
    global _tracker
    if _tracker:
        d = _tracker.to_dict()
        d["rating"] = getattr(_tracker, "_final_rating", None)
        return jsonify(d)
    return jsonify({"is_running": False, "is_complete": False})


# 【功能】用户与 Agent 的"辩论"接口:用户发消息,Agent 结合进化记忆与当前分析作答。
# 【请求体】{"symbol": "RB", "message": "...",
#             "history": [{"role": "user"|"agent", "content": "..."}]}
# 【返回】{"reply": "Agent 回复(截断 2000 字)"};空消息返回 400;LLM 异常也返回 200,
#   但 reply 内容为错误提示。
# 【关键】若最近一次分析已完成,把综合研判结论(RATING + 投资计划前 1500 字)拼进提示词,
#   使 Agent 辩论有上下文;history 只取最后 6 轮。
@app.route("/api/feedback", methods=["POST"])
def api_feedback():
    """Simple debate: user sends message, Agent responds using evolution memory context."""
    data = request.json or {}
    symbol = data.get("symbol", "RB")
    user_msg = data.get("message", "")
    history = data.get("history", [])  # [{role: "user"|"agent", content: "..."}]

    if not user_msg.strip():
        return jsonify({"error": "Empty message"}), 400

    # Build debate prompt with current analysis + evolution context
    evo_ctx = get_evolution_context(symbol)  # 【调用函数】读取进化记忆作为辩论背景
    # Include current analysis results if available
    current_analysis = ""
    if _tracker and _tracker.is_complete:
        reports = getattr(_tracker, "_stage_reports", {})
        syn = reports.get("synthesis", "")
        rating = getattr(_tracker, "_final_rating", {})
        if syn:
            current_analysis = f"## Current Analysis Summary\nRATING: {rating.get('rating', '?')} | CONFIDENCE: {rating.get('confidence', '?')}\n{syn[:1500]}\n\n"
    debate_prompt = (
        f"You are an expert commodity futures analyst. A user is debating your analysis of {symbol}.\n\n"
        f"{current_analysis}"
        f"Past learning context:\n{evo_ctx[:1500] if evo_ctx else 'No prior learning.'}\n\n"
        f"Debate rules: Be data-driven. Distinguish facts from opinions. "
        f"Push back against unsupported claims. Be open to being wrong. "
        f"If the user makes a good point, acknowledge it. "
        f"Respond in Chinese, 100-300 words. Be conversational.\n\n"
        f"User's message: {user_msg}"
    )

    # Add chat history
    if history:
        history_text = "\n".join(
            [
                f"{'User' if h['role'] == 'user' else 'Agent'}: {h['content'][:500]}"
                for h in history[-6:]
            ]
        )
        debate_prompt += f"\n\nRecent conversation:\n{history_text}"

    try:
        client = create_llm_client(
            config["llm_provider"],
            config.get("quick_think_llm", config["deep_think_llm"]),  # 【调用函数】按配置创建 LLM 客户端
        )
        llm = client.get_llm()
        result = llm.invoke(debate_prompt)  # 【调用函数】调用 LLM 生成辩论回复
        reply = result.content if hasattr(result, "content") else str(result)
        return jsonify({"reply": reply[:2000]})
    except Exception as e:
        return jsonify({"reply": f"Agent unavailable: {str(e)[:200]}"})


# 【功能】分析完成后取最终报告与评级(前端"查看结果"按钮调用)。
# 【返回】{"ready": true, "reports": {节点: 报告, ...}, "rating": {...},
#           "predicted_magnitude": 预测涨跌幅度} 或 {"ready": false}(尚未完成)。
# 【关键】predicted_magnitude 由 SCORE 换算 (score-5)*0.5(5=中性,0/10=极端);
#   若综合研判文本里有 "预测/目标/预期(涨幅|跌幅|幅度|变化): X%" 则优先用该数值。
@app.route("/api/analysis_results")
def api_analysis_results():
    """Get stored analysis reports and debate after completion."""
    global _tracker
    if not _tracker or not _tracker.is_complete:
        return jsonify({"ready": False})
    reports = getattr(_tracker, "_stage_reports", {})
    rating = getattr(_tracker, "_final_rating", None)
    # Compute predicted magnitude from SCORE (5=neutral, 0/10=extreme)
    predicted_magnitude = None
    if rating and rating.get("score"):
        predicted_magnitude = round((rating["score"] - 5) * 0.5, 1)
    # Also try to extract from synthesis text
    syn_text = reports.get("synthesis", "")
    mag_match = re.search(
        r"(?:预测|目标|预期)(?:涨幅|跌幅|幅度|变化)[：:]\s*([+-]?\d+\.?\d*)\s*%", syn_text
    )
    if mag_match:
        predicted_magnitude = float(mag_match.group(1))
    return jsonify(
        {
            "ready": True,
            "reports": {
                k: v for k, v in reports.items() if not k.startswith("_") or k == "_divergence"
            },
            "rating": rating,
            "predicted_magnitude": predicted_magnitude,
        }
    )


# ── PDF / Markdown Export ──────────────────────────────────────────────────


# 【功能】用 fpdf2 把 Markdown 报告渲染成 PDF(内存字节流,不落盘)。
# 【参数】content: Markdown 文本; filename: 文件名(仅用于页眉显示);
#   rating: 评级 dict(可选,非空则打印在标题下方)。
# 【关键逻辑】中文字体需手动注册,按 Windows/Linux/Mac 常见路径逐一探测 CJK 字体;
#   找不到则退回 Helvetica(只能渲染 ASCII,中文会乱码)。正文只取前 500 行、每行截 200 字符。
def _generate_pdf(content, filename, rating=None):
    """Generate a PDF from markdown report using fpdf2."""
    from fpdf import FPDF  # 【调用包】PDF 生成库(fpdf2)

    pdf = FPDF()
    pdf.add_page()

    # Try to use a CJK font
    cjk_font = None
    font_candidates = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simsun.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ]
    for fp in font_candidates:
        if os.path.exists(fp):
            try:
                pdf.add_font("CJK", "", fp, uni=True)
                cjk_font = "CJK"
                break
            except Exception:
                continue

    if cjk_font:
        pdf.set_font(cjk_font, "", 10)
    else:
        # Fallback: ASCII only, strip non-ASCII
        pdf.set_font("Helvetica", "", 10)

    # Title
    pdf.set_font_size(18)
    pdf.cell(0, 12, "FuturesMind Analysis Report", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_font_size(10)
    pdf.cell(0, 8, f"File: {filename}", new_x="LMARGIN", new_y="NEXT", align="C")

    if rating:
        pdf.set_font_size(14)
        rating_text = f"RATING: {rating.get('rating', '?')} | CONFIDENCE: {rating.get('confidence', '?')} | SCORE: {rating.get('score', '?')}/10"
        pdf.cell(0, 10, rating_text, new_x="LMARGIN", new_y="NEXT", align="C")

    pdf.ln(8)

    # Content (basic md → text conversion)
    pdf.set_font_size(9)
    lines = content.split("\n")
    for line in lines[:500]:  # Limit to 500 lines
        line = re.sub(r"[#*_`~>|]", "", line).strip()
        if not line:
            pdf.ln(4)
            continue
        if cjk_font:
            pdf.set_font(cjk_font, "", 9)
        with contextlib.suppress(Exception):
            pdf.multi_cell(0, 5, line[:200])

    # Footer
    pdf.ln(8)
    pdf.set_font_size(7)
    disclaimer = (
        "Disclaimer: This report is AI-generated for research purposes only. Not financial advice."
    )
    pdf.cell(0, 5, disclaimer, new_x="LMARGIN", new_y="NEXT", align="C")

    return pdf.output()


# 【功能】下载报告 PDF 文件。
# 【安全】os.path.basename 防路径穿越;文件不存在返回 404。
# 【返回】application/pdf 附件流;生成失败返回 500。
@app.route("/api/report/<path:filename>/pdf")
def api_report_pdf(filename):
    """Download a report as PDF."""
    safe_name = os.path.basename(filename)
    fpath = REPORT_DIR / safe_name
    if not fpath.exists():
        return jsonify({"error": "Not found"}), 404

    with open(fpath, encoding="utf-8") as f:
        content = f.read()

    # Extract rating
    rating_match = re.search(
        r"RATING:\s*(.+?)\s*\|\s*CONFIDENCE:\s*(.+?)\s*\|\s*SCORE:\s*(\d+)", content
    )
    rating = None
    if rating_match:
        rating = {
            "rating": rating_match.group(1).strip(),
            "confidence": rating_match.group(2).strip(),
            "score": int(rating_match.group(3)),
        }

    try:
        pdf_data = _generate_pdf(content, safe_name, rating)
        return send_file(
            io.BytesIO(pdf_data),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"{safe_name.replace('.md', '')}.pdf",
        )
    except Exception as e:
        return jsonify({"error": f"PDF generation failed: {e}"}), 500


# 【功能】下载报告原始 Markdown 文件(作为附件)。
# 【返回】text/markdown 附件流;文件不存在返回 404。
@app.route("/api/report/<path:filename>/md")
def api_report_md(filename):
    """Download a report as Markdown."""
    safe_name = os.path.basename(filename)
    fpath = REPORT_DIR / safe_name
    if not fpath.exists():
        return jsonify({"error": "Not found"}), 404

    return send_file(fpath, mimetype="text/markdown", as_attachment=True, download_name=safe_name)


# ── Config endpoint ───────────────────────────────────────────────────────


# 【功能】读写前端界面配置(主题、LLM provider、模型名)。
#   GET: 返回合并默认值后的配置(默认值来自环境变量,前端保存值优先)。
#   POST: 把前端传来的配置保存到 ~/.tradingagents/web_config.json。
@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    """Read/write web UI configuration."""
    if request.method == "GET":
        cfg = _load_web_config()
        # Merge with defaults
        defaults = {
            "llm_provider": os.environ.get("TRADINGAGENTS_LLM_PROVIDER", "deepseek"),
            "deep_think_llm": os.environ.get("TRADINGAGENTS_DEEP_THINK_LLM", "deepseek-v4-pro"),
            "quick_think_llm": os.environ.get("TRADINGAGENTS_QUICK_THINK_LLM", "deepseek-v4-pro"),
            "theme": cfg.get("theme", "dark"),
        }
        defaults.update(cfg)
        return jsonify(defaults)

    cfg = request.json or {}
    _save_web_config(cfg)
    return jsonify({"status": "saved"})


# ── Data update pipeline (SSE) ─────────────────────────────────────────────


# 【功能】一键数据更新流水线:采集 → 解析粉丝数 → 情绪聚合 → 回测 →
#   生成 TradingAgents JSON → 更新价格 → 平台统计。
# 【请求体】{"per_kw": 每关键词采集条数(默认15), "min_notes": 最少帖子数(默认3),
#             "platforms": ["weibo"], "since_date": "YYYY-MM-DD"(可选)}
# 【返回】SSE 事件流(text/event-stream)。事件类型:
#   · {"type": "step", "step", "label", "progress"} —— 阶段切换
#   · {"type": "log", "msg"} —— 进度日志
#   · {"type": "platform_summary", ...} —— 平台统计汇总
#   · {"type": "complete", "msg"} —— 全部完成
# 【关键逻辑】generate() 是生成器,每个 yield 就是一条 SSE 消息;
#   采集 / 更新价格用 subprocess 调用思路2项目脚本(超时 600 秒)。
@app.route("/api/update_data", methods=["POST"])
def api_update_data():
    """One-click pipeline: collect → fix_fans → aggregate → backtest → regenerate."""
    data = request.json or {}
    per_kw = data.get("per_kw", 15)
    min_notes = data.get("min_notes", 3)
    platforms = data.get("platforms", ["weibo"])
    since_date = data.get("since_date", "")  # YYYY-MM-DD or empty

    # 【功能】SSE 生成器:依次产出 7 个阶段的 step 事件与 log 事件,前端据此渲染流水线进度。
    def generate():
        steps = [
            ("collect", "采集数据"),
            ("fix_fans", "解析粉丝数"),
            ("aggregate", "情绪聚合"),
            ("backtest", "回测优化"),
            ("regenerate", "生成TradingAgents JSON"),
            ("update_price", "更新价格数据"),
            ("platform_summary", "平台统计"),
        ]
        total = len(steps)  # 【变量】流水线阶段总数(用于进度显示)

        # 依次执行每个阶段;每阶段先推一条 "step" 消息,再推若干 "log" 消息。
        for i, (key, label) in enumerate(steps):
            payload = json.dumps(
                {"type": "step", "step": key, "label": label, "progress": f"{i + 1}/{total}"},
                ensure_ascii=False,
            )
            yield f"data: {payload}\n\n"

            try:
                if key == "collect":
                    yield f"data: {json.dumps({'type': 'log', 'msg': f'Starting collection (per_kw={per_kw}, platforms={platforms})...'}, ensure_ascii=False)}\n\n"
                    if not THINK2_DIR or not THINK2_DIR.exists():
                        yield f"data: {json.dumps({'type': 'log', 'msg': 'ERROR: THINK2 directory not found'}, ensure_ascii=False)}\n\n"
                        continue

                    import subprocess  # 【调用包】子进程调用(运行采集脚本)

                    venv_py = os.path.join(os.path.dirname(sys.executable), "python")

                    # 【关键逻辑】采集子进程在后台线程里跑, 主生成器只负责轮询推进度,
                    # 避免整条 SSE 流被 subprocess.run 阻塞住(前端"1/7 采集数据"看起来像卡住)。
                    # 每个平台一个 batch_collect.py 子进程顺序执行; 单平台失败(凭据缺失/超时)
                    # 记入 results 后继续下一个, 不影响其他平台。
                    # 一个关键词对应一个 batch_{平台}_{时间戳}.jsonl, 每 4 秒对比一次输出目录,
                    # 有新批次文件就实时推一条 log(完成几个关键词/最新文件多少条);
                    # 若超过 15 秒无新文件, 推一条心跳消息保持"活着"的观感。子进程结束(含
                    # 600 秒超时转异常)后再把最终 stdout/stderr 里关键行推出去。
                    pre_batches = set(glob.glob(str(THINK2_OUTPUT / "batch_*.jsonl")))  # 【变量】采集前已有批次文件集合(用于发现新文件)
                    holder = {}  # 【变量】跨线程容器:各平台子进程结果写 holder["res"],异常写 holder["err"]

                    # 【功能】在后台线程里顺序跑各平台采集子进程,结果/异常写入 holder 容器,避免阻塞 SSE 生成器。
                    def _run_collect(venv_py=venv_py, holder=holder):
                        results = []  # 【变量】各平台采集结果列表: [(platform, subprocess.CompletedProcess|None), ...]
                        for _p in platforms:  # 【关键逻辑】多平台顺序采集(原只采 platforms[0])
                            _cmd = [
                                venv_py,
                                "batch_collect.py",
                                "--platform",
                                _p,
                                "--per-kw",
                                str(per_kw),
                                "--turbo",
                                "--no-detail",
                            ]
                            if since_date:
                                _cmd.extend(["--since", since_date])
                            try:
                                results.append(
                                    (_p, subprocess.run(  # 【调用函数】后台线程里运行思路2采集脚本 batch_collect.py(单平台)
                                        _cmd,
                                        cwd=str(THINK2_DIR),
                                        capture_output=True,
                                        text=True,
                                        timeout=600,  # 【变量】单平台采集子进程超时上限 600 秒
                                    ))
                                )
                            except Exception as e:  # noqa: BLE001
                                results.append((_p, None))  # 单平台失败: 记为 None, 继续下一平台
                                holder["errs"] = holder.get("errs", []) + [f"{_p}: {e}"]
                        holder["res"] = results

                    th = threading.Thread(target=_run_collect, daemon=True)
                    th.start()

                    start_ts = time.time()  # 【变量】采集启动时间戳(用于心跳等待时长)
                    last_count = 0  # 【变量】已上报的批次文件数(避免同一文件重复上报)
                    last_report_ts = start_ts  # 【变量】上次上报日志的时间戳(超 15s 无进展则发心跳)
                    while th.is_alive():
                        time.sleep(4)
                        new_batches = sorted(
                            set(glob.glob(str(THINK2_OUTPUT / "batch_*.jsonl"))) - pre_batches
                        )
                        now_ts = time.time()
                        # 批次计数变化才推消息(避免同一文件被重复上报刷屏);
                        # 计数不变但超过 15 秒无进展则推一条心跳消息保持"活着"的观感。
                        if len(new_batches) != last_count:
                            last_count = len(new_batches)
                            last_report_ts = now_ts
                            if new_batches:
                                latest = new_batches[-1]
                                n_notes = 0
                                try:
                                    with open(latest, encoding="utf-8", errors="ignore") as f:
                                        n_notes = sum(1 for _ in f)
                                except OSError:
                                    n_notes = 0
                                msg = (
                                    f"[采集中] 已完成 {len(new_batches)} 个关键词批次, "
                                    f"最新文件 {os.path.basename(latest)} ({n_notes} 条)"
                                )
                                yield f"data: {json.dumps({'type': 'log', 'msg': msg}, ensure_ascii=False)}\n\n"
                        elif now_ts - last_report_ts >= 15:
                            last_report_ts = now_ts
                            waited = int(now_ts - start_ts)
                            yield f"data: {json.dumps({'type': 'log', 'msg': f'[采集中] 关键词批处理进行中, 已等待 {waited}s (完成批次: {last_count})...'}, ensure_ascii=False)}\n\n"

                    th.join()
                    for _perr in holder.get("errs", []):
                        yield f"data: {json.dumps({'type': 'log', 'msg': f'collect 单平台失败(已跳过): {_perr}'}, ensure_ascii=False)}\n\n"
                    for _p, result in holder.get("res", []):
                        if result is None:
                            continue  # 平台失败已在 errs 里单独上报
                        lines = (result.stdout or "").split("\n") + (result.stderr or "").split("\n")
                        for line in lines:
                            if any(
                                kw in line.lower()
                                for kw in [
                                    "complete", "total notes", "done", "error",
                                    "采集失败", "凭证", "重新登录", "未采集到",
                                ]
                            ):
                                yield f"data: {json.dumps({'type': 'log', 'msg': line.strip()[:200]}, ensure_ascii=False)}\n\n"

                elif key == "fix_fans":
                    yield f"data: {json.dumps({'type': 'log', 'msg': 'Parsing fan count strings...'}, ensure_ascii=False)}\n\n"
                    if not THINK2_DIR or not THINK2_DIR.exists():
                        continue
                    sys.path.insert(0, str(THINK2_DIR))
                    from platforms.weibo_adapter import (
                        _parse_fans_count,  # 【调用包】微博适配器:粉丝数字符串解析
                    )

                    fixed_total = 0
                    for bf in sorted(THINK2_OUTPUT.glob("batch_*.jsonl")):
                        lines_out = []
                        file_fixed = 0
                        with open(bf, encoding="utf-8") as f:
                            for line in f:
                                if not line.strip():
                                    continue
                                d = json.loads(line)
                                raw = d.get("author_fans", 0)
                                if isinstance(raw, str):
                                    d["author_fans"] = _parse_fans_count(raw)  # 【调用函数】把粉丝数字符串(如"1.2万")解析为整数
                                    file_fixed += 1
                                lines_out.append(json.dumps(d, ensure_ascii=False))
                        if file_fixed:
                            with open(bf, "w", encoding="utf-8") as f:
                                f.write("\n".join(lines_out) + "\n")
                            fixed_total += file_fixed
                    yield f"data: {json.dumps({'type': 'log', 'msg': f'Fixed {fixed_total} author_fans records'}, ensure_ascii=False)}\n\n"

                elif key == "aggregate":
                    yield f"data: {json.dumps({'type': 'log', 'msg': 'Running sentiment aggregation (author-weighted)...'}, ensure_ascii=False)}\n\n"
                    if not THINK2_DIR or not THINK2_DIR.exists():
                        continue
                    sys.path.insert(0, str(THINK2_DIR))
                    from trend_aggregator import aggregate  # 【调用包】跨平台情绪聚合(作者加权)

                    paths = sorted(glob.glob(str(THINK2_OUTPUT / "batch_*.jsonl")))
                    result = aggregate(paths)  # 【调用函数】跨平台情绪聚合(作者加权)
                    yield f"data: {json.dumps({'type': 'log', 'msg': f'Aggregated {len(result)} varieties'}, ensure_ascii=False)}\n\n"
                    top = sorted(
                        result.items(),
                        key=lambda x: x[1].get("stats", {}).get("total_notes", 0),
                        reverse=True,
                    )[:5]
                    for vname, vdata in top:
                        s = vdata.get("stats", {})
                        notes = s.get("total_notes", 0)
                        authors = s.get("unique_authors", 0)
                        msg = f"  {vname}: {notes} notes, {authors} authors"
                        payload = json.dumps({"type": "log", "msg": msg}, ensure_ascii=False)
                        yield f"data: {payload}\n\n"

                elif key == "backtest":
                    yield f"data: {json.dumps({'type': 'log', 'msg': 'Running backtest (multi-horizon)...'}, ensure_ascii=False)}\n\n"
                    if not THINK2_DIR or not THINK2_DIR.exists():
                        continue
                    sys.path.insert(0, str(THINK2_DIR))
                    from backtest_weights import run_all  # 【调用包】多周期回测权重优化

                    result_b = run_all(min_points=10, horizons=[1, 3, 5])  # 【调用函数】多周期回测,优化平台权重
                    gb = result_b.get("global_backtest", {})
                    h1 = gb.get("results_by_horizon", {}).get("h1", {})
                    sc = h1.get("signal_comparison", {})
                    aw = sc.get("author_weighted", {})
                    acc = aw.get("direction_accuracy", 0)
                    n = aw.get("data_points", 0)
                    yield f"data: {json.dumps({'type': 'log', 'msg': f'Backtest done: author_weighted acc={acc:.1%} (n={n})'}, ensure_ascii=False)}\n\n"

                elif key == "regenerate":
                    yield f"data: {json.dumps({'type': 'log', 'msg': 'Generating TradingAgents sentiment JSONs...'}, ensure_ascii=False)}\n\n"
                    if not THINK2_DIR or not THINK2_DIR.exists():
                        continue
                    sys.path.insert(0, str(THINK2_DIR))
                    from generate_tradingagents_sentiment import (  # 【调用包】生成 TradingAgents 情绪 JSON
                        OUTPUT_DIR as GEN_OUTPUT,
                        generate_sentiment_json,
                        load_trends_data,
                    )

                    varieties, index, global_weights = load_trends_data(THINK2_TRENDS)  # 【调用函数】读取趋势数据与全局权重
                    GEN_OUTPUT.mkdir(parents=True, exist_ok=True)
                    gen_count = 0
                    for vname in sorted(varieties.keys()):
                        output = generate_sentiment_json(
                            vname, varieties[vname], index, global_weights  # 【调用函数】生成单品种 TradingAgents 情绪 JSON
                        )
                        if output is None:
                            continue
                        if output["data"]["social_sentiment"]["total_posts_analyzed"] < min_notes:
                            continue
                        sym = output["variety"]
                        with open(GEN_OUTPUT / f"{sym}_sentiment.json", "w", encoding="utf-8") as f:
                            json.dump(output, f, ensure_ascii=False, indent=2)  # 【调用函数】情绪 JSON 落盘到输出目录
                        gen_count += 1
                    yield f"data: {json.dumps({'type': 'log', 'msg': f'Generated {gen_count} varieties (min_notes={min_notes})'}, ensure_ascii=False)}\n\n"

                elif key == "update_price":
                    yield f"data: {json.dumps({'type': 'log', 'msg': 'Fetching latest prices via AKShare...'}, ensure_ascii=False)}\n\n"
                    if THINK2_DIR and THINK2_DIR.exists():
                        import subprocess  # 【调用包】子进程调用(运行行情更新脚本)

                        venv_py = os.path.join(os.path.dirname(sys.executable), "python")
                        result = subprocess.run(
                            [venv_py, "price_fetcher.py"],  # 【调用函数】调用思路2的行情更新脚本
                            cwd=str(THINK2_DIR),
                            capture_output=True,
                            text=True,
                            timeout=120,  # 【变量】价格更新子进程超时上限 120 秒
                        )
                        for line in (result.stdout + result.stderr).split("\n"):
                            if any(
                                kw in line
                                for kw in ["Fetching", "fetched", "Done", "Error", "Updated"]
                            ):
                                yield f"data: {json.dumps({'type': 'log', 'msg': line.strip()[:200]}, ensure_ascii=False)}\n\n"
                        yield f"data: {json.dumps({'type': 'log', 'msg': 'Price data updated. Latest: 2026-07-21'}, ensure_ascii=False)}\n\n"
                    else:
                        yield f"data: {json.dumps({'type': 'log', 'msg': 'THINK2 directory not found, skipping price update'}, ensure_ascii=False)}\n\n"

                elif key == "platform_summary":
                    yield f"data: {json.dumps({'type': 'log', 'msg': 'Counting posts by platform...'}, ensure_ascii=False)}\n\n"
                    if not THINK2_OUTPUT or not THINK2_OUTPUT.exists():
                        yield f"data: {json.dumps({'type': 'log', 'msg': 'No output directory found'}, ensure_ascii=False)}\n\n"
                        continue

                    # Scan ALL batch JSONL files and count by platform
                    platform_counts = {}
                    total_posts = 0
                    total_seen = set()
                    earliest_time = None
                    latest_time = None

                    jsonl_files = sorted(glob.glob(str(THINK2_OUTPUT / "batch_*.jsonl")))
                    for bf in jsonl_files:
                        try:
                            with open(bf, encoding="utf-8") as f:
                                for line in f:
                                    if not line.strip():
                                        continue
                                    d = json.loads(line)
                                    nid = d.get("note_id", "")
                                    if nid in total_seen:
                                        continue
                                    total_seen.add(nid)
                                    # platform 字段缺失时按 url 域名兜底推断(2026-07 早期数据缺该字段)
                                    plat = d.get("platform") or _infer_platform(d.get("url", ""))
                                    platform_counts[plat] = platform_counts.get(plat, 0) + 1
                                    total_posts += 1
                                    pt = (d.get("publish_time", "") or "")[:10]
                                    if pt:
                                        if not earliest_time or pt < earliest_time:
                                            earliest_time = pt
                                        if not latest_time or pt > latest_time:
                                            latest_time = pt
                        except Exception:
                            pass

                    # Send structured summary
                    summary = {
                        "type": "platform_summary",
                        "platforms": platform_counts,
                        "total": total_posts,
                        "earliest": earliest_time or "?",
                        "latest": latest_time or "?",
                    }
                    yield f"data: {json.dumps(summary, ensure_ascii=False)}\n\n"

                    # Also as log messages
                    q = "?"
                    yield f"data: {json.dumps({'type': 'log', 'msg': '── 平台数据汇总 ──'}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'type': 'log', 'msg': f'总计: {total_posts} 条 (去重后), 覆盖 {earliest_time or q} ~ {latest_time or q}'}, ensure_ascii=False)}\n\n"
                    for plat, count in sorted(platform_counts.items(), key=lambda x: -x[1]):
                        pct = f"({count / total_posts * 100:.0f}%)" if total_posts else ""
                        yield f"data: {json.dumps({'type': 'log', 'msg': f'  {plat}: {count} 条 {pct}'}, ensure_ascii=False)}\n\n"

            except Exception as e:
                yield f"data: {json.dumps({'type': 'log', 'msg': f'ERROR in {key}: {e}'}, ensure_ascii=False)}\n\n"

        yield f"data: {json.dumps({'type': 'complete', 'msg': 'Data update pipeline complete'}, ensure_ascii=False)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ═══════════════════════════════════════════════════════════════════
# P0: Database-backed stats & scheduler control
# P0 数据库统计与调度器控制(/api/db/*、/api/scheduler/*)。
# ═══════════════════════════════════════════════════════════════════


# 【功能】返回数据库统计:各平台帖子数、总帖子数、最近采集历史、未确认告警数。
@app.route("/api/db/stats")
def api_db_stats():
    """Get database stats: posts by platform, total counts."""
    db = get_db()  # 【调用函数】获取 SQLite 数据库会话
    return jsonify(
        {
            "platforms": db.get_platform_stats(),  # 【调用函数】各平台帖子数统计
            "total_posts": db.get_total_posts(),  # 【调用函数】帖子总量
            "collection_history": db.get_collection_history(10),  # 【调用函数】最近 10 次采集历史
            "unacknowledged_alerts": db.get_unacknowledged_count(),  # 【调用函数】未确认告警数
        }
    )


# 【功能】查询告警列表。
# 【参数】limit=条数(默认50);unacknowledged=1 时只返回未确认告警。
# 【返回】告警列表 JSON。
@app.route("/api/db/alerts")
def api_db_alerts():
    limit = request.args.get("limit", 50, type=int)
    unack = request.args.get("unacknowledged", 0, type=int)
    alerts = get_db().get_alerts(limit=limit, unacknowledged_only=bool(unack))
    return jsonify(alerts)


# 【功能】把指定 ID 的告警标记为已确认。
@app.route("/api/db/alerts/<int:alert_id>/ack", methods=["POST"])
def api_ack_alert(alert_id):
    get_db().acknowledge_alert(alert_id)
    return jsonify({"status": "ok"})


# 【功能】查询后台调度器(APScheduler)运行状态与任务列表。
# 【关键】import 放在函数内,避免模块启动时 scheduler 尚未初始化而报错。
@app.route("/api/scheduler/status")
def api_scheduler_status():
    """Get scheduler status."""
    try:
        from scheduler import _scheduler  # 【调用包】调度器实例(读取运行状态)

        if _scheduler and _scheduler.running:
            jobs = [
                {
                    "id": j.id,
                    "name": j.name,
                    "next_run": str(j.next_run_time) if j.next_run_time else "?",
                }
                for j in _scheduler.get_jobs()
            ]
            return jsonify({"running": True, "jobs": jobs})
        return jsonify({"running": False, "jobs": []})
    except Exception as e:
        return jsonify({"running": False, "error": str(e)})


# 【功能】启动调度器。
# 【请求体】{"schedule_times": ["08:00", "18:00"]}(可选,缺省这两个时间点)。
# 【返回】{"status": "started", "schedule_times": [...]}。
@app.route("/api/scheduler/start", methods=["POST"])
def api_scheduler_start():
    try:
        from scheduler import start_scheduler  # 【调用包】调度器启动

        data = request.json or {}
        times = data.get("schedule_times", ["08:00", "18:00"])
        start_scheduler(schedule_times=times)
        return jsonify({"status": "started", "schedule_times": times})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# 【功能】停止调度器。
# 【返回】{"status": "stopped"}。
@app.route("/api/scheduler/stop", methods=["POST"])
def api_scheduler_stop():
    try:
        from scheduler import stop_scheduler  # 【调用包】调度器停止

        stop_scheduler()
        return jsonify({"status": "stopped"})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# ── P0: Auth ──────────────────────────────────────────────────────
# P0 鉴权:登录 / 登出 / 状态 + _auth_required 装饰器。


# 【装饰器】简单 Token 鉴权:从请求头 X-Auth-Token 或 Cookie auth_token 取 token,
# 不在 _auth_tokens 集合中则返回 401;否则放行原视图函数。
def _auth_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        token = request.headers.get("X-Auth-Token", "") or request.cookies.get("auth_token", "")
        if not token or token not in _auth_tokens:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)

    return wrapper


# 内存中有效登录 token 集合(登录时加入,登出时移除;重启后清空)。
_auth_tokens: set[str] = set()  # 【变量】内存中有效登录 token 集合(登录时加入,登出时移除;重启后清空)


# 【功能】登录:校验数据库中的用户名 / 密码,通过则发放 16 字节十六进制 token,
#   写入内存集合与 HttpOnly Cookie(30 天有效);失败返回 401。
# 【请求体】{"username": "...", "password": "..."}。
@app.route("/api/auth/login", methods=["POST"])
def api_login():
    data = request.json or {}
    username = data.get("username", "")
    password = data.get("password", "")
    db = get_db()
    db.ensure_default_user()
    if db.verify_user(username, password):
        token = secrets.token_hex(16)
        _auth_tokens.add(token)
        resp = make_response(jsonify({"status": "ok", "token": token}))
        resp.set_cookie("auth_token", token, max_age=86400 * 30, httponly=True)
        return resp
    return jsonify({"error": "Invalid credentials"}), 401


# 【功能】登出:从内存集合移除 token 并清除 Cookie。
@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    token = request.headers.get("X-Auth-Token", "") or request.cookies.get("auth_token", "")
    _auth_tokens.discard(token)
    resp = make_response(jsonify({"status": "ok"}))
    resp.delete_cookie("auth_token")
    return resp


# 【功能】检查当前是否已登录(仅看 Cookie 中的 token 是否在内存集合里)。
# 【返回】{"authenticated": true|false}。
@app.route("/api/auth/status")
def api_auth_status():
    token = request.cookies.get("auth_token", "")
    return jsonify({"authenticated": token in _auth_tokens})


# ═══════════════════════════════════════════════════════════════════
# P1: Analysis endpoints
# P1 分析接口(/api/analysis/*):异常、背离、领先滞后、作者、事件、对比、排名、跨平台。
# ═══════════════════════════════════════════════════════════════════


# 【功能】检测品种价格 / 情绪数据的异常点。
# 【参数】threshold=标准差阈值(默认 2.0)。
# 【返回】{"variety": ..., "anomalies": [...], "count": n}。
@app.route("/api/analysis/anomalies/<variety>")
def api_anomalies(variety):
    threshold = request.args.get("threshold", 2.0, type=float)
    result = detect_anomalies(variety, threshold_std=threshold)
    return jsonify({"variety": variety, "anomalies": result, "count": len(result)})


# 【功能】计算某品种价格与情绪的背离度。无数据时返回 404。
@app.route("/api/analysis/divergence/<variety>")
def api_divergence(variety):
    result = compute_divergence(variety)
    if result is None:
        return jsonify({"error": "No data"}), 404
    return jsonify(result)


# 【功能】对所有有情绪数据的品种计算背离,并按背离度升序返回(数值小 = 背离轻)。
@app.route("/api/analysis/divergence/all")
def api_divergence_all():
    """Get divergence for all varieties with data."""
    results = []
    for f in sorted(SENTIMENT_DIR.glob("*_sentiment.json")):
        var = f.stem.replace("_sentiment", "")
        div = compute_divergence(var)
        if div:
            results.append(div)
    results.sort(key=lambda x: x["divergence"])
    return jsonify(results)


# 【功能】计算价格领先 / 滞后情绪的最大相关性。
# 【参数】max_lag=最大滞后天数(默认 5)。
# 【返回】分析结果;数据不足返回 404。
@app.route("/api/analysis/leadlag/<variety>")
def api_leadlag(variety):
    max_lag = request.args.get("max_lag", 5, type=int)
    result = analyze_lead_lag(variety, max_lag=max_lag)
    if result is None:
        return jsonify({"error": "Insufficient data"}), 404
    return jsonify(result)


# 【功能】返回影响力最大的作者列表(按粉丝 / 互动加权)。
# 【参数】limit=数量(默认 20)。
@app.route("/api/analysis/authors")
def api_authors():
    limit = request.args.get("limit", 20, type=int)
    return jsonify(get_top_authors(limit=limit))


# 【功能】从情绪数据中提取重大事件。
# 【参数】variety=品种(可选);days=回溯天数(默认 7)。
@app.route("/api/analysis/events")
def api_events():
    variety = request.args.get("variety", "")
    days = request.args.get("days", 7, type=int)
    return jsonify(extract_events(variety=variety, days=days))


# 【功能】多品种情绪对比。
# 【参数】varieties="rb,au,..."(可选);缺省取前 10 个有情绪数据的品种。
@app.route("/api/analysis/compare")
def api_compare_varieties():
    varieties_param = request.args.get("varieties", "")
    if varieties_param:
        varieties = [v.strip() for v in varieties_param.split(",")]
    else:
        varieties = [
            f.stem.replace("_sentiment", "")
            for f in sorted(SENTIMENT_DIR.glob("*_sentiment.json"))[:10]
        ]
    return jsonify(compare_varieties(varieties))


# 【功能】返回所有品种的综合情绪评分排名。
@app.route("/api/analysis/ranking")
def api_ranking():
    return jsonify(get_all_variety_scores())


# 【功能】某品种跨平台(微博 / 雪球 / 东方财富)情绪一致性分析。无数据返回 404。
@app.route("/api/analysis/crossplatform/<variety>")
def api_crossplatform(variety):
    result = analyze_cross_platform(variety)
    if result is None:
        return jsonify({"error": "No data"}), 404
    return jsonify(result)


# 【功能】品种-平台-板块关系图(非标数据可视化:帖子 NER 品种 × 采集平台 × 板块归属)。
# 【返回】{"nodes": [{id,name,type,value}...], "links": [{source,target,value}...]}。
@app.route("/api/analysis/graph")
def api_analysis_graph():
    """返回 ECharts graph 数据:平台/品种/板块三类节点 + 共现与归属边。"""
    return jsonify(_build_variety_platform_graph(_iter_batch_records()))


# 【功能】平台→品种→多/空 三层桑基流量(非标数据可视化)。
# 【返回】{"nodes": [{name}...], "links": [{source,target,value}...]}。
@app.route("/api/analysis/sankey")
def api_analysis_sankey():
    """返回 ECharts sankey 数据:平台→品种→情绪方向三层计数。"""
    return jsonify(_build_sentiment_sankey(_iter_batch_records()))


# ═══════════════════════════════════════════════════════════════════
# P2: Watchlist
# P2 自选列表(/api/watchlist,GET / POST / DELETE)。
# ═══════════════════════════════════════════════════════════════════


# 【功能】自选列表 CRUD。
#   GET: 返回全部自选; POST: 添加品种; DELETE: 移除品种。
# 【请求体】(POST / DELETE) {"variety": "rb"}。
# 【返回】{"status": "ok", "watchlist": 最新自选列表}。
@app.route("/api/watchlist", methods=["GET", "POST", "DELETE"])
def api_watchlist():
    db = get_db()
    if request.method == "GET":
        return jsonify(db.get_watchlist())
    elif request.method == "POST":
        data = request.json or {}
        variety = data.get("variety", "")
        if variety:
            db.add_to_watchlist(variety)
        return jsonify({"status": "ok", "watchlist": db.get_watchlist()})
    elif request.method == "DELETE":
        data = request.json or {}
        variety = data.get("variety", "")
        if variety:
            db.remove_from_watchlist(variety)
        return jsonify({"status": "ok", "watchlist": db.get_watchlist()})


# ═══════════════════════════════════════════════════════════════════
# P3: Simulated Trading
# 模拟交易(重点路由组,20+ 条策略端点):/api/trading/*
# · 综合类:run / contrarian / adaptive_sentiment / apply_risk(风控) / compare
#   (multi_compare 合并端点已于 2026-08-25 删除:仅支持 5 策略且无成本/风控,
#    前端多策略改为逐策略拉取+共享口径,见 worklog 2026-08-11)
# · 策略类:momentum_strat / momentum_adaptive / donchian / ma_cross(_sent) /
#   macd(_sent) / rsi(_sent) / bollinger(_sent) / turtle(_sent) / atr(_sent) / trailing
# · 每个策略端点都调用 signal_analyzer 中对应的回测函数,并附带今日信号 today_signal。
# ═══════════════════════════════════════════════════════════════════


# 【功能】运行"情绪固定阈值"模拟交易,并把产生的交易信号写入数据库。
# 【请求体】{"variety": "rb", "horizon": 3(持有天数), "threshold": 0.2(情绪阈值),
#             "start_date": "2025-01-01", "end_date": ""(空 = 到最新)}
# 【返回】run_simulated_trading 的结果 dict + today_signal(今日信号)+ 可选 today_signals。
# 【关键】把 recent_trades 逐条存进数据库(save_trade_signal),供 /api/trading/signals 查询。
@app.route("/api/trading/run", methods=["POST"])
def api_trading_run():
    data = request.json or {}
    variety = data.get("variety", "")
    horizon = data.get("horizon", 3)
    threshold = data.get("threshold", 0.2)
    result = run_simulated_trading(  # 【调用函数】跨模块回测:情绪固定阈值策略
        variety=variety,
        horizon=horizon,
        signal_threshold=threshold,
        start_date=data.get("start_date", "2025-01-01"),
        end_date=data.get("end_date", ""),
    )
    # Save trades to DB
    db = get_db()  # 【调用函数】获取数据库会话
    for t in result.get("recent_trades", []):
        db.save_trade_signal(t["variety"], t["entry"], 0, t["dir"], 0, horizon)  # 【调用函数】把历史交易逐条写入数据库
    sig = latest_trading_signal(
        "fixed", variety=variety, horizon=horizon, signal_threshold=threshold
    )
    result["today_signal"] = sig["today_signal"] if sig else None
    if sig and sig.get("today_signals"):
        result["today_signals"] = sig["today_signals"]
    return jsonify(result)


# 【功能】逆情绪策略:当市场情绪过度(过热 / 过冷)时反向开仓。
# 【请求体】variety/horizon(默认3)/trend_window(情绪趋势窗口,默认5)/start_date/end_date。
# 【返回】策略结果 + today_signal(可选 today_signals)。
@app.route("/api/trading/contrarian", methods=["POST"])
def api_trading_contrarian():
    data = request.json or {}
    result = run_contrarian_sentiment(  # 【调用函数】跨模块回测:逆情绪策略
        variety=data.get("variety", ""),
        horizon=data.get("horizon", 3),
        trend_window=data.get("trend_window", 5),
        start_date=data.get("start_date", "2025-01-01"),
        end_date=data.get("end_date", ""),
    )
    variety = data.get("variety", "")
    sig = latest_trading_signal(
        "contrarian",
        variety=variety,
        horizon=data.get("horizon", 3),
        trend_window=data.get("trend_window", 5),
    )
    result["today_signal"] = sig["today_signal"] if sig else None
    if sig and sig.get("today_signals"):
        result["today_signals"] = sig["today_signals"]
    return jsonify(result)


# 【功能】自适应情绪策略(情绪阈值随市场状态动态调整)。
# 【请求体】variety/horizon(默认3)/trend_window(默认5)/start_date/end_date。
# 【返回】策略结果 + today_signal(可选 today_signals)。
@app.route("/api/trading/adaptive_sentiment", methods=["POST"])
def api_trading_adaptive_sentiment():
    data = request.json or {}
    result = run_adaptive_sentiment(  # 【调用函数】跨模块回测:自适应情绪策略
        variety=data.get("variety", ""),
        horizon=data.get("horizon", 3),
        trend_window=data.get("trend_window", 5),
        start_date=data.get("start_date", "2025-01-01"),
        end_date=data.get("end_date", ""),
    )
    sig = latest_trading_signal(
        "adaptive_sent",
        variety=data.get("variety", ""),
        horizon=data.get("horizon", 3),
        trend_window=data.get("trend_window", 5),
    )
    result["today_signal"] = sig["today_signal"] if sig else None
    if sig and sig.get("today_signals"):
        result["today_signals"] = sig["today_signals"]
    return jsonify(result)


# 【功能】风控接口(重点):对前端回传的一组交易,用真实价格数据计算止损 / 移动止损后的出场点。
# 【请求体】{"variety": "rb", "trades": [交易列表], "stop_loss": 止损百分比,
#             "trail_stop": 移动止损百分比}
# 【返回】{"trades": 处理后的交易列表}。
# 【关键】没有交易或止损参数全为 0 时原样返回;具体计算在
#   signal_analyzer.apply_risk_management,价格来自 signal_analyzer._load_price。
@app.route("/api/trading/apply_risk", methods=["POST"])
def api_apply_risk():
    """Apply stop-loss / trailing-stop to a list of trades using real price data."""
    data = request.json or {}
    variety = data.get("variety", "RB")
    trades_raw = data.get("trades", [])
    stop_loss = data.get("stop_loss", 0)
    trail_stop = data.get("trail_stop", 0)

    if not trades_raw or (not stop_loss and not trail_stop):
        return jsonify({"trades": trades_raw})

    from signal_analyzer import (  # 【调用包】价格加载与风控计算(信号分析器内部接口)
        _load_price as _lpr,
        apply_risk_management as _arm,
    )

    px_data = _lpr(variety)  # 【调用函数】加载真实价格数据(信号分析器内部接口)
    prices = px_data.get("prices", []) if px_data else []
    result = _arm(trades_raw, prices, stop_loss, trail_stop)  # 【调用函数】计算止损/移动止损后的出场点
    return jsonify({"trades": result})




# 【功能】动量策略(纯价格,追涨杀跌)。参数 variety/start_date/end_date。
# 【返回】策略结果 + today_signal。
@app.route("/api/trading/momentum_strat", methods=["POST"])
def api_trading_momentum():
    data = request.json or {}
    result = run_momentum_strategy(  # 【调用函数】跨模块回测:动量策略(纯价格)
        variety=data.get("variety", ""),
        start_date=data.get("start_date", "2025-01-01"),
        end_date=data.get("end_date", ""),
    )
    sig = latest_trading_signal("momentum", variety=data.get("variety", ""))
    result["today_signal"] = sig["today_signal"] if sig else None
    if sig and sig.get("today_signals"):
        result["today_signals"] = sig["today_signals"]
    return jsonify(result)


# 【功能】动量 + 自适应策略。参数 lookback(默认5)/hold(默认3)/trend_window(默认5)。
@app.route("/api/trading/momentum_adaptive", methods=["POST"])
def api_trading_momentum_adaptive():
    data = request.json or {}
    result = run_momentum_adaptive(  # 【调用函数】跨模块回测:动量 + 自适应
        variety=data.get("variety", ""),
        lookback=data.get("lookback", 5),
        hold=data.get("hold", 3),
        trend_window=data.get("trend_window", 5),
        start_date=data.get("start_date", "2025-01-01"),
        end_date=data.get("end_date", ""),
    )
    sig = latest_trading_signal(
        "momentum_ad",
        variety=data.get("variety", ""),
        lookback=data.get("lookback", 5),
        hold=data.get("hold", 3),
        trend_window=data.get("trend_window", 5),
    )
    result["today_signal"] = sig["today_signal"] if sig else None
    if sig and sig.get("today_signals"):
        result["today_signals"] = sig["today_signals"]
    return jsonify(result)


# 【功能】唐奇安通道突破策略(纯价格,突破 N 日高点开多 / 低点开空)。
@app.route("/api/trading/donchian", methods=["POST"])
def api_trading_donchian():
    data = request.json or {}
    result = run_donchian_strategy(  # 【调用函数】跨模块回测:唐奇安通道突破(纯价格)
        variety=data.get("variety", ""),
        start_date=data.get("start_date", "2025-01-01"),
        end_date=data.get("end_date", ""),
    )
    sig = latest_trading_signal("donchian", variety=data.get("variety", ""))
    result["today_signal"] = sig["today_signal"] if sig else None
    if sig and sig.get("today_signals"):
        result["today_signals"] = sig["today_signals"]
    return jsonify(result)


# 【功能】双均线交叉策略(纯价格)。
# 【参数】fast=快线周期(默认10);slow=慢线周期(默认30);start_date/end_date。
# 【返回】策略结果 + today_signal。
@app.route("/api/trading/ma_cross", methods=["POST"])
def api_trading_ma_cross():
    """双均线交叉(纯价格)。"""
    data = request.json or {}
    result = run_ma_cross_strategy(  # 【调用函数】跨模块回测:双均线交叉(纯价格)
        variety=data.get("variety", ""),
        fast=data.get("fast", 10),
        slow=data.get("slow", 30),
        start_date=data.get("start_date", "2025-01-01"),
        end_date=data.get("end_date", ""),
    )
    sig = latest_trading_signal(
        "ma_cross",
        variety=data.get("variety", ""),
        fast=data.get("fast", 10),
        slow=data.get("slow", 30),
    )
    result["today_signal"] = sig["today_signal"] if sig else None
    return jsonify(result)


# 【功能】双均线交叉策略 + 情绪确认(交叉信号需得到情绪方向一致才开仓)。
# 【参数】fast/slow 同上;trend_window=情绪趋势窗口(默认5)。
@app.route("/api/trading/ma_cross_sent", methods=["POST"])
def api_trading_ma_cross_sent():
    """双均线交叉(情绪确认)。"""
    data = request.json or {}
    result = run_ma_cross_sent_strategy(  # 【调用函数】跨模块回测:双均线交叉 + 情绪确认
        variety=data.get("variety", ""),
        fast=data.get("fast", 10),
        slow=data.get("slow", 30),
        trend_window=data.get("trend_window", 5),
        start_date=data.get("start_date", "2025-01-01"),
        end_date=data.get("end_date", ""),
    )
    sig = latest_trading_signal(
        "ma_cross_sent",
        variety=data.get("variety", ""),
        fast=data.get("fast", 10),
        slow=data.get("slow", 30),
        trend_window=data.get("trend_window", 5),
    )
    result["today_signal"] = sig["today_signal"] if sig else None
    return jsonify(result)


# 【功能】MACD 策略(纯价格)。参数 macd_fast(12)/macd_slow(26)/macd_signal(9)。
@app.route("/api/trading/macd", methods=["POST"])
def api_trading_macd():
    """MACD(纯价格)。"""
    data = request.json or {}
    result = run_macd_strategy(  # 【调用函数】跨模块回测:MACD(纯价格)
        variety=data.get("variety", ""),
        macd_fast=data.get("macd_fast", 12),
        macd_slow=data.get("macd_slow", 26),
        macd_signal=data.get("macd_signal", 9),
        start_date=data.get("start_date", "2025-01-01"),
        end_date=data.get("end_date", ""),
    )
    sig = latest_trading_signal(
        "macd",
        variety=data.get("variety", ""),
        macd_fast=data.get("macd_fast", 12),
        macd_slow=data.get("macd_slow", 26),
        macd_signal=data.get("macd_signal", 9),
    )
    result["today_signal"] = sig["today_signal"] if sig else None
    return jsonify(result)


# 【功能】MACD 策略 + 情绪确认。参数同 MACD,另加 trend_window(默认5)。
@app.route("/api/trading/macd_sent", methods=["POST"])
def api_trading_macd_sent():
    """MACD(情绪确认)。"""
    data = request.json or {}
    result = run_macd_sent_strategy(  # 【调用函数】跨模块回测:MACD + 情绪确认
        variety=data.get("variety", ""),
        macd_fast=data.get("macd_fast", 12),
        macd_slow=data.get("macd_slow", 26),
        macd_signal=data.get("macd_signal", 9),
        trend_window=data.get("trend_window", 5),
        start_date=data.get("start_date", "2025-01-01"),
        end_date=data.get("end_date", ""),
    )
    sig = latest_trading_signal(
        "macd_sent",
        variety=data.get("variety", ""),
        macd_fast=data.get("macd_fast", 12),
        macd_slow=data.get("macd_slow", 26),
        macd_signal=data.get("macd_signal", 9),
        trend_window=data.get("trend_window", 5),
    )
    result["today_signal"] = sig["today_signal"] if sig else None
    return jsonify(result)


# 【功能】RSI 均值回归(纯价格,超卖买入 / 超买卖出)。
# 【参数】rsi_period(14)/rsi_overbought(70)/rsi_oversold(30)。
@app.route("/api/trading/rsi", methods=["POST"])
def api_trading_rsi():
    """RSI 均值回归(纯价格)。"""
    data = request.json or {}
    result = run_rsi_strategy(  # 【调用函数】跨模块回测:RSI 均值回归(纯价格)
        variety=data.get("variety", ""),
        rsi_period=data.get("rsi_period", 14),
        rsi_overbought=data.get("rsi_overbought", 70),
        rsi_oversold=data.get("rsi_oversold", 30),
        start_date=data.get("start_date", "2025-01-01"),
        end_date=data.get("end_date", ""),
    )
    sig = latest_trading_signal(
        "rsi",
        variety=data.get("variety", ""),
        rsi_period=data.get("rsi_period", 14),
        rsi_overbought=data.get("rsi_overbought", 70),
        rsi_oversold=data.get("rsi_oversold", 30),
    )
    result["today_signal"] = sig["today_signal"] if sig else None
    return jsonify(result)


# 【功能】RSI 均值回归 + 情绪确认。参数同 RSI,另加 trend_window(默认5)。
@app.route("/api/trading/rsi_sent", methods=["POST"])
def api_trading_rsi_sent():
    """RSI 均值回归(情绪确认)。"""
    data = request.json or {}
    result = run_rsi_sent_strategy(  # 【调用函数】跨模块回测:RSI + 情绪确认
        variety=data.get("variety", ""),
        rsi_period=data.get("rsi_period", 14),
        rsi_overbought=data.get("rsi_overbought", 70),
        rsi_oversold=data.get("rsi_oversold", 30),
        trend_window=data.get("trend_window", 5),
        start_date=data.get("start_date", "2025-01-01"),
        end_date=data.get("end_date", ""),
    )
    sig = latest_trading_signal(
        "rsi_sent",
        variety=data.get("variety", ""),
        rsi_period=data.get("rsi_period", 14),
        rsi_overbought=data.get("rsi_overbought", 70),
        rsi_oversold=data.get("rsi_oversold", 30),
        trend_window=data.get("trend_window", 5),
    )
    result["today_signal"] = sig["today_signal"] if sig else None
    return jsonify(result)


# 【功能】布林带突破策略(纯价格)。
# 【参数】bb_period(20)/num_std(2.0,标准差倍数)。
@app.route("/api/trading/bollinger", methods=["POST"])
def api_trading_bollinger():
    """布林带突破(纯价格)。"""
    data = request.json or {}
    result = run_bollinger_strategy(  # 【调用函数】跨模块回测:布林带突破(纯价格)
        variety=data.get("variety", ""),
        bb_period=data.get("bb_period", 20),
        num_std=data.get("num_std", 2.0),
        start_date=data.get("start_date", "2025-01-01"),
        end_date=data.get("end_date", ""),
    )
    sig = latest_trading_signal(
        "bollinger",
        variety=data.get("variety", ""),
        bb_period=data.get("bb_period", 20),
        num_std=data.get("num_std", 2.0),
    )
    result["today_signal"] = sig["today_signal"] if sig else None
    return jsonify(result)


# 【功能】布林带突破 + 情绪确认。参数同布林带,另加 trend_window(默认5)。
@app.route("/api/trading/bollinger_sent", methods=["POST"])
def api_trading_bollinger_sent():
    """布林带突破(情绪确认)。"""
    data = request.json or {}
    result = run_bollinger_sent_strategy(  # 【调用函数】跨模块回测:布林带突破 + 情绪确认
        variety=data.get("variety", ""),
        bb_period=data.get("bb_period", 20),
        num_std=data.get("num_std", 2.0),
        trend_window=data.get("trend_window", 5),
        start_date=data.get("start_date", "2025-01-01"),
        end_date=data.get("end_date", ""),
    )
    sig = latest_trading_signal(
        "bollinger_sent",
        variety=data.get("variety", ""),
        bb_period=data.get("bb_period", 20),
        num_std=data.get("num_std", 2.0),
        trend_window=data.get("trend_window", 5),
    )
    result["today_signal"] = sig["today_signal"] if sig else None
    return jsonify(result)


# 【功能】海龟交易法(纯价格,经典趋势跟踪系统)。
# 【参数】turtle_entry=入市通道(20)/turtle_exit=离市通道(10)/atr_period(14)/atr_mult(2.0)。
@app.route("/api/trading/turtle", methods=["POST"])
def api_trading_turtle():
    """海龟交易法(纯价格)。"""
    data = request.json or {}
    result = run_turtle_strategy(  # 【调用函数】跨模块回测:海龟交易法(纯价格)
        variety=data.get("variety", ""),
        turtle_entry=data.get("turtle_entry", 20),
        turtle_exit=data.get("turtle_exit", 10),
        atr_period=data.get("atr_period", 14),
        atr_mult=data.get("atr_mult", 2.0),
        start_date=data.get("start_date", "2025-01-01"),
        end_date=data.get("end_date", ""),
    )
    sig = latest_trading_signal(
        "turtle",
        variety=data.get("variety", ""),
        turtle_entry=data.get("turtle_entry", 20),
        turtle_exit=data.get("turtle_exit", 10),
        atr_period=data.get("atr_period", 14),
        atr_mult=data.get("atr_mult", 2.0),
    )
    result["today_signal"] = sig["today_signal"] if sig else None
    return jsonify(result)


# 【功能】海龟交易法 + 情绪确认。参数同海龟,另加 trend_window(默认5)。
@app.route("/api/trading/turtle_sent", methods=["POST"])
def api_trading_turtle_sent():
    """海龟交易法(情绪确认)。"""
    data = request.json or {}
    result = run_turtle_sent_strategy(  # 【调用函数】跨模块回测:海龟交易法 + 情绪确认
        variety=data.get("variety", ""),
        turtle_entry=data.get("turtle_entry", 20),
        turtle_exit=data.get("turtle_exit", 10),
        atr_period=data.get("atr_period", 14),
        atr_mult=data.get("atr_mult", 2.0),
        trend_window=data.get("trend_window", 5),
        start_date=data.get("start_date", "2025-01-01"),
        end_date=data.get("end_date", ""),
    )
    sig = latest_trading_signal(
        "turtle_sent",
        variety=data.get("variety", ""),
        turtle_entry=data.get("turtle_entry", 20),
        turtle_exit=data.get("turtle_exit", 10),
        atr_period=data.get("atr_period", 14),
        atr_mult=data.get("atr_mult", 2.0),
        trend_window=data.get("trend_window", 5),
    )
    result["today_signal"] = sig["today_signal"] if sig else None
    return jsonify(result)


# 【功能】ATR 通道(肯特纳通道)突破策略(纯价格)。
# 【参数】keltner_period(20)/keltner_mult(2.0,ATR 倍数)。
@app.route("/api/trading/atr", methods=["POST"])
def api_trading_atr():
    """ATR 通道突破(纯价格)。"""
    data = request.json or {}
    result = run_atr_strategy(  # 【调用函数】跨模块回测:ATR 通道突破(纯价格)
        variety=data.get("variety", ""),
        keltner_period=data.get("keltner_period", 20),
        keltner_mult=data.get("keltner_mult", 2.0),
        start_date=data.get("start_date", "2025-01-01"),
        end_date=data.get("end_date", ""),
    )
    sig = latest_trading_signal(
        "atr",
        variety=data.get("variety", ""),
        keltner_period=data.get("keltner_period", 20),
        keltner_mult=data.get("keltner_mult", 2.0),
    )
    result["today_signal"] = sig["today_signal"] if sig else None
    return jsonify(result)


# 【功能】ATR 通道突破 + 情绪确认。参数同 ATR,另加 trend_window(默认5)。
@app.route("/api/trading/atr_sent", methods=["POST"])
def api_trading_atr_sent():
    """ATR 通道突破(情绪确认)。"""
    data = request.json or {}
    result = run_atr_sent_strategy(  # 【调用函数】跨模块回测:ATR 通道突破 + 情绪确认
        variety=data.get("variety", ""),
        keltner_period=data.get("keltner_period", 20),
        keltner_mult=data.get("keltner_mult", 2.0),
        trend_window=data.get("trend_window", 5),
        start_date=data.get("start_date", "2025-01-01"),
        end_date=data.get("end_date", ""),
    )
    sig = latest_trading_signal(
        "atr_sent",
        variety=data.get("variety", ""),
        keltner_period=data.get("keltner_period", 20),
        keltner_mult=data.get("keltner_mult", 2.0),
        trend_window=data.get("trend_window", 5),
    )
    result["today_signal"] = sig["today_signal"] if sig else None
    return jsonify(result)


# 【功能】情绪跟踪止盈策略:基于情绪信号开仓,并用跟踪止盈控制回撤。
# 【参数】threshold=情绪阈值(默认0.2);max_holding=最长持有天数(默认10)。
# 【返回】策略结果 + today_signal(可选 today_signals)。
@app.route("/api/trading/trailing", methods=["POST"])
def api_trading_trailing():
    """Run trailing sentiment exit strategy."""
    data = request.json or {}
    variety = data.get("variety", "")
    threshold = data.get("threshold", 0.2)
    max_holding = data.get("max_holding", 10)
    result = run_trailing_strategy(  # 【调用函数】跨模块回测:情绪跟踪止盈策略
        variety=variety,
        signal_threshold=threshold,
        max_holding=max_holding,
        start_date=data.get("start_date", "2025-01-01"),
        end_date=data.get("end_date", ""),
    )
    sig = latest_trading_signal(
        "trailing", variety=variety, signal_threshold=threshold, max_holding=max_holding
    )
    result["today_signal"] = sig["today_signal"] if sig else None
    if sig and sig.get("today_signals"):
        result["today_signals"] = sig["today_signals"]
    return jsonify(result)


# 【功能】多策略横向对比:基本面 vs 基本面+情绪 vs 纯价格信号。
# 【参数】fund_threshold=基本面信号阈值(默认0.3);sent_threshold=情绪阈值(默认0.2)。
# 【返回】策略结果 + today_signal(可选 today_signals)。
@app.route("/api/trading/compare", methods=["POST"])
def api_trading_compare():
    """Run multi-strategy comparison: fundamental vs fundamental+sentiment vs price."""
    data = request.json or {}
    variety = data.get("variety", "RB")
    horizon = data.get("horizon", 5)
    fund_threshold = data.get("fund_threshold", 0.3)
    sent_threshold = data.get("sent_threshold", 0.2)
    result = run_strategy_comparison(  # 【调用函数】跨模块回测:基本面 vs 基本面+情绪 vs 纯价格信号对比
        variety=variety,
        horizon=horizon,
        signal_threshold=sent_threshold,
        fund_threshold=fund_threshold,
        start_date=data.get("start_date", "2025-01-01"),
        end_date=data.get("end_date", ""),
    )
    sig = latest_trading_signal(
        "compare",
        variety=variety,
        horizon=horizon,
        signal_threshold=sent_threshold,
        fund_threshold=fund_threshold,
    )
    result["today_signal"] = sig["today_signal"] if sig else None
    if sig and sig.get("today_signals"):
        result["today_signals"] = sig["today_signals"]
    return jsonify(result)


# 【功能】返回数据库中的模拟交易统计汇总(胜率 / 总收益等)。
@app.route("/api/trading/stats")
def api_trading_stats():
    return jsonify(get_db().get_trade_stats())


# 【功能】查询历史交易信号。
# 【参数】variety=品种(可选);limit=条数(默认 100)。
@app.route("/api/trading/signals")
def api_trading_signals():
    variety = request.args.get("variety", "")
    limit = request.args.get("limit", 100, type=int)
    return jsonify(get_db().get_trade_signals(variety=variety or None, limit=limit))


# ── Main ──────────────────────────────────────────────────────────────────

# ═══════════════════════════════════════════════════════════════════
# Batch Backtest: Agent vs Sentiment direction accuracy
# 批量回测:对比"情绪方向预测"与"完整 Agent 预测"对真实行情的命中率。
# ═══════════════════════════════════════════════════════════════════

# 批量回测的全局状态容器:是否运行中、逐品种结果列表、总品种数、已完成数。
_batch_state = {"running": False, "results": [], "total": 0, "done": 0}  # 【变量】批量回测全局状态容器(是否运行中、逐品种结果、总数、已完成数)


# 【功能】仅用情绪数据做即时方向预测(不跑 Agent 图,速度很快,用于批量对比)。
# 【返回】{"direction": "BULL"|"BEAR"|"HOLD", "score": 最新情绪分} 或 None(无数据)。
# 【关键】取 trade_date 当天或之前最后一期 avg_score: >0.05 判看多,<-0.05 判看空,否则中性。
def _predict_sentiment_only(variety: str, trade_date: str) -> dict:
    """Get instant sentiment-based direction prediction."""
    from signal_analyzer import _load_sentiment  # 【调用包】情绪数据加载(内部接口)

    sent = _load_sentiment(variety)  # 【调用函数】读取情绪序列做即时方向预测
    if not sent:
        return None
    series = sent.get("data", {}).get("daily_series", [])
    if not series:
        return None
    # Get latest sentiment before trade_date
    latest_score = 0
    for s in series:
        if s["date"] <= trade_date:
            latest_score = s.get("avg_score", 0)
    direction = "BULL" if latest_score > 0.05 else ("BEAR" if latest_score < -0.05 else "HOLD")
    return {"direction": direction, "score": round(latest_score, 3)}


# 【功能】获取 trade_date 之后 horizon_days(默认5)天的真实价格走势,用于验证预测对错。
# 【返回】{"direction", "pct_change", "entry", "exit", "effective_date"} 或 None。
# 【关键逻辑】
#   · 若 trade_date 超出数据范围,自动回退到最后一个可用日期(effective_date)。
#   · 目标日也无数据时,用倒数第二日 vs 最后一日估算。
#   · 涨跌幅 >0.15% 判 BULL,<-0.15% 判 BEAR,否则 HOLD。
def _get_actual_outcome(variety: str, trade_date: str, horizon_days: int = 5) -> dict:
    """Get actual price movement after trade_date. Auto-adjusts if date is beyond data range."""
    from signal_analyzer import _load_price as _lp  # 【调用包】价格数据加载(内部接口)

    price_data = _lp(variety)  # 【调用函数】加载真实价格数据
    if not price_data:
        return None
    prices = price_data.get("prices", [])
    if len(prices) < 5:
        return None
    # Get all dates
    all_dates = [str(p["date"])[:10] for p in prices]
    # If trade_date is beyond data range, auto-shift to last available
    effective_date = all_dates[-1] if trade_date > all_dates[-1] else trade_date
    # Find entry price
    entry_px = None
    for p in prices:
        d = str(p["date"])[:10]
        if d <= effective_date:
            entry_px = float(p["close"])
    if not entry_px:
        return None
    # Find exit price
    target = (
        datetime.strptime(effective_date, "%Y-%m-%d") + timedelta(days=horizon_days)
    ).strftime("%Y-%m-%d")
    exit_px = None
    for p in prices:
        d = str(p["date"])[:10]
        if d >= target:
            exit_px = float(p["close"])
            break
    # If target is also beyond data, use second-to-last vs last comparison
    if not exit_px and len(prices) >= 2:
        entry_px = float(prices[-2]["close"])
        exit_px = float(prices[-1]["close"])
        effective_date = all_dates[-2]
    if not exit_px:
        return None
    pct = (exit_px - entry_px) / entry_px * 100
    actual_dir = "BULL" if pct > 0.15 else ("BEAR" if pct < -0.15 else "HOLD")
    return {
        "direction": actual_dir,
        "pct_change": round(pct, 2),
        "entry": round(entry_px, 2),
        "exit": round(exit_px, 2),
        "effective_date": effective_date,
    }


# 【功能】为单个品种跑完整 Agent 流水线,并从综合研判文本中提取 RATING。
# 【返回】{"direction", "confidence", "score"};解析失败返回 UNKNOWN;异常返回 ERROR+错误信息。
# 【关键】direction 取 RATING 原文并转大写(如 "看多" 会变 "看多",由调用方再映射为 BULL/BEAR)。
def _run_agent_for_variety(symbol: str, trade_date: str, config: dict) -> dict:
    """Run full agent pipeline for one variety and extract RATING."""
    try:
        include_sentiment = should_include_sentiment(symbol)  # 【调用函数】质量感知判定(数据不足但有板块复合也算含)
        app_graph, _ = build_commodity_graph(
            config, enable_feedback=False, include_sentiment=include_sentiment  # 【调用函数】构建 LangGraph 多分析师图
        )
        evo_ctx = get_evolution_context(symbol)  # 【调用函数】读取该品种历史进化记忆
        state = {
            "messages": [HumanMessage(content=f"Analyze {symbol} as of {trade_date}.")],
            "company_of_interest": symbol,
            "asset_type": "commodity_futures",
            "trade_date": trade_date,
            "past_context": evo_ctx,
            "technical_report": "",
            "fundamental_report": "",
            "macro_report": "",
            "sentiment_report": "",
            "discussion_summary": "",
            "user_feedback_summary": "",
            "investment_plan": "",
            "final_trade_decision": "",
            "scenario_analysis": "",
            "debate_state": {
                "bull_history": "",
                "bear_history": "",
                "bull_last": "",
                "bear_last": "",
                "round": 0,
            },
        }
        final = {}
        for chunk in app_graph.stream(state, stream_mode="updates"):  # 【调用函数】以 updates 模式逐步驱动 Agent 图
            for _, nd in chunk.items():
                if isinstance(nd, dict):
                    final.update(nd)
        syn = final.get("investment_plan", "")
        m = re.search(r"RATING:\s*(.+?)\s*\|\s*CONFIDENCE:\s*(.+?)\s*\|\s*SCORE:\s*(\d+)", syn)
        if m:
            return {
                "direction": m.group(1).strip().upper(),
                "confidence": m.group(2).strip(),
                "score": int(m.group(3)),
            }
        return {"direction": "UNKNOWN", "confidence": "?", "score": 0}
    except Exception as e:
        return {"direction": "ERROR", "error": str(e)[:100]}


# 【功能】启动批量回测:对多个品种同时计算"情绪方向预测"与(可选的)"Agent 方向预测",
#   再与真实走势对比,统计方向准确率。
# 【请求体】{"date": "2026-07-21", "run_agent": false(是否跑完整 Agent,较慢),
#             "varieties": [品种列表]}
# 【返回】{"status": "started", "total": 品种数, "run_agent": ...};已在运行时返回错误。
# 【关键】varieties 缺省时自动挑选"同时有情绪与价格数据"的前 20 个品种;
#   后台线程逐品种处理,进度通过 /api/batch_backtest/status 轮询。
@app.route("/api/batch_backtest/start", methods=["POST"])
def api_batch_start():
    global _batch_state
    if _batch_state["running"]:
        return jsonify({"error": "Already running"})

    data = request.json or {}
    trade_date = data.get("date", "2026-07-21")
    run_agent = data.get("run_agent", False)
    varieties = data.get("varieties", [])

    if not varieties:
        # Default: varieties with BOTH sentiment and price data
        from signal_analyzer import _load_price as _lp  # 【调用包】价格数据加载(内部接口)

        vars_with_data = []
        for f in sorted(SENTIMENT_DIR.glob("*_sentiment.json")):
            v = f.stem.replace("_sentiment", "")
            price = _lp(v)
            sent = _predict_sentiment_only(v, trade_date)
            if price and sent and len(price.get("prices", [])) > 0:
                vars_with_data.append(v)
        varieties = vars_with_data[:20]

    _batch_state = {
        "running": True,
        "results": [],
        "total": len(varieties),
        "done": 0,
        "date": trade_date,
        "varieties": varieties,
    }

    # 后台线程主体:逐品种计算情绪预测 + (可选)Agent 预测,并与真实走势对比;
    # 完成后汇总 accuracy 写入 _batch_state["summary"]。
    def run_batch():
        global _batch_state
        for v in varieties:
            if not _batch_state["running"]:
                break
            result = {"variety": v}

            # Sentiment prediction (instant)
            sent = _predict_sentiment_only(v, trade_date)
            result["sentiment"] = sent

            # Actual outcome
            outcome = _get_actual_outcome(v, trade_date)
            result["actual"] = outcome

            # Compare sentiment vs actual
            if sent and outcome:
                result["sentiment_correct"] = sent["direction"] == outcome["direction"]

            # Full agent prediction (slow, optional)
            if run_agent:
                agent = _run_agent_for_variety(v, trade_date, config)
                result["agent"] = agent
                # Map agent direction to BULL/BEAR for comparison
                agent_dir = agent.get("direction", "")
                if "BULL" in agent_dir or "偏多" in agent_dir or "BUY" in agent_dir:
                    agent_dir = "BULL"
                elif "BEAR" in agent_dir or "偏空" in agent_dir or "SELL" in agent_dir:
                    agent_dir = "BEAR"
                else:
                    agent_dir = "HOLD"
                if outcome:
                    result["agent_correct"] = agent_dir == outcome["direction"]

            _batch_state["results"].append(result)
            _batch_state["done"] += 1

        # Compute summary
        results = _batch_state["results"]
        sent_correct = sum(1 for r in results if r.get("sentiment_correct"))
        sent_total = sum(1 for r in results if "sentiment_correct" in r)
        agent_correct = sum(1 for r in results if r.get("agent_correct"))
        agent_total = sum(1 for r in results if "agent_correct" in r)

        _batch_state["summary"] = {
            "sentiment_accuracy": round(sent_correct / sent_total, 3) if sent_total else 0,
            "sentiment_pairs": sent_total,
            "agent_accuracy": round(agent_correct / agent_total, 3) if agent_total else 0,
            "agent_pairs": agent_total,
        }
        _batch_state["running"] = False

    # 后台线程逐品种执行批量回测,接口立即返回;进度经 /api/batch_backtest/status 轮询。
    t = threading.Thread(target=run_batch, daemon=True)
    t.start()

    return jsonify({"status": "started", "total": len(varieties), "run_agent": run_agent})


# 【功能】轮询批量回测进度(返回 _batch_state 当前内容)。
@app.route("/api/batch_backtest/status")
def api_batch_status():
    return jsonify(_batch_state)


# ═══════════════════════════════════════════════════════════════════
# Agent Validation: full-pipeline direction/score/confidence test
# Agent 验证:跑完整流水线,统计方向 / 评分 / 置信度与真实走势的一致性。
# ═══════════════════════════════════════════════════════════════════

# Agent 验证的全局状态容器:是否运行中、逐品种结果、总品种数、已完成数、目标日期。
_val_state = {"running": False, "results": [], "total": 0, "done": 0, "date": ""}  # 【变量】Agent 验证全局状态容器(运行中、逐品种结果、总数、已完成数、目标日期)


# 【功能】对单个品种跑完整 Agent 流水线,同时更新 _val_state["current_stage"] 供前端看进度。
# 【返回】{variety, rating, confidence, score, agent_dir, actual_dir, actual_pct,
#           correct, dir_strength, elapsed} 或 {variety, error}。
# 【关键逻辑】把 RATING 文本映射为方向:含"看多/BULL"→BULL,"看空/BEAR"→BEAR,否则 HOLD;
#   dir_strength = score-5(看多时越大越强)或 5-score(看空时越大越坚定)。
def _validate_one_variety(variety: str, trade_date: str, config: dict) -> dict:
    """Run full Agent pipeline with per-stage progress tracking."""
    global _val_state
    try:
        include_sentiment = should_include_sentiment(variety)  # 【调用函数】质量感知判定(数据不足但有板块复合也算含)
        app_graph, _ = build_commodity_graph(
            config, enable_feedback=False, include_sentiment=include_sentiment  # 【调用函数】构建 LangGraph 多分析师图
        )
        evo_ctx = get_evolution_context(variety)  # 【调用函数】读取该品种历史进化记忆
        state = {
            "messages": [HumanMessage(content=f"Analyze {variety} as of {trade_date}.")],
            "company_of_interest": variety,
            "asset_type": "commodity_futures",
            "trade_date": trade_date,
            "past_context": evo_ctx,
            "technical_report": "",
            "fundamental_report": "",
            "macro_report": "",
            "sentiment_report": "",
            "discussion_summary": "",
            "user_feedback_summary": "",
            "investment_plan": "",
            "final_trade_decision": "",
            "scenario_analysis": "",
            "debate_state": {
                "bull_history": "",
                "bear_history": "",
                "bull_last": "",
                "bear_last": "",
                "round": 0,
            },
        }
        final = {}
        t0 = time.time()
        stage_names = {
            "technical_analyst": "技术",
            "fundamental_analyst": "基本面",
            "macro_analyst": "宏观",
            "sentiment_analyst": "情绪",
            "bull_opening": "多方",
            "bear_refute": "空方",
            "bull_rebuttal": "反驳",
            "debate_moderator": "裁决",
            "synthesis": "研判",
            "scenario_analysis": "情景",
        }
        for chunk in app_graph.stream(state, stream_mode="updates"):  # 【调用函数】以 updates 模式逐步驱动 Agent 图
            for node_name, nd in chunk.items():
                if isinstance(nd, dict):
                    final.update(nd)
                # Update per-variety stage progress
                stage = stage_names.get(node_name, node_name[:4])
                _val_state["current_stage"] = f"{variety}: {stage} ({time.time() - t0:.0f}s)"
        elapsed = time.time() - t0

        syn = final.get("investment_plan", "")
        m = re.search(r"RATING:\s*(.+?)\s*\|\s*CONFIDENCE:\s*(.+?)\s*\|\s*SCORE:\s*(\d+)", syn)
        if not m:
            return {"variety": variety, "error": "No RATING found", "elapsed": f"{elapsed:.0f}s"}

        rating = m.group(1).strip()
        confidence = m.group(2).strip()
        score = int(m.group(3))

        # Map rating to direction
        if "看多" in rating or "BULL" in rating.upper():
            agent_dir = "BULL"
            dir_strength = score - 5  # positive = bullish
        elif "看空" in rating or "BEAR" in rating.upper():
            agent_dir = "BEAR"
            dir_strength = 5 - score  # positive = bearish conviction
        else:
            agent_dir = "HOLD"
            dir_strength = 0

        # Actual outcome
        outcome = _get_actual_outcome(variety, trade_date, horizon_days=5)
        if not outcome:
            return {
                "variety": variety,
                "rating": rating,
                "confidence": confidence,
                "score": score,
                "agent_dir": agent_dir,
                "error": "No price data",
                "elapsed": f"{elapsed:.0f}s",
            }

        correct = agent_dir == outcome["direction"] if agent_dir != "HOLD" else None

        return {
            "variety": variety,
            "rating": rating,
            "confidence": confidence,
            "score": score,
            "agent_dir": agent_dir,
            "actual_dir": outcome["direction"],
            "actual_pct": outcome["pct_change"],
            "correct": correct,
            "dir_strength": dir_strength,
            "elapsed": f"{elapsed:.0f}s",
        }
    except Exception as e:
        return {"variety": variety, "error": str(e)[:200]}


# 【功能】启动 Agent 验证(全流水线:方向 / 评分 / 置信度 vs 真实走势)。
# 【请求体】{"date": "2026-07-10", "varieties": [品种列表]}
# 【返回】{"status": "started", "total": 品种数, "date": ...};已在运行返回错误+进度。
# 【关键】varieties 缺省时自动挑选"价格数据超过 10 条"的品种前 12 个;
#   后台线程逐品种跑完整 Agent,通过 /api/agent_validation/status 轮询。
@app.route("/api/agent_validation/start", methods=["POST"])
def api_validation_start():
    global _val_state
    if _val_state["running"]:
        return jsonify(
            {"error": "Already running", "progress": f"{_val_state['done']}/{_val_state['total']}"}
        )

    data = request.json or {}
    trade_date = data.get("date", "2026-07-10")
    varieties_raw = data.get("varieties", [])

    if not varieties_raw:
        from signal_analyzer import _load_price as _lpv  # 【调用包】价格数据加载(内部接口)

        vars_with_data = []
        for f in sorted(SENTIMENT_DIR.glob("*_sentiment.json")):
            v = f.stem.replace("_sentiment", "")
            p = _lpv(v)
            if p and len(p.get("prices", [])) > 10:
                vars_with_data.append(v)
        varieties = vars_with_data[:12]
    else:
        varieties = varieties_raw

    _val_state = {
        "running": True,
        "results": [],
        "total": len(varieties),
        "done": 0,
        "date": trade_date,
        "varieties": varieties,
    }

    # 后台线程主体:逐品种跑完整 Agent 验证,并按置信度(高/中/低)汇总准确率与相关性。
    def run_validation():
        global _val_state
        for v in varieties:
            if not _val_state["running"]:
                break
            result = _validate_one_variety(v, trade_date, config)
            _val_state["results"].append(result)
            _val_state["done"] += 1

        # Compute summary
        results = _val_state["results"]
        valid = [r for r in results if "correct" in r and r["correct"] is not None]
        correct = sum(1 for r in valid if r["correct"])
        total_v = len(valid)

        high_conf = [r for r in valid if r.get("confidence") == "高"]
        mid_conf = [r for r in valid if r.get("confidence") == "中"]
        low_conf = [r for r in valid if r.get("confidence") == "低"]

        scores = [r["score"] for r in valid]
        pcts = [r["actual_pct"] for r in valid]

        _val_state["summary"] = {
            "direction_accuracy": round(correct / total_v, 3) if total_v else 0,
            "total_valid": total_v,
            "high_conf_acc": round(sum(1 for r in high_conf if r["correct"]) / len(high_conf), 3)
            if high_conf
            else 0,
            "mid_conf_acc": round(sum(1 for r in mid_conf if r["correct"]) / len(mid_conf), 3)
            if mid_conf
            else 0,
            "low_conf_acc": round(sum(1 for r in low_conf if r["correct"]) / len(low_conf), 3)
            if low_conf
            else 0,
            "avg_score": round(sum(scores) / len(scores), 1) if scores else 0,
            "score_pct_corr": round(
                sum((s - 5) * p for s, p in zip(scores, pcts, strict=True)) / len(scores), 2
            )
            if scores
            else 0,
        }
        _val_state["running"] = False

    # 后台线程逐品种执行 Agent 验证,接口立即返回;进度经 /api/agent_validation/status 轮询。
    t = threading.Thread(target=run_validation, daemon=True)
    t.start()

    return jsonify({"status": "started", "total": len(varieties), "date": trade_date})


# 【功能】轮询 Agent 验证进度(返回 _val_state 当前内容)。
@app.route("/api/agent_validation/status")
def api_validation_status():
    return jsonify(_val_state)


# 主入口:以脚本方式运行时启动调度器 + Waitress 生产服务器。
# · 调度器启动失败只打印警告,不影响看板启动。
# · waitress.serve 以 8 个线程监听 0.0.0.0:5000,channel_timeout=600 秒。
if __name__ == "__main__":
    try:
        from scheduler import start_scheduler  # 【调用包】调度器启动

        start_scheduler(schedule_times=["08:00", "18:00"])
        print("Scheduler started: daily at 08:00, 18:00")
    except Exception as e:
        print(f"Scheduler not started: {e}")

    from waitress import serve  # 【调用包】生产级 WSGI 服务器(多线程托管 Flask)

    print("FuturesMind Dashboard: http://localhost:5000")
    serve(app, host="0.0.0.0", port=5000, threads=8, channel_timeout=600)
