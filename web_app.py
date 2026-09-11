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
#   3.5) 运行分析输入数据: /api/run_input_data/<品种>(价格/基差/库存/情绪/新闻/宏观最新快照,供运行分析页小看板)
#   4) 主页面:     / 、/test
#   5) 分析工具:   /api/run_analysis(SSE 流式) + /api/progress 轮询 +
#                  /api/pause /api/resume /api/stop /api/feedback,
#                  /api/analysis_results、/api/backtest、/api/history、/api/compare
#   5.5) 研报管理: /api/research/upload(上传)、/api/research(列表)、
#                  /api/research/<id>(详情)、/api/research/<id>/download(下载原件)、
#                  /api/research/<id> DELETE(删除);
#                  上传后后台线程提取文本(PDF/图片 OCR)→ LLM 结构化 → 落库 + 写聚合 JSON
#   5.6) 国君数据:  /api/gtja/views(周度观点信号;晨报日度已下线)、
#                  /api/gtja/dataset/basis、/api/gtja/dataset/inventory(数据仓库基差/仓单数据集)
#   6) 报告导出:   /api/report/<文件>/html(网页版)、/api/report/<文件>/pdf、/api/report/<文件>/md
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

import concurrent.futures  # 【调用包】线程池超时异常(宏观块 20s 超时降级用)
import glob  # 【调用包】批量路径匹配(如 batch_*.jsonl 文件列举)
import html  # 【调用包】HTML 转义(网页版报告标题/文件名安全显示)
import io  # 【调用包】内存字节流(BytesIO,PDF 下载响应)
import json  # 【调用包】JSON 序列化/反序列化(配置、行情缓存、批次文件)
import logging  # 【调用包】日志记录(报告落盘异常等)
import os  # 【调用包】路径/环境变量操作
import re  # 【调用包】正则提取报告章节与评级字段
import secrets  # 【调用包】生成安全 token 与 secret_key
import sys  # 【调用包】模块搜索路径调整、解释器路径获取
import tempfile  # 【调用包】临时文件(Playwright 打印 PDF 的中转文件)
import threading  # 【调用包】后台线程与进度锁
import time  # 【调用包】耗时统计与轮询间隔
from collections import defaultdict  # 【调用包】字典计数(缺失键自动给默认值,非标数据聚合用)
from concurrent.futures import ThreadPoolExecutor  # 【调用包】线程池(数据看板三数据源并行拉取)
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
    ACTIVE_VARIETIES,  # 【调用包】20 品种活跃池(2026-09-09 起 UI/采集/展示白名单)
    VARIETY_METADATA,
    get_futures_basis,
    get_futures_indicators,  # 【调用包】技术指标(MA/MACD/RSI/BOLL/ATR,CSV)供小看板技术面块
    get_futures_inventory,
    get_futures_macro,  # 【调用包】中国宏观指标(GDP/PMI/固投/地产/工业增加值/建筑业/CPI/PPI/M2/LPR/社融,格式化文本)
    get_futures_news,  # 【调用包】商品+宏观新闻(格式化文本,经 route_to_vendor 供分析师工具使用)
    get_futures_price,
    get_futures_supply_demand,  # 【调用包】供需指标(外部JSON+免费API,格式化文本)供小看板基本面块
    get_variety_info,  # 【调用包】品种元数据(JSON 字符串)供小看板品种信息块
    get_verified_quote,  # 【调用包】目标日校验行情快照(VERIFIED_SNAPSHOT,分析师数值单一事实来源)
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
# 【品种池】2026-09-09 起只展示 ACTIVE_VARIETIES(20 品种);元数据其余键保留但隐藏。
@app.route("/api/varieties")
def api_varieties():
    """List all supported varieties with metadata."""
    result = []
    for code, meta in sorted(VARIETY_METADATA.items()):
        if code not in ACTIVE_VARIETIES:
            continue
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
            # 【关键】_pearson 在序列方差为 0(恒定值)时返回 None,直接 round 会 TypeError → 先判空
            _r = _pearson(px, iv)
            result["price_inventory_r"] = round(_r, 4) if _r is not None else None
            result["price_inventory_n"] = len(px)
        if len(px) >= 6:  # 【关键】变化率 R:close 日环比 vs 库存日环比,去掉量纲差异
            px_chg = [_pct(a, b) for a, b in zip(px[1:], px[:-1], strict=True)]
            iv_chg = [_pct(a, b) for a, b in zip(iv[1:], iv[:-1], strict=True)]
            valid = [(a, b) for a, b in zip(px_chg, iv_chg, strict=True) if a is not None and b is not None]
            if len(valid) >= 5:
                _r2 = _pearson([a for a, _ in valid], [b for _, b in valid])
                result["ret_inventory_r"] = round(_r2, 4) if _r2 is not None else None

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
            _rb = _pearson([a for a, _ in pairs], [b for _, b in pairs])
            result["basis_price_r"] = round(_rb, 4) if _rb is not None else None

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
# 【返回】{"_meta": {variety, name, sector, price_points, inventory_available, basis_available,
#           research_available, ...}, "price": [{date, close}], "inventory": {available, points, note},
#           "basis": {available, points, note}, "research": {available, note, overlay, standalone},
#           "analysis": {关联分析结果}}。
# 【关键】价格走 _adjusted_price_points 后复权(与 /api/price、/api/overlay 口径一致);
#   仓单库存/基差分别经 _inventory_points/_basis_points 解析,数据源不可用(DATA_*/NO_DATA_*)时优雅降级为
#   available=false + note,不抛错。无仓单品种(SH/WR)、无基差品种(AO/CS/LC/SI)由此自然呈现空态。
#   研报基本面指标(第 4 源)经 _research_dashboard_series 读 DB 结构化 data_points,同样只降级不抛错。
@app.route("/api/dashboard/<variety>")
def api_dashboard(variety):
    """Return price / inventory / basis / research-metric series + relationship analysis for the dashboard tab."""
    code = variety.upper()
    days = request.args.get("days", 180, type=int)
    days = max(30, min(days, 365))  # 【变量】回看天数钳制 30~365
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    # 【关键】四数据源用线程池并行拉取:基差最慢(futures_spot_price_daily 实测 13~175s,现已 6h TTL
    #   缓存),并行后首拉约等于最慢一项、刷新则全部命中缓存毫秒级返回;各项各自 try/except,
    #   任一失败只降级该项(price→price_note / inventory、basis→available=false / research→available=false),
    #   不拖垮整个看板。研报指标读 SQLite(每操作新连接、线程局部 get_db,并发安全)。
    price: list[dict] = []
    price_note = ""
    inv = {"available": False, "points": [], "note": ""}
    basis = {"available": False, "points": [], "note": ""}
    research = {"available": False, "note": "", "overlay": {}, "standalone": {}}
    margin = {"available": False, "note": "", "series": [], "stats": {}}

    def _load_price():
        nonlocal price, price_note
        try:
            price_result = get_futures_price(code, start_date, end_date)  # 【调用函数】跨模块获取行情 CSV 文本
            price, _, _ = _adjusted_price_points(price_result, code)  # 【调用函数】实时主连 → 后复权序列
        except Exception as e:  # 【关键】价格异常不 500:降级为空序列 + note,前端显示失败提示
            logger.warning("dashboard price %s: %s", code, e)
            price_note = f"DATA_ERROR: {e}"

    def _load_inventory():
        # 仓单库存(东财 futures_inventory_em;SH/WR 等品种无数据 → 优雅降级)
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

    def _load_basis():
        # 基差(akshare futures_spot_price_daily;AO/CS/LC/SI 等品种无数据 → 优雅降级)
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

    def _load_research():
        # 研报基本面指标(DB 结构化 data_points → 时序点;_research_dashboard_series 内部已 try/except)
        try:
            return _research_dashboard_series(get_db(), code)  # 【调用函数】纯函数,读 DB 不读聚合
        except Exception as e:
            logger.warning("dashboard research %s: %s", code, e)
            return {"available": False, "note": f"DATA_ERROR: {e}", "overlay": {}, "standalone": {}}

    def _load_margin():
        # 盘面利润(期货价格合成产业链利润,仅 RB/HC/J 有配方;无配方品种优雅降级)
        from tradingagents.dataflows.futures_margin import (  # 【调用包】懒导入避免加重模块初始化
            MARGIN_FORMULAS,
            compute_margin_series,
        )
        if code not in MARGIN_FORMULAS:
            margin["note"] = (
                f"MARGIN_NO_FORMULA: {code} 无盘面利润配方(当前支持 "
                f"{', '.join(sorted(MARGIN_FORMULAS))})"
            )
            return
        result = compute_margin_series(code, start_date, end_date)
        if result is None:
            margin["note"] = f"NO_DATA_AVAILABLE: {code} 盘面利润配方腿价格缺失"
            return
        margin["available"] = True
        margin["series"] = result["points"]
        margin["stats"] = {
            k: result[k]
            for k in ("name", "note", "latest", "latest_date", "pct_rank",
                      "mean", "min", "max", "wow", "legs")
        }

    with ThreadPoolExecutor(max_workers=5) as ex:  # 【变量】并行池:价格/库存/基差/研报指标/盘面利润 5 个任务
        futures = [ex.submit(fn) for fn in (_load_price, _load_inventory, _load_basis, _load_research, _load_margin)]
        for fut in futures:  # 【关键】任务内部已 catch 全部异常,result() 不会抛,等各项都完成
            fut.result()
        research = futures[3].result()  # _load_research 返回值(其余走闭包变量原地写回)

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
        "price_note": price_note,
        "inventory_available": inv["available"],
        "basis_available": basis["available"],
        "inventory_note": inv["note"],
        "basis_note": basis["note"],
        "research_available": research["available"],
        "research_note": research.get("note", ""),
        "margin_available": margin["available"],
        "margin_note": margin["note"],
    }
    return jsonify({
        "_meta": meta, "price": price, "inventory": inv, "basis": basis,
        "research": research, "margin": margin, "analysis": analysis,
    })


# ---------------------------------------------------------------------------
# 研报基本面指标进数据看板:研报结构化 data_points → 时序点(纯函数,读 DB 不读聚合)
# ---------------------------------------------------------------------------


# 【变量】研报基本面指标白名单(键, 中文标签)—— 只取 LLM 结构化/确定性补漏落库的
#         数值点,启发式文本兜底(_heuristic_fund_from_text)不进看板序列。
_RESEARCH_DASH_KEYS = (
    ("basis", "基差"),
    ("warehouse_receipts", "仓单"),
    ("operating_rate", "开工率"),
    ("processing_margin", "加工利润"),
    ("spot_price", "现货价"),
    ("social_inventory", "社会库存"),
    ("mill_inventory", "钢厂库存"),
)
# 【变量】允许叠加到现有看板面板的键(口径安全):仅 basis(元/吨,与看板基差轴同
#         单位);仓单(张/手)与东财库存序列单位是否一致未实证,先一律独立卡,
#         实测一致再把键挪进本元组即可(前端按 overlay 键自动叠加)。
_RESEARCH_DASH_OVERLAY_KEYS = ("basis",)


def _dash_num(v) -> float | None:
    """研报指标值 → 可绘图 float;非数值(文本/空)返回 None(不进序列)。

    【关键】只做类型归一:int/float 直接收;"85%"/"1,234" 等去 % 与逗号后可解析才收;
            其余(日期串、区间描述文本)一律 None —— 看板序列里不允许启发式猜测值。
    """
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").rstrip("%")
    try:
        return float(s)
    except ValueError:
        return None


def _research_dashboard_series(db, code: str) -> dict:
    """研报结构化指标 → 数据看板时序点(叠加 + 独立卡两个出口)。

    【功能】/api/dashboard 第 4 数据源:把该品种历史研报里 LLM 抽出的基本面读数
            (基差/仓单/开工率/加工利润/现货价/社会库存/钢厂库存)落成时序点,
            随每天研报采集/上传自动补新点。读 DB 全量(聚合 JSON 只存最近 10 份
            且无 publish_date,时序必须走 DB),复用 _row_research_fund_metrics 的
            段匹配/合并模式,但只收确定性数值(启发式兜底文本不进图)。
    【参数】db: AgentSenseDB 实例;code: 品种代码(大写)。
    【返回】{"available": bool, "note": str,
             "overlay": {key: [点]}, "standalone": {key: [点]}}
            点 = {date, value, unit, note, point_date, source, title, report_id}。
    【关键逻辑】· 日期归键:publish_date 优先,缺则回退 uploaded_at[:10];
              · (键, 日期) 去重:同键同日多份研报留 uploaded_at 最新一份;
              · 叠加/独立分流:键在 _RESEARCH_DASH_OVERLAY_KEYS → overlay,其余 standalone;
              · 整体 try/except:任何异常降级 available=False,看板绝不 500。
    """
    empty = {"available": False, "note": "", "overlay": {}, "standalone": {}}
    try:
        rows = [r for r in (db.list_research_reports(code, limit=2000) or [])
                if (r.get("status") or "") == "done"]
        if not rows:
            return {**empty, "note": "该品种暂无已入库研报"}
        code_u = str(code or "").upper()
        latest: dict[tuple[str, str], tuple[str, dict]] = {}  # (键,日期) → (uploaded_at, 点)
        for r in rows:
            try:
                sd = json.loads(r.get("structured_data") or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(sd, dict):
                continue
            # 日期归键统一三优先级:DB publish_date 列 → structured_data.publish_date → uploaded_at
            eff = (
                (r.get("publish_date") or "").strip()
                or (sd.get("publish_date") or "").strip()
                or (r.get("uploaded_at") or "")
            )[:10]
            if not eff:
                continue
            items = sd.get("varieties")
            seg = next(
                (i for i in (items or []) if isinstance(i, dict) and str(i.get("variety") or "").upper() == code_u),
                items[0] if isinstance(items, list) and items else sd,
            )
            if not isinstance(seg, dict):
                continue
            # 指标可能在段内 data_points(嵌套)或段顶层(旧行平铺);两处合并,嵌套优先
            dp = {}
            nested = seg.get("data_points")
            if isinstance(nested, dict):
                dp.update(nested)
            for k, _ in _RESEARCH_DASH_KEYS:
                if k in dp:
                    continue
                v = seg.get(k)
                if v is not None and v != "":
                    dp[k] = v
            uploaded = r.get("uploaded_at") or ""
            for k, _ in _RESEARCH_DASH_KEYS:
                fv = _fund_value(dp.get(k))  # 【调用函数】{value,unit,date,note} 归一
                if fv is None:
                    continue
                val = _dash_num(fv["value"])
                if val is None:
                    continue  # 文本值(区间描述等)不进时序
                point = {
                    "date": eff,
                    "value": val,
                    "unit": fv.get("unit") or "",
                    "note": fv.get("note") or "",
                    "point_date": fv.get("date") or "",
                    "source": r.get("source") or "",
                    "title": r.get("title") or "",
                    "report_id": r.get("id"),
                }
                prev = latest.get((k, eff))
                if prev is None or uploaded >= prev[0]:  # 同键同日留最新一份
                    latest[(k, eff)] = (uploaded, point)
        overlay: dict[str, list] = {}
        standalone: dict[str, list] = {}
        for (k, _), (_, pt) in latest.items():
            bucket = overlay if k in _RESEARCH_DASH_OVERLAY_KEYS else standalone
            bucket.setdefault(k, []).append(pt)
        for d in (overlay, standalone):
            for pts in d.values():
                pts.sort(key=lambda p: p["date"])  # 时序升序供 ECharts line
        if not overlay and not standalone:
            return {**empty, "note": "研报结构化指标暂无可绘图数值(未披露或文本值)"}
        return {"available": True, "note": "", "overlay": overlay, "standalone": standalone}
    except Exception as e:  # 【关键】研报指标异常不拖垮看板:降级空态
        logger.warning("research dashboard series %s: %s", code, e)
        return {**empty, "note": f"DATA_ERROR: {e}"}


# ---------------------------------------------------------------------------
# 运行分析输入数据小看板:纯解析函数 + 聚合接口
# ---------------------------------------------------------------------------


# 【功能】解析 get_futures_basis CSV(含 # 尾注)→ (points, structure|None)。
# 【返回】points: [{date, spot_price, dom_basis, dom_basis_rate, near_basis, near_basis_rate}, ...];
#        structure 取自 '# Latest basis: x.xx — BACKWARDATION (...)' 尾注,无尾注返回 None。
def _run_input_basis_points(csv_text: str) -> tuple[list[dict], str | None]:
    structure = None
    lines = []
    for ln in csv_text.strip().split("\n"):
        ln = ln.rstrip()
        if ln.lstrip().startswith("#"):
            if "Latest basis" in ln:
                m = re.search(r"—\s*([A-Za-z]+)", ln)
                if m:
                    structure = m.group(1)
            continue
        lines.append(ln)
    if not lines:
        return [], structure
    header = [h.strip() for h in lines[0].split(",")]
    idx = {h: i for i, h in enumerate(header)}

    def _col(row, name):
        i = idx.get(name)
        if i is None or i >= len(row):
            return None
        v = row[i].strip()
        return v or None

    def _f(v):
        try:
            return float(v) if v is not None else None
        except ValueError:
            return None

    points = []
    for ln in lines[1:]:
        row = [c.strip() for c in ln.split(",")]
        date = _col(row, "date")
        if not date:
            continue
        points.append({
            "date": date,
            "spot_price": _f(_col(row, "spot_price")),
            "dom_basis": _f(_col(row, "dom_basis")),
            "dom_basis_rate": _f(_col(row, "dom_basis_rate")),
            "near_basis": _f(_col(row, "near_basis")),
            "near_basis_rate": _f(_col(row, "near_basis_rate")),
        })
    return points, structure


# 【功能】基差结构兜底:优先 dom_basis,无则 near_basis;正=BACKWARDATION 负=CONTANGO 零=FLAT。
def _structure_from_basis(point: dict) -> str | None:
    b = point.get("dom_basis") if point.get("dom_basis") is not None else point.get("near_basis")
    if b is None:
        return None
    return "BACKWARDATION" if b > 0 else ("CONTANGO" if b < 0 else "FLAT")


# 【功能】从 get_futures_inventory 的 '# Warehouse receipt trend: BUILDING (...)' 尾注取趋势词。
def _inventory_trend(csv_text: str) -> str | None:
    for ln in csv_text.split("\n"):
        if ln.lstrip().startswith("#") and "Warehouse receipt trend" in ln:
            m = re.search(r"trend:\s*(\w+)", ln)
            if m:
                return m.group(1)
    return None


# 【功能】解析 get_futures_news 文本 → [{time, source, title, summary}]。
# 【关键】行格式: '<time> [<source>] <title>',可选下一行 '  <summary>'。
#        '# ' 注释行与空行跳过;解析不出任何条目返回 []。
_NEWS_ITEM_RE = re.compile(r"^(.+?)\s+\[([^\]]+)\]\s*(.*)$")


def _parse_news_text(text: str) -> list[dict]:
    lines = [ln.rstrip() for ln in text.strip().split("\n")]
    items, i = [], 0
    while i < len(lines):
        line = lines[i]
        if not line or line.lstrip().startswith("#"):
            i += 1
            continue
        m = _NEWS_ITEM_RE.match(line)
        if not m:
            i += 1
            continue
        item = {
            "time": m.group(1).strip(),
            "source": m.group(2).strip(),
            "title": m.group(3).strip(),
            "summary": "",
        }
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if nxt.startswith("  ") and nxt.strip() and not nxt.lstrip().startswith("#"):
            item["summary"] = nxt.strip()
            i += 2
        else:
            i += 1
        items.append(item)
    return items


# 【功能】解析 get_futures_macro 文本 → (items, raw)。
# 【返回】items: [{name, value, note}];value=None+note 表示该指标不可用。
# 【关键】'## 节' + 两空格缩进键值;失败节 '## X: UNAVAILABLE (err)' → value None + note;
#        没有任何 '## ' 节(格式完全不符)→ 返回 ([], 原文),前端透传原文。
_MACRO_VALUE_LABELS = [
    ("GDP", "GDP 同比"),
    ("PMI", "制造业PMI"),
    ("固定资产投资", "同比增长"),
    ("房地产", "指数值"),
    ("工业增加值", "同比增长"),
    ("建筑业", "指数值"),
    ("CPI", "CPI 同比"),
    ("PPI", "PPI 同比"),
    ("货币供应", "M2 同比"),
    ("LPR", "LPR1Y"),
    ("社会融资规模增量", "增量"),
]


def _pick_macro_value(name: str, kv: list[tuple[str, str]]) -> str | None:
    """按优先级标签挑该节最有信息量的值;节名子串匹配,避免 '建筑业指数' vs '建筑业' 差异。"""
    for key, want in _MACRO_VALUE_LABELS:
        if name.startswith(key) or key.startswith(name):
            for k, v in kv:
                if want in k:
                    return v
    for _k, v in kv:  # 兜底:第一个非空值
        if v:
            return v
    return None


def _parse_macro_text(text: str) -> tuple[list[dict], str]:
    sections, cur = [], None
    for ln in text.strip().split("\n"):
        ln = ln.rstrip()
        if ln.startswith("## "):
            header = ln[3:].strip()
            if "UNAVAILABLE" in header:
                name, _, err = header.partition(":")
                cur = {"name": name.strip(), "available": False, "err": err.strip(), "kv": []}
            else:
                cur = {"name": header.split("(")[0].strip(), "available": True, "err": "", "kv": []}
            sections.append(cur)
        elif cur and cur["available"] and ln.startswith("  ") and ":" in ln:
            k, _, v = ln.strip().partition(":")
            cur["kv"].append((k.strip(), v.strip()))
    if not sections:
        return [], text
    items = []
    for s in sections:
        items.append({
            "name": s["name"],
            "value": _pick_macro_value(s["name"], s["kv"]) if s["available"] else None,
            "note": s["err"] if not s["available"] else "",
        })
    return items, ""


# 【功能】解析 get_futures_indicators 的 CSV → (最新一行指标 dict, 最近 N 行指标 dict 列表)。
# 【关键】指标列多(sma/ema/macd/rsi/boll/atr/volume/oi),保留全部列;latest 供顶部摘要,rows 供下拉表。
def _parse_indicators_csv(csv_text: str, rows: int = 5) -> tuple[dict | None, list[dict]]:
    lines = [ln.rstrip() for ln in csv_text.strip().split("\n") if ln.strip() and not ln.lstrip().startswith("#")]
    if len(lines) < 2:
        return None, []
    header = [h.strip() for h in lines[0].split(",")]
    out = []
    for ln in lines[1:]:
        cells = [c.strip() for c in ln.split(",")]
        if len(cells) < len(header):
            continue
        out.append(dict(zip(header, cells, strict=False)))  # 已保证 cells 不缺列,zip 无截断
    if not out:
        return None, []
    return out[-1], out[-rows:]


# 【功能】解析 get_verified_quote 的 VERIFIED_SNAPSHOT 文本 → 结构化 dict。
# 【返回】{name, date, exchange, unit, price_limit, margin, ohlcv:{...}, levels:{...}, raw}
def _parse_verified_quote(text: str) -> dict:
    snap: dict = {"ohlcv": {}, "levels": {}, "raw": text}
    for ln in text.split("\n"):
        ln = ln.strip()
        if ln.startswith("VERIFIED_SNAPSHOT |"):
            head = ln.split("|")
            if len(head) > 1:
                snap["name"] = head[1].strip()
            if len(head) > 2:
                snap["date"] = head[2].strip()
        elif ln.startswith(("Exchange:", "Price Limit:")):
            for part in ln.split("|"):
                part = part.strip()
                if part.startswith("Exchange:"):
                    snap["exchange"] = part.split(":", 1)[1].strip()
                elif part.startswith("Unit:"):
                    snap["unit"] = part.split(":", 1)[1].strip()
                elif part.startswith("Price Limit:"):
                    snap["price_limit"] = part.split(":", 1)[1].strip()
                elif part.startswith("Margin:"):
                    snap["margin"] = part.split(":", 1)[1].strip()
        elif ln.startswith(("Open:", "High:", "Low:", "Close:", "Volume:", "Open Int:", "Day Change:")):
            k, _, v = ln.partition(":")
            snap["ohlcv"][k.strip()] = v.strip()
        elif ln.startswith("SMA(") or ln.startswith("Price vs SMA20:"):
            k, _, v = ln.partition(":")
            snap["levels"][k.strip()] = v.strip()
    return snap


# 【功能】解析 get_futures_supply_demand 的格式化文本 → [{title, lines}] 顶层 '## ' 节。
# 【关键】'### ' 与两空格缩进行并入所属节的 lines;无 '## ' 时返回 []。
def _parse_supply_demand(text: str) -> list[dict]:
    sections, cur = [], None
    for ln in text.strip().split("\n"):
        ln = ln.rstrip()
        if ln.startswith("## "):
            cur = {"title": ln[3:].strip(), "lines": []}
            sections.append(cur)
        elif cur and ln.strip():
            cur["lines"].append(ln)
    return sections


# 【功能】运行分析输入数据小看板:返回所选品种"接下来分析将用到的真实数据"的最新可用快照。
# 【参数】URL 路径 <variety>: 品种代码(如 rb)。
# 【返回】_meta + price/basis/inventory/sentiment/news/macro 六块;各块 available=false + note 优雅降级。
# 【关键】1) 时间口径与数据看板一致(end_date=datetime.now(),meta.data_as_of 标注最新数据日);
#        2) 价格/基差/库存/新闻走 ThreadPoolExecutor(max_workers=4) 并行(api_dashboard 同款);
#        3) 宏观(11 个 akshare 子指标,最慢)另起单线程带 60s 超时(进程内 30 分钟新鲜度缓存,重复调用不重打网),超时只降级该项不拖垮首拉;
#        4) 情绪直接读 {code}_sentiment.json(本地毫秒级);
#        5) 新闻/宏观为格式化文本,经 _parse_news_text/_parse_macro_text 结构化;解析失败 raw 透传。
@app.route("/api/run_input_data/<variety>")
def api_run_input_data(variety):
    """Return latest-available snapshot of the data the run-analysis will consume."""
    code = variety.upper()
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    price = {"available": False, "note": "", "date": None, "latest_close": None,
             "change_pct": None, "series": []}
    basis = {"available": False, "note": "", "date": None, "near_basis": None,
             "near_basis_rate": None, "dom_basis": None, "dom_basis_rate": None,
             "structure": None, "series": []}
    inv = {"available": False, "note": "", "date": None, "inventory": None,
           "change": None, "trend": None, "series": []}
    news = {"available": False, "note": "", "items": [], "raw": ""}
    macro = {"available": False, "note": "", "items": [], "raw": ""}
    sentiment = {"available": False, "note": "", "label": None, "score": None,
                 "bullish_ratio": None, "bearish_ratio": None, "trend_label": None, "data_end": None}
    variety_info = {"available": False, "note": "", "data": None}
    indicators = {"available": False, "note": "", "latest": None, "rows": []}
    verified_quote = {"available": False, "note": "", "snapshot": None}
    supply_demand = {"available": False, "note": "", "text": "", "sections": []}

    def _load_price():
        try:
            raw = get_futures_price(code, start_date, end_date)  # 【调用函数】跨模块获取行情 CSV 文本
            if raw.startswith(("DATA_ERROR", "DATA_UNAVAILABLE", "NO_DATA_AVAILABLE")):
                price["note"] = raw
                return
            pts, _, _ = _adjusted_price_points(raw, code)  # 【调用函数】实时主连 → 后复权序列
            if pts:
                price["available"] = True
                price["latest_close"] = pts[-1]["close"]
                price["date"] = pts[-1]["date"]
                price["series"] = pts[-10:]  # 【关键】近10日序列(下拉表展示完整走势)
                if len(pts) >= 2 and pts[-2]["close"]:
                    price["change_pct"] = (pts[-1]["close"] - pts[-2]["close"]) / pts[-2]["close"] * 100
            else:
                price["note"] = "NO_DATA_AVAILABLE: 价格数据不足"
        except Exception as e:  # 【关键】单项异常不 500,降级为 note
            logger.warning("run-input price %s: %s", code, e)
            price["note"] = f"DATA_ERROR: {e}"

    def _load_basis():
        try:
            raw = get_futures_basis(code, start_date, end_date)  # 【调用函数】跨模块获取基差 CSV 文本
            if raw.startswith(("DATA_ERROR", "DATA_UNAVAILABLE", "NO_DATA_AVAILABLE")):
                basis["note"] = raw
                return
            pts, structure = _run_input_basis_points(raw)  # 【调用函数】基差全列 + 结构尾注
            if pts:
                basis["available"] = True
                basis["date"] = pts[-1]["date"]
                basis["near_basis"] = pts[-1]["near_basis"]
                basis["near_basis_rate"] = pts[-1]["near_basis_rate"]
                basis["dom_basis"] = pts[-1]["dom_basis"]
                basis["dom_basis_rate"] = pts[-1]["dom_basis_rate"]
                basis["structure"] = structure or _structure_from_basis(pts[-1])
                basis["series"] = pts[-10:]  # 【关键】近10日基差序列(下拉表)
            else:
                basis["note"] = "NO_DATA_AVAILABLE: 基差无数据(该品种此接口不覆盖)"
        except Exception as e:
            logger.warning("run-input basis %s: %s", code, e)
            basis["note"] = f"DATA_ERROR: {e}"

    def _load_inventory():
        try:
            raw = get_futures_inventory(code)  # 【调用函数】跨模块获取仓单库存 CSV 文本
            if raw.startswith(("DATA_ERROR", "DATA_UNAVAILABLE", "NO_DATA_AVAILABLE")):
                inv["note"] = raw
                return
            pts = _inventory_points(raw)  # 【调用函数】解析仓单 CSV → 序列
            if pts:
                inv["available"] = True
                inv["date"] = pts[-1]["date"]
                inv["inventory"] = pts[-1]["inventory"]
                inv["change"] = pts[-1]["change"]
                inv["trend"] = _inventory_trend(raw)  # 【调用函数】趋势尾注 → BUILDING/DRAINING/STABLE
                inv["series"] = pts[-10:]  # 【关键】近10日仓单序列(下拉表)
            else:
                inv["note"] = "NO_DATA_AVAILABLE: 仓单库存无数据(该品种此接口不覆盖)"
        except Exception as e:
            logger.warning("run-input inventory %s: %s", code, e)
            inv["note"] = f"DATA_ERROR: {e}"

    def _load_news():
        try:
            raw = get_futures_news(code)  # 【调用函数】新闻格式化文本(1 次网络)
            if raw.startswith(("DATA_ERROR", "DATA_UNAVAILABLE", "NO_DATA_AVAILABLE")):
                news["note"] = raw
                return
            items = _parse_news_text(raw)  # 【调用函数】文本 → items
            if items:
                news["available"] = True
                news["items"] = items
            else:
                news["note"] = "NEWS_PARSE_ERROR: 新闻文本解析失败,已透传原文"
                news["raw"] = raw
        except Exception as e:
            logger.warning("run-input news %s: %s", code, e)
            news["note"] = f"DATA_ERROR: {e}"

    def _load_variety_info():
        try:
            raw = get_variety_info(code)  # 【调用函数】品种元数据 JSON 字符串(交易所/合约单位/涨跌停/保证金)
            data = json.loads(raw)
            variety_info["available"] = True
            variety_info["data"] = data
        except Exception as e:
            logger.warning("run-input variety_info %s: %s", code, e)
            variety_info["note"] = f"DATA_ERROR: {e}"

    def _load_indicators():
        try:
            raw = get_futures_indicators(code, start_date, end_date)  # 【调用函数】技术指标 CSV(内部复用价格缓存)
            if raw.startswith(("DATA_ERROR", "DATA_UNAVAILABLE", "NO_DATA_AVAILABLE")):
                indicators["note"] = raw
                return
            latest, rows = _parse_indicators_csv(raw)  # 【调用函数】CSV → 最新行 + 近5行
            if latest:
                indicators["available"] = True
                indicators["latest"] = latest
                indicators["rows"] = rows
            else:
                indicators["note"] = "NO_DATA_AVAILABLE: 技术指标无数据"
        except Exception as e:
            logger.warning("run-input indicators %s: %s", code, e)
            indicators["note"] = f"DATA_ERROR: {e}"

    def _load_verified_quote():
        try:
            raw = get_verified_quote(code, date=end_date)  # 【调用函数】目标日(今日)校验快照;非交易日自动取最近
            if raw.startswith(("VERIFIED_SNAPSHOT_ERROR", "VERIFIED_SNAPSHOT_UNAVAILABLE")):
                verified_quote["note"] = raw
                return
            snap = _parse_verified_quote(raw)  # 【调用函数】文本 → OHLCV + 关键位
            verified_quote["available"] = True
            verified_quote["snapshot"] = snap
        except Exception as e:
            logger.warning("run-input verified_quote %s: %s", code, e)
            verified_quote["note"] = f"DATA_ERROR: {e}"

    def _load_supply_demand():
        try:
            raw = get_futures_supply_demand(code)  # 【调用函数】供需文本(外部 JSON + 免费 API)
            if raw.startswith(("DATA_ERROR", "DATA_UNAVAILABLE", "NO_DATA_AVAILABLE")):
                supply_demand["note"] = raw
                return
            sections = _parse_supply_demand(raw)  # 【调用函数】文本 → 顶层节
            if sections:
                supply_demand["available"] = True
                supply_demand["text"] = raw
                supply_demand["sections"] = sections
            else:
                supply_demand["note"] = "NO_DATA_AVAILABLE: 供需数据为空"
        except Exception as e:
            logger.warning("run-input supply_demand %s: %s", code, e)
            supply_demand["note"] = f"DATA_ERROR: {e}"

    def _load_macro():
        try:
            raw = get_futures_macro()  # 【调用函数】宏观文本(内部 11 指标各自 try/except)
            items, raw_err = _parse_macro_text(raw)  # 【调用函数】文本 → items
            if items and any(it["value"] is not None for it in items):
                macro["available"] = True
                macro["items"] = items
            elif items and not raw_err:
                macro["note"] = "NO_DATA_AVAILABLE: 宏观指标全部不可用"
                macro["items"] = items
            else:
                macro["note"] = "MACRO_PARSE_ERROR: 宏观文本解析失败,已透传原文"
                macro["raw"] = raw
        except Exception as e:
            logger.warning("run-input macro: %s", e)
            macro["note"] = f"DATA_ERROR: {e}"

    # 【关键】价格/基差/库存/新闻/品种信息/技术指标/校验报价并行(每任务内部已 catch,result 不抛)。
    #         指标与校验报价内部复用价格缓存(5min),品种信息纯本地,不显著增延迟。
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(fn) for fn in (
            _load_price, _load_basis, _load_inventory, _load_news,
            _load_variety_info, _load_indicators, _load_verified_quote,
        )]
        for f in futs:
            f.result()

    # 【关键】宏观 + 供需另起双线程,各 20s 超时;超时只降级对应块。shutdown(wait=False) 让后台线程
    #        跑完再返回,避免 with 块退出时阻塞等待(否则超时形同虚设)。
    mex = ThreadPoolExecutor(max_workers=2)
    mf = mex.submit(_load_macro)
    sf = mex.submit(_load_supply_demand)
    try:
        mf.result(timeout=60)
    except concurrent.futures.TimeoutError:
        macro["note"] = "MACRO_TIMEOUT: 宏观数据抓取超时(60s),可稍后刷新重试"
    try:
        sf.result(timeout=20)
    except concurrent.futures.TimeoutError:
        supply_demand["note"] = "SUPPLY_DEMAND_TIMEOUT: 供需数据抓取超时(20s),可稍后刷新重试"
    mex.shutdown(wait=False)

    # 情绪:本地 JSON 同步读(与 api_sentiment 同口径)
    sent_path = SENTIMENT_DIR / f"{code}_sentiment.json"
    if sent_path.exists():
        try:
            with open(sent_path, encoding="utf-8") as f:
                sd = json.load(f)
            ss = sd.get("data", {}).get("social_sentiment", {})
            ds = sd.get("data", {}).get("daily_series", [])
            sentiment["available"] = True
            sentiment["label"] = ss.get("overall_sentiment_label")
            sentiment["score"] = ss.get("avg_score")
            sentiment["bullish_ratio"] = ss.get("bullish_ratio")
            sentiment["bearish_ratio"] = ss.get("bearish_ratio")
            sentiment["trend_label"] = ss.get("trend_label")
            if ds:
                sentiment["data_end"] = ds[-1].get("date")
            elif ss.get("date_range"):
                sentiment["data_end"] = str(ss["date_range"]).split("~")[-1].strip()
        except Exception as e:
            logger.warning("run-input sentiment %s: %s", code, e)
            sentiment["note"] = f"DATA_ERROR: {e}"
    else:
        sentiment["note"] = f"无情绪数据({code})"

    # 数据截至:取各可用来源最新日期,无则用请求日
    dates = [d for d in (price["date"], basis["date"], inv["date"], sentiment["data_end"]) if d]
    data_as_of = max(dates) if dates else datetime.now().strftime("%Y-%m-%d")
    meta = {
        "variety": code,
        "name": VARIETY_METADATA.get(code, {}).get("name", code),
        "sector": _get_sector(code),
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data_as_of": data_as_of,
    }
    return jsonify({
        "_meta": meta, "price": price, "basis": basis, "inventory": inv,
        "sentiment": sentiment, "news": news, "macro": macro,
        "variety_info": variety_info, "indicators": indicators,
        "verified_quote": verified_quote, "supply_demand": supply_demand,
    })


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
    codes = [c for c in sector_map.get(sector, []) if c in ACTIVE_VARIETIES]  # 【品种池】只展示池内品种
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
#         elapsed: 本次分析耗时秒数;modules_note: 本次跳过的模块说明(可选,
#         非空时在报告头部写一行"本次运行跳过模块",默认空=完整档不写)。
# 【返回】写入成功的文件路径;目录不存在会自动创建。
def _persist_analysis_report(symbol, trade_date, final_state, elapsed, modules_note=""):
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
    # 2026-09-03:机构(研报) vs 散户(社媒) 对比卡作为第 8 段(守卫追加,兼容历史 7 段调用)。
    if final_state.get("group_compare") and final_state["group_compare"].get("available"):
        reports.append(
            (
                "Group Sentiment Comparison (机构研报 vs 散户社媒)",
                _group_compare_markdown(final_state["group_compare"]),
            )
        )
    with open(fpath, "w", encoding="utf-8") as f:  # 以 UTF-8 写入(跳过空内容段)
        f.write(f"# Commodity Futures Analysis: {symbol}\n\n")
        f.write(f"**Date**: {trade_date}\n")
        f.write(f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"**Elapsed**: {elapsed:.0f}s\n")
        # 文件头自述本次跳过的模块,避免轻量/极简档的历史报告被误读成"完整分析"。
        if modules_note:
            f.write(f"> 本次运行跳过模块: {modules_note}\n")
        f.write("\n---\n\n")
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
        ("douyin", "douyin"),  # 2026-09-07 补:抖音(视频/评论双形态)
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

    rating = _parse_rating(content)

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


# 【功能】把前端/请求体里的开关值稳健地解析为布尔。支持 JSON 布尔、0/1、以及
#         "true"/"false"/"on"/"off"/"yes"/"no" 等字符串;解析不了返回 default。
# 【参数】value: 待解析值;default: 缺省/解析失败时的返回值。
def _as_bool(value, default=True):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "yes", "on", "include"):
            return True
        if v in ("false", "0", "no", "off", "exclude"):
            return False
    return default


# 【功能】解析一次运行分析的模块开关,并应用"综合研判是主开关"的强制规则。
# 【参数】symbol: 品种代码(大写);data: 请求 JSON(可含 include_sentiment 与
#         include_debate/include_synthesis/include_scenario,缺省全 True=完整档)。
# 【返回】dict {include_sentiment, include_debate, include_synthesis, include_scenario}:
#         include_sentiment 沿用 auto/include/exclude 判定(有数据才带);
#         include_synthesis=False ⇒ include_debate/include_scenario 连锁关(否则白跑)。
def resolve_run_options(symbol, data):
    data = data or {}
    # 决定本次是否运行情绪分析师(auto 默认:该品种存在情绪数据才包含,否则退化为 3 分析师)。
    inc_choice = str(data.get("include_sentiment", "auto")).strip().lower()
    if inc_choice == "include":
        include_sentiment = True
    elif inc_choice == "exclude":
        include_sentiment = False
    else:  # "auto" (default)
        include_sentiment = should_include_sentiment(symbol)  # 【调用函数】质量感知判定(数据不足但有板块复合也算含)
    include_debate = _as_bool(data.get("include_debate"), True)
    include_synthesis = _as_bool(data.get("include_synthesis"), True)
    include_scenario = _as_bool(data.get("include_scenario"), True)
    # 综合研判是"主开关":关闭它意味着不要最终结论 → 辩论素材与情景推演都无人消费,连锁关闭。
    if not include_synthesis:
        include_debate = False
        include_scenario = False
    return {
        "include_sentiment": include_sentiment,
        "include_debate": include_debate,
        "include_synthesis": include_synthesis,
        "include_scenario": include_scenario,
    }


# 【功能】按本次启用的模块过滤 PIPELINE_STAGES:未启用模块对应的阶段 id 一并剔除,
#         使进度条只显示实际会跑的阶段(纯函数,便于单测)。
# 【参数】opts: resolve_run_options(...) 的返回 dict(含 include_sentiment/debate/synthesis/scenario)。
# 【返回】过滤后的阶段列表(与 PIPELINE_STAGES 同结构,顺序保持)。
def stages_for_run(opts):
    dropped = set()  # 【变量】本次剔除的阶段 id
    if not opts.get("include_sentiment"):
        dropped.add("sentiment")
    if not opts.get("include_debate"):
        dropped.update(("bull_opening", "bear_refute", "bull_rebuttal", "moderator"))
    if not opts.get("include_synthesis"):
        dropped.add("synthesis")
    if not opts.get("include_scenario"):
        dropped.add("scenario")
    return [s for s in PIPELINE_STAGES if s["id"] not in dropped]


# 【功能】启动一次完整的多分析师分析(技术/基本面/宏观/情绪 + 多方辩论 + 综合研判 + 情景分析)。
# 【请求体】{"symbol": "RB", "date": "2026-07-14",
#             "include_sentiment": "auto" | "include" | "exclude",
#             "include_debate"/"include_synthesis"/"include_scenario": 布尔(可选,缺省 true=完整档)}
#   · include_sentiment=include: 必定带情绪分析师; exclude: 不带;
#     auto(默认)= 有该品种情绪数据就带,否则退化为 3 分析师(技术/基本面/宏观)。
#   · include_debate/synthesis/scenario: 模块细粒度跳选;综合研判=false ⇒ 辩论+情景连锁关(极简档)。
# 【返回】立即返回 {"status": "started", "include_sentiment": ..., "modules": {...}, "stages": [...]};
#   真正的分析在后台线程异步执行,前端通过 /api/progress 轮询进度。
# 【关键逻辑】
#   · 阶段列表随本次启用的模块变化:未启用模块对应的阶段 id 从 PIPELINE_STAGES 剔除,
#     进度条随之只显示实际会跑的阶段(前端拿到 d1.stages 后按权威列表重绘)。
#   · 后端把每个 Agent 节点名映射为阶段 id(stage_map),节点跑完就标记该阶段完成。
#   · 分析流会检查 _tracker.stop_requested / wait_if_paused(),实现前端"停止/暂停"。
@app.route("/api/run_analysis", methods=["POST"])
def api_run_analysis():
    """Run full analysis with SSE streaming — reports, debate, synthesis, rating."""
    global _tracker
    data = request.json or {}
    symbol = data.get("symbol", "RB").upper()
    trade_date = data.get("date", "2026-07-14")

    # 解析本次运行的模块开关(情绪 auto/include/exclude + 辩论/综合/情景的细粒度跳选),
    # 并把闭包要用到的 include_* 就地展开为局部变量。缺省全开 = 现行为(完整档)。
    opts = resolve_run_options(symbol, data)  # 【调用函数】解析运行模块开关(含综合研判主开关的强制规则)
    include_sentiment = opts["include_sentiment"]
    include_debate = opts["include_debate"]
    include_synthesis = opts["include_synthesis"]
    include_scenario = opts["include_scenario"]

    # 阶段列表:按本次启用的模块过滤 PIPELINE_STAGES,进度条也随之只显示实际会跑的阶段。
    stages = stages_for_run(opts)

    _tracker = ProgressTracker(symbol=symbol, trade_date=trade_date, stages=stages)
    _tracker.is_running = True

    # 【关键逻辑】客户端标识必须在请求处理阶段(有 request 上下文)取好再传进后台线程:
    # _client_tag() 读 request.headers,后台线程里没有请求上下文,直接调用会抛
    # "Working outside of request context" 使分析一开始就 mark_error(2026-09-08 修复)。
    client_tag = _client_tag()

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
            app_graph, _ = build_commodity_graph(  # 【调用函数】构建 LangGraph 多分析师图(按本次启用的模块接线)
                config,
                enable_feedback=False,
                include_sentiment=include_sentiment,
                include_debate=include_debate,
                include_synthesis=include_synthesis,
                include_scenario=include_scenario,
            )
            evo_ctx = get_evolution_context(symbol)  # 【调用函数】读取该品种历史进化记忆
            initial_state = {  # 【变量】LangGraph 初始状态(分析流水线的输入骨架,含会话与各报告槽位)
                "messages": [HumanMessage(content=f"Analyze {symbol} as of {trade_date}.")],
                "company_of_interest": symbol,
                "asset_type": "commodity_futures",
                "trade_date": trade_date,
                "client_tag": client_tag,  # 【隔离】自传数据仅注入与上传电脑相同客户端发起的分析(请求阶段取好)
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

            # 机构(研报) vs 散户(社媒) 观点对比卡:确定性计算(无 LLM、无图节点)。
            # 仅在包含情绪分析师时产出,避免"未跑情绪"被误显示为"散户无数据"。
            try:
                if include_sentiment:
                    gc = _build_group_compare(symbol)
                    if gc and gc.get("available"):
                        final_state["group_compare"] = gc
                        _tracker._stage_reports["group_compare"] = gc
            except Exception:
                logger.exception("group_compare 计算失败(非阻断)")

            _tracker._stage_reports["_final_state"] = final_state
            _tracker.mark_complete(final_state)

            # 把本次分析落盘为历史报告(与 CLI 相同格式),供 /api/history 与
            # /api/report/<file> 读取。此前 Web 分析从不写盘,历史报告因此停更。
            # 落盘失败只记日志,不阻断分析完成状态。跳过模块时在文件头自述,防静默降级。
            skipped_mods = []
            if not include_debate:
                skipped_mods.append("辩论对抗")
            if not include_synthesis:
                skipped_mods.append("综合研判")
            if not include_scenario:
                skipped_mods.append("情景分析")
            try:
                _persist_analysis_report(
                    symbol,
                    trade_date,
                    final_state,
                    elapsed=_tracker.elapsed,
                    modules_note="、".join(skipped_mods),
                )
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
            "modules": {  # 【变量】本次实际启用的模块(供前端记录/提示档位)
                "sentiment": include_sentiment,
                "debate": include_debate,
                "synthesis": include_synthesis,
                "scenario": include_scenario,
            },
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


# ── Research Report upload module ───────────────────────────────────────────


# 研报上传模块:用户在运行分析页上传 PDF / 图片 / Markdown 研报,后台线程
# 提取文本(PDF 用 PyMuPDF,扫描件/图片用仓库自带 Ollama OCR 管线,均优雅
# 降级)→ LLM 结构化提取 + 观点结论 → 落库 research_reports 表 + 写聚合
# JSON(research_data.py)。聚合数据是 run_analysis 中 get_research_report
# 工具与 merge_basis_data / merge_inventory_data / get_futures_supply_demand
# 并入的最高优先级数据源(RESEARCH > EXTERNAL > FREE_API)。

RESEARCH_UPLOAD_DIR = Path.home() / ".tradingagents" / "research_reports"  # 【变量】研报原始文件存储目录(按品种分子目录)
RESEARCH_ALLOWED_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".md", ".txt"}  # 【变量】研报允许的扩展名白名单
RESEARCH_MAX_SIZE = 20 * 1024 * 1024  # 【变量】研报最大上传体积(20MB)


# 【功能】按需加载仓库自带 Ollama OCR 管线(data_collection/validate/image_pipeline_v2.py)。
# 【返回】module | None:加载成功返回模块对象;文件缺失/执行异常返回 None(调用方优雅降级)。
# 【关键逻辑】该目录没有 __init__.py,不是包,不能用普通 import;必须用
#           importlib.util 从文件路径直载。加载失败只记 warning,不抛错,
#           保证 Ollama 未安装时上传流程照常走"纯文本/报错"路径。
def _load_ocr_pipeline():
    import importlib.util  # 【调用包】从文件路径加载非包模块(OCR 管线直载)

    target = Path(__file__).parent / "data_collection" / "validate" / "image_pipeline_v2.py"
    if not target.exists():
        return None
    spec = importlib.util.spec_from_file_location("image_pipeline_v2", target)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        logger.warning("Failed to load OCR pipeline %s", target, exc_info=True)
        return None


# 【功能】以常见编码读取纯文本文件(优先 utf-8,失败回退 gbk,再失败按替换符读取)。
# 【参数】path: 文件路径。
# 【返回】str:文件文本内容。
def _read_text_file(path: Path) -> str:
    for enc in ("utf-8", "gbk"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


# 【功能】用 PyMuPDF(fitz)提取 PDF 文本层(旧行为:block 分组词→行版面还原)。
# 【2026-09-08 方案三】词→行版面还原逻辑整体下沉到
# tradingagents/dataflows/pdf_layout.py(避免 dataflows→web_app 循环导入);
# 该模块另提供图表感知的 extract_layout_text(矢量聚类把图表聚成【图】块、
# 表格聚成【表】块,落 research_reports.layout_text 供 RAG 消费);
# 本别名保持旧调用点(_extract_report_text / ingest_local_pdfs)不变。
from tradingagents.dataflows.pdf_layout import extract_plain_text as _extract_pdf_text  # noqa: E402


# 【功能】用 Ollama OCR 管线识别单张图片。
# 【参数】path: 图片文件路径。
# 【返回】str:OCR 文本;管线缺失 / OCR 失败返回空串(不抛错)。
def _ocr_image(path: Path) -> str:
    pipe = _load_ocr_pipeline()
    if pipe is None:
        return ""
    try:
        res = pipe.stage1_classify_and_ocr(str(path))
        return (res.get("ocr_text") or "") if isinstance(res, dict) else ""
    except Exception:
        logger.warning("OCR failed for image %s", path, exc_info=True)
        return ""


# 【功能】扫描版 PDF 逐页渲染成 PNG 后走 Ollama OCR。
# 【参数】path: PDF 文件路径。
# 【返回】str:各页 OCR 文本拼接;任一步骤失败返回空串。
# 【关键逻辑】文本层 <200 字符才判定为扫描件调用本函数;每页以 dpi=150
#           get_pixmap 渲染,临时 PNG 用后即删。Ollama 不可用 → 返回空串,
#           由调用方降级为保留 PDF 自身文本(可能为空)。
def _ocr_pdf(path: Path) -> str:
    try:
        import pymupdf  # 【调用包】PyMuPDF(≥1.24 推荐入口):扫描页渲染成位图
    except ImportError:
        try:
            import fitz as pymupdf  # 【调用包】旧版 PyMuPDF 兼容名(fitz,未来将移除)
        except ImportError:
            return ""
    pipe = _load_ocr_pipeline()
    if pipe is None:
        return ""
    texts: list[str] = []
    try:
        doc = pymupdf.open(str(path))
        for page in doc:
            pix = page.get_pixmap(dpi=150)
            fd, tmp_path = tempfile.mkstemp(suffix=".png")  # 【调用包】临时 PNG 中转文件
            os.close(fd)
            try:
                pix.save(tmp_path)
                res = pipe.stage1_classify_and_ocr(tmp_path)
                if isinstance(res, dict) and res.get("ocr_text"):
                    texts.append(res["ocr_text"])
            finally:
                Path(tmp_path).unlink(missing_ok=True)
        doc.close()
    except Exception:
        logger.warning("OCR failed for scanned PDF %s", path, exc_info=True)
    return "\n\n".join(texts)


# 【功能】从上传文件提取研报文本。
# 【参数】file_path: 已落盘文件路径。
# 【返回】(text, used_ocr):(提取出的文本, 是否走了 OCR)。
# 【关键逻辑】md/txt 直读;PDF 先取文本层,不足 200 字符判定为扫描件 → 逐页
#           OCR;图片直接 OCR。OCR 不可用时不中断——PDF 保留已提取文本,图片
#           得到空串(由上层转成错误提示)。
def _extract_report_text(file_path: str) -> tuple[str, bool]:
    path = Path(file_path)
    ext = path.suffix.lower()
    if ext in (".md", ".txt"):
        return _read_text_file(path), False
    if ext == ".pdf":
        text = _extract_pdf_text(path)
        if len(text.strip()) >= 200:
            return text, False
        ocr_text = _ocr_pdf(path)
        if ocr_text:
            return ocr_text, True
        return text, False
    if ext in (".png", ".jpg", ".jpeg"):
        ocr_text = _ocr_image(path)
        return ocr_text, bool(ocr_text)
    return "", False


# 【功能】从 LLM 回复文本中提取 JSON 对象(与 image_pipeline_v2.parse_json_response 同逻辑)。
# 【参数】text: LLM 原始回复。
# 【返回】dict | None:解析成功返回 dict;无 JSON / 解析失败返回 None。
# 【关键逻辑】1) 剥离 ```json 代码围栏;2) 取第一个 { 到最后一个 } 的子串;
#           3) json.loads,失败返回 None(由调用方兜底)。
def _extract_json_object(text: str) -> dict | None:
    if not text:
        return None
    cleaned = text.strip()
    if "```" in cleaned:
        parts = cleaned.split("```")
        cleaned = parts[1] if len(parts) > 1 else cleaned
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    start, end = cleaned.find("{"), cleaned.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(cleaned[start:end])
        except json.JSONDecodeError:
            return None
    return None


# 【变量】品种别名/子品种映射:LLM 或研报里出现的非标准叫法 → 标准代码。
# 用于 fxbaogao 自动接入时,子品种(光伏玻璃→玻璃FG)、简称(螺纹→RB)、
# 带"沪/价"后缀(沪铜→CU)等无法被 VARIETY_METADATA 直接命中时的兜底归一化。
_VARIETY_ALIASES = {
    # 玻璃系(子品种)
    "光伏玻璃": "FG", "平板玻璃": "FG", "浮法玻璃": "FG", "low-e玻璃": "FG", "lowe玻璃": "FG",
    # 钢材系(简称/子品种)
    "螺纹": "RB", "热卷": "HC", "热轧": "HC", "线材": "WR", "盘螺": "RB",
    # 煤焦
    "炼焦煤": "JM",
    # 铁合金
    "硅铁": "SF", "锰硅": "SM", "硅锰": "SM",
    # 金属(带"沪/价"等后缀)
    "沪铜": "CU", "沪铝": "AL", "沪镍": "NI", "沪锌": "ZN", "沪铅": "PB", "沪锡": "SN",
    "沪金": "AU", "沪银": "AG", "金价": "AU", "银价": "AG", "铜价": "CU", "铝价": "AL",
    "氧化铝": "AO", "不锈钢": "SS",
    # 能源
    "低硫燃": "LU", "燃料油": "FU",
    # 化工
    "聚乙烯": "L", "聚氯乙烯": "V",
    "合成橡胶": "BR", "丁二烯橡胶": "BR",
    # 农产品(带"价")
    "豆价": "M", "棉价": "CF", "糖价": "SR",
}
# 反向:标准中文名 + 别名 → 代码(供关键词恢复扫描用)。
_VARIETY_KEYWORD_MAP = {v["name"]: k for k, v in VARIETY_METADATA.items()}
_VARIETY_KEYWORD_MAP.update(_VARIETY_ALIASES)


# 【功能】把 LLM 输出的品种名/代码归一化为标准品种代码。
# 【参数】raw: LLM 输出的品种标识(如 "RB"、"rb"、"螺纹钢"、"光伏玻璃")。
# 【返回】str | None:标准大写代码;无法识别返回 None。
# 【关键逻辑】1) 大写后先查 VARIETY_METADATA 直接命中;2) 中文名反查;
#           3) 别名/子品种表(_VARIETY_ALIASES,如 光伏玻璃→FG);
#           4) 兜底:保留字母数字并大写(未知品种也能落库,消费端按同代码匹配)。
def _normalize_variety_code(raw) -> str | None:
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    up = s.upper()
    if up in VARIETY_METADATA:
        return up
    name_map = {v["name"]: k for k, v in VARIETY_METADATA.items()}
    if s in name_map:
        return name_map[s]
    if s in _VARIETY_ALIASES:
        return _VARIETY_ALIASES[s]
    cleaned = re.sub(r"[^A-Za-z0-9]", "", up)
    return cleaned or None


# 【功能】LLM 未识别出品种时的最后兜底:扫描标题+正文中的品种关键词。
# 【参数】text: 研报文本;selected: 用户/调用方指定的主品种(优先)。
# 【返回】dict | None:{"variety": 代码, "direction":"中性", "confidence":0.3~0.4}
#            找到最长匹配的品种关键词则返回;否则 None(交由上层判失败)。
# 【关键逻辑】优先用 selected(若可归一化);否则在标题+正文里按关键词长度
#              降序匹配 _VARIETY_KEYWORD_MAP(标准名+别名),命中即返回。
#              用于 fxbaogao 自动接入的子品种研报(如"光伏玻璃周度报告"→FG)。
def _recover_variety_from_text(text: str, selected: str) -> dict | None:
    if selected:
        code = _normalize_variety_code(selected)
        if code:
            return {"variety": code, "direction": "中性", "confidence": 0.4}
    if not text:
        return None
    title = (text.split("\n", 1)[0] or "")[:80]
    hay = text[:8000]
    best_code, best_len = None, 0
    # 先扫标题(权重高,命中即返回);再扫正文取最长匹配
    for kw, code in sorted(_VARIETY_KEYWORD_MAP.items(), key=lambda kv: len(kv[0]), reverse=True):
        if len(kw) < 2:
            continue
        if kw in title:
            return {"variety": code, "direction": "中性", "confidence": 0.4}
        if kw in hay and len(kw) > best_len:
            best_code, best_len = code, len(kw)
    if best_code:
        return {"variety": best_code, "direction": "中性", "confidence": 0.3}
    return None


# 【功能】把 LLM 输出的方向词统一成 看多/看空/中性。
# 【参数】raw: 原始方向词(中文或英文)。
# 【返回】str:归一化后的方向。
def _normalize_direction(raw: str) -> str:
    dir_map = {
        "看多": "看多", "多头": "看多", "利好": "看多", "bullish": "看多", "buy": "看多",
        "看空": "看空", "空头": "看空", "利空": "看空", "bearish": "看空", "sell": "看空",
    }
    return dir_map.get(raw.lower(), "中性")


# 【变量】已知品种提示(代码+中文名),传给 LLM 提升它输出标准代码的概率。
_SUPPORTED_VARIETY_HINT = "、".join(f"{k}({v['name']})" for k, v in VARIETY_METADATA.items())


# 【功能】LLM 第一步:提取研报元数据 + 全部涉及品种的结构化数据。
# 【参数】llm: 已创建的大模型客户端;variety: 用户选择的品种(可为空,仅作提示);
#           text: 研报文本。
# 【返回】dict:{"report_title","publisher","publish_date","report_type","varieties":[...]};
#           varieties 每元素含该品种的现货价/库存/供需/成本/目标价/关键事件/
#           方向/置信度/评级;提取失败返回空 dict(不抛错)。
# 【关键逻辑】1) 一份研报可能覆盖多个品种,prompt 要求列出所有品种并各自输出;
#           2) 旧版单品种输出(无 varieties 键)向后兼容包装成 varieties 数组;
#           3) 品种代码归一化去重;每品种方向/置信度独立归一化。
def _llm_extract_structured(llm, variety: str, text: str) -> dict:
    prompt = (
        "你是中国商品期货基本面分析师。请从下面这份研报文本中提取信息。\n"
        "只输出一个 JSON 对象(不要输出任何其他文字、不要用代码围栏),结构如下:\n"
        '{"report_title":"研报标题","publisher":"发行方/机构名称","publish_date":"YYYY-MM-DD或留空",\n'
        ' "report_type":"日报/周报/其它(按研报自述周期,与日期类产品对应时填日报或周报)",\n'
        ' "varieties":[{\n'
        '   "variety":"品种标准代码(研报为哪几个品种给出观点/数据就列哪几个)",\n'
        '   "spot_price":{"value":number,"unit":"元/吨","date":"YYYY-MM-DD"},\n'
        '   "basis":{"value":number,"unit":"元/吨","date":"YYYY-MM-DD","note":"现货对盘面升贴水/基差说明,如\"现货升水 120\"(可选)"},\n'
        '   "social_inventory":{"value":number,"unit":"万吨","date":"YYYY-MM-DD"},\n'
        '   "mill_inventory":{"value":number,"unit":"万吨","date":"YYYY-MM-DD"},\n'
        '   "warehouse_receipts":{"value":number,"unit":"张","date":"YYYY-MM-DD","note":"交易所仓单口径/增减(可选)"},\n'
        '   "operating_rate":{"value":number,"unit":"%","date":"YYYY-MM-DD","note":"开工率/负荷率口径,如\"炼厂开工/聚酯负荷\"(可选)"},\n'
        '   "supply":{"note":"...","date":"..."},\n'
        '   "demand":{"note":"...","date":"..."},\n'
        '   "costs":{"note":"...","date":"..."},\n'
        '   "processing_margin":{"value":number,"unit":"元/吨","date":"YYYY-MM-DD","note":"加工利润/加工费/盘面价差口径(可选)"},\n'
        '   "target_price":number,\n'
        '   "key_events":[{"event":"...","detail":"...","impact":"bullish/bearish/neutral","source":"..."}],\n'
        '   "direction":"看多/看空/中性",\n'
        '   "confidence":0.6,\n'
        '   "operation_advice":"研报原文中针对该品种的操作建议原句(如\\"逢低做多\\"/\\"区间操作\\"/\\"买入套保\\"),逐字摘录不改写,研报未给操作建议则 null",\n'
        '   "rating":"买入/增持/中性/减持/卖出"}]\n'
        "}\n"
        "要求:\n"
        "重要:上方 JSON 示例里的数值(如 confidence 0.6)只是字段格式示意,一律不得照抄;\n"
        "1) varieties 列出研报中出现的所有品种,每个品种一份,同一品种不要重复;若只涉及一个品种就放一个元素。\n"
        "2) 品种代码用标准代码,例如: " + _SUPPORTED_VARIETY_HINT + "\n"
        "3) 除 confidence 外:研报中没有出现的字段留空字符串或 null,绝对不要编造;\n"
        "   operation_advice 必须是研报原文里给该品种的操作建议原句,严禁自行拟写。\n"
        "3c) confidence 含义与取值:confidence 表示你对 direction 判断的把握程度(0~1 小数)。\n"
        "    研报明确看多/看空、且数据与事件支撑充分强 → 0.75~0.95;\n"
        "    方向倾向明确但证据一般或研报语气谨慎 → 0.55~0.75;\n"
        "    中性/区间/无明确倾向 → 0.4~0.6;\n"
        "    研报几乎没有给出可供判断方向的依据 → 0.3~0.5(此时 direction 宜为中性)。\n"
        "    数据/观点相互冲突或语焉不详取低档。严禁所有品种都填同一数值,严禁照抄示例 0.6;\n"
        "    不要一看到 direction=中性就固定 0.5,应按该品种证据强弱在 0.4~0.6 内浮动。\n"
        "3b) basis(基差/现货升贴水)/operating_rate(开工率/负荷率)/warehouse_receipts"
        "(交易所仓单)/processing_margin(加工利润/加工费/价差)只在研报给出具体数字时"
        "填 value,note 写口径(如\"炼厂开工/唐山高炉/PTA加工差\"),研报没给数就整体留空。\n"
        "3d) direction 必须跟随所选品种自身(同一研报不同品种方向可以不同,严禁照搬标题"
        "或其它品种的倾向);研报以价差/套利形式给方向时(如\"多PX 空PTA\"\"多PR 空PTA\")"
        "被做多的品种记看多、被做空的品种记看空;正文该品种小节给出明确单边倾向且与价差腿"
        "冲突时,才以正文单边结论为准,并把该判断依据写进 supply/demand note。\n"
        "4) report_title / publisher 从研报中识别,识别不到留空。\n"
        f"用户选择的主品种(仅供参考,可不含在 varieties 中):{variety}\n---\n研报文本:\n{text[:8000]}"
    )
    try:
        result = llm.invoke(prompt)
        content = result.content if hasattr(result, "content") else str(result)
        data = _extract_json_object(str(content)) or {}
    except Exception:
        logger.warning("LLM structured extraction failed for %s", variety, exc_info=True)
        data = {}

    # 向后兼容:旧版单品种输出(无 varieties 键)包装成 varieties 数组;
    # 单品种输出里通常没有 variety 字段,用用户选择的品种代码兜底。
    if "varieties" not in data or not isinstance(data.get("varieties"), list):
        entry = {
            k: v for k, v in data.items()
            if k not in ("report_title", "publisher", "publish_date", "report_type", "varieties")
        }
        if entry or data.get("direction"):
            if "variety" not in entry:
                entry["variety"] = variety or "?"
            data["varieties"] = [entry]
        else:
            data["varieties"] = []

    cleaned: list[dict] = []
    seen: set[str] = set()
    for item in data["varieties"]:
        if not isinstance(item, dict):
            continue
        code = _normalize_variety_code(item.get("variety"))
        if not code or code in seen:
            continue
        seen.add(code)
        item["variety"] = code
        item["direction"] = _normalize_direction(str(item.get("direction", "中性")))
        # confidence:只有模型真的给了 0~1 的数才保留(钳到合法区间);缺失/非法 → None,
        # 前端显示"—(未给置信度)",绝不拿 0.5 冒充(2026-09-03 根因:全部默认 0.5)。
        try:
            c = float(item["confidence"])
            if c != c:  # NaN
                c = None
        except (TypeError, ValueError, KeyError):
            c = None
        item["confidence"] = None if c is None else max(0.0, min(1.0, c))
        cleaned.append(item)
    data["varieties"] = cleaned
    return data


# 【变量】单份研报最多生成结论的品种数(防止超长多品种研报拖慢后台处理)。
MAX_CONCLUSION_VARIETIES = 6


# 【变量】结论"交易要素"节的标题关键词 —— _extract_key_opinion 据此把该节收进单元格
#         (新口径放综述/多空要点之后,旧口径放首行);scripts/reconclude_research.py
#         的 _NEW_FMT_MARKERS 与此语义双份同步(脚本刻意不 import web_app,
#         改任一侧须对齐另一侧)。
_TRADE_TITLE_HINTS = ("交易要素", "头寸与风险", "仓位与风险")


# 【功能】LLM 第二步:按品种逐一生成"核心观点"分析结论(markdown),与品种一一对应。
# 【参数】llm: 大模型客户端;text: 研报文本;varieties: 已归一化的品种 dict 列表。
# 【返回】dict {品种代码: markdown 结论}。
# 【关键逻辑】1) 每个品种单独一次 LLM 调用(聚焦该品种,不经标题切分,稳健);
#           2) 逐品种结论=两段式 markdown:第一部分多角度核心观点(固定八小节:
#              供需格局/库存与结构/成本与利润/现货与目标价/事件与驱动/观点与依据/
#              多空要点/交易要素与风险,每节一句关键数据+含义,360 字左右,研报未
#              披露指标该节明写不得编造;『多空要点』为综述+利多/利空条目
#              (逻辑/风险),供观点要点单元格直接引用);
#              第二部分附列信息:## 数据支撑(关键佐证清单)/## 与系统自动分析的
#              潜在分歧(对照已提取 direction/confidence)/## 建议权重(权重建议)。
#           3) 单个品种失败降级为提示文案,不中断其它品种。
def _llm_opinion_conclusion(llm, text: str, varieties: list[dict], report_id: int | None = None) -> dict:
    conclusions: dict[str, str] = {}
    name_map = {k: v["name"] for k, v in VARIETY_METADATA.items()}
    for item in varieties[:MAX_CONCLUSION_VARIETIES]:
        code = item.get("variety") or ""
        if not code:
            continue
        label = f"{code} ({name_map.get(code, '')})".strip()
        # 【RAG 增强】检索该品种近期历史研报片段作背景参考(排除本篇自己,避免模型
        # 复述刚输入的原文);RAG 未启用/失败返回 "",零影响。
        rag_ctx = ""
        if report_id:
            rag_ctx = _rag_context_for_variety(
                f"{label} 基本面核心观点 供需 库存 成本 现货",
                variety=code, limit=3, exclude_report_id=report_id,
            )
        prompt = (
            f"你是中国商品期货基本面分析师。研报全文见下。请只针对品种 {label} 输出一份"
            "观点分析结论(markdown),分两部分:\n"
            "【第一部分 · 多角度核心观点(总篇幅 360 字左右,300~440)】小节标题固定:\n"
            "## 供需格局\n## 库存与结构\n## 成本与利润\n## 现货与目标价\n"
            "## 事件与驱动\n## 观点与依据\n## 多空要点\n## 交易要素与风险\n"
            "写作要求:\n"
            "1) 每节只写该品种在该角度最要紧的一句话,引用研报原文里的具体数字与日期,"
            "并点一句其含义,不铺陈展开;某角度研报未披露时该节写\"研报未披露该指标\","
            "严禁编造数据或观点。\n"
            "1a) 研报披露了下面指标时必须写进对应小节(带数字与日期):开工率/负荷率→"
            "『供需格局』(供给强弱);加工利润/加工费/盘面价差→『成本与利润』;"
            "交易所仓单增减→『库存与结构』;基差或现货升贴水(正基差=现货升水偏多,"
            "负=贴水)→『现货与目标价』。研报没给的不写。\n"
            "1b) 各小节互不重复:同一数字/事实只在与它最相关的一节出现一次;"
            "『库存与结构』只写库存与月差结构,不重复『供需格局』已写的供给事实;"
            "『观点与依据』的推理链用短语指代前文已给过的事实(如\"低库存+到港少\"),"
            "不再复述具体数字。\n"
            "2) 只谈该品种,不涉及其他品种;『观点与依据』先给一句推理链:"
            "因<事实/依据> → 推演<逻辑> → 方向<看多/看空/中性> + 单边/区间,"
            "再附 1 个需跟踪的边际变量/风险。\n"
            "2a) 『多空要点』是表格直接引用的浓缩节,固定首行\"综述：<一句话总括方向与"
            "区间,30 字内>\";随后按看法列条目,利多/利空各 0~2 条、合计至少 1 条,每条"
            "格式\"利多：<因素>(逻辑：<支持该看法的依据/数据短语>；风险：<该看法被"
            "证伪的触发点或风险来源>)\"、\"利空：<因素>(逻辑：<…>；风险：<…>)\";"
            "条目用短语指代前文已给过的事实,不复述数字;确无明确看法时只写综述一行;"
            "逐行纯文本输出,严禁写成 markdown 表格或加列表符号。\n"
            "3) 『交易要素与风险』必须单行输出,五段用中文分号分隔,依次为:\n"
            "方向:看多/看空/中性;形态与区间:<单边<看多/看空, 运行或目标区间 a~b> "
            "或 区间震荡(区间 a~b),研报没给区间写—>;头寸:<具体手数/手数区间优先"
            "(如\"多头 30 手\"\"建仓 20~40 手\"),无手数则给仓位建议(轻仓/逢低分批/"
            "主力持有/等回调分批),都没有写—>;头寸范围:<加仓/减仓/止损或触发价位区间"
            "(如\"回落 7800 加仓\"),研报没给写—>;风险:<主要风险或需跟踪边际变量(1 条)>。\n"
            "严禁编造手数与价位;研报未披露的一律写「—」,不得写\"未披露\"字样。\n"
            "4) 保持紧凑:第一部分 340 字左右,不写套话。\n"
            "【第二部分 · 附列信息】紧接第一部分,再依次输出三节:\n"
            "## 数据支撑\n把支撑上述判断的关键数据/研报原文要点列成简明清单,每条注明数字"
            "与日期;已被第一部分完整引述的数据不重复堆砌,只补最有分量的佐证。\n"
            "## 与系统自动分析的潜在分歧\n对照该品种已提取结构中的 direction/confidence"
            "(自动识别倾向/置信度),指出其与第一部分多空定性的分歧点;无实质分歧写"
            "\"与系统自动分析基本一致\"。\n"
            "## 建议权重\n给出该观点的配置/关注权重建议(如\"多头 20%~30%\"或\"区间操作为主,"
            "权重 0.5\"),用一句话说明理由。\n"
            f"该品种已提取结构(可能不完整,以研报原文为准):{json.dumps(item, ensure_ascii=False)}\n"
            "---\n研报文本:\n" + text[:12000]
        )
        if rag_ctx:
            prompt += (
                "\n---\n【参考资料 · 检索自近期同品种历史研报片段】\n" + rag_ctx + "\n"
                "以上片段来自其他日期的历史研报,仅供补充背景与校对数字,严禁照抄其方向结论;"
                "你的判断必须以本次研报正文为准,确需引用历史数据时注明其日期。\n"
            )
        try:
            result = llm.invoke(prompt)
            content = result.content if hasattr(result, "content") else str(result)
            conclusions[code] = str(content)[:4000]
        except Exception:
            logger.warning("LLM conclusion failed for %s", code, exc_info=True)
            conclusions[code] = f"## 观点与依据\n({code} 观点生成失败,请人工阅读研报原文。)"
    return conclusions


# 【功能】研报有效日期:发布日期优先,缺则回退入库日期。
# 【关键逻辑】publish_date 列(采集源接口自带/LLM 抽取回填,真实发布日)是日期分组的
#           首选键;老行/手动上传无此值时回退 uploaded_at 前 10 位(入库日)。
#           只切"研报属于哪一天"的语义;入库时间本身仍用于去重窗口/排序。
def _report_date(r: dict) -> str:
    """研报有效日期:publish_date 优先,缺则回退 uploaded_at 前 10 位。"""
    return (r.get("publish_date") or "").strip()[:10] or str(r.get("uploaded_at") or "")[:10]


# 【功能】发布日期自愈:列空而 LLM 抽出 publish_date 时写回列(手动上传/老采集器行的兜底)。
# 【参数】db: AgentSenseDB;report_id: 研报主键;current: 行内现有 publish_date;
#           structured: 第一步 LLM 结构化结果(顶层 publish_date)。
# 【返回】生效的发布日期(YYYY-MM-DD 或空串,供聚合 JSON 同步使用)。
def _self_heal_publish_date(db, report_id: int, current: str, structured: dict) -> str:
    cur = (current or "").strip()[:10]
    if cur:
        return cur
    pd = str((structured or {}).get("publish_date") or "").strip()[:10]
    if re.match(r"^\d{4}-\d{2}-\d{2}$", pd):
        with contextlib.suppress(Exception):
            db.update_research_report(report_id, publish_date=pd)
        return pd
    return ""


# 【功能】研报类型自愈:列空而 LLM 抽出 report_type 时写回列(标题启发式没命中的
#           存量行/手动上传行的兜底,与 _self_heal_publish_date 同型)。
# 【参数】db: AgentSenseDB;report_id: 研报主键;current: 行内现有 report_type;
#           structured: 第一步 LLM 结构化结果(顶层 report_type)。
# 【返回】生效的研报类型('日报'/'周报' 或空串,供聚合 JSON 同步使用)。
# 【关键逻辑】LLM 只在给出有效枚举值(日报/周报)时才写回,'其它'不落库(留空待
#           后续自愈),避免把无类型研报错标成日报/周报。
def _self_heal_report_type(db, report_id: int, current: str, structured: dict) -> str:
    cur = (current or "").strip()
    if cur:
        return cur
    rt = str((structured or {}).get("report_type") or "").strip()
    if rt in ("日报", "周报"):
        with contextlib.suppress(Exception):
            db.update_research_report(report_id, report_type=rt)
        return rt
    return ""


# 【功能】把一份研报按品种拆分写各品种聚合 JSON(供 get_research_report / merge_* 读取)。
# 【参数】report_id: 研报主键(作为聚合记录 id);uploaded_at: 上传/发布时间;
#           publish_date: 研报真实发布日期(YYYY-MM-DD,空=未知,消费端回退 uploaded_at);
#           report_type: 研报类型('日报'/'周报'/空=未知,前端徽标与每日总结过滤用);
#           title/source: 已解析的标题与发行方;codes: 覆盖品种列表;
#           varieties: 归一化品种 dict 列表;conclusions: 各品种 markdown 结论。
# 【返回】无。
# 【关键逻辑】每个品种只落自己的数据点/方向/置信度/结论;记录 id=report_id,
#           同 id 覆盖(research_data.upsert 语义),不产生重复条目。由 _process_
#           research_report 与 reconclude_research_report(只重跑结论)共用。
def _write_research_aggregates(
    report_id: int,
    uploaded_at: str,
    title: str,
    source: str,
    codes: list[str],
    varieties: list[dict],
    conclusions: dict[str, str],
    publish_date: str = "",
    report_type: str = "",
) -> None:
    from tradingagents.dataflows.research_data import (
        upsert_research_report,  # 【调用包】研报聚合 JSON 写入
    )

    data_point_keys = (
        "spot_price", "basis", "social_inventory", "mill_inventory",
        "warehouse_receipts", "operating_rate", "supply", "demand", "costs",
        "processing_margin", "target_price", "key_events", "rating",
    )
    for item in varieties:
        code = item["variety"]
        upsert_research_report(
            code,
            {
                "id": report_id,
                "title": title,
                "source": source,
                "uploaded_at": uploaded_at,
                "publish_date": (publish_date or "").strip()[:10],  # 真实发布日期(观点总览日期分组键,缺则消费端回退 uploaded_at)
                "report_type": (report_type or "").strip(),  # 研报类型:日报/周报(空=未知;前端周报徽标+每日总结只收日报)
                "varieties": codes,  # 覆盖品种标注:读该品种时提示"本研报还覆盖 X/Y"
                "direction": item.get("direction", "中性"),
                "confidence": item.get("confidence", 0.5),
                # 研报原文操作建议原句(第一步结构化提取;2026-09-04 新增,旧行无此键)
                "report_advice": str(item.get("operation_advice") or ""),
                "conclusion": conclusions.get(code, ""),
                "data_points": {k: val for k, val in item.items() if k in data_point_keys},
            },
        )


# 【功能】研报 RAG(tradingagents/rag,2026-09-08)薄适配层:依赖(chromadb /
#         sentence-transformers)未安装时全部静默降级 no-op,任何异常只打日志,
#         绝不影响研报主链路与既有测试。
def _rag_index_report_safely(report_id: int) -> None:
    """把一份 done 研报切块向量化进 Chroma;失败只 warning。"""
    try:
        from tradingagents.rag import is_available, service  # 【调用包】懒导入重依赖

        if not is_available():
            return
        row = get_db().get_research_report(report_id)
        if not row or row.get("status") != "done":
            return
        n = service.index_report(row)
        if n:
            logger.info("RAG indexed report %s: %s chunks", report_id, n)
    except Exception as e:
        logger.warning("RAG index failed for report %s: %s", report_id, e)


def _rag_delete_vectors_safely(report_id: int) -> None:
    """删除一份研报的全部向量;失败只 warning。"""
    try:
        from tradingagents.rag import is_available, service  # 【调用包】懒导入重依赖

        if is_available():
            service.delete_report(report_id)
    except Exception as e:
        logger.warning("RAG delete vectors failed for report %s: %s", report_id, e)


def _rag_context_for_variety(
    query_text: str,
    variety: str | None = None,
    limit: int = 4,
    exclude_report_id: int | None = None,
) -> str:
    """检索同品种历史研报片段并拼成提示词参考块;未启用/失败返回 ""。"""
    try:
        from tradingagents.rag import is_available, service  # 【调用包】懒导入重依赖

        if not is_available():
            return ""
        return service.context_for_variety(
            query_text, variety=variety, limit=limit, exclude_report_id=exclude_report_id
        )
    except Exception:
        return ""


# 【功能】薄适配:位图图表视觉重述(chart_vision.describe_for_hook)——本地视觉
#          模型把矢量版面提取覆盖不到的位图图表(约 32/76 份研报)重述成文字,
#          追加到 layout_text 供 RAG 检索。
# 【参数】report_id: research_reports 主键;file_path: 研报原件路径;
#         layout_text: 刚落库的版面提取文本。
# 【返回】重述后的完整 layout_text(没做/失败时原样返回,绝不空)。
# 【关键逻辑】describe_for_hook 内部全兜底(Ollama 不可达 2s 放行/图表数限
#           RAG_VISION_MAX_CHARTS/单图失败跳过);本层再包一层 try/except,
#           与 _rag_*_safely 同规:挂点失败只 warning,绝不拖垮研报主流程。
#           测试由 conftest._disable_rag 一并 no-op。
def _vision_describe_safely(report_id: int, file_path: str, layout_text: str) -> str:
    try:
        from tradingagents.dataflows.chart_vision import SECTION_MARK, describe_for_hook

        if SECTION_MARK in (layout_text or ""):
            return layout_text  # 已有重述节(幂等),不再重跑
        vision_layout = describe_for_hook(Path(file_path or ""))
        return vision_layout or layout_text
    except Exception:
        logger.warning("chart vision describe failed for report %s", report_id, exc_info=True)
        return layout_text


# 【功能】后台线程:处理一份已入库的研报(提取文本 → LLM 两步 → 落库 + 按品种写聚合)。
# 【参数】report_id: research_reports 表主键。
# 【返回】无。过程状态推进:processing → done / error。
# 【关键逻辑】1) 镜像 run_analysis 的 daemon 线程模式,不阻塞上传响应;
#           2) 一份研报可覆盖多个品种:第一步 LLM 识别全部品种并逐品种提取
#              数据(含标题/发行方自动识别),第二步按品种逐一生成结论;
#           3) 成功 → status=done,并把每个品种的数据点/方向/置信度/结论
#              分别 upsert 到对应品种的聚合 JSON(upssert_research_report),
#              get_research_report / merge_* 按品种读取即自动一一匹配;
#           4) 主品种 = 用户选择优先、否则第一个识别品种,回写 DB 行供列表
#              徽标/主方向展示;异常 → status=error + error 前 2000 字符。
def _process_research_report(report_id: int):
    db = get_db()
    report = db.get_research_report(report_id)
    if not report:
        logger.warning("Research report %s not found, skip processing", report_id)
        return
    selected = (report.get("variety") or "").upper().strip()
    try:
        text, _used_ocr = _extract_report_text(report.get("file_path") or "")
        if not text.strip():
            # 空文本喂给 LLM 只会得到空/编造结果,直接判失败让用户看到明确原因
            # (扫描版 PDF 无 OCR、图片 OCR 失败、或文件本身为空都属于这种情况)。
            raise ValueError("未能从文件中提取到文本(文件为空,或需 OCR 但 OCR 不可用)")
        # 【2026-09-08 方案三】版面感知提取(图表聚成【图】/【表】块)落 layout_text,
        # 供 RAG 切块优先使用;失败/非 PDF 返回空串 → 回退 extracted_text,不阻断。
        try:
            from tradingagents.dataflows.pdf_layout import extract_layout_text
            layout_text = extract_layout_text(Path(report.get("file_path") or ""))[:60000]
        except Exception:
            logger.warning("layout extraction failed for report %s", report_id, exc_info=True)
            layout_text = ""
        db.update_research_report(
            report_id, status="processing", extracted_text=text[:20000], layout_text=layout_text
        )

        client = create_llm_client(
            config["llm_provider"],
            config.get("quick_think_llm", config["deep_think_llm"]),
        )
        llm = client.get_llm()

        # 第一步:研报元数据 + 多品种结构化数据(标题/发行方自动识别,用户手填兜底)
        structured = _llm_extract_structured(llm, selected, text)
        varieties = structured.get("varieties") or []
        if not varieties:
            # LLM 未输出品种时的最后兜底:从标题/正文关键词恢复(子品种研报如
            # "光伏玻璃周度报告"→FG),避免 fxbaogao 自动接入时整份研报被丢弃。
            rec = _recover_variety_from_text(text, selected)
            if rec:
                varieties = [rec]
        if not varieties:
            raise ValueError("未能识别研报中的品种与数据,请确认研报内容或重新上传")
        # 【品种池】2026-09-09 起只入库 ACTIVE_VARIETIES(20 品种)覆盖的研报;
        # 池外品种研报(如 RB/I)标 error 跳过,不进总结/聚合(存量行保留不删)。
        if not (set(codes_all := [str(v.get("variety", "")).upper() for v in varieties]) & ACTIVE_VARIETIES):
            pool_hint = "、".join(sorted(ACTIVE_VARIETIES))
            raise ValueError(f"识别品种 {','.join(codes_all)} 均不在当前品种池内(仅支持: {pool_hint}),已跳过入库")
        # 【入库即直接提取四类基本面】LLM 四键提取后仍缺的键,用研报原文确定性补上并
        # 随结构化 data_points 一起落库(新研报不靠读时兜底;总结遗漏也不丢数)。
        _ingest_backfill_fund_metrics(varieties, text)
        title = (
            (structured.get("report_title") or "").strip()
            or (report.get("title") or "").strip()
            or Path(report.get("filename") or "研报").stem
        )
        source = (
            (structured.get("publisher") or "").strip()
            or (report.get("source") or "").strip()
            or "上传"
        )
        codes = [v["variety"] for v in varieties]
        primary = selected if selected in codes else codes[0]  # 主品种:用户选择优先,否则第一个识别品种
        primary_item = next(v for v in varieties if v["variety"] == primary)

        # 第二步:按品种生成结论(markdown),与品种一一对应
        conclusions = _llm_opinion_conclusion(llm, text, varieties, report_id=report_id)
        full_conclusion = "\n\n".join(
            f"## {code} 结论\n{conclusions.get(code, '')}" for code in codes
        ).strip()

        db.update_research_report(
            report_id,
            status="done",
            title=title,
            source=source,
            variety=primary,
            varieties=",".join(codes),
            structured_data=json.dumps(structured, ensure_ascii=False),
            conclusion_md=full_conclusion,
            direction=primary_item.get("direction", "中性"),
            confidence=primary_item.get("confidence", 0.5),
        )
        # 【发布日期自愈】入库时没拿到发布日期的行(手动上传/老采集器),第一步
        # LLM 抽出 publish_date 后抄进列里,让日期分组立即按发布日期生效。
        eff_publish = _self_heal_publish_date(db, report_id, report.get("publish_date") or "", structured)
        # 【研报类型自愈】同上:标题启发式没命中的行由 LLM 补标日报/周报。
        eff_type = _self_heal_report_type(db, report_id, report.get("report_type") or "", structured)
        # 写聚合 JSON:按品种拆分,每个品种只落自己的数据点/方向/置信度/结论,
        # 消费端 get_research_report("X") / merge_* 读 X 的聚合即自动一一匹配。
        _write_research_aggregates(
            report_id, report.get("uploaded_at") or "", title, source, codes, varieties, conclusions,
            publish_date=eff_publish, report_type=eff_type,
        )
        # 【位图图表视觉重述】status 已落 done(不拖慢用户看到结论),重述完再
        # 回写 layout_text;必须在 _rag_index_report_safely 之前(重述节要进本次索引)。
        layout_text = _vision_describe_safely(report_id, report.get("file_path") or "", layout_text)
        if layout_text:
            with contextlib.suppress(Exception):
                db.update_research_report(report_id, layout_text=layout_text[:60000])
        # 【RAG 自动索引】必须在发布日期/类型自愈之后(向量 metadata 要带自愈后的值);
        # 内部全 try/except,失败只打日志,不影响研报主流程。
        _rag_index_report_safely(report_id)
        logger.info("Research report %s (%s) processed OK, varieties=%s", report_id, primary, codes)
    except Exception as e:
        logger.exception("Research processing failed for report %s", report_id)
        with contextlib.suppress(Exception):
            db.update_research_report(report_id, status="error", error=str(e)[:2000])


# 【功能】只重跑研报的"核心观点"结论步骤(不重跑品种识别/结构化提取)。
# 【参数】report_id: research_reports 主键;llm: 可注入的模型客户端(默认按配置新建)。
# 【返回】{"ok": bool, "report_id": …, "codes": [代码]};失败含 "error" 字段。
# 【关键逻辑】1) 读已入库行的 extracted_text(缺则从文件重提)+ structured_data 里
#              归一化好的 varieties,用当前 _llm_opinion_conclusion 提示词重生成
#              逐品种观点;2) 回写 DB conclusion_md(status 保持 done,不改
#              structured_data/标题/方向),再按品种覆盖写聚合 JSON(upsert 按 id
#              替换,不新增重复);3) 供"观点提示词升级后刷新存量研报观点"使用,
#              异常只写 error 字段、不把已 done 行打成 error。
def reconclude_research_report(report_id: int, llm=None) -> dict:
    db = get_db()
    report = db.get_research_report(report_id)
    if not report:
        return {"ok": False, "report_id": report_id, "error": "研报不存在"}
    text = (report.get("extracted_text") or "").strip()
    if not text:
        text, _used_ocr = _extract_report_text(report.get("file_path") or "")
    if not (text or "").strip():
        return {"ok": False, "report_id": report_id, "error": "无研报正文,无法重生成观点"}
    try:
        structured = json.loads(report.get("structured_data") or "{}")
    except (TypeError, ValueError):
        structured = {}
    varieties = [v for v in (structured.get("varieties") or []) if v.get("variety")]
    if not varieties:
        # 老行 structured 无 varieties 时的兜底:退回主品种单品种重跑
        code = (report.get("variety") or "").upper().strip()
        if code:
            varieties = [{"variety": code, "direction": "中性", "confidence": 0.5}]
    if not varieties:
        return {"ok": False, "report_id": report_id, "error": "未识别出品种,无法生成观点"}
    try:
        if llm is None:
            client = create_llm_client(
                config["llm_provider"],
                config.get("quick_think_llm", config["deep_think_llm"]),
            )
            llm = client.get_llm()
        conclusions = _llm_opinion_conclusion(llm, text, varieties, report_id=report_id)
        codes = [v["variety"] for v in varieties]
        full_conclusion = "\n\n".join(
            f"## {code} 结论\n{conclusions.get(code, '')}" for code in codes
        ).strip()
        db.update_research_report(report_id, status="done", conclusion_md=full_conclusion)
        _write_research_aggregates(
            report_id,
            report.get("uploaded_at") or "",
            report.get("title") or "",
            report.get("source") or "",
            codes,
            varieties,
            conclusions,
            publish_date=(report.get("publish_date") or "").strip()[:10],
            report_type=(report.get("report_type") or "").strip(),
        )
        logger.info("Research report %s reconcluded, varieties=%s", report_id, codes)
        return {"ok": True, "report_id": report_id, "codes": codes}
    except Exception as e:
        logger.exception("Reconclude failed for research report %s", report_id)
        with contextlib.suppress(Exception):
            db.update_research_report(report_id, error=str(e)[:2000])
        return {"ok": False, "report_id": report_id, "error": str(e)}


# 【功能】存量研报"结构化重提取":重跑第一步 _llm_extract_structured(修复后的提示词),
# 把置信度语义/四键落地前入库的存量研报缺失字段补上并回写 DB 与聚合 JSON。
# 【参数】report_id: research_reports 主键;llm: 可注入的模型客户端(默认按配置新建)。
# 【返回】{"ok": bool, "report_id": …, "codes": [代码]};失败含 "error" 字段。
# 【关键逻辑】1) 只重跑第一步(不动第二步,不重生成结论):重新逐品种提取,重点拿到
#              · 真实置信度(旧提示词无语义区间 → 全部默认 0.5 / 抄示例 0.0);
#              · 四类基本面(基差/交易所仓单/开工率/加工利润)——存量行此前根本没提取过,
#                只在读时用启发式从总结里捞(2026-09-03 用户要求存量也重提取);
#              重提取后仍缺的四键交给 _ingest_backfill_fund_metrics 从研报原文确定性补漏,
#              与新研报入库路径完全一致(2026-09-03);
#           2) 以已入库 varieties 的代码集为锚(结论是按它生成的,不能孤儿化):每个存储
#              品种,新提取命中 → confidence 一律采用新值(新归一化:缺/非法给 None,
#              前端显示"—(未给)",不再拿 0.5 冒充),四键有值才覆盖;方向/评级保留,
#              避免与既有结论文本冲突;新提取未命中的代码原样保留;
#           3) 回写 DB structured_data(varieties 段替换)与主行 variety/varieties/
#              direction/confidence,并按品种覆盖聚合 JSON——各品种 conclusion 先从现有
#              聚合按 id 取回、原样写回(不重跑第二步 → 观点文本不变);
#           4) 异常只写 error 字段、不把已 done 行打成 error(同 reconclude)。
def re_extract_research_report(report_id: int, llm=None) -> dict:
    db = get_db()
    report = db.get_research_report(report_id)
    if not report:
        return {"ok": False, "report_id": report_id, "error": "研报不存在"}
    text = (report.get("extracted_text") or "").strip()
    if not text:
        text, _used_ocr = _extract_report_text(report.get("file_path") or "")
    if not (text or "").strip():
        return {"ok": False, "report_id": report_id, "error": "无研报正文,无法重提取结构化数据"}
    try:
        structured = json.loads(report.get("structured_data") or "{}")
    except (TypeError, ValueError):
        structured = {}
    stored = [v for v in (structured.get("varieties") or []) if v.get("variety")]
    if not stored:
        # 老行 structured 无 varieties 时的锚:退回主品种单品种
        code = (report.get("variety") or "").upper().strip()
        if code:
            stored = [{"variety": code}]
    if not stored:
        return {"ok": False, "report_id": report_id, "error": "未识别出品种,无法重提取"}
    try:
        if llm is None:
            client = create_llm_client(
                config["llm_provider"],
                config.get("quick_think_llm", config["deep_think_llm"]),
            )
            llm = client.get_llm()
        new_structured = _llm_extract_structured(llm, report.get("variety") or "", text)
        fresh = {
            v["variety"]: v for v in (new_structured.get("varieties") or []) if v.get("variety")
        }

        # 以存储品种代码集为锚合并:命中 → confidence 一律新值,四键有值才覆盖
        merged = []
        for ov in stored:
            code = ov["variety"]
            item = dict(ov)
            nv = fresh.get(code)
            if nv:
                item["confidence"] = nv["confidence"]  # 修复后的真实置信度(可为 None=未给)
                for k in ("basis", "warehouse_receipts", "operating_rate", "processing_margin"):
                    if _fund_value(nv.get(k)) is not None:
                        item[k] = nv[k]  # 四类基本面:存量行此前根本没提取
            merged.append(item)
        # 仍缺的四键用研报原文确定性补漏(同 _process_research_report 新研报路径)
        _ingest_backfill_fund_metrics(merged, text)

        codes = [v["variety"] for v in merged]
        primary = (report.get("variety") or "").upper().strip()
        if primary not in codes:
            primary = codes[0]
        primary_item = next(v for v in merged if v["variety"] == primary)
        structured["varieties"] = merged  # 顶层标题/发行方/日期保留,仅替换 varieties 段

        # 各品种结论先从现有聚合按 id 取回 → 原样写回(不重跑第二步,观点文本不变)
        conclusions = {}
        for code in codes:
            conclusions[code] = _report_conclusion_for_variety(code, report_id)

        db.update_research_report(
            report_id,
            status="done",
            variety=primary,
            varieties=",".join(codes),
            structured_data=json.dumps(structured, ensure_ascii=False),
            direction=primary_item.get("direction", "中性"),
            confidence=primary_item.get("confidence"),
        )
        # 重提取也顺带自愈发布日期/研报类型(第一步重新抽过,老行缺列值的在此补上)
        eff_publish = _self_heal_publish_date(
            db, report_id, report.get("publish_date") or "", new_structured
        )
        eff_type = _self_heal_report_type(
            db, report_id, report.get("report_type") or "", new_structured
        )
        _write_research_aggregates(
            report_id,
            report.get("uploaded_at") or "",
            report.get("title") or "",
            report.get("source") or "",
            codes,
            merged,
            conclusions,
            publish_date=eff_publish,
            report_type=eff_type,
        )
        logger.info("Research report %s re-extracted, varieties=%s", report_id, codes)
        return {"ok": True, "report_id": report_id, "codes": codes}
    except Exception as e:
        logger.exception("Re-extract failed for research report %s", report_id)
        with contextlib.suppress(Exception):
            db.update_research_report(report_id, error=str(e)[:2000])
        return {"ok": False, "report_id": report_id, "error": str(e)}


@app.route("/api/research/upload", methods=["POST"])
def api_research_upload():
    """上传研报(PDF/图片/MD/TXT):校验 → 落盘 → 入库 processing → 后台处理,立即返回 {id}。

    variety/title/source 全部可选:留空由 LLM 在后台自动识别(多品种研报会识别
    出全部品种并拆分;标题/发行方从文本中识别)。文件必填。
    """
    variety = (request.form.get("variety") or "").strip().upper()
    title = (request.form.get("title") or "").strip()
    source = (request.form.get("source") or "").strip()
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "缺少上传文件"}), 400

    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in RESEARCH_ALLOWED_EXTS:
        return jsonify({"error": f"不支持的文件类型 {ext},仅支持 PDF/图片/Markdown/TXT"}), 400

    f.stream.seek(0, os.SEEK_END)
    size = f.stream.tell()
    f.stream.seek(0)
    if size > RESEARCH_MAX_SIZE:
        return jsonify({"error": "文件超过 20MB 上限"}), 400

    from werkzeug.utils import secure_filename  # 【调用包】文件名安全化(防路径穿越)

    safe = secure_filename(f.filename) or "report"
    upload_dir = RESEARCH_UPLOAD_DIR / (variety or "MULTI")  # 未选品种时归入 MULTI 目录
    upload_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_path = upload_dir / f"{ts}_{safe}"
    f.save(str(file_path))

    report_id = get_db().insert_research_report(
        variety=variety, title=title, source=source, filename=f.filename,
        file_path=str(file_path), ingest_source="manual",  # 【来源】网页人工上传
    )
    threading.Thread(target=_process_research_report, args=(report_id,), daemon=True).start()
    return jsonify({
        "id": report_id, "status": "processing",
        "message": "研报已接收,后台处理中(标题/发行方/品种将自动识别)",
    })


# ── 自传数据(2026-09-07):Excel/CSV/MD/TXT → LLM 格式识别 → 分析师注入 ────

USER_DATA_ALLOWED_EXTS = (".xlsx", ".xls", ".csv", ".md", ".txt")  # 【变量】自传数据支持的扩展名(xls 老格式需 xlrd,缺失时报错提示转存)
USER_DATA_MAX_SIZE = 20 * 1024 * 1024  # 【变量】上传大小上限 20MB(与研报上传一致)


def _client_tag() -> str:
    """当前请求的客户端标识(IP):自传数据"仅在上传电脑上使用"的隔离键。

    【关键逻辑】隧道部署(cloudflared)下 remote_addr 恒为本机回环,真实来源在
              CF-Connecting-IP / X-Forwarded-For 头里;按优先级取第一个非空值,
              归一化 IPv4-mapped 前缀(::ffff:),本机直连归一为 127.0.0.1。
    """
    ip = (
        request.headers.get("CF-Connecting-IP")
        or (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        or request.remote_addr
        or ""
    ).strip()
    ip = ip.replace("::ffff:", "").replace("::1", "127.0.0.1")
    return ip or "unknown"


@app.route("/api/userdata/upload", methods=["POST"])
def api_userdata_upload():
    """上传自传数据文件(Excel/CSV/MD/TXT):落盘 → 入库 processing → 后台解析,立即返回 {id}。

    【表单】file 必填;variety 可选(手选品种优先,留空由 LLM 从样张/文件名识别)。
    【处理】后台线程:user_data.ingest_file —— 确定性读行 → LLM 看样张产解析规格
            (品种/数据类型/日期列/列含义/单位/频率)→ 归一化入库 done;失败落 error。
    【隐私】数据集打上上传者客户端标识(仅在上传电脑上使用);**原文件解析完成后
            立即从服务器删除**(服务器只留归一化数据行,不留原件)。
    """
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "缺少上传文件"}), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in USER_DATA_ALLOWED_EXTS:
        return jsonify({"error": f"不支持的文件类型 {ext},仅支持 Excel/CSV/MD/TXT"}), 400
    f.stream.seek(0, os.SEEK_END)
    if f.stream.tell() > USER_DATA_MAX_SIZE:
        return jsonify({"error": "文件超过 20MB 上限"}), 400
    f.stream.seek(0)

    hint_variety = (request.form.get("variety") or "").strip().upper()
    client_tag = _client_tag()  # 【隔离】上传者标识:该数据集只在此客户端发起的分析中注入
    from werkzeug.utils import secure_filename  # 【调用包】文件名安全化(防路径穿越)

    safe = secure_filename(f.filename) or "dataset"
    if not safe.endswith(ext):  # 纯中文文件名会被 secure_filename 清空,保住扩展名(解析器按扩展名分流)
        safe += ext
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    upload_dir = RESEARCH_UPLOAD_DIR.parent / "user_datasets"  # 与 user_data.USER_DATA_DIR 同根同目录
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / f"{ts}_{safe}"
    f.save(str(file_path))

    dataset_id = get_db().insert_user_dataset(f.filename, str(file_path), hint_variety,
                                              client_tag=client_tag)
    threading.Thread(
        target=_process_user_dataset, args=(dataset_id, str(file_path), f.filename, hint_variety),
        daemon=True,
    ).start()
    return jsonify({
        "id": dataset_id, "status": "processing",
        "message": "数据文件已接收,后台解析中(LLM 识别格式;原文件解析后即从服务器删除)",
    })


def _process_user_dataset(dataset_id: int, file_path: str, filename: str, hint_variety: str):
    """后台解析自传数据:构造 quick 档 LLM 客户端交给 user_data.ingest_file。

    【关键】①解析失败不抛出(user_data 内部落 status=error),线程静默结束;
            ②**无论成败,处理完即删服务器上的原文件**(用户要求不留原件;
            归一化数据行已入库,失败原因也已落库,原件无需保留)。
    """
    from tradingagents.dataflows.user_data import ingest_file  # 【调用包】解析管线(懒导入)

    try:
        client = create_llm_client(
            config["llm_provider"],
            config.get("quick_think_llm", config["deep_think_llm"]),
        )
        ingest_file(dataset_id, file_path, filename, hint_variety, client)
    except Exception:
        logger.warning("User dataset %s processing crashed", dataset_id, exc_info=True)
        with contextlib.suppress(Exception):
            get_db().update_user_dataset(dataset_id, status="error", error="处理线程异常")
    finally:
        with contextlib.suppress(Exception):
            os.remove(file_path)  # 【隐私】服务器不留存自传数据原件(解析后即删)


@app.route("/api/userdata")
def api_userdata_list():
    """自传数据列表(元数据;variety 过滤可选;**只显示当前客户端上传的**)。"""
    variety = (request.args.get("variety") or "").strip().upper() or None
    return jsonify({"datasets": get_db().list_user_datasets(variety,
                                                            client_tag=_client_tag())})


@app.route("/api/userdata/<int:dataset_id>")
def api_userdata_detail(dataset_id: int):
    """自传数据详情:元数据 + 解析规格 + 数据行预览(前 100 行)。

    【隔离】只允许查看当前客户端上传的数据集(与列表同口径,防跨机窥探)。
    """
    ds = get_db().get_user_dataset(dataset_id)
    if not ds:
        return jsonify({"error": "数据集不存在"}), 404
    if (ds.get("client_tag") or "") != _client_tag():
        return jsonify({"error": "数据集不存在"}), 404  # 他人数据集对当前客户端不可见
    try:
        spec = json.loads(ds.get("spec") or "{}")
    except (json.JSONDecodeError, TypeError):
        spec = {}
    try:
        rows = json.loads(ds.get("data") or "[]")
    except (json.JSONDecodeError, TypeError):
        rows = []
    ds.pop("data", None)  # 大字段不整包返回,只给预览
    ds.pop("spec", None)
    return jsonify({"dataset": ds, "spec": spec, "rows_preview": rows[:100],
                    "row_count": len(rows)})


@app.route("/api/userdata/<int:dataset_id>", methods=["DELETE"])
def api_userdata_delete(dataset_id: int):
    """删除自传数据:DB 行 + 落盘文件一起清理(文件缺失不阻塞)。"""
    from contextlib import suppress  # 【调用包】孤儿文件清理失败不影响删行

    ds = get_db().get_user_dataset(dataset_id)
    if not ds:
        return jsonify({"error": "数据集不存在"}), 404
    if (ds.get("client_tag") or "") != _client_tag():
        return jsonify({"error": "数据集不存在"}), 404  # 只能删除本机上传的数据集
    get_db().delete_user_dataset(dataset_id)
    with suppress(Exception):
        os.remove(ds.get("file_path") or "")  # 落盘文件清理(路径来自库内,不存在即忽略)
    return jsonify({"ok": True})


def _report_conclusion_for_variety(code: str, rid) -> str:
    """读品种研报聚合 JSON,取该报告 id 的逐品种总结文本(启发式提取的兜底语料)。

    【关键】存量研报 DB structured_data 无四键,但其结论总结文本在聚合 JSON 里有
            (按品种拆分)。取同 id 报告的 conclusion;文件缺失/漂移删行 → 空串。
    """
    from tradingagents.dataflows.research_data import load_research_data  # 【调用包】聚合 JSON 读取

    data = load_research_data(code) or {}
    for rep in data.get("reports") or []:
        if rep.get("id") == rid:
            return rep.get("conclusion") or ""
    return ""


def _row_research_fund_metrics(r: dict, fallback_text: str = "") -> list[dict]:
    """DB 研报行 → 该行主品种段的四类基本面指标(结构化优先 + 总结文本启发式兜底)。

    【关键】多品种研报 structured_data.varieties[] 每品种一段;列表行显示的是 r["variety"]
            主品种,故优先取匹配段的 data_points(段内嵌或顶层展平都兼容),取不到才回退首段;
            四个键仍缺的,再交 _merge_fund_metrics 用 fallback_text(逐品种总结)启发式补齐。
    """
    try:
        sd = json.loads(r.get("structured_data") or "{}")
    except (json.JSONDecodeError, TypeError):
        sd = {}
    items = sd.get("varieties")
    if not isinstance(items, list) or not items:
        items = [sd]
    code = str(r.get("variety") or "").upper()
    seg = next((i for i in items if str((i or {}).get("variety") or "").upper() == code), items[0] or {})
    # 四类指标可能在段内 data_points(嵌套)或段顶层(旧行平铺);两处合并,嵌套优先。
    dp = {}
    if isinstance(seg, dict):
        nested = seg.get("data_points")
        if isinstance(nested, dict):
            dp.update(nested)
        for k, _, _ in _RESEARCH_FUND_METRICS:
            if k in dp:
                continue
            v = seg.get(k)
            if v is not None and v != "":
                dp[k] = v
    return _merge_fund_metrics(dp, fallback_text)


@app.route("/api/research")
def api_research():
    """研报列表:按品种/来源/类型过滤;剔除超长字段(原文/结构化/结论/路径)只给列表元数据。"""
    variety = (request.args.get("variety") or "").strip().upper()
    ingest_source = (request.args.get("ingest_source") or "").strip() or None
    report_type = (request.args.get("report_type") or "").strip() or None
    rows = get_db().list_research_reports(
        variety or None, ingest_source=ingest_source, report_type=report_type
    )
    for r in rows:
        # 2026-09-04:总览/列表不再带四类基本面(列已换交易要素);指标仍随
        # data_points 入库,数据看板 _research_dashboard_series 照常读 DB。
        r.pop("extracted_text", None)
        r.pop("structured_data", None)
        r.pop("conclusion_md", None)
        r.pop("file_path", None)
    return jsonify({"reports": rows})


def _compact_md(seg: str) -> str:
    """剥 markdown 装饰(标题符/粗斜体/行内码),换行并空并成单行文本。

    【功能】供观点要点摘录把研报结论 markdown 段落转成适合表格单元格的单行短文本。
    【参数】seg: 原始 markdown 片段。
    【返回】str: 去装饰、压缩空白后的单行文本(上限 400 字符)。
    【关键逻辑】先摘行首标题符,再摘 * _ ` 装饰符,段落内换行折成空格,连续空白并一个。
    """
    import re

    seg = re.sub(r"^#{1,6}\s*", "", seg or "", flags=re.M)  # 标题符
    seg = re.sub(r"(\*\*|__|\*|`|~~)", "", seg)  # 粗斜体/行内码
    seg = seg.replace("\n", " ").strip()
    return re.sub(r"\s{2,}", " ", seg)[:400]


def _parse_duo_points(body: str) -> list[str]:
    """『多空要点』节正文 → 单元格行列表(综述首行 + 利多/利空条目)。

    【功能】新口径(2026-09-07)观点要点单元格的解析器。提示词要求 LLM 按条输出
            "综述：…/利多：…/利空：…",但存在全半角冒号混用、长条目折行、
            markdown 装饰等噪声 → 先整段 _compact_md 压成单行再按前缀切开,防丢行。
    【参数】body: 该节原始 markdown 正文。
    【返回】list[str]: 每条一行(综述必居首);空条目/裸前缀剔除;整段无任何前缀时
            降级为综述一行;解析不出内容返回 [](调用方回落旧口径渲染)。
    【关键逻辑】LLM 存在把该节写成 markdown 表格的倾向(实测 id=164:| 综述 | … |),
                先把表格行还原成"前缀：内容"条目行再走统一解析。
    """
    import re

    pre: list[str] = []
    for ln in (body or "").splitlines():
        if not ln.strip().startswith("|"):  # 普通行原样保留
            pre.append(ln)
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        cells = [c for c in cells if c and not re.fullmatch(r":?-{2,}:?", c)]  # 去分隔行
        if not cells:
            continue
        if cells[0] in ("综述", "利多", "利空"):  # 条目行还原(表头/续行不计)
            pre.append(f"{cells[0]}：" + "；".join(cells[1:]))
    compact = _compact_md("\n".join(pre))
    if not compact or "未披露" in compact:
        return []
    parts = re.split(r"(?=综述[：:]|利多[：:]|利空[：:])", compact)
    lines = [p.strip() for p in parts if p.strip()]
    if len(lines) == 1 and not re.match(r"综述[：:]", lines[0]):
        lines = [f"综述：{lines[0]}"]
    lines = [re.sub(r"^(综述|利多|利空)[：:]", r"\1：", ln) for ln in lines]  # 半角冒号归一
    lines.sort(key=lambda s: 0 if s.startswith(("综述：", "综述:")) else 1)  # 稳定排序,综述居首
    return [ln for ln in lines if len(ln) > 3]  # 剔除"利多：/综述："这类裸前缀空条目


def _extract_key_opinion(conclusion: str, max_len: int = 360, include_trade: bool = True) -> str:
    """逐品种结论 markdown → 观点表格"观点要点"单元格(双口径渲染)。

    【功能】新口径(2026-09-07)结论正文 = 八个小节,新增『## 多空要点』(综述 +
            利多/利空条目,每条带逻辑/风险)→ 单元格改为用户指定结构:
            综述 → 利多/利空行,(include_trade=True 时)交易要素行最后 ——
            每条看法直接对着逻辑支持与风险来源,不再按推理链罗列小节。
            旧口径(2026-09-03,七小节无多空要点)结论原样走推理链渲染兜底:
            其余小节按文档顺序逐节抽成 `小节名: 一句话` —— 供需/库存/成本/
            现货/事件即推理链的"为什么",观点与依据收口方向。
            空节("研报未披露该指标"占位)给「—」:结构完整、不编造。
    【参数】conclusion: 该品种结论 markdown(聚合 JSON conclusion 字段)。
            max_len: 总长软上限(超则 _fit_section_lines 结构性裁剪)。
            include_trade: 是否保留「交易要素与风险」行。观点总览(2026-09-05)
            已把单边/区间+头寸+研报建议合并为独立「头寸」列,单元格再带交易行
            属重复 → 传 False 剔除;每日总结等无独立列的场景保持 True(默认)。
            新口径下交易行排在多空要点之后(综述/多空优先于 2026-09-04 的交易行
            前置);旧口径仍前置。
    【返回】str: 多行要点;无内容返回空串(前端显示 —)。
    【关键逻辑】范围止于 ## 数据支撑 —— 数据支撑/潜在分歧是证据复述与回看,不进
                单元格;点研报标题进详情弹层可看完整小节。旧四段式(## 核心观点
                开头)无 ## 供需格局 → 兜底取首个非标题段(保持旧行为)。
    """
    import re

    text = conclusion or ""
    if not text.strip():
        return ""
    start = text.find("## 供需格局")
    if start == -1:  # 旧口径兜底:首个非标题段(以 # 开头的标题行不算正文)
        para = next(
            (p.strip() for p in text.splitlines() if p.strip() and not p.strip().startswith("#")),
            "",
        )
        return _compact_md(para)
    end = text.find("## 数据支撑", start)
    seg = text[start: end if end != -1 else len(text)]
    blocks = re.findall(r"## ([^\n]+)\n(.*?)(?=\n## |\Z)", seg, re.S)
    out: list[str] = []
    trade_rows: list[str] = []  # 交易要素行:旧口径前置首行;新口径收尾(见下)
    duo_lines: list[str] | None = None  # 【关键】新口径『多空要点』行(综述+利多/利空)
    for title, body in blocks:
        t = (title or "").strip().strip("#").strip() or "小节"
        if "多空要点" in t:  # 新口径专节,单独解析,不进推理链行
            duo_lines = _parse_duo_points(body)
            continue
        compact = _compact_md(body)
        val = "—" if (not compact or "未披露" in compact) else compact
        row = f"{t}：{val}"
        if any(h in t for h in _TRADE_TITLE_HINTS):
            if include_trade:  # 观点总览已单列「头寸」→ 传 False 不再重复带交易行
                trade_rows.append(row)
        else:
            out.append(row)
    if duo_lines:  # 新口径:综述 → 利多/利空 → (include_trade 时)交易行;综述行强制保全
        return _fit_section_lines(duo_lines + trade_rows, max_len, must_keep=(0,))
    # 旧口径:受保行 = 交易要素行(前置,include_trade 时) + 供需格局行(推理链起点,
    # 原"首节"保护位被交易行占走后必须显式点名,否则超预算时最长的供需行会先出局)
    # + 末行(观点与依据,_fit_section_lines 内置)。无交易行时退化为首末保护。
    keep = list(range(len(trade_rows)))
    if out:
        keep.append(len(trade_rows))
    return _fit_section_lines(trade_rows + out, max_len, must_keep=tuple(keep))


def _fit_section_lines(lines: list[str], max_len: int = 240, must_keep: tuple[int, ...] = ()) -> str:
    """小节要点 → 单单元格文本;超预算时保证受保行完整(首行+末行+must_keep)。

    【功能】视图表单元格内容一多就会挤破软上限。此前从尾部整行截断会把
            收口方向的末节「观点与依据」砍掉 —— 恰是"为什么这么看"的落点。改为
            结构性裁剪:非受保小节按「真实内容优先、短节优先」依次入位,预算不足时
            最先让位的是纯占位行(「：—」,研报未披露无信息),其次才是较长的内容
            行 —— 尽量保住更多小节维度;再极端才硬截到行尾补 …。首行(交易要素或
            供需)、末行(观点与依据)及 must_keep 指定的行(如交易要素行)固定保全,
            表格任意一行都能看到 交易要素 → 依据 → 方向。
    【参数】lines: 逐行 `小节名：一句话`(顺序 = 输出顺序,交易要素行已前置)。
            max_len: 总长软上限。
            must_keep: 额外必须保全的行索引(与首末行并集;超预算时同样硬截兜底)。
    【返回】str: 裁剪后多行文本。
    【关键逻辑】占位行「：—」keep 优先级最低(放最后入位)故最先出局;内容行短节
                优先入位、长节后入位故先出局。首末行索引固定必保。保序返回
                (节顺序 = 输入顺序,不重排)。
    """
    joined = "\n".join(lines)
    if len(joined) <= max_len:
        return joined
    protect = {0, len(lines) - 1} | set(must_keep)  # 首末行 + 调用方点名必保的行
    if len(protect) >= len(lines):  # 全部受保:无让位空间,硬截兜底
        return joined[:max_len].rsplit("\n", 1)[0] + "\n…"
    other = [i for i in range(len(lines)) if i not in protect]
    # 出局优先级:纯占位「：—」行先出局,内容行按长度短者先出(信息量小先让位)
    other.sort(key=lambda i: (lines[i].rstrip().endswith("：—"), len(lines[i])))
    used = sum(len(lines[i]) for i in protect) + (len(protect) - 1)  # 受保行 + 其间换行
    keep = set()
    for i in other:
        cost = len(lines[i]) + 1  # 自身 + 前导换行
        if used + cost <= max_len:
            used += cost
            keep.add(i)
    trimmed = "\n".join(lines[i] for i in sorted(keep | protect))  # 恢复文档顺序
    if len(trimmed) <= max_len:
        return trimmed
    return trimmed[:max_len].rsplit("\n", 1)[0] + "\n…"


# 交易要素行五要素(观点总览「单边/区间」「头寸」列的解析口径):结论
# 「## 交易要素与风险」节为单行分号分隔五要素 —— 方向:X;形态与区间:X;头寸:X;头寸范围:X;风险:X。
# 2026-09-04 起,总览表的原四类基本面列(基差/仓单/开工率/加工利润)由这里取研报自己的
# 交易表述替代(指标本身仍随 data_points 入库,数据看板照常展示)。
_TRADE_ELEMENT_KEYS = ("方向", "形态与区间", "头寸", "头寸范围", "风险")


def _parse_trade_elements(conclusion: str) -> dict:
    """解析结论「交易要素与风险」节单行五要素 → {key: value}(未披露/「—」的键不返回)。

    【功能】观点总览的交易要素列直接取研报自己的表述,不再走结构化四键。
            兼容中英文冒号/分号(LLM 输出混用);值为「—」或「未披露」视为缺,不给键。
    【参数】conclusion: 该品种结论 markdown(聚合 JSON conclusion 字段)。
    【返回】dict: 如 {"方向": "反弹做多", "头寸": "轻仓试多"};无交易节/解析不出 → {}。
    """
    text = conclusion or ""
    m = re.search(r"##\s*交易要素[^\n]*\n([^\n]+)", text)
    if not m:
        return {}
    out: dict[str, str] = {}
    for tok in re.split(r"[;；]", m.group(1)):
        kv = re.match(r"\s*([^:：]{1,12})[:：](.*)", tok)
        if not kv:
            continue
        k, v = kv.group(1).strip(), kv.group(2).strip()
        if k and v and v not in ("—", "未披露"):
            out[k] = v
    return out


def _extract_advice(conclusion: str, max_len: int = 120) -> str:
    """取「## 建议权重」节正文(压缩单行,供观点总览「建议」列)。

    【返回】str: 压缩后 ≤max_len 字符;无该节/纯占位 → ""(前端显示 —)。
    """
    text = conclusion or ""
    m = re.search(r"##\s*建议权重\s*\n(.*?)(?=\n## |\Z)", text, re.S)
    if not m:
        return ""
    compact = _compact_md(m.group(1))
    if not compact or compact == "—" or "未披露" in compact[:12]:
        return ""
    return compact[:max_len]


def _merge_trade_cell(te: dict, report_advice: str) -> str:
    """交易要素三源 → 观点总览一列「头寸」单元格文本(片段级语义去重)。

    【功能】单边/区间、头寸、研报建议三列语义高度重复(如研报建议"多配为主"与
            头寸"多配为主"同句),2026-09-05 起合并为一列。源顺序:
            形态与区间(单边/区间表述)→ 头寸(缺时回退头寸范围)→ 研报原文操作
            建议原句。逐源按分隔符拆片段,「—/未披露」片段丢弃,与已收片段整含
            重复的丢弃(双向:短的先在则长的替换之,保信息量最大表述)。
    【参数】te: _parse_trade_elements 结果(键 缺 = 未披露);report_advice: 聚合
            记录的研报原文操作建议(旧行可能无此键)。
    【返回】str: 以「; 」连接的去重片段;全缺 → ""(前端显示 —)。
    """
    parts: list[str] = []

    def _key(s: str) -> str:
        return re.sub(r"\s+", "", s).lower()

    for raw in (
        te.get("形态与区间") or "",
        te.get("头寸") or te.get("头寸范围") or "",
        report_advice or "",
    ):
        for frag in re.split(r"[;；,，、\n]", str(raw)):
            frag = frag.strip().strip("。.;；,，、 ").strip()
            if not frag or frag == "—" or "未披露" in frag:
                continue
            k = _key(frag)
            hit = next((p for p in parts if len(k) >= 2 and k in _key(p)), None)
            if hit:  # 新片段已被已有片段整含 → 丢弃
                continue
            sub = next((p for p in parts if len(_key(p)) >= 2 and _key(p) in k), None)
            if sub:  # 已有片段被新片段整含 → 换成信息量更大的新片段
                parts[parts.index(sub)] = frag
                continue
            parts.append(frag)
    return "; ".join(parts)


# 研报四类基本面指标(基差/交易所仓单/开工率·负荷率/加工利润·加工费)的
# 键 → 表格列名/口径。数据来自研报结构化 data_points(研报没报则整项不出现)。
_RESEARCH_FUND_METRICS = (  # 【变量】(键, 表格列名, tooltip 口径) —— 供总览表/列表行/详情展示
    ("basis", "基差", "基差(现货价 − 近月合约价)"),
    ("warehouse_receipts", "仓单", "交易所注册仓单"),
    ("operating_rate", "开工率", "开工率/负荷率"),
    ("processing_margin", "加工利润", "加工利润/加工费"),
)


def _fund_value(v):
    """研报单个指标值归一化:{value,unit,date,note} 对象或标量都行;无值/空 → None。

    【返回】None 或 {"value","unit","date","note"}。
    """
    if isinstance(v, dict):
        val = v.get("value")
        if val is None or val == "":
            return None
        return {
            "value": val,
            "unit": v.get("unit") or "",
            "date": str(v.get("date") or "")[:10],
            "note": v.get("note") or "",
        }
    if v is None or v == "":
        return None
    return {"value": v, "unit": "", "date": "", "note": ""}


def _research_fund_metrics(dp) -> list[dict]:
    """从研报 data_points dict 抽四类基本面指标,固定序、只含有值项(研报没报=留空)。

    【返回】[{k,label,tip,value,unit,date,note}, ...];无任何一项 → 空列表(前端显示研报未披露)。
    """
    out = []
    for k, label, tip in _RESEARCH_FUND_METRICS:
        v = _fund_value((dp or {}).get(k))
        if v is not None:
            v["k"], v["label"], v["tip"] = k, label, tip
            out.append(v)
    return out


def _heuristic_fund_from_text(text: str, source_label: str = "自总结") -> dict:
    """从研报文本按规则提取四类基本面指标(仅 data_points 缺项时的兜底)。

    Args:
        text: 待扫描文本(存量研报的逐品种总结,或新研报入库时的研报原文)。
        source_label: 命中项 note 的来源前缀;读时兜底默认"自总结",新研报入库
            补漏用"研报原文",便于 UI 区分两条提取路径(都以『原文片段』结尾可识别)。

    【背景】存量研报在四键结构化落地前处理 → data_points 无 basis/仓单/开工率/加工利润,
            但这些数往往已写在 LLM 生成的逐品种结论总结里。用户要求"直接从总结中提取"
            补进逐品种表,而非重跑 LLM。故用保守规则提取,只在 数值+单位 相邻、语义词匹配
            时采信,并把命中原文片段写进 note("<source_label>『原文片段』")供前端悬停核对。
    【防误】· 开工率只用"开工率/负荷率/产能利用率"(不用裸"开工",避免"新开工/竣工"地产词);
            · 基差/加工利润要求紧跟 元/吨、元 等货币单位(否则"宁夏基差及1-5月价差"里的
              日期数字 1/5 不会误采);仓单要求紧跟 手/张/吨/万吨 等量词;
            · 逐句扫描(按。；;\\n 切句),只在同句内找"词+数值",避免跨句抓错数。
    【返回】{k: {k,label,tip,value,unit,date:"",note:"<source_label>『原文片段』"}};没可靠命中则为空。
    """
    if not text:
        return {}
    text = re.sub(r"[#*`>]", "", text)  # 【关键】去 markdown 装饰,避免把 # 号当字符干扰切句
    sentences = [s for s in re.split(r"[。；;\n]", text) if s.strip()]
    out: dict[str, dict] = {}
    # 统一模式: (?P<kw>语义词)\s*[:：为约达约]?\s*(?P<num>数值)\s*(?P<unit>单位)
    _NUM = r"[+-]?\d+(?:\.\d+)?"

    def _find(pat_kw: str, unit: str, name: str):
        """逐句找首个命中;返回 metric obj 或 None。name 供单测定位防误报。"""
        pattern = rf"(?P<kw>{pat_kw})\s*[:：为约达约]?\s*(?P<num>{_NUM})\s*(?P<unit>{unit})"
        for s in sentences:
            m = re.search(pattern, s)
            if not m:
                continue
            start = max(0, m.start("kw") - 8)
            snip = s[start:m.end() + 12].strip().replace("\n", " ")
            if len(snip) > 40:
                snip = snip[:40] + "…"
            return m.group("kw"), m.group("num"), m.group("unit"), snip
        return None

    hit = _find("开工率|负荷率|产能利用率", r"%", "operating_rate")
    if hit:
        out["operating_rate"] = _fund_metric_obj(
            "operating_rate", float(hit[1]), "%", f"{source_label}『{hit[3]}』")

    hit = _find("仓单", r"(?:万手|手|万吨|张|吨)", "warehouse_receipts")
    if hit:
        out["warehouse_receipts"] = _fund_metric_obj(
            "warehouse_receipts", hit[1], hit[2], f"{source_label}『{hit[3]}』")

    hit = _find("加工利润|加工费|加工差|加工价差|毛利|盘面利润", r"(?:元/吨|美元/吨|元)", "processing_margin")
    if hit:
        out["processing_margin"] = _fund_metric_obj(
            "processing_margin", float(hit[1]), hit[2], f"{source_label}『{hit[3]}』")

    hit = _find("现货升水|现货贴水|对盘面升水|对盘面贴水|基差", r"(?:元/吨|元)", "basis")
    if hit:
        val = float(hit[1])
        if "贴水" in hit[0]:  # 【关键】贴水 = 现货低于盘面 → 基差取负
            val = -abs(val)
        out["basis"] = _fund_metric_obj("basis", val, hit[2], f"{source_label}『{hit[3]}』")
    return out


def _fund_metric_obj(k: str, value, unit: str, note: str) -> dict:
    """把启发式命中打包成与 _research_fund_metrics 同 schema 的指标对象。"""
    label = tip = ""
    for kk, lb, tp in _RESEARCH_FUND_METRICS:
        if kk == k:
            label, tip = lb, tp
            break
    return {"k": k, "label": label, "tip": tip, "value": value, "unit": unit or "", "date": "", "note": note}


def _merge_fund_metrics(dp, text: str) -> list[dict]:
    """四类基本面 = 结构化 data_points 优先;缺项用总结文本启发式兜底;固定序返回。"""
    by_key = {m["k"]: m for m in _research_fund_metrics(dp)}
    for k, v in _heuristic_fund_from_text(text).items():
        by_key.setdefault(k, v)
    return [by_key[k] for k, _, _ in _RESEARCH_FUND_METRICS if k in by_key]


# 【功能】新研报入库补强:LLM 四键结构化提取后仍缺的键,用"研报原文"确定性补上。
# 【背景】新研报走 _llm_extract_structured(rule 3b 已含 basis/仓单/开工率/加工利润),
#         但 LLM 偶发漏填某键。用户在存量研报接受"从总结文本提取"的读时兜底,同时要求
#         新研报在入库时就"直接提取出这些指标"(总结本身仍有遗漏 → 不能只靠读时从总结捞),
#         故在此把仍缺的键用 _heuristic_fund_from_text 扫"该品种相关原文句子"补进结构化
#         data_points(确定性、零额外 LLM 成本),入库即自带,读时不再需要 fallback。
# 【防误】· 单品种研报扫全文;多品种研报只扫含该品种代码/中文名的句子,防止把另一子品种
#          的开工率/基差串到本品种(找不到含本品种名的句子则放弃,不冒险);
#         · 已有结构化值(LLM 给的)一律不动,只补真缺的键;
#         · note 标"研报原文『…』"与 LLM 结构化值区分(UI 以 『 结尾识别为文本提取)。
# 【返回】无;原地改 varieties 各元素(与 structured["varieties"] 同对象,DB/聚合同步落库)。
def _ingest_backfill_fund_metrics(varieties: list[dict], text: str) -> None:
    name_map = {k: v["name"] for k, v in VARIETY_METADATA.items()}
    single = len(varieties) <= 1  # 单品种研报 → 扫全文最稳;多品种才需要按品种名划句

    def _scope(code: str) -> str:
        if single or not text:
            return text or ""
        toks = [t for t in (code, name_map.get(code, "")) if t]
        return "\n".join(s for s in re.split(r"[。；;\n]", text) if s and any(t in s for t in toks))

    for item in varieties:
        found = _heuristic_fund_from_text(_scope(item.get("variety") or ""), source_label="研报原文")
        if not found:
            continue
        for k in ("basis", "warehouse_receipts", "operating_rate", "processing_margin"):
            if _fund_value(item.get(k)) is not None:
                continue  # LLM 已给结构化值,不覆盖
            m = found.get(k)
            if not m:
                continue
            item[k] = {"value": m["value"], "unit": m.get("unit") or "",
                       "date": m.get("date") or "", "note": m.get("note") or ""}


def _fmt_avg(x) -> str:
    """置信度均值格式化:None → '—',否则两位小数。"""
    return "—" if x is None else f"{x:.2f}"


# 【功能】确定性计算"机构(研报) vs 散户(社媒)"观点对比(无 LLM、无网络)。
# 【参数】symbol: 品种代码。
# 【返回】dict | None:两侧都无数据时返回 {"available": False};否则含
#          institution(机构侧:在库研报计数/平均置信度/主要研报)与
#          retail(散户侧:社媒多空占比/标签/avg_score/帖数/趋势)与
#          judgement{kind: 共振|背离|分化|单边, detail}。
# 【关键逻辑】机构侧 = research_data.summarize_research_views(按 direction 全量计数);
#           散户侧 = load_sentiment_data 的 social_sentiment(以 bullish/bearish_ratio
#           差 >5pp 判净方向);两侧同在→同向净=共振/反向净=背离/一侧中性=分化;
#           仅一侧有数据→单边。全部确定性纯算术,不新增 LLM 调用。
def _build_group_compare(symbol: str) -> dict:
    """Build the institutional (research) vs retail (social) group-sentiment compare card."""
    from tradingagents.dataflows.research_data import (
        summarize_research_views,  # 【调用包】研报方向汇总(确定性)
    )
    from tradingagents.dataflows.sentiment_data import (
        load_sentiment_data,  # 【调用包】社媒情绪数据(思路2 采集)
    )

    res = summarize_research_views(symbol)
    inst_present = res["count"] > 0
    inst_dir = res["net_dir"]

    sd = load_sentiment_data(symbol) or {}
    ss = (sd.get("data") or {}).get("social_sentiment") or {}
    posts = int(ss.get("total_posts_analyzed", 0) or 0)
    retail_present = posts > 0
    b_r = float(ss.get("bullish_ratio") or 0.0)
    e_r = float(ss.get("bearish_ratio") or 0.0)
    n_r = float(ss.get("neutral_ratio") or 0.0)
    if retail_present and b_r > e_r + 0.05:
        retail_dir = "看多"
    elif retail_present and e_r > b_r + 0.05:
        retail_dir = "看空"
    else:
        retail_dir = "中性"

    if not inst_present and not retail_present:
        return {"available": False}

    if inst_present and retail_present:
        if inst_dir in ("看多", "看空") and inst_dir == retail_dir:
            kind, detail = (
                "共振",
                f"机构研报净方向{inst_dir}(看多{res['counts']['bull']}/看空{res['counts']['bear']}份)"
                f"与散户社媒{retail_dir}方向一致,趋势相互强化;若散户同时极度一边倒,警惕反向泡沫。",
            )
        elif inst_dir in ("看多", "看空") and retail_dir in ("看多", "看空"):
            kind, detail = (
                "背离",
                f"机构研报净方向{inst_dir},而散户社媒{retail_dir},两群体方向相反——"
                f"警惕反转/波动放大(机构有论据与置信度,散户是群体心理,注意权衡)。",
            )
        else:
            kind, detail = (
                "分化",
                f"机构研报净方向{inst_dir},散户社媒{retail_dir},一方偏中性——"
                f"既无强共振也无清晰背离,信号中性偏弱。",
            )
    else:
        side = "仅机构(研报)" if inst_present else "仅散户(社媒)"
        kind = "单边"
        detail = f"另一方暂无数据:{side}。对比参考价值有限。"

    return {
        "available": True,
        "symbol": symbol,
        "updated": res["updated"] or (sd.get("updated") or ""),
        "institution": {
            "present": inst_present,
            "count": res["count"],
            "counts": res["counts"],
            "conf_avg": res["conf_avg"],
            "net_dir": inst_dir,
            "items": res["items"][:6],
        },
        "retail": {
            "present": retail_present,
            "dir": retail_dir,
            "label": ss.get("overall_sentiment_label", ""),
            "bull_ratio": round(b_r, 4),
            "bear_ratio": round(e_r, 4),
            "neutral_ratio": round(n_r, 4),
            "avg_score": ss.get("avg_score"),
            "posts": posts,
            "trend": ss.get("trend_label", ""),
        },
        "judgement": {"kind": kind, "detail": detail},
    }


# 【功能】把对比卡 dict → 持久化历史报告的 markdown(确定性)。
# 【参数】gc: _build_group_compare 的返回值。
# 【返回】str: 表格化文本;不可用返回空串(调用方守卫,不新增历史章节)。
def _group_compare_markdown(gc: dict) -> str:
    """Render the group-compare dict into markdown for the persisted report."""
    if not gc or not gc.get("available"):
        return ""
    j = gc["judgement"]
    inst = gc["institution"]
    rt = gc["retail"]
    lines = [
        f"**判定: {j['kind']}** — {j['detail']}",
        "",
        "| 群体 | 净方向 | 多/空/中 计数 | 关键读数 |",
        "|---|---|---|---|",
    ]
    lines.append(
        f"| 机构(研报) | {inst.get('net_dir') or '—'} | "
        f"看多 {inst['counts']['bull']} / 中性 {inst['counts']['neutral']} / 看空 {inst['counts']['bear']} "
        f"({inst['count']} 份) | 平均置信度 多={_fmt_avg(inst['conf_avg']['bull'])} "
        f"中性={_fmt_avg(inst['conf_avg']['neutral'])} 空={_fmt_avg(inst['conf_avg']['bear'])} |"
    )
    if rt.get("present"):
        lines.append(
            f"| 散户(社媒) | {rt['dir']} | 多 {rt['bull_ratio']:.0%} / 中性 {rt['neutral_ratio']:.0%} / "
            f"空 {rt['bear_ratio']:.0%} | avg_score {rt.get('avg_score')}, {rt.get('posts')} 帖, "
            f"趋势 {rt.get('trend') or '—'} |"
        )
    else:
        lines.append("| 散户(社媒) | 无数据 | — | 暂无独立社媒数据 |")
    top = "".join(
        f"\n- [{i['direction']}] {i['title']} ({i['source']})" for i in (inst.get("items") or [])
    )
    if top:
        lines.append("\n机构侧主要研报:" + top)
    return "\n".join(lines)


def _research_view_row(r: dict) -> dict:
    """聚合 JSON 单条研报 → 观点表格行(只带表格要用的字段,剥离结论全文降载荷)。"""
    conclusion = r.get("conclusion") or ""
    te = _parse_trade_elements(conclusion)
    return {
        "id": r.get("id"),
        "title": r.get("title") or "",
        "source": r.get("source") or "",
        "uploaded_at": r.get("uploaded_at") or "",
        "publish_date": r.get("publish_date") or "",
        "report_type": r.get("report_type") or "",
        "direction": r.get("direction") or "中性",
        "confidence": r.get("confidence"),
        "covers": list(r.get("varieties") or []),  # 覆盖品种标注:多品种研报提示只展示当前品种部分
        "key_opinion": _extract_key_opinion(conclusion, include_trade=False),
        # 头寸列(2026-09-05 替换原单边/区间+头寸+研报建议三列,三源语义去重合并):
        # 形态与区间(头寸缺时回退头寸范围)取自「## 交易要素与风险」节=研报原始
        # 表述 + 研报原文操作建议原句(2026-09-04 新增,旧行无此键);
        # 建议=「## 建议权重」节=系统 LLM 基于研报观点生成的建议(非研报原文)。
        "trade": _merge_trade_cell(te, str(r.get("report_advice") or "")),
        "advice": _extract_advice(conclusion),
    }


@app.route("/api/research/views")
def api_research_views():
    """逐品种观点总览:某品种某研报日期各发行方的 方向/置信度/观点要点。

    【功能】观点表格后端:读品种聚合 JSON(external_data/{CODE}_research.json,字段
            conclusion 即 2026-09-02 两段式逐品种观点),按研报当天(publish_date 真实
            发布日优先,旧聚合记录回退 uploaded_at 前 10 位)列出各行。缺省取该品种
            最新研报日期;前端传 date 则切到对应日期。
    【参数】URL query: variety(品种代码,必填); date(YYYY-MM-DD,可选);
            report_type(日报/周报,可选,空=全部 —— 日报与周报结论分开统计,2026-09-07)。
    【返回】json: {variety, name, date, dates(可选日期倒序), rows[]}。
    【关键逻辑】方向/置信度/观点均为该品种口径(聚合 JSON 本就是按品种拆的);
                dates 由现有研报当天集合去重降序得出,无研报返回空数组;
                传 report_type 时先按类型过滤再算日期并集(口径与每日/周报总结一致:
                日报=非周报行含未打类型,周报=仅周报行)。
    """
    code = (request.args.get("variety") or "").strip().upper()
    date = (request.args.get("date") or "").strip()[:10]
    rtype = (request.args.get("report_type") or "").strip()  # 空=全部;日报/周报分开统计
    if not code:
        return jsonify({"variety": "", "name": "", "date": "", "dates": [], "rows": []})
    from tradingagents.dataflows.research_data import load_research_data  # 【调用包】聚合 JSON 读取

    data = load_research_data(code) or {}
    reports = data.get("reports") or []
    # 类型口径与每日/周报总结一致(2026-09-07):「日报」=非周报行(未打类型的行也进日报
    # 口径,否则总结里有、总览筛日报却查不到);「周报」=仅周报行。
    if rtype == "周报":
        reports = [r for r in reports if (r.get("report_type") or "").strip() == "周报"]
    elif rtype:
        reports = [r for r in reports if (r.get("report_type") or "").strip() != "周报"]

    def _rdate(r: dict) -> str:
        # 日期分组键:聚合记录 publish_date(真实发布日)优先,旧聚合文件无此键回退 uploaded_at
        return (r.get("publish_date") or "").strip()[:10] or str(r.get("uploaded_at") or "")[:10]

    dates = sorted({_rdate(r) for r in reports if _rdate(r)}, reverse=True)
    if not dates:
        meta = VARIETY_METADATA.get(code, {})
        return jsonify({"variety": code, "name": meta.get("name", code), "date": "", "dates": [], "rows": []})
    if date in dates:
        rows = [r for r in reports if _rdate(r) == date]
    else:  # 传入日期不属于该品种(切换品种残留) → 回退最新研报日期
        rows = [r for r in reports if _rdate(r) == dates[0]]
        date = dates[0]
    rows.sort(key=lambda r: str(r.get("uploaded_at") or ""), reverse=True)
    meta = VARIETY_METADATA.get(code, {})
    return jsonify({
        "variety": code,
        "name": meta.get("name", code),
        "date": date,
        "dates": dates,
        "rows": [_research_view_row(r) for r in rows],
    })


# ---------------------------------------------------------------------
# 研报每日总结(2026-09-04):把某一天(发布日期优先,缺则回退 uploaded_at 前 10 位)
# 在库全部研报的逐品种分析结论交 LLM 汇总成一份 markdown(各公司观点 / 观点对比与冲突 /
# 可信程度分析 / 综合结论),落盘 ~/.tradingagents/research_daily/{date}.md,
# 供前端"每日总结"卡随时调阅;已生成过的日期直接读文件,不重复烧 LLM。
# ---------------------------------------------------------------------
RESEARCH_DAILY_DIR = Path.home() / ".tradingagents" / "research_daily"
RESEARCH_WEEKLY_DIR = Path.home() / ".tradingagents" / "research_weekly"  # 周报总结独立落盘(2026-09-07 与日报分开)


def _summary_dir(rtype: str = "") -> Path:
    """总结落盘目录:周报口径 → research_weekly,日报口径(默认) → research_daily。"""
    return RESEARCH_WEEKLY_DIR if (rtype or "").strip() == "周报" else RESEARCH_DAILY_DIR


def _research_daily_path(date: str, rtype: str = "") -> Path:
    """某日期的总结 md 文件路径(周报 → research_weekly/{date}.md,日报 → research_daily/{date}.md)。"""
    return _summary_dir(rtype) / f"{date}.md"


def _research_daily_dates(db, rtype: str = "") -> list[str]:
    """可用日期并集倒序 = 库内已有 done 结论的对应类型研报日期(发布日优先) ∪ 已生成总结文件的日期。

    【关键】日报口径(默认)排除周报行(每日总结只收日报,2026-09-05 用户定);周报口径
            (rtype='周报')只收周报行 —— 两类日期与总结文件各自独立,前端下拉分开(2026-09-07)。
    """
    if (rtype or "").strip() == "周报":
        rows = [r for r in db.list_research_reports(limit=500)
                if (r.get("report_type") or "").strip() == "周报"]
    else:
        rows = [r for r in db.list_research_reports(limit=500)
                if (r.get("report_type") or "") != "周报"]
    dates = {
        d for d in (_report_date(r) for r in rows)
        if re.match(r"^\d{4}-\d{2}-\d{2}$", d)  # 只认合法日期(剔除空/异常时间戳前缀)
    }
    sdir = _summary_dir(rtype)
    if sdir.is_dir():
        dates |= {p.stem for p in sdir.glob("????-??-??.md")}
    return sorted(dates, reverse=True)


def _daily_variety_segment(conclusion_md: str, code: str) -> str:
    """多品种结论 md 里抠「## {code} 结论」段;没有(单品种/旧格式)→ 原文。

    【关键】段内还有自己的 ## 小节(供需格局等),不能见到 \n## 就断 —— 前瞻只认
            下一个「## <品种代码> 结论」块(品种代码为字母数字)或全文结束。
    """
    m = re.search(
        rf"##\s*{re.escape(code)}\s*结论\s*\n(.*?)(?=\n##\s*[A-Za-z0-9]+\s*结论\b|\Z)",
        conclusion_md or "", re.S,
    )
    return m.group(1).strip() if m else (conclusion_md or "").strip()


def _variety_agg_conf(code: str, report_id, cache: dict) -> float | None:
    """聚合 JSON {CODE}_research.json 内该研报对该品种的逐品种置信度;读不到返回 None。

    【供】_collect_daily_report_items / _collect_variety_report_items 共用;cache 为
    调用方传入的 {code: {report_id(str): conf}} 字典(每次收集一份,进程内不跨请求)。
    """
    if code not in cache:
        try:
            from tradingagents.dataflows.research_data import load_research_data
            data = load_research_data(code) or {}
            cache[code] = {
                str(rr.get("id")): rr.get("confidence")
                for rr in (data.get("reports") or [])
            }
        except Exception:
            cache[code] = {}
    return cache[code].get(str(report_id))


def _collect_daily_report_items(rows: list[dict], date: str, rtype: str = "") -> list[dict]:
    """某天全部 done 的对应类型研报 → 逐品种结论条目(供总结 prompt,只带要用的字段)。

    【关键】多品种研报按 conclusion_md 的「## {code} 结论」段拆成多条;每条的
            方向优先取该段交易要素行的「方向」(_parse_trade_elements,逐品种口径),
            取不到才回退 DB 行主方向;置信度逐品种口径(2026-09-07 修复:原实现把
            DB 行级值无差别塞给每个品种段,双品种研报的次品种会冒用主品种置信度,
            与观点总览不一致)——优先取聚合 JSON {CODE}_research.json 内该研报的
            逐品种置信度,取不到回退:主品种用 DB 行值,次品种不标(—)。
            类型口径(2026-09-07 拆分):日报口径(默认)只收非周报行(未知类型不排斥);
            周报口径(rtype='周报')只收周报行 —— 日报/周报总结互不混收。
    """
    items: list[dict] = []
    weekly_mode = (rtype or "").strip() == "周报"
    name_map = {k: v["name"] for k, v in VARIETY_METADATA.items()}
    agg_cache: dict[str, dict] = {}  # code → {report_id(str): 逐品种置信度}

    for r in rows:
        if r.get("status") != "done" or _report_date(r) != date:
            continue
        if weekly_mode != ((r.get("report_type") or "").strip() == "周报"):
            continue  # 口径不匹配:日报口径跳周报行,周报口径跳日报/未知行
        codes = [c for c in str(r.get("varieties") or "").split(",") if c] or [r.get("variety") or ""]
        codes = [c for c in codes if c in ACTIVE_VARIETIES]  # 【品种池】池外品种段隐藏(存量行保留)
        if not codes:
            continue
        for code in dict.fromkeys(codes):  # 去重保序
            seg = _daily_variety_segment(r.get("conclusion_md") or "", code)
            opinion = _extract_key_opinion(seg, max_len=150)  # 单条短摘:一天全品种条目多,预算留给覆盖面
            if not opinion:
                continue
            te = _parse_trade_elements(seg)
            conf = _variety_agg_conf(code, r.get("id"), agg_cache)  # 逐品种置信度(聚合 JSON 口径,与观点总览一致)
            if conf is None and code == codes[0]:
                conf = r.get("confidence")  # 主品种/单品种回退 DB 行值;次品种取不到不标(—)
            items.append({
                "variety": f"{code}({name_map.get(code, '')})",
                "report_id": r.get("id"),  # 供单品种总结卡的来源观点对比跳转原件(/api/research/{id}/file)
                "source": r.get("source") or "未知",
                "title": r.get("title") or "(未命名)",
                "direction": te.get("方向") or r.get("direction") or "中性",
                "confidence": f"{round(float(conf) * 100)}%" if conf is not None else "—",
                "opinion": opinion,
            })
    return items


def _build_daily_summary_md(items: list[dict], llm_body: str, generated_at: str) -> str:
    """拼接落盘 md:元信息头(HTML 注释,渲染前剥掉)+ 标题 + LLM 正文。"""
    head = (
        f"<!-- AgentSense 每日总结 | generated_at:{generated_at} "
        f"| reports:{len(items)} -->\n\n"
    )
    return head + llm_body.strip() + "\n"


def _daily_summary_meta(md: str) -> dict:
    """读 md 头部元信息 → {generated_at, reports};旧文件/缺标 → 空值兜底。"""
    m = re.search(r"<!--.*?generated_at:([^;|]+).*?reports:(\d+).*?-->", md or "")
    return {"generated_at": m.group(1).strip() if m else "", "reports": int(m.group(2)) if m else 0}


def _generate_daily_summary(date: str, force: bool = False, rtype: str = "") -> dict:
    """生成(或复用)某日研报总结;返回 {ok, date, content, generated_at, reports} 或 {ok: False, error}。

    【参数】rtype: 口径('周报'=周报总结,''/日报=每日总结),决定收哪些行与落盘目录。
    """
    weekly_mode = (rtype or "").strip() == "周报"
    path = _research_daily_path(date, rtype)
    if path.is_file() and not force:  # 已生成过 → 直接读文件,不重复烧 LLM(force 重跑覆盖)
        content = path.read_text(encoding="utf-8")
        meta = _daily_summary_meta(content)
        return {"ok": True, "date": date, "content": content, "cached": True, **meta}

    db = get_db()
    items = _collect_daily_report_items(db.list_research_reports(limit=500), date, rtype)
    if not items:
        return {"ok": False, "error": f"{date} 在库没有已完成分析的{'周报' if weekly_mode else '日报'}研报"}

    payload = "\n\n".join(
        f"【{i['variety']}】{i['source']}《{i['title']}》 方向:{i['direction']}"
        f"(置信度 {i['confidence']})\n要点: {i['opinion']}"
        for i in items
    )
    # 【RAG 增强】检索各品种近期历史研报片段作"历史背景参考"(非当日材料);
    # 未启用/失败返回 "",注入块为空、提示词保持原样,零影响。
    name_map = {k: v["name"] for k, v in VARIETY_METADATA.items()}
    hist_blocks: list[str] = []
    seen_codes: set[str] = set()
    for i in items:
        code = (i.get("variety") or "").split("(")[0].strip().upper()
        if not code or code in seen_codes:
            continue
        seen_codes.add(code)
        ctx = _rag_context_for_variety(
            f"{code} {name_map.get(code, '')} 基本面 观点", variety=code, limit=2
        )
        if ctx:
            hist_blocks.append(ctx)
    hist_ctx = "\n\n".join(hist_blocks)
    kind_title = "研报周报总结" if weekly_mode else "研报每日总结"
    prompt = (
        "你是中国商品期货研究主管。下面是同一天各券商/机构研报的逐品种分析结论摘要。"
        f"请输出一份「{kind_title}」(markdown, 全中文, 不写套话),固定四节:\n"
        "## 一、当日观点总表\n"
        "(markdown 表格,列固定为:品种 | 发行方 | 方向 | 置信度 | 一句话核心观点;"
        "同一品种的多家机构合并为一行,发行方/方向/置信度各用「/」分隔且一一对应,"
        "只有一家就只写一个;同一机构对该品种有多份研报时,发行方名只写一次,"
        "方向/置信度仍逐份对应列出;材料中出现的全部品种每行一个全部列出,"
        "严禁省略、严禁「其余从略/均从略」之类的合并表述)\n"
        "## 二、观点对比与冲突\n"
        "(覆盖下面材料中出现的全部品种,一个品种一条、一条不多一条不少,不受总表取舍限制:"
        "对比各家方向与逻辑,指出明确分歧点及根源——数据口径/时间尺度/关注权重不同;"
        "各家无分歧的品种也保留一条,一句话写明方向一致与共识逻辑;"
        "每条不超过 40 字,严禁省略品种或多品种合并成一条)\n"
        "## 三、可信程度分析\n"
        "(按置信度与论据扎实程度给各观点分层:可信度高/中/低,点明哪些依据有具体数字、"
        "哪些只是定性判断;多空明显一边倒时提示拥挤风险)\n"
        "## 四、综合结论与关注要点\n"
        "(汇总当日净倾向,列出未来 1~2 周最值得跟踪的 2~3 个数据/事件)\n"
        "写作要求:只依据下面材料,严禁编造数据;总表与第二节都必须覆盖全部品种,"
        "单条表述保持简短,总篇幅可放宽到 1800 字左右。\n"
    )
    if hist_ctx:
        prompt += (
            "---\n【历史背景参考(其他日期历史研报片段,非当日材料)】\n"
            + hist_ctx
            + "\n第一~三节(总表/对比/可信度)只依据当日材料;上述历史参考仅可在"
            "「四、综合结论与关注要点」中引用并标注发布日期,严禁与当日材料混写。\n"
        )
    prompt += f"---\n研报日期:{date}\n\n{payload[:28000]}"
    try:
        client = create_llm_client(
            config["llm_provider"],
            config.get("quick_think_llm", config["deep_think_llm"]),
        )
        result = client.get_llm().invoke(prompt)
        body = str(result.content if hasattr(result, "content") else result).strip()
    except Exception:
        logger.warning("Daily research summary LLM call failed for %s", date, exc_info=True)
        return {"ok": False, "error": "LLM 生成失败,请稍后重试"}
    if not body:
        return {"ok": False, "error": "LLM 返回为空,请重试"}

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    content = _build_daily_summary_md(items, body, generated_at)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {"ok": True, "date": date, "content": content, "cached": False,
            "generated_at": generated_at, "reports": len(items)}


@app.route("/api/research/daily")
def api_research_daily_list():
    """每日/周报总结:可用日期列表(库内研报日期 ∪ 已生成文件),标注哪些已生成。

    【参数】report_type 查询参数('周报'=周报口径,空=日报口径)——两类日期与文件分开。
    """
    rtype = (request.args.get("report_type") or "").strip()
    db = get_db()
    generated = {p.stem for p in _summary_dir(rtype).glob("????-??-??.md")}
    return jsonify({
        "dates": [
            {"date": d, "generated": d in generated}
            for d in _research_daily_dates(db, rtype)
        ],
    })


@app.route("/api/research/daily/generate", methods=["POST"])
def api_research_daily_generate():
    """生成(或复用)某日总结:POST json {date, report_type};LLM 同步调用,前端需 loading 等待。"""
    data = request.get_json(silent=True) or {}
    date = str(data.get("date") or "").strip()[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        return jsonify({"ok": False, "error": "date 参数格式应为 YYYY-MM-DD"}), 400
    rtype = str(data.get("report_type") or "").strip()
    return jsonify(_generate_daily_summary(date, force=bool(data.get("force")), rtype=rtype))


@app.route("/api/research/daily/<date>")
def api_research_daily_get(date: str):
    """读取已生成的某日总结 md;未生成 → {exists: False}(前端可再点生成)。

    【参数】report_type 查询参数('周报'读 research_weekly,空读 research_daily)。
    """
    date = (date or "").strip()[:10]
    rtype = (request.args.get("report_type") or "").strip()
    path = _research_daily_path(date, rtype)
    if not path.is_file():
        return jsonify({"date": date, "exists": False})
    content = path.read_text(encoding="utf-8")
    return jsonify({"date": date, "exists": True, "content": content, **_daily_summary_meta(content)})


# ---------------------------------------------------------------------
# 研报单品种总结(2026-09-09):把某日在库研报中"涉及某品种"的全部逐品种结论
# 交 LLM 汇总成一份注重逻辑的总结(多空驱动/分歧共识/倾向,数据仅作附录),
# 落盘 ~/.tradingagents/research_variety/{daily|weekly}/{date}_{CODE}.md;
# 已生成的品种直接读文件,不重复烧 LLM。前端在每日总结下方自动排队补齐未生成品种。
# ---------------------------------------------------------------------
RESEARCH_VARIETY_DIR = Path.home() / ".tradingagents" / "research_variety"


def _variety_summary_path(date: str, code: str, rtype: str = "") -> Path:
    """某日某品种总结 md 路径(research_variety/{daily|weekly}/{date}_{CODE}.md)。"""
    sub = "weekly" if (rtype or "").strip() == "周报" else "daily"
    return RESEARCH_VARIETY_DIR / sub / f"{date}_{code}.md"


def _collect_variety_report_items(rows: list[dict], date: str, code: str, rtype: str = "") -> list[dict]:
    """某天涉及 code 的全部 done 对应类型研报 → 逐篇条目(整段结论+四指标,供单品种总结)。

    【与 _collect_daily_report_items 的区别】那边只留 150 字短摘(一天全品种要覆盖面),
            这边保留整段「## {code} 结论」(单品种预算充足,LLM 要梳理完整逻辑链);
            四指标用 _row_research_fund_metrics 取"该品种段"({**r, variety: code}
            保证多品种研报不串到主品种段)。日期/类型/置信度口径与每日总结完全一致。
    """
    code = (code or "").strip().upper()
    weekly_mode = (rtype or "").strip() == "周报"
    name_map = {k: v["name"] for k, v in VARIETY_METADATA.items()}
    agg_cache: dict[str, dict] = {}
    items: list[dict] = []
    for r in rows:
        if r.get("status") != "done" or _report_date(r) != date:
            continue
        if weekly_mode != ((r.get("report_type") or "").strip() == "周报"):
            continue  # 口径不匹配:日报口径跳周报行,周报口径跳日报/未知行
        codes = [c.strip().upper() for c in str(r.get("varieties") or "").split(",") if c.strip()] \
            or [str(r.get("variety") or "").upper()]
        if code not in codes:
            continue
        seg = _daily_variety_segment(r.get("conclusion_md") or "", code)
        te = _parse_trade_elements(seg)
        conf = _variety_agg_conf(code, r.get("id"), agg_cache)
        if conf is None and codes[0] == code:
            conf = r.get("confidence")  # 主品种/单品种回退 DB 行值;次品种取不到不标(—)
        items.append({
            "code": code,
            "name": name_map.get(code, ""),
            "source": r.get("source") or "未知",
            "title": r.get("title") or "(未命名)",
            "direction": te.get("方向") or r.get("direction") or "中性",
            "confidence": f"{round(float(conf) * 100)}%" if conf is not None else "—",
            "segment": seg,
            "metrics": _row_research_fund_metrics({**r, "variety": code}, fallback_text=seg),
        })
    return items


def _generate_variety_summary(date: str, code: str, rtype: str = "", force: bool = False) -> dict:
    """生成(或复用)某日某品种研报总结;返回 {ok, date, code, content, ...} 或 {ok: False, error}。

    【口径】逻辑为主+数据附录(2026-09-09 用户定):正文写多空驱动/分歧共识/倾向,
            数据一笔带过;末节固定「## 当日关键数据」;严禁表格、严禁编造。
    """
    code = (code or "").strip().upper()
    if not re.match(r"^[A-Z0-9]{1,6}$", code):  # 单字母合法品种: I 铁矿 / J 焦炭 / T 国债
        return {"ok": False, "error": f"品种代码不合法: {code!r}"}
    path = _variety_summary_path(date, code, rtype)
    if path.is_file() and not force:  # 已生成过 → 直接读文件,不重复烧 LLM(force 重跑覆盖)
        content = path.read_text(encoding="utf-8")
        meta = _daily_summary_meta(content)
        return {"ok": True, "date": date, "code": code, "content": content, "cached": True, **meta}

    db = get_db()
    items = _collect_variety_report_items(db.list_research_reports(limit=500), date, code, rtype)
    if not items:
        return {"ok": False, "error": f"{date} 在库没有涉及 {code} 的已完成分析研报"}

    payload = "\n\n".join(
        f"【{i['source']}《{i['title']}》】方向:{i['direction']}(置信度 {i['confidence']})\n"
        f"结论:\n{i['segment'][:1600]}"
        for i in items
    )
    # 当日关键数据备查:研报结构化/启发式提取的四指标(有值才列),供 LLM 写附录节。
    metric_lines = []
    for i in items:
        vals = "; ".join(
            f"{m['label']}={m.get('value')}{' ' + m['unit'] if m.get('unit') else ''}"
            for m in (i.get("metrics") or [])
        )
        if vals:
            metric_lines.append(f"- {i['source']}《{i['title']}》: {vals}")
    metrics_block = ("\n\n【当日关键数据备查(研报结构化提取,仅供「当日关键数据」节参考)】\n"
                     + "\n".join(metric_lines)) if metric_lines else ""
    name = {k: v["name"] for k, v in VARIETY_METADATA.items()}.get(code, "")
    # 【RAG 增强】同品种近期历史研报片段作背景参考(非当日材料),与每日总结同款、失败零影响。
    hist_ctx = _rag_context_for_variety(f"{code} {name} 基本面 观点", variety=code, limit=2)
    prompt = (
        f"你是{code}({name})期货研究员。下面是同一天多家机构关于该品种研报的逐品种结论。"
        f"请输出一份「{date} {code}({name})单品种研报总结」(markdown, 全中文, 不写套话, 500~800 字),"
        "以逻辑为主线,固定小节:\n"
        "## 多空驱动与逻辑链\n"
        "(提炼各家研报的核心逻辑:供给/需求/成本/库存/宏观等驱动如何传导到价格,"
        "各家因果链条是否一致;数据只作逻辑支撑一笔带过,严禁罗列数据)\n"
        "## 分歧与共识\n"
        "(各家观点的分歧点及根源——数据口径/时间尺度/关注权重不同;无分歧就写共识逻辑;"
        "材料里没有的方面不要硬写,只写有依据的)\n"
        "## 倾向与跟踪要点\n"
        "(综合当日净倾向(偏多/偏空/震荡)与未来 1~2 周值得跟踪的数据或事件)\n"
        "## 当日关键数据\n"
        "(无序列表,列材料中出现的基差/仓单/开工率/加工利润等关键数据,数值+出处机构;"
        "材料没有就写「当日材料未提供」)\n"
        "写作要求:只依据下面材料,严禁编造;严禁使用 markdown 表格;严禁写套话。\n"
    )
    if hist_ctx:
        prompt += (
            "---\n【历史背景参考(其他日期历史研报片段,非当日材料)】\n"
            + hist_ctx
            + "\n上述历史参考仅可在「倾向与跟踪要点」中引用并标注发布日期,"
            "严禁与当日材料混写。\n"
        )
    prompt += f"---\n研报日期:{date}\n\n{payload[:28000]}{metrics_block[:4000]}"
    try:
        client = create_llm_client(
            config["llm_provider"],
            config.get("quick_think_llm", config["deep_think_llm"]),
        )
        result = client.get_llm().invoke(prompt)
        body = str(result.content if hasattr(result, "content") else result).strip()
    except Exception:
        logger.warning("Variety research summary LLM call failed for %s %s", date, code, exc_info=True)
        return {"ok": False, "error": "LLM 生成失败,请稍后重试"}
    if not body:
        return {"ok": False, "error": "LLM 返回为空,请重试"}

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    content = (
        f"<!-- AgentSense 单品种总结 | code:{code} | generated_at:{generated_at} "
        f"| reports:{len(items)} -->\n\n{body}\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {"ok": True, "date": date, "code": code, "content": content, "cached": False,
            "generated_at": generated_at, "reports": len(items)}


@app.route("/api/research/variety-summary/<date>")
def api_research_variety_summary_list(date: str):
    """某日单品种总结列表:按当日逐品种条目聚合品种,标注哪些已生成(前端据此自动排队)。

    【返回】{date, report_type, varieties: [{code, name, report_count, directions,
            report_rows(逐份研报 来源/方向/置信度/id, 供来源观点对比表),
            summary_md|None, generated_at, reports(条数, 缓存元信息), cached}]}。
    """
    date = (date or "").strip()[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        return jsonify({"ok": False, "error": "date 参数格式应为 YYYY-MM-DD"}), 400
    rtype = (request.args.get("report_type") or "").strip()
    db = get_db()
    # 品种聚合直接复用每日总结的逐品种条目(同日期/类型/方向口径),按 code 前缀分组;
    # 同时攒逐份研报的来源/方向/置信度,供前端单品种卡渲染"来源观点对比"表(2026-09-09)。
    items = _collect_daily_report_items(db.list_research_reports(limit=500), date, rtype)
    groups: dict[str, dict] = {}
    for it in items:
        code = (it.get("variety") or "").split("(")[0].strip().upper()
        if not code:
            continue
        g = groups.setdefault(code, {"code": code, "report_count": 0, "directions": [], "report_rows": []})
        g["report_count"] += 1
        d = it.get("direction") or "中性"
        if d not in g["directions"]:
            g["directions"].append(d)
        g["report_rows"].append({
            "report_id": it.get("report_id"),
            "source": it.get("source") or "未知",
            "title": it.get("title") or "(未命名)",
            "direction": d,
            "confidence": it.get("confidence") or "—",
        })
    name_map = {k: v["name"] for k, v in VARIETY_METADATA.items()}
    out = []
    for code in sorted(groups):
        g = groups[code]
        path = _variety_summary_path(date, code, rtype)
        if path.is_file():
            content = path.read_text(encoding="utf-8")
            g.update(summary_md=content, cached=True, **_daily_summary_meta(content))
        else:
            g.update(summary_md=None, cached=False, generated_at="", reports=0)
        g["name"] = name_map.get(code, "")
        out.append(g)
    return jsonify({"date": date, "report_type": rtype, "varieties": out})


@app.route("/api/research/variety-summary/generate", methods=["POST"])
def api_research_variety_summary_generate():
    """生成(或复用)某日某品种总结:POST json {date, code, report_type, force};LLM 同步调用。"""
    data = request.get_json(silent=True) or {}
    date = str(data.get("date") or "").strip()[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        return jsonify({"ok": False, "error": "date 参数格式应为 YYYY-MM-DD"}), 400
    rtype = str(data.get("report_type") or "").strip()
    code = str(data.get("code") or "").strip()
    return jsonify(_generate_variety_summary(date, code, rtype=rtype, force=bool(data.get("force"))))


# GTJA 观点信号进程内缓存:variety → (拉取时间戳, 视图 payload);TTL 内复用,防止前端
# 切换品种/来回切研报 tab 时反复打国君接口(周度只回最新一帧,时效要求低)。
_GTJA_VIEWS_CACHE: dict[str, tuple[float, dict]] = {}  # 【变量】GTJA 观点缓存
GTJA_VIEWS_CACHE_TTL = 1800  # 【变量】GTJA 观点缓存有效期(秒)


def _gtja_weekly_date(weekly) -> str:
    """观点信号周度帧的 reportDate(YYYY-MM-DD);空返回空串。"""
    d = (weekly or {}).get("reportDate") or ""
    return str(d)[:10] if str(d)[:10] else ""


@app.route("/api/gtja/views")
def api_gtja_views():
    """国君(国泰君安 GTJA)周度观点信号卡片后端。

    【功能】研报 tab「国君周度观点」卡片取数:调国君 commodity.weekly.viewpoint
            .queryByCode.do(2026-09-03 晨报日度下线,只留周度)。queryByCode 只回该
            品种最新一帧(不分日期),故按品种返回单条 weekly"当前信号"。
    【参数】URL query: variety(品种代码,必填)。
    【返回】json: {variety, name, updated, signals:{weekly:{...}|None}, error: str|None,
                   source}。
    【关键逻辑】1) 未配置 GTJA key → error 提示,前端显示空态;2) 拉取失败只让
                weekly 为 None;3) 进程内 1800s TTL 缓存,防切品种狂打接口;
                4) 与研报观点并列独立卡片,不写研报聚合文件、不改研报数据。
    """
    code = (request.args.get("variety") or "").strip().upper()
    empty = {"variety": "", "name": "", "updated": "",
             "signals": {"weekly": None}, "error": "", "source": "国泰君安(GTJA)"}
    if not code:
        return jsonify(empty)
    now = time.time()
    cached = _GTJA_VIEWS_CACHE.get(code)
    if cached and now - cached[0] < GTJA_VIEWS_CACHE_TTL:
        payload = cached[1]
    else:
        try:  # 【调用包】GTJA 观点提供者(可选增强;缺依赖/导入失败按未配置降级)
            from tradingagents.dataflows import gtja_api
        except Exception:  # noqa: BLE001 - 可选模块,绝不影响其它研报路由
            gtja_api = None
        if gtja_api is not None and gtja_api.configured():
            try:
                res = gtja_api.fetch_viewpoint(code)
            except Exception:  # noqa: BLE001 - 拉取异常不炸接口
                res = {"weekly": None, "error": "国君观点接口暂不可用"}
        else:
            res = {"weekly": None, "error": "未配置国君观点数据源"}
        meta = VARIETY_METADATA.get(code, {})
        payload = {
            "variety": code,
            "name": meta.get("name", code),
            "updated": _gtja_weekly_date(res.get("weekly")),
            "signals": {"weekly": res.get("weekly")},
            "error": res.get("error"),
            "source": "国泰君安(GTJA)",
        }
        _GTJA_VIEWS_CACHE[code] = (now, payload)
    return jsonify(payload)


# ---------------------------------------------------------------------------
# GTJA 数据集(数据仓库「国君数据集」卡): 基差 / 仓单, 按品种 + 可选日期区间
# ---------------------------------------------------------------------------
def _gtja_num(x):
    """把行内数值清洗成 JSON 安全值:None 保持 None;float NaN → None;原样返回其它。"""
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return x
    return None if f != f else x  # NaN != NaN 判空,避免 JSON 序列化 NaN


def _gtja_iso_date(v):
    """归一化 date 字段 → YYYY-MM-DD:int 20260901 / 已带横杠字符串都兼容。"""
    if isinstance(v, int):
        s = str(int(v))
        if len(s) == 8 and s.isdigit():
            return f"{s[:4]}-{s[4:6]}-{s[6:]}"
        return s
    return str(v)


@app.route("/api/gtja/dataset/basis")
def api_gtja_dataset_basis():
    """国君基差/现货数据集(数据仓库卡): 品种 + 可选日期区间 → 多日归一化表。

    【返回】成功 {ok:True, variety, name, source, count, start, end,
      rows:[{date(YYYY-MM-DD), spot_price, dominant_contract, dominant_contract_price,
            dom_basis, dom_basis_rate}]};空(SC 等无现货指数品种)→ ok:True + rows 空。
      未配 key → {ok:False, error};缺参 400;start>end 400。
    """
    code = (request.args.get("variety") or "").strip().upper()
    start = (request.args.get("start") or "").strip()
    end = (request.args.get("end") or "").strip()
    if not code:
        return jsonify({"ok": False, "error": "缺少品种参数(variety)"}), 400
    try:  # 可选增强:缺依赖/导入失败按未配置降级
        from tradingagents.dataflows import gtja_api
    except Exception:  # noqa: BLE001 - 可选模块,绝不影响其它路由
        gtja_api = None
    if gtja_api is None or not gtja_api.configured():
        return jsonify({"ok": False, "error": "未配置国君(GTJA)数据源密钥"}), 200
    if start and end and start > end:
        return jsonify({"ok": False, "error": f"开始日期不能晚于结束日期({start} > {end})"}), 400
    today = datetime.now().date()
    start = start or (today - timedelta(days=180)).strftime("%Y-%m-%d")
    end = end or today.strftime("%Y-%m-%d")
    df = gtja_api.fetch_basis_df(code, start, end)
    meta = VARIETY_METADATA.get(code, {})
    rows = []
    if df is not None and not df.empty:
        keys = ["date", "spot_price", "dominant_contract", "dominant_contract_price",
                "dom_basis", "dom_basis_rate"]
        for rec in df.to_dict("records"):
            row = {k: _gtja_num(rec.get(k)) for k in keys}
            if row.get("date") is not None:
                row["date"] = _gtja_iso_date(row["date"])
            rows.append(row)
    return jsonify({"ok": True, "variety": code, "name": meta.get("name", code),
                    "source": "国泰君安(GTJA)", "count": len(rows),
                    "start": start, "end": end, "rows": rows})


@app.route("/api/gtja/dataset/inventory")
def api_gtja_dataset_inventory():
    """国君交易所仓单数据集(数据仓库卡): 品种 + 可选日期区间 → 多日表。

    【返回】成功 {ok:True, variety, name, source, count, start, end,
      rows:[{date(YYYY-MM-DD), inventory, change}]};空 → ok:True + rows 空。
      未配 key → {ok:False, error};缺参 400;start>end 400。
    【关键】两端都空 → fetch_inventory_df 近 240 天默认窗口(兼容 commodity_futures 下游)。
    """
    code = (request.args.get("variety") or "").strip().upper()
    start = (request.args.get("start") or "").strip()
    end = (request.args.get("end") or "").strip()
    if not code:
        return jsonify({"ok": False, "error": "缺少品种参数(variety)"}), 400
    try:  # 可选增强:缺依赖/导入失败按未配置降级
        from tradingagents.dataflows import gtja_api
    except Exception:  # noqa: BLE001 - 可选模块,绝不影响其它路由
        gtja_api = None
    if gtja_api is None or not gtja_api.configured():
        return jsonify({"ok": False, "error": "未配置国君(GTJA)数据源密钥"}), 200
    if start and end and start > end:
        return jsonify({"ok": False, "error": f"开始日期不能晚于结束日期({start} > {end})"}), 400
    today = datetime.now().date()
    if start and end:
        s_arg, e_arg = start, end
        s_disp, e_disp = start, end
    else:  # 两端皆空/只给一端 → 交给 fetch_inventory_df(默认 240 天窗口)
        s_arg = e_arg = None
        s_disp = start or (today - timedelta(days=240)).strftime("%Y-%m-%d")
        e_disp = end or today.strftime("%Y-%m-%d")
    df = gtja_api.fetch_inventory_df(code, s_arg, e_arg)
    meta = VARIETY_METADATA.get(code, {})
    rows = []
    if df is not None and not df.empty:
        keys = ["date", "inventory", "change"]
        for rec in df.to_dict("records"):
            row = {k: _gtja_num(rec.get(k)) for k in keys}
            if row.get("date") is not None:
                row["date"] = _gtja_iso_date(row["date"])
            rows.append(row)
    return jsonify({"ok": True, "variety": code, "name": meta.get("name", code),
                    "source": "国泰君安(GTJA)", "count": len(rows),
                    "start": s_disp, "end": e_disp, "rows": rows})


@app.route("/api/research/<int:report_id>")
def api_research_detail(report_id):
    """研报详情:含结构化 JSON(转回 dict)与结论 markdown,供前端查看弹层。"""
    r = get_db().get_research_report(report_id)
    if not r:
        return jsonify({"error": "研报不存在"}), 404
    try:
        r["structured_data"] = json.loads(r.get("structured_data") or "{}")
    except (json.JSONDecodeError, TypeError):
        r["structured_data"] = {}
    return jsonify(r)


@app.route("/api/research/<int:report_id>/download")
def api_research_download(report_id):
    """下载研报原件(PDF/图片/MD/TXT):仅服务本研报在 RESEARCH_UPLOAD_DIR 内的原始文件。

    路径穿越防护:解析后的绝对路径必须落在研报上传基准目录内,否则 403;文件缺失 404。
    自动接入(fxbaogao)研报无独立原件(file_path 即 .md 文本),前端『查看原文』直接展示 extracted_text。
    """
    r = get_db().get_research_report(report_id)
    if not r:
        return jsonify({"error": "研报不存在"}), 404
    fp = r.get("file_path")
    if not fp:
        return jsonify({"error": "该研报无原件文件(自动接入研报请使用『查看原文』)"}), 404
    p = Path(fp).resolve()
    base = RESEARCH_UPLOAD_DIR.resolve()
    if p != base and base not in p.parents:
        return jsonify({"error": "非法文件路径"}), 403
    if not p.is_file():
        return jsonify({"error": "原件文件不存在(可能已被移动)"}), 404
    mime = {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".md": "text/markdown; charset=utf-8",
        ".txt": "text/plain; charset=utf-8",
    }.get(p.suffix.lower(), "application/octet-stream")
    download_name = r.get("filename") or p.name
    return send_file(str(p), mimetype=mime, as_attachment=True, download_name=download_name)


@app.route("/api/research/<int:report_id>/file")
def api_research_file(report_id):
    """在线查看研报原件(浏览器内直接渲染 PDF/图片,不弹下载框)。

    与 /download 同一套路径防护与缺文件兜底;区别仅 inlined=False —— 2026-09-04
    上线"其他设备经网站直接查看 PDF":内网穿透(cloudflared)或局域网访问时,
    新标签页即可原样翻阅研报原件。md/txt 自动接入研报无二进制原件,仍走『查看原文』。
    """
    r = get_db().get_research_report(report_id)
    if not r:
        return jsonify({"error": "研报不存在"}), 404
    fp = r.get("file_path")
    if not fp:
        return jsonify({"error": "该研报无原件文件(自动接入研报请使用『查看原文』)"}), 404
    p = Path(fp).resolve()
    base = RESEARCH_UPLOAD_DIR.resolve()
    if p != base and base not in p.parents:
        return jsonify({"error": "非法文件路径"}), 403
    if not p.is_file():
        return jsonify({"error": "原件文件不存在(可能已被移动)"}), 404
    mime = {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".md": "text/markdown; charset=utf-8",
        ".txt": "text/plain; charset=utf-8",
    }.get(p.suffix.lower())
    if not mime:  # 在线预览只放行可渲染类型,其余引导走下载
        return jsonify({"error": "该格式不支持在线预览, 请使用『下载原件』"}), 415
    return send_file(str(p), mimetype=mime, as_attachment=False)


def _delete_research_report_full(report_id: int) -> bool:
    """删除研报全链路:聚合 JSON 记录 + 原始文件 + DB 行 + 孤儿清扫。

    【供】api_research_delete 路由与采集器"同名日报删旧迎新"共用
          (research_collector_gtja._supersede_same_title)。
    【返回】False=研报不存在。
    """
    db = get_db()
    r = db.get_research_report(report_id)
    if not r:
        return False
    # 多品种研报的记录存在于多个品种聚合文件中,按 varieties 列逐一清除;
    # 旧行 varieties 为空时回退主品种列。
    varieties = [v.strip() for v in (r.get("varieties") or "").split(",") if v.strip()]
    if not varieties and r.get("variety"):
        varieties = [r["variety"]]
    try:
        from tradingagents.dataflows.research_data import (
            remove_research_report,  # 【调用包】聚合 JSON 记录删除
        )

        for v in varieties:
            remove_research_report(v, report_id)
    except Exception as e:
        logger.warning("Failed to update research aggregate on delete %s: %s", report_id, e)
    fp = r.get("file_path")
    if fp:
        try:
            Path(fp).unlink(missing_ok=True)
        except OSError as e:
            logger.warning("Failed to delete research file %s: %s", fp, e)
    db.delete_research_report(report_id)
    # 【RAG 同步删向量】采集器"删旧迎新"也走本函数,一处覆盖两个入口。
    _rag_delete_vectors_safely(report_id)
    # 【兜底清扫】行 varieties 为空/不全时上面逐品种清理会漏 → 以现存 DB id 集合
    # 全量清扫聚合 JSON 孤儿(2026-09-04,根治"研报删了还出现在观点总览")。
    try:
        from tradingagents.dataflows.research_data import (
            sweep_orphan_reports,  # 【调用包】聚合 JSON 孤儿清扫
        )

        valid_ids = {row["id"] for row in db.list_research_reports(limit=1000)}
        removed = sweep_orphan_reports(valid_ids)
        if removed:
            logger.info("Swept %d orphan research aggregate entries after delete %s", removed, report_id)
    except Exception as e:
        logger.warning("Orphan sweep after research delete failed: %s", e)
    return True


@app.route("/api/research/<int:report_id>", methods=["DELETE"])
def api_research_delete(report_id):
    """删除研报:同步删聚合 JSON 记录 + 原始文件,再删数据库行。"""
    if not _delete_research_report_full(report_id):
        return jsonify({"error": "研报不存在"}), 404
    return jsonify({"ok": True})


# 【功能】研报 RAG 状态(前端问答卡片展示索引进度/未启用提示)。
# 【返回】{"rag_enabled": bool, "chunks": int, "reports": int}(未启用/异常时为 False/0/0)。
@app.route("/api/research/rag/status", methods=["GET"])
def api_research_rag_status():
    from tradingagents.rag import is_available  # 【调用包】懒导入(is_available 不拉重依赖)

    if not is_available():
        return jsonify({"rag_enabled": False, "chunks": 0, "reports": 0})
    try:
        from tradingagents.rag import service  # 【调用包】懒导入重依赖

        status = service.index_status()
        return jsonify({"rag_enabled": status["chunks"] > 0, **status})
    except Exception as e:
        logger.warning("RAG status failed: %s", e)
        return jsonify({"rag_enabled": False, "chunks": 0, "reports": 0})


# 【功能】研报问答:检索研报片段(余弦近邻,可按品种/类型过滤)→ LLM 依据片段作答,
#         返回带编号引用(点击跳 /api/research/<id>/file 原件)。
# 【请求体】{"question"(必填), "variety"?(品种代码过滤), "report_type"?("日报"/"周报"),
#           "top_k"?(1~12,默认 6)}。
# 【返回】{"ok", "answer", "citations":[{no,report_id,title,publish_date,variety,
#         chunk_index,snippet,score}], "retrieved", "rag_enabled"};未启用/无命中/
#         检索失败/生成失败均 HTTP 200 由前端渲染提示,仅 question 缺失返回 400。
# 【关键逻辑】品种过滤用超采样+后过滤(varieties 是逗号串,Chroma where 只能精确匹配);
#           LLM 走 quick 模型(与研报提取链路一致);提示词强制"只依据材料、
#           标注 [n]、注明发布日期、材料不足直说"。
@app.route("/api/research/ask", methods=["POST"])
def api_research_ask():
    body = request.get_json(silent=True) or {}
    question = str(body.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question 必填"}), 400
    variety = str(body.get("variety") or "").strip().upper() or None
    report_type = str(body.get("report_type") or "").strip() or None
    try:
        top_k = max(1, min(int(body.get("top_k") or 6), 12))
    except (TypeError, ValueError):
        top_k = 6

    from tradingagents.rag import is_available  # 【调用包】懒导入

    if not is_available():
        return jsonify({
            "ok": False,
            "error": "RAG 未启用:需安装 chromadb 与 sentence-transformers,"
            "并运行 python scripts/backfill_rag_index.py 回填索引",
            "rag_enabled": False,
        })
    try:
        from tradingagents.rag import service  # 【调用包】懒导入重依赖

        hits = service.retrieve_hits(
            question, top_k=top_k, variety=variety, report_type=report_type
        )
    except Exception as e:
        logger.warning("RAG retrieve failed: %s", e)
        return jsonify({"ok": False, "error": "检索失败,请稍后重试", "rag_enabled": True})
    if not hits:
        return jsonify({
            "ok": True, "answer": "", "citations": [], "retrieved": 0,
            "rag_enabled": True, "error": "未检索到相关研报片段,试试放宽品种/类型过滤",
        })

    include_ctx = bool(body.get("include_context"))  # 评测用:随响应返回片段全文
    citations = []
    for i, h in enumerate(hits, 1):
        c = {
            "no": i,
            "report_id": int(h["metadata"].get("report_id", 0)),
            "title": h["metadata"].get("title") or "无标题",
            "publish_date": h["metadata"].get("publish_date") or "",
            "variety": h["metadata"].get("variety") or "",
            "chunk_index": h["metadata"].get("chunk_index", 0),
            "snippet": (h["text"] or "")[:200],
            "score": round(float(h.get("score", 0.0)), 4),
        }
        if include_ctx:
            c["text"] = h["text"] or ""
        citations.append(c)
    context = service.format_context(hits)
    try:
        client = create_llm_client(
            config["llm_provider"],
            config.get("quick_think_llm", config["deep_think_llm"]),
        )
        prompt = (
            "你是中国商品期货研究助理。下面给出用户问题与若干研报片段(带编号,来自不同"
            "日期的历史研报)。请只依据片段内容用中文回答,回答中用 [n] 标注所引用的片段"
            "编号,引用观点/数据时注明其发布日期;片段不足以回答的部分明确说明,严禁编造。"
            "若问题涉及多个方面(如技术面/基本面/宏观面),请逐方面作答;某方面片段未覆盖时"
            "只说明该方面不足,不要因部分缺失而整体拒答。\n\n"
            f"用户问题:{question}\n\n"
            f"{context}\n\n"
            "回答(markdown,400 字以内):"
        )
        result = client.get_llm().invoke(prompt)
        answer = str(result.content if hasattr(result, "content") else result).strip()
    except Exception as e:
        logger.warning("RAG answer generation failed: %s", e)
        return jsonify({
            "ok": False, "citations": citations, "retrieved": len(hits),
            "rag_enabled": True, "error": "已检索到片段,但生成回答失败,请稍后重试",
        })
    return jsonify({
        "ok": True, "answer": answer, "citations": citations,
        "retrieved": len(hits), "rag_enabled": True,
    })


# ── 研报问答·多轮智能知识库(2026-09-12):会话持久化 + 上下文改写 + 日期筛选 ──
# 旧 /api/research/ask 保持单轮无状态契约不动;多轮版走 /api/research/qa/*。
# 并发:账户 LLM 并发经验上限 5(>5 撞 429),QA 单请求最多占 2 次调用(改写+作答),
# 信号量包整段(两次调用一起抢,避免请求间交错占用)。

_QA_LLM_SEM = threading.BoundedSemaphore(3)
_QA_REWRITE_TURNS = 3  # 送入改写的历史轮数上限(3 轮=6 条消息)
_QA_REWRITE_CHAR_BUDGET = 6000
_QA_REWRITE_QUERY_MAX = 120  # 改写产物长度钳制


def _rewrite_query_for_retrieval(history: list[dict], question: str) -> str:
    """把追问+历史改写成独立检索 query;失败/无历史返回 ""(调用方回退原问题)。

    【为什么】多轮 RAG 关键步骤:用户追问"那成本端呢?"直接拿去向量检索命中率
            极低,需结合历史补全省略主语/代词成独立 query。
    【降级】history 空(首轮)跳过 LLM 返回 "";改写 LLM 抛异常/产物为空同样
            返回 "" —— 检索质量降一档,但问答链路永不被改写卡死。
            返回值同时就是落库的 rewritten_query("" = 首轮或回退,审计可辨)。
    """
    if not history:
        return ""
    try:
        lines = []
        for m in history:
            tag = "用户" if m.get("role") == "user" else "助理"
            content = m.get("content") or ""
            if m.get("role") != "user":
                content = content[:400]  # 助理回答截断,历史只供理解意图
            lines.append(f"{tag}:{content}")
        hist_text = "\n".join(lines)[:_QA_REWRITE_CHAR_BUDGET]
        prompt = (
            "你是检索查询改写器。根据对话历史,把用户最新问题改写成一个不依赖上下文、"
            "可独立用于向量检索的中文查询。要求:\n"
            "- 补全省略主语与代词(如\"它\"\"这个品种\"→具体品种名),保留最新问题的真实意图;\n"
            "- 若最新问题已切换话题,以最新问题为准,不要强行拼接历史;\n"
            "- 只输出一行查询文本,不要解释、不要加引号。\n\n"
            f"对话历史(供参考):\n{hist_text}\n\n"
            f"用户最新问题:{question}\n改写后的检索查询:"
        )
        client = create_llm_client(
            config["llm_provider"],
            config.get("quick_think_llm", config["deep_think_llm"]),
        )
        result = client.get_llm().invoke(prompt)
        rewritten = str(result.content if hasattr(result, "content") else result).strip()
        rewritten = rewritten.splitlines()[0].strip().strip("\"'“”")[:_QA_REWRITE_QUERY_MAX]
        return rewritten
    except Exception as e:
        logger.warning("QA query rewrite failed, fallback to raw question: %s", e)
        return ""


def _qa_build_citations(hits: list[dict], include_ctx: bool = False) -> list[dict]:
    """检索结果 → citations 列表(与旧 ask 路由同一构造,保持前端渲染兼容)。"""
    citations = []
    for i, h in enumerate(hits, 1):
        c = {
            "no": i,
            "report_id": int(h["metadata"].get("report_id", 0)),
            "title": h["metadata"].get("title") or "无标题",
            "publish_date": h["metadata"].get("publish_date") or "",
            "variety": h["metadata"].get("variety") or "",
            "chunk_index": h["metadata"].get("chunk_index", 0),
            "snippet": (h["text"] or "")[:200],
            "score": round(float(h.get("score", 0.0)), 4),
        }
        if include_ctx:
            c["text"] = h["text"] or ""
        citations.append(c)
    return citations


@app.route("/api/research/qa/sessions", methods=["GET"])
def api_qa_sessions_list():
    """问答会话列表(最近活跃倒序)。"""
    limit = request.args.get("limit", 50, type=int)
    return jsonify({"ok": True, "sessions": get_db().list_qa_sessions(limit=max(1, min(limit, 200)))})


@app.route("/api/research/qa/sessions", methods=["POST"])
def api_qa_sessions_create():
    """显式建会话(前端一般不调,首轮 ask 自动建)。"""
    body = request.get_json(silent=True) or {}
    s = get_db().create_qa_session(title=str(body.get("title") or ""))
    return jsonify({"ok": True, "session": s})


@app.route("/api/research/qa/sessions/<int:sid>", methods=["DELETE"])
def api_qa_session_delete(sid):
    """删除会话(连消息一起删);不存在返回 404。"""
    if not get_db().delete_qa_session(sid):
        return jsonify({"error": "会话不存在"}), 404
    return jsonify({"ok": True})


@app.route("/api/research/qa/sessions/<int:sid>/messages", methods=["GET"])
def api_qa_session_messages(sid):
    """会话消息回放(刷新页面后找回历史)。"""
    session = get_db().get_qa_session(sid)
    if not session:
        return jsonify({"error": "会话不存在"}), 404
    messages = get_db().get_qa_messages(sid)
    return jsonify({"ok": True, "session": session, "messages": messages})


@app.route("/api/research/qa/ask", methods=["POST"])
def api_qa_ask():
    """多轮研报问答:会话持久化 + 上下文改写检索 + 引用作答。

    【入参】{"question"必填, "session_id"?(缺省自动建), "variety"?, "report_type"?,
            "date_from"?(YYYY-MM-DD), "date_to"?, "top_k"?(1~12,默认 6)}。
    【返回】{"ok", "session_id", "message_id", "answer", "citations", "retrieved",
            "rewritten_query", "rag_enabled"};失败矩阵同旧 ask(HTTP 200 + ok:false)。
    【关键逻辑】前端不回传历史,后端按 session_id 自取最近 3 轮做改写(口径单点);
            每次问答 user/assistant 两条落库(qa_messages 即审计日志),失败轮也落。
    """
    body = request.get_json(silent=True) or {}
    question = str(body.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question 必填"}), 400
    variety = str(body.get("variety") or "").strip().upper() or None
    report_type = str(body.get("report_type") or "").strip() or None
    date_from = str(body.get("date_from") or "").strip() or None
    date_to = str(body.get("date_to") or "").strip() or None
    try:
        top_k = max(1, min(int(body.get("top_k") or 6), 12))
    except (TypeError, ValueError):
        top_k = 6
    filters = {"variety": variety or "", "report_type": report_type or "",
               "date_from": date_from or "", "date_to": date_to or ""}

    db = get_db()
    session = None
    session_id = int(body.get("session_id") or 0)
    if session_id:
        session = db.get_qa_session(session_id)
        if not session:
            return jsonify({"ok": False, "error": "会话不存在"})
    if session is None:
        session = db.create_qa_session(title=question)  # 首问截 30 字作标题
        session_id = session["id"]
    # user 消息先落库(任何后续失败都不丢用户原话)
    db.insert_qa_message(session_id, "user", question, filters=filters)

    from tradingagents.rag import is_available  # 【调用包】懒导入

    if not is_available():
        err = "RAG 未启用:需安装 chromadb 与 sentence-transformers,并回填索引"
        db.insert_qa_message(session_id, "assistant", "", filters=filters, error=err)
        db.update_qa_session(session_id, variety=variety, report_type=report_type,
                             date_from=date_from, date_to=date_to)
        return jsonify({"ok": False, "rag_enabled": False, "session_id": session_id, "error": err})

    from tradingagents.rag import service  # 【调用包】懒导入重依赖

    history = db.get_qa_history_for_rewrite(session_id, max_turns=_QA_REWRITE_TURNS,
                                            max_chars=_QA_REWRITE_CHAR_BUDGET)
    prior_history = history[:-1] if history else []  # 末条是刚插入的当前问题,排除
    t0 = time.monotonic()
    with _QA_LLM_SEM:
        rewritten = _rewrite_query_for_retrieval(prior_history, question)
        search_query = rewritten or question  # ""(首轮/改写失败)回退原问题
        try:
            hits = service.retrieve_hits(
                search_query, top_k=top_k, variety=variety, report_type=report_type,
                date_from=date_from, date_to=date_to,
            )
        except Exception as e:
            logger.warning("QA retrieve failed: %s", e)
            err = "检索失败,请稍后重试"
            db.insert_qa_message(session_id, "assistant", "", filters=filters, error=err)
            db.update_qa_session(session_id, variety=variety, report_type=report_type,
                                 date_from=date_from, date_to=date_to)
            return jsonify({"ok": False, "error": err, "rag_enabled": True,
                            "session_id": session_id})
    if not hits:
        err = "未检索到相关研报片段,试试放宽品种/类型/日期过滤"
        db.insert_qa_message(session_id, "assistant", "", filters=filters, error=err)
        db.update_qa_session(session_id, variety=variety, report_type=report_type,
                             date_from=date_from, date_to=date_to)
        return jsonify({
            "ok": True, "answer": "", "citations": [], "retrieved": 0,
            "rag_enabled": True, "session_id": session_id, "error": err,
            "rewritten_query": rewritten or "",
        })

    citations = _qa_build_citations(hits)
    context = service.format_context(hits)
    # 新鲜度提示:让模型自知材料边界,回答"截至 X 日"
    latest_date = max((c["publish_date"] for c in citations if c["publish_date"]), default="未知")
    try:
        with _QA_LLM_SEM:
            client = create_llm_client(
                config["llm_provider"],
                config.get("quick_think_llm", config["deep_think_llm"]),
            )
            prompt = (
                "你是中国商品期货研究助理。下面给出用户问题与若干研报片段(带编号,来自不同"
                "日期的历史研报)。请只依据片段内容用中文回答,回答中用 [n] 标注所引用的片段"
                "编号,引用观点/数据时注明其发布日期;片段不足以回答的部分明确说明,严禁编造。"
                "若问题涉及多个方面(如技术面/基本面/宏观面),请逐方面作答;某方面片段未覆盖时"
                "只说明该方面不足,不要因部分缺失而整体拒答。\n"
                "用 markdown 组织回答,结构清晰,一般 600~1200 字,数据对比适合用表格;"
                "按问题复杂度伸缩,不凑字数也不偷懒。\n"
                f"这是一次多轮对话中的回答;检索查询已根据对话历史改写完成,你只需针对当前"
                f"问题独立作答。\n"
                f"检索片段最新发布日期:{latest_date}\n\n"
                f"用户问题:{question}\n\n"
                f"{context}\n\n"
                "回答(markdown):"
            )
            result = client.get_llm().invoke(prompt)
            answer = str(result.content if hasattr(result, "content") else result).strip()
    except Exception as e:
        logger.warning("QA answer generation failed: %s", e)
        err = "已检索到片段,但生成回答失败,请稍后重试"
        mid = db.insert_qa_message(
            session_id, "assistant", "", citations=citations, retrieved=len(hits),
            filters=filters, rewritten_query=rewritten or "", error=err,
        )["id"]
        db.update_qa_session(session_id, variety=variety, report_type=report_type,
                             date_from=date_from, date_to=date_to)
        return jsonify({
            "ok": False, "citations": citations, "retrieved": len(hits),
            "rag_enabled": True, "session_id": session_id, "message_id": mid,
            "error": err,
        })

    latency_ms = int((time.monotonic() - t0) * 1000)
    mid = db.insert_qa_message(
        session_id, "assistant", answer, citations=citations, retrieved=len(hits),
        filters=filters, rewritten_query=rewritten or "",
        latency_ms=latency_ms,
    )["id"]
    db.update_qa_session(session_id, variety=variety, report_type=report_type,
                         date_from=date_from, date_to=date_to)
    return jsonify({
        "ok": True, "session_id": session_id, "message_id": mid,
        "answer": answer, "citations": citations, "retrieved": len(hits),
        "rewritten_query": rewritten or "", "rag_enabled": True,
    })


# 【变量】研报自动接入并发标志(防止手动/定时并发重复接入)。
_research_collecting = False


# 【功能】查询"研报自动接入"是否进行中(前端采集按钮置灰/轮询用)。
# 【返回】{"collecting": bool}。
@app.route("/api/research/reconclude", methods=["POST"])
def api_research_reconclude():
    """存量研报逐品种观点批量重跑(scripts/reconclude_research.py 的 HTTP 口)。

    【功能】按白名单 ids 逐条重跑结论(复用 reconclude_research_report=当前结论
            提示词),同步执行;单条失败不中断,逐条返回结果。
    【参数】POST json: {"ids": [47, 53]}(必填非空白名单,防误把全库重跑;单次
            ≤10 条,防请求线程被占过久)。
    【返回】json: {"results": [{ok, report_id, codes|error}, ...]}。
    【关键逻辑】2026-09-05 新增:低内存机器上不再为批量重跑起第二个 Python 进程
            (web_app + 重跑脚本曾两度被系统 OOM 杀)——由 curl 把并发压进本进程
            线程(调用方控制并发 ≤4,与账户 LLM 并发上限 5 兼容),内存零增量。
    """
    body = request.get_json(silent=True) or {}
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "ids 须为非空白名单数组,如 {\"ids\": [47, 53]}"}), 400
    if len(ids) > 10:
        return jsonify({"error": "单次最多 10 条,分批调用"}), 400
    results = [reconclude_research_report(int(i)) for i in ids]
    return jsonify({"results": results})


@app.route("/api/research/collect", methods=["GET"])
def api_research_collect_status():
    return jsonify({"collecting": _research_collecting})


# 【功能】手动触发"研报自动接入":华泰天玑 20 品种池日报
#           + 国泰君安云 API 研报(2026-09-04 开通)+ 东证繁微观点(2026-09-08 接入)。
#           (发现报告源 2026-09-09 剔除:三家 API 源已覆盖,重复度高。)
# 【请求体】可选 {"dry_run": true, "source": "all"|"htfc"|"gtja"|"dongzheng",
#           "varieties": ["SC","TA",...](品种代码白名单,只采指定品种,防止全量
#           采集 LLM 耗时过长;华泰/国君生效;
#           缺省/空 = 全部品种)};缺省 source=all 三源都跑。
# 【返回】{"status": "started"} / 进行中 409 / source 或品种非法 400 / 异常 500。
# 【关键逻辑】daemon 线程按 source 顺序跑
#           research_collector_htfc.ingest_today(华泰天玑,日报当日+周报近10天,requested=品种集)、
#           research_collector_gtja.ingest_recent(国君云 API,近 2 天,requested=品种集)、
#           research_collector_dongzheng.ingest_recent(东证繁微,动态快评近 1 天,
#           品种由 LLM 识别无法预判,不支持 varieties 预过滤),
#           与上传端点后台线程同模式,不阻塞响应;模块级 _research_collecting 标志防并发
#           (手动/定时共用);失败记 alert,便于前端告警中心可见。每篇同步走
#           LLM 提取(与定时路径一致),全量可能需数十分钟。
@app.route("/api/research/collect", methods=["POST"])
def api_research_collect():
    global _research_collecting
    if _research_collecting:
        return jsonify({"status": "busy", "message": "研报接入正在进行中,请稍候"}), 409
    body = request.json or {}
    dry_run = bool(body.get("dry_run"))
    source = str(body.get("source") or "all").lower()
    # 【源裁撤】2026-09-09 起发现报告(fxbaogao)源剔除:三家 API 源(华泰天玑/国君/
    # 东证繁微)已覆盖,fxbaogao 按机构抓取、品种无法预判且重复度高;source 兼容值
    # 移除,历史 research_{time} 调度 job 一并下线(research_collector.py 文件保留)。
    if source not in ("all", "htfc", "gtja", "dongzheng"):
        return jsonify({"error": "source 须为 all / htfc / gtja / dongzheng"}), 400
    # 【品种筛选】可选 varieties 代码列表,非空时只采指定品种(采集器内部按
    # 品种 tag/代码过滤);非法代码直接 400,避免静默采到 0 篇。
    requested: set[str] | None = None
    raw_varieties = body.get("varieties")
    if isinstance(raw_varieties, list) and raw_varieties:
        # 【品种池】2026-09-09 起校验基准改为 ACTIVE_VARIETIES(20 品种池),
        # 不再引用 gtja 采集器的 TARGET_VARIETIES(后者也统一 import 同一常量)。
        requested = {str(v).strip().upper() for v in raw_varieties if str(v).strip()}
        unknown = requested - ACTIVE_VARIETIES
        if unknown:
            return jsonify({"error": f"未知品种代码: {', '.join(sorted(unknown))}"}), 400
        requested = requested or None  # 全空白串 → 视为全部品种

    def _run():
        global _research_collecting
        # 懒导入:避免 web_app 顶部 import 块进一步膨胀;本地作用域内排序保持 ruff 干净。
        from contextlib import suppress  # 【调用包】忽略告警写入失败的异常

        today = datetime.now().strftime("%Y-%m-%d")
        try:
            if source in ("all", "htfc"):
                from research_collector_htfc import ingest_today  # 【调用包】华泰天玑采集器(懒导入)

                htfc_result = ingest_today(today, requested=requested, dry_run=dry_run)
                logger.info("Research auto-collect (htfc) done: %s", htfc_result)
            if source in ("all", "gtja"):
                from research_collector_gtja import (
                    ingest_recent,  # 【调用包】国君云 API 采集器(懒导入)
                )

                gtja_result = ingest_recent(days=1, requested=requested, dry_run=dry_run)
                logger.info("Research auto-collect (gtja) done: %s", gtja_result)
            if source in ("all", "dongzheng"):
                from research_collector_dongzheng import (
                    ingest_recent as dz_ingest_recent,  # 【调用包】东证繁微采集器(懒导入)
                )

                # 【品种预过滤不支持】动态快评入库前无法预判品种(LLM 识别),
                # requested 非空时同样跳过,避免品种筛选下全量白跑
                if requested is None:
                    dz_result = dz_ingest_recent(days=1, dry_run=dry_run)
                    logger.info("Research auto-collect (dongzheng) done: %s", dz_result)
        except Exception as e:
            logger.exception("Research auto-collect failed")
            with suppress(Exception):
                get_db().create_alert(  # 【调用函数】写入"研报接入异常"告警
                    "research_error",
                    "Manual research collect failed",
                    str(e)[:300],
                    severity="error",
                )
        finally:
            _research_collecting = False

    _research_collecting = True
    threading.Thread(target=_run, daemon=True).start()  # 【调用函数】后台线程执行接入(不阻塞响应)
    label = {"all": "华泰天玑 + 国君云API + 东证繁微",
             "htfc": "华泰天玑", "gtja": "国君云API", "dongzheng": "东证繁微"}[source]
    vlabel = "全部品种" if not requested else "品种 " + "/".join(sorted(requested))
    if requested and source == "all":
        label = "华泰天玑 + 国君云API(东证繁微已排除)"
    return jsonify({"status": "started", "source": source, "dry_run": dry_run,
                    "varieties": sorted(requested) if requested else [],
                    "message": f"研报采集已启动({label},{vlabel}),后台处理中"})


# ── PDF / Markdown Export ──────────────────────────────────────────────────


# ── Report rendering: HTML page + PDF(与 Web 前端同风格)─────────────────────


# 【功能】从报告 markdown 中提取 RATING 评级头(api_report / api_report_pdf / 网页版 HTML 共用)。
# 【参数】content: 报告 markdown 文本。
# 【返回】{rating, confidence, score} dict;未命中返回 None。
def _parse_rating(content):
    """Extract {rating, confidence, score} from a report's RATING header."""
    m = re.search(
        r"RATING:\s*(.+?)\s*\|\s*CONFIDENCE:\s*(.+?)\s*\|\s*SCORE:\s*(\d+)", content
    )
    if not m:
        return None
    return {
        "rating": m.group(1).strip(),
        "confidence": m.group(2).strip(),
        "score": int(m.group(3)),
    }


# 【功能】报告网页版/PDF 的深色主题 CSS——显式写死 Web 前端 :root 的深色变量值,
#   并复制 web_template.html 的 .report-content(报告正文)与 .signal-banner(RATING 横幅)
#   全套规则,保证网页版与 PDF 的外观和 Web 前端一致。
_REPORT_CSS = """
:root {
  --brand: #ff5a1f;
  --bg: #0a0a0a;
  --bg-elevated: #0f0f0f;
  --bg-input: #1a1a1a;
  --border: #2a2a2a;
  --border-light: #333;
  --text: #f5f1eb;
  --text-secondary: #a0a0a0;
  --text-muted: #666;
  --green: #22c55e;
  --red: #ef4444;
  --amber: #fbbf24;
  --blue: #60a5fa;
  --radius-sm: 6px;
  --radius-lg: 16px;
}
@page { size: A4; margin: 14mm 16mm; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "WenQuanYi Zen Hei", -apple-system, "Segoe UI", sans-serif;
  font-size: 14px;
  line-height: 1.7;
  margin: 0;
  padding: 24px;
}
.report-header {
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 18px 24px;
  margin-bottom: 18px;
}
.report-header .report-symbol { font-size: 1.5rem; font-weight: 900; color: var(--brand); }
.report-header .report-file, .report-header .report-time { font-size: 0.78rem; color: var(--text-muted); margin-top: 4px; }
.signal-banner {
  background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-lg);
  padding: 24px 32px;
  text-align: center;
  margin-bottom: 20px;
}
.signal-banner .signal-label {
  font-size: 0.75rem;
  letter-spacing: 2px;
  color: var(--text-muted);
  text-transform: uppercase;
}
.signal-banner .signal-rating {
  font-size: 2.4rem;
  font-weight: 900;
  margin: 4px 0;
}
.signal-banner .signal-meta {
  font-size: 0.9rem;
  color: var(--text-secondary);
}
.report-content {
  line-height: 1.7;
  font-size: 0.88rem;
}
.report-content h1, .report-content h2, .report-content h3 {
  color: var(--brand);
  margin: 16px 0 8px;
}
.report-content h1 { font-size: 1.4rem; }
.report-content h2 { font-size: 1.2rem; }
.report-content h3 { font-size: 1rem; }
.report-content table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.82rem;
  margin: 8px 0;
}
.report-content th {
  background: var(--bg-input);
  color: var(--brand);
  padding: 6px 10px;
  text-align: left;
  border-bottom: 2px solid var(--border);
}
.report-content td {
  padding: 5px 10px;
  border-bottom: 1px solid var(--border);
}
.report-content ul, .report-content ol { margin: 8px 0; padding-left: 20px; }
.report-content li { margin: 2px 0; }
.report-content code {
  background: var(--bg-input);
  padding: 2px 6px;
  border-radius: 3px;
  font-size: 0.85em;
}
.report-content pre {
  background: var(--bg-input);
  padding: 12px;
  border-radius: var(--radius-sm);
  overflow-x: auto;
  font-size: 0.82em;
}
.report-content blockquote {
  border-left: 3px solid var(--brand);
  padding: 8px 14px;
  margin: 8px 0;
  background: var(--bg-input);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
}
.report-content a { color: var(--blue); text-decoration: none; }
.report-content a:hover { text-decoration: underline; }
.report-content hr { border: none; border-top: 1px solid var(--border); margin: 16px 0; }
"""

# 【功能】报告网页版的 HTML 骨架模板(占位符由 _report_to_html 逐个替换)。
#   · __CSS__     —— 内嵌深色主题 CSS(_REPORT_CSS);
#   · __MARKED__  —— 内嵌前端同款 marked.min.js(markdown 解析器,与前端一致);
#   · __CONTENT__ —— markdown 正文的 JSON 字符串(json.dumps 转义,防 </script> 截断);
#   · __BANNER__  —— RATING 横幅(可空)。
_REPORT_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>__CSS__</style>
</head>
<body>
<div class="report-header">
  <div class="report-symbol">__HEADER__</div>
  <div class="report-file">__FILENAME__</div>
  <div class="report-time">__GENERATED__</div>
</div>
__BANNER__
<div class="report-content" id="report-content"></div>
<script>__MARKED__</script>
<script>
  document.getElementById('report-content').innerHTML = marked.parse(__CONTENT__);
</script>
</body>
</html>
"""


# 【功能】把报告 markdown 渲染成自包含 HTML 网页(深色主题,与 Web 前端同款外观)。
# 【参数】content: 报告 markdown; filename: 报告文件名(commodity_{品种}_{时间戳}.md);
#   rating: 评级 dict(可空,自动用 _parse_rating 从正文提取)。
# 【返回】完整 HTML 文档字符串。
# 【关键逻辑】用 json.dumps 把 markdown 正文注入 JS 字符串,并额外转义 "</" 为 "<\/",
#   保证正文里的 </script>、引号、换行都不会截断脚本;marked.min.js 与 CSS 均内嵌,
#   页面可独立打开/打印(浏览器 Ctrl+P 也能得到同款排版)。
def _report_to_html(content, filename, rating=None):
    """Render a markdown report into a standalone dark-theme HTML page."""
    if rating is None:
        rating = _parse_rating(content)

    sym = "?"
    if filename.startswith("commodity_"):
        parts = filename[len("commodity_"):].split("_", 1)
        sym = parts[0] if parts else "?"
    gen_match = re.search(r"\*\*Generated\*\*:\s*(.+)", content)
    generated = gen_match.group(1).strip() if gen_match else ""

    header = "Commodity Futures Analysis"
    if sym != "?":
        header += f" — {html.escape(sym)}"

    banner_html = ""
    if rating:
        score = rating.get("score")
        color = (
            "var(--green)"
            if (score is not None and score >= 6)
            else ("var(--red)" if (score is not None and score <= 4) else "var(--amber)")
        )
        banner_html = (
            '<div class="signal-banner">'
            '<div class="signal-label">RATING</div>'
            f'<div class="signal-rating" style="color:{color}">{html.escape(rating.get("rating", "?"))}</div>'
            f'<div class="signal-meta">CONFIDENCE: {html.escape(rating.get("confidence", "?"))} | SCORE: {score if score is not None else "?"}/10</div>'
            "</div>"
        )

    marked_path = Path(__file__).resolve().parent / "static" / "marked.min.js"
    marked_js = marked_path.read_text(encoding="utf-8") if marked_path.exists() else ""
    content_json = json.dumps(content, ensure_ascii=False).replace("</", "<\\/")

    title = html.escape(f"{header} — {filename}")
    return (
        _REPORT_PAGE.replace("__TITLE__", title)
        .replace("__CSS__", _REPORT_CSS)
        .replace("__MARKED__", marked_js)
        .replace("__CONTENT__", content_json)
        .replace("__HEADER__", header)
        .replace("__FILENAME__", html.escape(filename))
        .replace("__GENERATED__", html.escape(generated))
        .replace("__BANNER__", banner_html)
    )


# 【功能】用 headless Chromium(Playwright)把报告网页版打印成 PDF(内存字节流,不落盘)。
# 【参数】content: Markdown 文本; filename: 文件名(仅用于页眉显示);
#   rating: 评级 dict(可选,非空则打印在标题下方)。
# 【关键逻辑】先 _report_to_html 生成与 Web 前端同风格的深色主题 HTML,再用 Playwright
#   的 chromium 打开并 page.pdf() 导出;与前端同渲染引擎,PDF 外观与网页天然一致。
#   任何一步失败(Playwright 未装/浏览器缺失/渲染异常)都回退到 fpdf2 纯文本版
#   _generate_pdf_fpdf,保证 PDF 导出永不 500。
def _generate_pdf(content, filename, rating=None):
    """Generate a PDF from a markdown report — styled like the web frontend."""
    try:
        from playwright.sync_api import sync_playwright  # 【调用包】浏览器自动化(PDF 打印引擎)
    except Exception:
        return _generate_pdf_fpdf(content, filename, rating)

    tmp = None
    try:
        html = _report_to_html(content, filename, rating)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            tmp = f.name
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.set_content(html, wait_until="load")
                page.wait_for_selector("#report-content", timeout=15000)  # 【关键】等 marked 渲染完成
                page.pdf(
                    path=tmp,
                    format="A4",
                    print_background=True,
                    margin={"top": "14mm", "bottom": "14mm", "left": "16mm", "right": "16mm"},
                )
            finally:
                browser.close()
        with open(tmp, "rb") as f:
            return f.read()
    except Exception:
        return _generate_pdf_fpdf(content, filename, rating)
    finally:
        if tmp and os.path.exists(tmp):
            with contextlib.suppress(Exception):
                os.remove(tmp)


# 【功能】fpdf2 纯文本版 PDF 渲染(Playwright 不可用时的兜底路径)。
# 【参数】content: Markdown 文本; filename: 文件名(仅用于页眉显示);
#   rating: 评级 dict(可选,非空则打印在标题下方)。
# 【关键逻辑】中文字体需手动注册,按 Windows/Linux/Mac 常见路径逐一探测 CJK 字体;
#   找不到则退回 Helvetica(只能渲染 ASCII,中文会乱码)。正文只取前 500 行、每行截 200 字符。
def _generate_pdf_fpdf(content, filename, rating=None):
    """Generate a PDF from markdown report using fpdf2 (fallback renderer)."""
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
    """View a report as PDF in-browser (styled like the web frontend)."""
    safe_name = os.path.basename(filename)
    fpath = REPORT_DIR / safe_name
    if not fpath.exists():
        return jsonify({"error": "Not found"}), 404

    with open(fpath, encoding="utf-8") as f:
        content = f.read()

    try:
        pdf_data = _generate_pdf(content, safe_name, _parse_rating(content))
        return send_file(
            io.BytesIO(pdf_data),
            mimetype="application/pdf",
            as_attachment=False,  # 【关键】inline:浏览器新标签直接打开 PDF 查看,而非强制下载
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


# 【功能】网页版查看历史报告:把 markdown 渲染成独立深色主题 HTML 页面(与前端同风格,
#   含 RATING 横幅与卡式章节),可直接在浏览器打开/打印,也可右键另存为网页。
# 【安全】os.path.basename 防路径穿越;文件不存在返回 404。
# 【返回】text/html 页面。
@app.route("/api/report/<path:filename>/html")
def api_report_html(filename):
    """Open a saved report as a standalone web-styled HTML page."""
    safe_name = os.path.basename(filename)
    fpath = REPORT_DIR / safe_name
    if not fpath.exists():
        return jsonify({"error": "Not found"}), 404

    with open(fpath, encoding="utf-8") as f:
        content = f.read()

    return Response(_report_to_html(content, safe_name), mimetype="text/html")


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
# 【请求体】{"schedule_times": ["08:00", "18:00"], "research_times": ["08:10","18:00"]}(可选,缺省用默认)。
# 【返回】{"status": "started", "schedule_times": [...], "research_times": [...]}。
@app.route("/api/scheduler/start", methods=["POST"])
def api_scheduler_start():
    try:
        from scheduler import start_scheduler  # 【调用包】调度器启动

        data = request.json or {}
        times = data.get("schedule_times", ["08:00", "18:00"])
        research_times = data.get("research_times", ["08:10", "18:00"])
        start_scheduler(schedule_times=times, research_times=research_times)
        return jsonify({"status": "started", "schedule_times": times, "research_times": research_times})
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
        if var.upper() not in ACTIVE_VARIETIES:  # 【品种池】池外品种隐藏(存量文件保留)
            continue
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
            for f in sorted(SENTIMENT_DIR.glob("*_sentiment.json"))
            if f.stem.replace("_sentiment", "").upper() in ACTIVE_VARIETIES  # 【品种池】
        ][:10]
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
            if v.upper() not in ACTIVE_VARIETIES:  # 【品种池】池外品种不进批量预测
                continue
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
            if v.upper() not in ACTIVE_VARIETIES:  # 【品种池】池外品种不进批量验证
                continue
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


# ----【启动保护 2026-09-03】旧实例占用 :5000 时拒绝启动,避免 SO_REUSEADDR 双绑后浏览器跑旧代码 ----
def _parse_netstat_listeners(output: str, port: int) -> list[int]:
    """从 netstat -ano 文本解析"监听给定端口"的进程 PID 列表(纯函数,便于单测)。"""
    pids: list[int] = []
    seen: set[int] = set()
    suffix = f":{port}"
    for raw in output.splitlines():
        parts = raw.split()
        # 数据行形如: TCP    0.0.0.0:5000    0.0.0.0:0    LISTENING    258248
        if len(parts) < 5 or not parts[0].startswith("TCP"):
            continue
        local, state = parts[1], parts[3]
        if state != "LISTENING" or not local.endswith(suffix):
            continue
        try:
            pid = int(parts[4])
        except ValueError:
            continue
        if pid not in seen:
            seen.add(pid)
            pids.append(pid)
    return pids


def _listeners_on_port(port: int = 5000) -> list[int]:
    """查询本机当前监听 port 的进程 PID(仅 Windows 有效;其它平台返回空=不拦截)。"""
    if sys.platform != "win32":
        return []
    try:
        import subprocess  # 【调用包】子进程调用(运行 netstat)

        out = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            timeout=15,
            encoding="utf-8",
            errors="replace",
            check=False,
        ).stdout or ""
    except Exception:
        return []
    return _parse_netstat_listeners(out, port)


def _prompt_and_exit(message: str, code: int = 1) -> None:
    """打印提示并停留等待回车,再以 code 退出(让用户看清启动保护的原因)。"""
    print(message, flush=True)
    with contextlib.suppress(EOFError, KeyboardInterrupt):
        input("    按回车键退出…")  # 非交互(后台/脚本)启动时没有输入,直接退出
    raise SystemExit(code)


# 主入口:以脚本方式运行时启动调度器 + Waitress 生产服务器。
# · 调度器启动失败只打印警告,不影响看板启动。
# · waitress.serve 以 8 个线程监听 0.0.0.0:5000,channel_timeout=600 秒。
if __name__ == "__main__":
    # 【启动保护】:换代码重启前,若旧实例仍占着 :5000,先停下再启(拒绝静默双绑)。
    occupiers = _listeners_on_port(5000)
    if occupiers:
        pids = "、".join(str(p) for p in occupiers)
        _prompt_and_exit(
            "\n[启动保护] 检测到端口 :5000 正被进程 PID " + pids + " 监听——"
            "通常是上一次启动的服务器实例还没停。\n"
            "为避免再次\"重开网页却跑到旧代码\",本次不启动。\n"
            "请先停止旧实例:双击项目目录下的 stop_web.bat,"
            "或在旧服务器窗口按 Ctrl+C;然后重新启动本服务。\n"
        )

    try:
        from scheduler import start_scheduler  # 【调用包】调度器启动

        start_scheduler(
            schedule_times=["08:00", "18:00"],  # 情感采集管道
            research_times=["08:10", "18:00"],  # 开盘前研报自动接入(日盘 09:00 前 + 夜盘 21:00 前)
        )
        print("Scheduler started: daily 08:00/18:00, research 08:10/18:00")
    except Exception as e:
        print(f"Scheduler not started: {e}")

    # 【启动自愈】清扫聚合 JSON 孤儿条目(DB 已删但聚合未同步的残留),根治
    # "研报在数据仓库删了还出现在观点总览/分析师取数"的历史漂移(2026-09-04)。
    try:
        from tradingagents.dataflows.research_data import (
            sweep_orphan_reports,  # 【调用包】聚合 JSON 孤儿清扫
        )

        _db = get_db()
        _removed = sweep_orphan_reports({r["id"] for r in _db.list_research_reports(limit=1000)})
        if _removed:
            print(f"Orphan research aggregate entries swept: {_removed}")
    except Exception as e:
        print(f"Orphan research sweep skipped: {e}")

    from waitress import serve  # 【调用包】生产级 WSGI 服务器(多线程托管 Flask)

    print("FuturesMind Dashboard: http://localhost:5000")
    serve(app, host="0.0.0.0", port=5000, threads=8, channel_timeout=600)
