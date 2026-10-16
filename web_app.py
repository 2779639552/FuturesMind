# =====================================================================
# web_app.py —— 本项目 Flask Web 看板的后端入口
#
# 【整体角色】
#   · 这是一个 Flask Web 服务器(生产环境用 Waitress 托管),默认监听
#     http://localhost:5000 ,浏览器访问 5000 端口即可打开看板前端。
#   · 以脚本运行时(__main__ 分支)会自动启动:
#       1) 后台调度器 scheduler.start_scheduler() —— 每天 08:00 / 18:00
#          执行定时采集 / 聚合等任务(研报采集 08:10 / 18:00 / 22:00);
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
import difflib  # 【调用包】标题/正文相似度(同源多篇研报的"同篇双采"判重)
import glob  # 【调用包】批量路径匹配(如 batch_*.jsonl 文件列举)
import html  # 【调用包】HTML 转义(网页版报告标题/文件名安全显示)
import io  # 【调用包】内存字节流(BytesIO,PDF 下载响应)
import json  # 【调用包】JSON 序列化/反序列化(配置、行情缓存、批次文件)
import logging  # 【调用包】日志记录(报告落盘异常等)
import os  # 【调用包】路径/环境变量操作
import queue  # 【调用包】RAG worker 客户端的应答等待队列
import re  # 【调用包】正则提取报告章节与评级字段
import secrets  # 【调用包】生成安全 token 与 secret_key
import shutil  # 【调用包】递归删除临时目录(文件夹上传的 tmp 清理)
import subprocess  # 【调用包】RAG worker 子进程拉取(scheduler 采集亦用)
import sys  # 【调用包】模块搜索路径调整、解释器路径获取
import tempfile  # 【调用包】临时文件(Playwright 打印 PDF 的中转文件)
import threading  # 【调用包】后台线程与进度锁
import time  # 【调用包】耗时统计与轮询间隔
from collections import defaultdict  # 【调用包】字典计数(缺失键自动给默认值,非标数据聚合用)
from concurrent.futures import ThreadPoolExecutor  # 【调用包】线程池(数据看板三数据源并行拉取)
from datetime import datetime, timedelta, timezone  # 【调用包】日期解析与时间窗计算(UTC 判僵尸年龄)
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

# 【2026-09-14 修复】让同进程内后续 `from web_app import ...` 复用本副本,而不是
#   再导入一份。web_app.py 以脚本方式启动时模块名是 __main__;此时采集器/调度器
#   里的 `from web_app import X` 会以 'web_app' 为名**再执行一遍本文件**,得到一份
#   模块级状态完全独立的副本 —— 其中 RAG worker 的进程句柄 _rag_proc 与串行锁
#   _rag_call_lock 各自一份,于是同一 web 进程下起了 2 组 rag_worker,两个客户端
#   并发读写同一个 chroma 目录(09-10 向量库损坏的一致成因;09-14 进程树实测
#   主进程 4932 下挂着 4420 与 19692 两组 worker)。
#   注册后 sys.modules['web_app'] 就是本副本,所有导入者共享同一份状态:
#   只有一组 worker、一把锁,且不再重复创建 Flask app / 路由。
if __name__ == "__main__":
    sys.modules.setdefault("web_app", sys.modules[__name__])

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
import update_tracker as ut  # noqa: E402  # 【调用包】各平台增量统计(跨批次去重 + 按更新轮次归组)
import collect_pool  # noqa: E402  # 【调用包】有界并行调度(与定时管道共用并发度/顺序/隔离语义)
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
    ACTIVE_VARIETIES,  # 【调用包】21 品种活跃池(2026-09-09 起 UI/采集/展示白名单)
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

# ── 量化/技术分析模块(2026-09-28 新增, 第 8 个 tab) ─────────────────────────
# 【设计】7 个子模块(K线/技术指标/基差期限/仓单库存/量化信号/策略回测/截面排行)
#   独立实现在 quant_module.py, 以 Flask Blueprint 挂载 —— 避免继续膨胀本文件;
#   在本处注册只需一行, 且蓝图内部对 web_app 的依赖全部延迟导入, 无循环导入。
# 【失败降级】蓝图导入失败只记警告, 不影响主应用启动(模块整体不可用 ≠ 整站不可用)。
try:
    from quant_module import quant_bp  # 【调用包】量化模块蓝图(7 个子模块接口)

    app.register_blueprint(quant_bp)  # 【调用函数】挂载全部 /api/quant/* 路由
    logger.info("量化/技术分析模块已挂载(quant_module, /api/quant/*)")
except Exception as _qm_exc:  # noqa: BLE001 - 蓝图不可用不应阻断整站启动
    logger.warning("量化模块挂载失败(该模块将不可用): %s", _qm_exc)

# ── 基本面量化模块(2026-09-29 新增, 与量化分析并列的第 9 个 tab) ──────────────
# 【设计】4 个子模块(三指标策略系统 / 估值+驱动二维四象 / 加工进口利润 / 虚实盘比逼仓),
#   独立实现在 fund_module.py; 方法论来自用户级技能 qihuo-duokong-logic。
# 【口径复用】取数一律委托 quant_module 的同名函数(_fetch_basis/_fetch_inventory/
#   _fetch_profit/_fetch_term_structure), 不另起一套 —— 防止"同品种两套基差/仓单口径"。
# 【失败降级】同 quant: 蓝图导入失败只记警告, 不影响主应用启动。
try:
    from fund_module import fund_bp  # 【调用包】基本面量化模块蓝图(4 个子模块接口)

    app.register_blueprint(fund_bp)  # 【调用函数】挂载全部 /api/fund/* 路由
    logger.info("基本面量化模块已挂载(fund_module, /api/fund/*)")
except Exception as _fm_exc:  # noqa: BLE001 - 蓝图不可用不应阻断整站启动
    logger.warning("基本面量化模块挂载失败(该模块将不可用): %s", _fm_exc)


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
# 【品种池】2026-09-09 起只展示 ACTIVE_VARIETIES(21 品种);元数据其余键保留但隐藏。
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


# 【功能】按平台分层抽样:先给每个平台保底名额,再把各平台名额轮转交错。
# 【参数】items: 已按发布时间倒序的帖子 dict 列表(每条含 platform/time);
#         limit: 目标条数上限。
# 【返回】list[dict]: 至多 limit 条,平台覆盖尽量均衡,长度不足时原样返回。
# 【关键逻辑】纯函数可单测。见 api_sentiment_posts 的调用处注释说明为什么需要它。
def _stratified_sample(items, limit):
    if not items or len(items) <= limit:
        return list(items)
    buckets = {}
    for _p in items:
        buckets.setdefault(_p["platform"], []).append(_p)
    quota = max(1, limit // len(buckets))  # 每平台保底名额(至少 1)
    picked = []
    for _items in buckets.values():
        picked.extend(_items[:quota])
    if len(picked) < limit:
        rest = [_p for _items in buckets.values() for _p in _items[quota:]]
        rest.sort(key=lambda _p: _p["time"], reverse=True)
        picked.extend(rest[: limit - len(picked)])
    # 【交错排序】把各平台名额**轮转交错**,而不是拢成"先雪球 35 条、再知乎 33 条…"。
    # 原因:调用方只渲染前 100 条。若按平台分块排列,排在后面的平台照样被截掉 ——
    # 那等于没修。轮转后**任意前缀都是均衡的**,无论下游截到 100 还是 20 都有效。
    by_plat = {}
    for _p in picked:
        by_plat.setdefault(_p["platform"], []).append(_p)
    seq = sorted(by_plat, key=lambda _k: max(_p["time"] for _p in by_plat[_k]), reverse=True)
    iters = [iter(by_plat[_k]) for _k in seq]
    merged = []
    while iters:
        alive = []
        for _it in iters:
            try:
                merged.append(next(_it))
                alive.append(_it)
            except StopIteration:
                pass
        iters = alive
    return merged[:limit]


# 【功能】返回最近采集到的社交帖子(全平台、抽样)。
# 【参数】days=最近 N 天(按发布时间过滤,可选);since=YYYY-MM-DD 截止日(可选)。
# ═══════════════════════════════════════════════════════════════════
# 发布日期卫生(展示层)
# ═══════════════════════════════════════════════════════════════════
# 【为什么 web 端也必须有一道闸】web_app **直接读原始 batch_*.jsonl** 渲染页面,
#   绕过了 trend_aggregator → data_cleaner.clean_records 的清洗链路。于是出现
#   一个割裂的现象:聚合产物(趋势/情绪 JSON)是干净的,**页面却能显示脏日期** ——
#   实测 xhs 卡片 id 为 UUID 形态时,采集端旧解码器把前 8 位当 Unix 秒,算出
#   2102 / 2104 年这类"看似合法的未来日期",且**永远落在"最近 N 天"窗口内**,
#   一路直达前端"数据覆盖 X ~ Y"与帖子发布时间列。
#   采集端解码器已修(2026-09-17),但三层理由要求展示层自己兜住:
#     ① 存量 jsonl 里的历史脏值不会自己消失;② batch 文件长期累积、跨 90 天窗口;
#     ③ 上游任何一次回归都会重新注入 —— 展示层不该依赖上游"永远正确"。
# 【规则与 data_cleaner 同源】格式须为 YYYY-MM-DD,且落在 [2015-01-01, 今天+1 天]。
#   那边是**记录级丢弃**(整条不要,用于聚合),这边是**字段级置空**(内容保留、
#   只把日期判为未知,用于展示),语义不同故不共用代码,但**区间与格式必须一致,
#   改一处务必同步另一处**。
_MIN_PUBLISH_DATE = "2015-01-01"  # 【变量】最早可信发布日期(2015 前无国内社媒期货内容)
_PUBLISH_DATE_RE = re.compile(r"\A\d{4}-\d{2}-\d{2}\Z")  # 【变量】发布日期格式(YYYY-MM-DD)
# 【变量】单次响应最多返回的帖子数。提成常量是因为它是**前后端契约**:
#   前端 App._POST_RENDER_LIMIT(=100)再渲染前 100 条。改这里必须同步前端常量,
#   否则"后端给了 300 条、前端只画 100 条"会导致用户以为丢数据。
POSTS_API_LIMIT = 200


def _sane_publish_date(raw, now=None):
    """把记录的 publish_time 归一到**可信**的 YYYY-MM-DD;不可信则返回空串。

    【参数】raw: 原始 publish_time(任意类型,容忍 None);now: 参照"今天"(便于测试注入)。
    【返回】可信 → "YYYY-MM-DD";不可信(空/长度不足/格式不符/早于 2015/晚于明天) → ""。
    【关键】返回 "" 的语义是"**日期未知**",调用方应"保留内容、但不采信该日期":
           既不把它计入 posts_start/posts_end,也不拿它做时间窗过滤。
           **绝不能把原始垃圾串原样透出** —— 那正是 2104 出现在页面上的直接原因。
    """
    s = str(raw or "").strip()
    if len(s) < 10:
        return ""
    d = s[:10]
    if not _PUBLISH_DATE_RE.match(d):
        return ""
    ref = now or datetime.now()  # 【变量】参照时刻(缺省取当前本地时间)
    if d < _MIN_PUBLISH_DATE or d > (ref + timedelta(days=1)).strftime("%Y-%m-%d"):
        return ""
    return d


# 【功能】把"根相对路径"的帖子 URL 补成可直接打开的绝对 URL。
# 【背景 2026-09-18】雪球适配器落盘的 url 是**根相对路径**(如 "/9551545711/399073978"
#   或股票页 "/S/SH600519/123456"), 前端写进 <a href="/9551545711/399073978"> 后,
#   浏览器按"本站域名下的路径"解析 => http://localhost:5000/9551545711/... => 必然 404。
#   页面上表现为"雪球帖子点了跳不过去", 而其它五个平台(都是绝对 URL)正常。
# 【为什么展示层还要再兜一层】采集侧已改为补域名, 但**存量 5000+ 条历史数据不会回溯重写**,
#   只靠采集侧修复的话, 用户在当前页面上看到的仍然是打不开的链接。
#   这与"正交降级通道"是同一思路: 不假设上游已修, 展示层各自能把数据收拾到可用状态。
# 【输入】url(原始字符串) / platform(平台键, 用于选域名)
# 【返回】绝对 URL; 空值原样返回 ""; 未知平台的相对路径也返回空(不猜域名, 免得指向错的站)。
_PLATFORM_URL_BASE = {"xueqiu": "https://xueqiu.com"}


def _absolutize_url(url, platform=""):
    """把根相对 URL 补全为绝对 URL(纯函数, 可单测)。"""
    u = (url or "").strip()
    if not u:
        return ""
    if u.startswith(("http://", "https://")):
        return u
    base = _PLATFORM_URL_BASE.get((platform or "").strip())
    if not base:
        return u  # 【保守】不认识域名就原样返回: 宁可点了不跳, 也不要跳到无关站点
    return base.rstrip("/") + "/" + u.lstrip("/")


def _split_post_text(d):
    """【功能】把一条帖子记录拆成「展示主体」与「出处附注」两段文本。

    【为什么需要 2026-09-18】抖音评论型记录(note_type="comment", 占抖音总量 89.8%)
      的 title 字段装的是**所属视频的标题**, 被写成 "[抖音评论] {视频标题}";
      而这条记录真正的内容 —— 用户想看的评论正文 —— 存在 desc 里。
      原实现一律取 `title or desc`(title 优先), 于是抖音卡片显示的全是视频题目,
      评论正文一个字都看不到 —— 用户的观感就是"抖音采的是标题不是评论"。
      实测 desc 在抖音记录里 100% 有值, 所以这不是采集缺陷, 是展示层选错了字段。
    【规则】评论型: 主体 = 评论正文, 附注 = 视频标题(保留"这条评论出自哪个视频"的上下文);
            其它型: 主体 = title(缺则 desc), 无附注
            —— video 型记录的 title 与 desc 本来内容相同, 无需特殊处理。
    【输入】d: batch jsonl 的一条原始记录 dict
    【返回】(body, parent) 二元组, 均为 str; parent 为空串表示"无附注"。
    """
    title = (d.get("title") or "").strip()
    desc = (d.get("desc") or "").strip()
    # 【双判据】用 note_type 为主, 前缀为辅: mediacrawler 导入链路的评论记录
    #   前缀写作 "[抖音评论·回复]", 而 note_type 同样是 comment —— 两个条件取或,
    #   任一路径生成的记录都能被正确识别(单看前缀会漏掉拼写变体)。
    is_comment = d.get("note_type") == "comment" or title.startswith("[抖音评论")
    if is_comment and desc:
        return desc[:120], title
    return (title or desc)[:120], ""


# 【返回】{"_meta": {posts_start, posts_end, total_posts, filter_since,
#                    platform_counts, platform_sampled, platform, scope_total},
#           "posts": 最多 200 条,按平台分层抽样并轮转交错}。
# 【关键逻辑】遍历 THINK2_OUTPUT 下 batch_*.jsonl 逐行解析;按 note_id 去重;
#   platform 为 "?" 的脏数据跳过;publish_time 经 _sane_publish_date 卫生;
#   最后经 _stratified_sample 分层抽样。
@app.route("/api/sentiment_posts")
def api_sentiment_posts():
    """Get recent sentiment posts from collected data (all platforms, sampled).

    Query params:
      days=N              只看最近 N 天(按发布时间)
      since=YYYY-MM-DD    只看该日之后(优先级高于 days)
      platform=<key>      只看该平台(空/缺省=全部)。**必须由后端筛**,理由见下方注释。
    """
    if not THINK2_OUTPUT or not THINK2_OUTPUT.exists():
        return jsonify({"_meta": {}, "posts": []})

    days = request.args.get("days", 0, type=int)
    since = request.args.get("since", "")
    # 【2026-09-17 新增】平台筛选。**为什么不在前端筛**:接口只回最多 200 条
    #   (全局分层抽样),前端手里没有"某平台的完整列表"。若在前端按 platform 过滤,
    #   切到抖音只会看到它被抽中的那 ~33 条(而实际采到 877 条)、切到小红书只剩几条,
    #   用户会直接判成"这个平台没采到" —— 正是 09-15 那次"抽样饿死弱源"的同一个坑,
    #   只不过这次是筛选器自己制造出来的。所以筛选下推到后端:选中某平台时,
    #   200 条名额**全部**留给该平台。
    platform = (request.args.get("platform", "") or "").strip()  # 【变量】平台筛选键(空=全部平台)

    # Calculate cutoff date
    cutoff = None
    if since:
        with contextlib.suppress(ValueError):
            cutoff = datetime.strptime(since, "%Y-%m-%d")
    elif days > 0:
        cutoff = datetime.now() - timedelta(days=days)
    cutoff_str = cutoff.strftime("%Y-%m-%d") if cutoff else None  # 【变量】窗口下界(ISO 字符串,可直接与日期比)

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
                    # 【日期卫生】不可信的 publish_time(如 UUID 前缀被算出的 2104 年)
                    # 一律判为"日期未知":内容照常展示,但时间置空、且**不计入
                    # all_times** —— 否则 min/max(all_times) 会把页面上的
                    # "数据覆盖 X ~ Y" 直接拉到 2104 年。
                    pub_raw = (d.get("publish_time", "") or "")
                    pub_date = _sane_publish_date(pub_raw)  # 【调用函数】可信 → "YYYY-MM-DD",否则 ""
                    if pub_date:
                        # 窗口下界过滤:ISO 字符串可直接比较,无需再解析(也不怕脏值抛错)
                        if cutoff_str and pub_date < cutoff_str:
                            continue
                        # 【2026-09-17 修】append 必须放在窗口过滤**之后**。原实现先 append
                        #   再判窗口,于是"数据覆盖"的上下界统计的是**见过的所有日期**,
                        #   包含已被窗口剔除的帖子 —— 实测 `days=30` 时 posts_start 仍是
                        #   2016-05-12,而同一份 meta 里 total_posts 却是窗口内的 4674 条,
                        #   两个口径打架;前端徽标因此长期显示一个不属于当前窗口的起点。
                        #   (posts_end 看不出来,只因为未来日期已被 _sane_publish_date 拦掉。)
                        all_times.append(pub_date)
                    # 【2026-09-18】评论型记录(抖音)的 title 是"所属视频标题", 真正的内容在
                    #   desc; 见 _split_post_text 的说明。拆成"主体 + 附注"两段再入列。
                    _body, _parent = _split_post_text(d)
                    posts.append(
                        {
                            "platform": d.get("platform", "?"),
                            "author": (d.get("author_name", "") or "")[:20],
                            "fans": d.get("author_fans", 0),
                            "title": _body,
                            "parent_title": _parent,
                            "sentiment": d.get("sentiment", "neutral"),
                            "score": d.get("sentiment_score", 0),
                            "varieties": [v["name"] for v in d.get("varieties", [])[:3]],
                            "likes": d.get("like_count", 0),
                            "time": pub_raw[:16] if pub_date else "",
                            # 【2026-09-18】存量数据里雪球 url 是根相对路径 => 前端点了 404。
                            #   采集侧已修, 但历史数据不回溯, 展示层必须再兜一层。
                            "url": _absolutize_url(d.get("url", ""), d.get("platform", "")),
                            "note_id": d.get("note_id", ""),
                        }
                    )
        except Exception:
            pass
    posts.sort(key=lambda p: p["time"], reverse=True)

    # ── 分层抽样:保证每个平台都露脸(2026-09-15) ──
    # 【为什么】原实现是"全局按发布时间倒序 → 取前 200"。雪球是准实时源,发布时间最新,
    #   于是 187~200 个名额被它一家占满,窗口在第 200 名处切在 2026-09-14 14:08;
    #   微博最新帖 09:00(全局第 281 名)、东财股吧 09:09(第 279 名)、抖音(第 490 名)、
    #   小红书(第 3952 名)全部被挤出 —— 页面上看起来"只剩雪球",但它们的采集其实是好的。
    #   也就是说这不是采集故障,是"抽样把弱源饿死"的展示缺陷。
    # 【做法】改用模块级 _stratified_sample(见其定义处):每平台保底名额 + 轮转交错,
    #   保证任意截断长度下六个平台都可见。抽成模块级纯函数是为了可单测。

    # ── 各平台真实采集量(全量、去重、窗口内)──
    # 【2026-09-17 调整】原先这段包在 `if all_times:` 里,只有"存在可信日期"时才输出。
    #   两个问题:① 新增的平台筛选器要**常显**各平台条数,选中某平台后其余平台的
    #   数字不能消失(否则"换个平台看看"就失去参照);② 这个计数描述的是**采集侧
    #   的真实产出**,与"日期是否可信"完全无关 —— 全部日期不可信时它照样该在。
    #   注意它**不受 platform 筛选影响**:始终是全平台的分布。
    plat_counts = {}
    for _p in posts:
        plat_counts[_p["platform"]] = plat_counts.get(_p["platform"], 0) + 1

    # ── 平台筛选 + 分层抽样 ──
    # 筛选只决定"拿哪些帖子去抽样展示";上面的计数、下面的 posts_start/end 仍是全平台口径。
    scoped = [p for p in posts if p["platform"] == platform] if platform else posts
    sampled = _stratified_sample(scoped, POSTS_API_LIMIT)  # 【调用函数】分层抽样:每平台保底名额 + 轮转交错

    # ── 时间范围元信息 ──
    # 【2026-09-17 修既有缺陷】原实现在 `all_times` 为空时走 `posts: []`,
    #   把**已经收集到的帖子整批丢掉**。触发条件是"这批记录里没有一条可信发布日期",
    #   在日期卫生上线后会真实发生(如 xhs 卡片空壳的脏日期被置空)。
    #   日期不可信只应影响"时间范围"这段元信息,**不该让整个列表变空**。
    meta = {}
    if all_times:
        meta["posts_start"] = min(all_times)
        meta["posts_end"] = max(all_times)
    # 平台真实分布(全平台、去重、窗口内)与抽样后分布分开报:
    # 前者让"某个平台是不是真的没采到"可被直接判断,后者说明这次实际展示了多少。
    plat_sampled = {}
    for _p in sampled:
        plat_sampled[_p["platform"]] = plat_sampled.get(_p["platform"], 0) + 1
    meta["total_posts"] = len(posts)  # 【变量】窗口内全平台去重后总数(不随筛选变)
    meta["platform_counts"] = plat_counts  # 【变量】各平台真实条数(全平台口径)
    meta["platform_sampled"] = plat_sampled  # 【变量】本次实际展示的条数分布
    meta["platform"] = platform  # 【变量】回显当前筛选(空=全部),便于前端判断"当前在看哪个"
    meta["scope_total"] = len(scoped)  # 【变量】当前筛选范围内条数(供"877 条中展示 200 条")
    if cutoff_str:
        meta["filter_since"] = cutoff_str

    return jsonify({"_meta": meta, "posts": sampled})


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


def _kline_points(result: str, variety_name: str | None = None) -> list[dict]:
    """解析 OHLCV CSV + 后复权 → K 线序列(保留 open/high/low/close/volume)。

    【与 _adjusted_price_points 的区别】后者只返回 date+close(折线用), K 线
    图需要完整 OHLCV。raw 解析时补 volume(CSV 第 6 列), 复权逻辑与换月日历
    候选(代码→中文名→主连反查)完全复用 _adjusted_price_points 的算法。
    【返回】[{date, open, high, low, close, volume}] 按日期升序(已后复权)。
    """
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
            pt = {
                "date": parts[0].strip(),
                "open": float(parts[1]),
                "high": float(parts[2]),
                "low": float(parts[3]),
                "close": float(parts[4]),
            }
            # 【2026-10-13 修】原判定 `parts[5].lstrip("-").isdigit()` 只认纯整数串,
            #   而天勤输出的成交量是**浮点串**("1063482.0"), 会被判非法直接丢弃 →
            #   volume 缺键 → 下面 int(p.get("volume", 0)) 兜成 0, 日K 成交量整列恒为 0。
            #   改用 try/float: 整数串/浮点串/科学计数都吃得下, 非数值才丢。
            if len(parts) >= 6 and parts[5].strip():
                try:
                    pt["volume"] = int(float(parts[5]))
                except (TypeError, ValueError):
                    pass
            raw.append(pt)
    if len(raw) < 2:
        return raw
    # 真实日历优先(与 _adjusted_price_points 同候选逻辑): 代码→中文名 / 主连反查 / 代码本身
    cal_dates = None
    if variety_name:
        cal = _load_rollover_calendar()
        code_to_name = {v: k for k, v in NAME_TO_CODE.items()}
        main_index = {
            (entry.get("main_contract") or "").rstrip("0"): key
            for key, entry in (cal or {}).items()
            if entry and (entry.get("main_contract") or "").endswith("0")
        }
        cands = []
        for c in (code_to_name.get(variety_name, variety_name), main_index.get(variety_name), variety_name):
            if c and c not in cands:
                cands.append(c)
        cal_key = next((c for c in cands if c in cal), None)
        if cal_key:
            ro = (cal.get(cal_key, {}) or {}).get("rollover_dates", []) or []
            if ro:
                cal_dates = {r["date"] for r in ro}
    adj, _ = _backward_adjust(raw, calendar_dates=cal_dates)
    return [
        {
            "date": p["date"],
            "open": p["open"], "high": p["high"], "low": p["low"], "close": p["close"],
            "volume": int(p.get("volume", 0)),
        }
        for p in adj
    ]


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


@app.route("/api/kline/<variety>")
def api_kline(variety):
    """K 线数据(2026-09-24 新增, 支持日K/分钟K)。

    【功能】返回指定品种的 K 线序列(open/high/low/close/volume)。
            - period=day(默认): 日 K, akshare 主连日线 + _backward_adjust 后复权;
            - period=1/5/15/30/60: 分钟 K, akshare 新浪分钟接口(主连 code0),
              不复权(主连分钟连续, 跳空小), 每周期约 1023 根。
    【参数】days: 回看天数(默认 180, 钳制 30~730, 仅日K生效);
            period: day/1/5/15/30/60(默认 day); bars: 分钟K返回根数上限(默认 400)。
    【返回】{"_meta": {name, symbol, points, start, end, period, adjusted}, "klines": [...]}。
    """
    days = request.args.get("days", 180, type=int)
    days = max(30, min(days, 730))  # 与 /api/price 同口径
    period = (request.args.get("period") or "day").strip().lower()
    bars = request.args.get("bars", 400, type=int)
    bars = max(50, min(bars, 2000))
    if period in ("1", "5", "15", "30", "60"):
        # ── 分钟 K: akshare 新浪分钟(主连), 不复权 ──
        klines = _kline_minute(variety, period)
        meta = {}
        if klines:
            name = VARIETY_METADATA.get(variety.upper(), {}).get("name", variety.upper())
            meta = {
                "name": name,
                "symbol": variety.upper(),
                "points": len(klines),
                "start": klines[0]["date"],
                "end": klines[-1]["date"],
                "period": f"{period}min",
                "adjusted": False,
            }
            if len(klines) > bars:  # 【容量】分钟数据量大(1分K 1023根), 按 bars 截断保留最近
                klines = klines[-bars:]
                meta["points"] = len(klines)
        return jsonify({"_meta": meta, "klines": klines})
    # ── 日 K: akshare 主连日线 + 后复权 ──
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    result = get_futures_price(variety, start_date, end_date)  # 【调用函数】akshare 主连 CSV
    klines = _kline_points(result, variety)
    meta = {}
    if klines:
        name = VARIETY_METADATA.get(variety.upper(), {}).get("name", variety.upper())
        meta = {
            "name": name,
            "symbol": variety.upper(),
            "points": len(klines),
            "start": klines[0]["date"],
            "end": klines[-1]["date"],
            "period": "day",
            "adjusted": True,
            "adjust_method": "backward",
        }
    return jsonify({"_meta": meta, "klines": klines})


def _kline_minute(symbol: str, period: str) -> list[dict]:
    """akshare 新浪期货分钟 K(主连 code0, 2026-09-24)。

    【接口】ak.futures_zh_minute_sina(symbol='TA0', period='1'/'5'/'15'/'30'/'60')
            → DataFrame[datetime, open, high, low, close, volume, hold],
            每周期固定约 1023 根(新浪上限: 1分≈4天 / 5分≈15天 / 60分≈8个月)。
    【返回】[{date: 'YYYY-MM-DD HH:MM', open, high, low, close, volume}] 升序。
    【2026-10-12 天勤优先】先试天勤(分钟历史深度远大于新浪的 ~1023 根上限);
    未配置账号/取数失败 → 自动回落下面的新浪链路,行为与接入前一致(可逆开关)。
    """
    try:
        from tradingagents.dataflows import tq_source  # 【调用包】天勤适配层(懒导入)
        bars = tq_source.fetch_minute_bars(str(symbol).upper(), str(period), length=2000)
        if bars:
            return bars  # 【返回】天勤已是目标结构(date/open/high/low/close/volume)
    except Exception:  # noqa: BLE001  天勤不可用即降级,不影响原链路
        pass

    import akshare as ak  # 【调用包】懒导入

    try:
        df = ak.futures_zh_minute_sina(symbol=f"{str(symbol).upper()}0", period=str(period))
    except Exception:  # noqa: BLE001  接口异常降级为空(前端空图提示)
        return []
    if df is None or df.empty:
        return []
    out = []
    for _, r in df.iterrows():
        out.append({
            "date": str(r["datetime"])[:16],  # 'YYYY-MM-DD HH:MM'
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "volume": int(r["volume"]),
        })
    try:  # 【2026-10-12】标记实际数据源(走的新浪链路),供前端如实标注
        from tradingagents.dataflows import tq_source
        tq_source.mark_source("akshare")
    except Exception:
        pass
    return out


# 【功能】把价格与情绪按日期对齐,供双轴叠加图使用。
# 【参数】days=回看天数(默认 180,范围 30~730)。
# 【返回】{"_meta": {...}, "overlay": [{date, close?, avg_score?, bullish_ratio?, adaptive?, ...}, ...]}。
# 【关键】以"价格与情绪的日期并集"为坐标轴;某天只有价格或只有情绪时,对应键缺失,
#   前端需自行容错;随后按 start_date 过滤到请求的时间窗内。
# 【2026-10-13 口径变更】x 轴由「价格日期 ∪ 情绪日期」并集**改为只含交易日** ——
#   非交易日(周末/法定假期)的情绪顺延归并到其后第一个交易日(同日多条按帖子数加权)。
#   原因: 并集会让"只有情绪没有价格"的非交易日占位 ⇒ close 为 None ⇒ 前端
#   connectNulls=false 把价格线切断(用户报的"曲线有截断")。价格数据本身并无缺口。
# 【2026-09-24 自适应因子】每个有价格+情绪的日期附一个 adaptive 因子(±1/0),与
#   signal_analyzer.run_adaptive_sentiment 的决策树同构: +1=系统当天"信情绪"顺势(动量),
#   -1="反情绪"逆向(背离/震荡), 0=接飞刀跳过。画在情绪得分 vs 价格图上做第三轴阶梯线。
ADAPTIVE_TREND_WINDOW = 5  # 【变量】自适应因子趋势窗口(与 run_adaptive_sentiment 的 trend_window 对齐)


def _adaptive_factor(trend_pct: float, score: float) -> int:
    """把 run_adaptive_sentiment 的决策树压成每日离散因子(±1/0)。

    【决策树(与 signal_analyzer.run_adaptive_sentiment 同构, 参数一致)】
      · 涨 + 看空背离    → -1(高置信逆向)
      · 跌 + 看多背离    → 强跌(>3%)接飞刀 → 0; 否则 → -1(抄底逆向)
      · 强趋势(>2%)      → +1(顺势, 情绪可信)
      · 弱趋势(<1%)      → -1(震荡市逆向)
      · 其余             → +1(默认顺势)
    【返回】int: +1 / -1 / 0。
    """
    if trend_pct > 1 and score < -0.1:      # 涨 + 看空 → 逆向
        return -1
    if trend_pct < -1 and score > 0.1:      # 跌 + 看多 → 看趋势强度
        return 0 if abs(trend_pct) > 3 else -1
    if abs(trend_pct) > 2:                  # 强趋势 → 顺势
        return 1
    if abs(trend_pct) < 1:                  # 弱趋势/震荡 → 逆向
        return -1
    return 1                                # 默认顺势


def _merge_sentiment_rows(rows: list) -> dict:
    """把同一交易日上的多条自然日情绪行合并成一条(按帖子数加权)。

    【为什么需要】情绪按**自然日**聚合(周末/法定假期也发帖), 而行情只有**交易日**。
    为使「情绪 vs 价格」图的 x 轴只保留交易日, 落在非交易日的情绪要**归并到其后的
    第一个交易日**; 于是一个交易日可能同时收到「当天自身 + 周末两天 + 假期数天」多条
    记录, 必须合并成一条才能挂到该交易日的一个点上。

    【口径】
      · avg_score / simple_avg: 按各自 total_notes **加权平均** —— 帖子多的那天影响更大,
        与"日聚合时每条帖子等权"的原始口径一致(不加权会让只有 1 帖的周末与有 50 帖的
        交易日等权, 明显失真)。
      · 多空比: 先把 bull/bear **计数求和**再相除。**不能对比率再求平均** ——
        那样会忽略各天样本量差异(1 帖 100% 看多 与 50 帖 60% 看多 等权是错的)。
      · total_notes: 求和(该交易日实际覆盖的帖子总数)。
    【退化】若所有天的 note_count 均为 0(缺字段) → 退回**等权平均**, 且不产生除零。
    【返回】dict: avg_score / simple_avg / bullish_ratio / bearish_ratio /
            total_notes / bull_count / bear_count。
    """
    n = len(rows)
    total = sum(int(r.get("total_notes") or 0) for r in rows)

    if total > 0:
        def _wavg(key):
            return sum(float(r.get(key) or 0) * int(r.get("total_notes") or 0)
                       for r in rows) / total
    else:
        def _wavg(key):
            return sum(float(r.get(key) or 0) for r in rows) / n

    bull = sum(int(r.get("bull_count") or 0) for r in rows)
    bear = sum(int(r.get("bear_count") or 0) for r in rows)
    return {
        "avg_score": round(_wavg("avg_score"), 4),
        "simple_avg": round(_wavg("simple_avg"), 4),
        "bullish_ratio": (bull / total) if total else 0,
        "bearish_ratio": (bear / total) if total else 0,
        "total_notes": total,
        "bull_count": bull,
        "bear_count": bear,
    }


@app.route("/api/overlay/<variety>")
def api_overlay(variety):
    """Get price + sentiment data aligned by date for dual-axis overlay chart.
    Query param: days (default 180, range 30-730).
    """
    days = request.args.get("days", 180, type=int)
    days = max(30, min(days, 730))

    # Price data (后复权:消除主力连续换月假跳空,与回测口径一致)
    # 【2026-09-24 自适应因子】取价窗口比展示窗口多取 ADAPTIVE_TREND_WINDOW 天,
    #   供自适应因子计算"前 5 日趋势"用; overlay 展示仍按用户请求窗口截断。
    end_date = datetime.now().strftime("%Y-%m-%d")
    fetch_start = (datetime.now() - timedelta(days=days + ADAPTIVE_TREND_WINDOW + 5)).strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    price_result = get_futures_price(variety, fetch_start, end_date)  # 【调用函数】跨模块获取行情 CSV 文本
    price_points, overlay_roll_dates, overlay_method = _adjusted_price_points(price_result, variety)  # 【调用函数】实时主连 → 后复权序列
    price_map = {p["date"]: p["close"] for p in price_points}  # 【变量】后复权收盘价映射(日期 → 收盘价)
    # 【2026-09-24 自适应因子】预计算每个交易日的前 N 日趋势(百分比), 供逐点算因子
    px_dates = sorted(price_map.keys())  # 【变量】价格日期序列(升序, 趋势索引基准)
    trend_pct_by_date: dict[str, float] = {}  # 【变量】date → 前 ADAPTIVE_TREND_WINDOW 日涨跌幅(%, 复用给所有日期)
    for i in range(ADAPTIVE_TREND_WINDOW, len(px_dates)):
        d_cur, d_prev = px_dates[i], px_dates[i - ADAPTIVE_TREND_WINDOW]
        c_prev = price_map[d_prev]
        if c_prev:
            trend_pct_by_date[d_cur] = (price_map[d_cur] - c_prev) / c_prev * 100

    # Sentiment data
    sent_path = SENTIMENT_DIR / f"{variety}_sentiment.json"
    sentiment_map = {}
    sent_meta = {}
    if sent_path.exists():
        with open(sent_path, encoding="utf-8") as f:
            sd = json.load(f)
        for d in sd["data"]["daily_series"]:
            _n = d.get("note_count", d.get("total_notes", 0)) or 0
            _bull = d.get("bull_count", 0) or 0
            _bear = d.get("bear_count", 0) or 0
            # 【2026-10-13 修】原写法取 d.get("bullish_ratio", 0) **恒为 0** ——
            #   daily_series 里从来没有 bullish_ratio/bearish_ratio 字段(实测 64 个品种
            #   1117 条记录, 字段只有 date/avg_score/simple_avg/note_count/bull_count/
            #   bear_count/weighted_score/platforms/top_authors), 于是 tooltip 的
            #   「多空比」一直显示 0% / 0%。改为由 bull/bear 计数与总帖数现算。
            sentiment_map[d["date"]] = {
                "avg_score": d.get("avg_score", 0),
                "simple_avg": d.get("simple_avg", 0),
                "bullish_ratio": (_bull / _n) if _n else 0,
                "bearish_ratio": (_bear / _n) if _n else 0,
                "total_notes": _n,
                "bull_count": _bull,
                "bear_count": _bear,
            }
        series_dates = sorted(sentiment_map.keys())
        if series_dates:
            sent_meta = {"sentiment_start": series_dates[0], "sentiment_end": series_dates[-1]}

    # ── x 轴 = 交易日; 非交易日的情绪归并到其后第一个交易日 ──────────────────
    # 【2026-10-13 用户拍板改口径】原实现: all_dates = 价格日期 ∪ 情绪日期 的**并集**,
    #   于是"只有情绪、没有价格"的非交易日(周末/法定假期)也占 x 轴位置, 其 close 为
    #   None; 而前端价格序列 connectNulls=false ⇒ **价格线在这些点被切断** ——
    #   即用户报的"曲线有截断"。实测(RB/TA/M/CF/SC/CU/I/J): 缺价日期 100% 是周末或
    #   法定假期(09-25 中秋、10-01/02/05/06/07 国庆), **没有任何交易日缺价**
    #   ⇒ 价格数据本身完整, 属展示口径问题。
    #   改为: x 轴只保留交易日; 落在非交易日的情绪**顺延到其后第一个交易日**
    #   (周末/假期不能交易, 这些帖子反映的情绪归到下一个可交易的时点), 同日多条加权合并。
    import bisect

    window_dates = [d for d in px_dates if d >= start_date]  # 【变量】窗口内交易日(x 轴)
    merged: dict = {}          # 【变量】交易日 → 归并到该日的原始情绪行列表
    sent_self = 0              # 【变量】本来就落在交易日上的情绪日数
    sent_carried = 0           # 【变量】从非交易日顺延过来的情绪日数
    orphan_dates: list = []    # 【变量】找不到窗口内归属交易日的情绪日(仅作透明统计)
    for _sd in sorted(sentiment_map.keys()):
        _i = bisect.bisect_left(px_dates, _sd)
        _t = px_dates[_i] if _i < len(px_dates) else None
        if _t is None or _t < start_date:
            orphan_dates.append(_sd)
            continue
        if _t == _sd:
            sent_self += 1
        else:
            sent_carried += 1
        merged.setdefault(_t, []).append(sentiment_map[_sd])

    overlay = []
    for date in window_dates:
        # 每个点都是交易日 ⇒ 必有收盘价 ⇒ 价格线不再有断点
        point = {"date": date, "close": price_map[date]}
        if date in merged:
            point.update(_merge_sentiment_rows(merged[date]))
        # 【2026-09-24 自适应因子】该交易日有(归并后的)情绪且趋势可算 → 附离散因子
        trend_pct = trend_pct_by_date.get(date)
        if trend_pct is not None and date in merged:
            point["adaptive"] = _adaptive_factor(trend_pct, point["avg_score"])
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
        # 【2026-10-13】x 轴口径改为只含交易日, 非交易日情绪已顺延归并 —— 这几个字段
        #   供前端与排查了解"画了几个交易日、顺延了多少天、丢了多少天"。
        "xaxis_mode": "trading_days",
        "trading_days": len(window_dates),
        "sentiment_days_on_trading_day": sent_self,
        "sentiment_days_carried": sent_carried,
        "sentiment_days_orphan": len(orphan_dates),
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
                if (r.get("status") or "") in ("done", "summarized")]  # 【2026-09-24】归档行含 summarized(历史观点保留)
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
    # 【品种数据库】2026-09-18 起纳入:用户在「品种数据库」页导入的产业库(库存/开工/产量/
    # 排产/裂差/基差)关键读数 —— 分析时基本面分析师会确定性拿到同一份数据,故看板同步展示,
    # 让用户在下单前就能看到"这次分析实际用到的产业数据"。纯本地文件读取(毫秒级)。
    variety_db = {"available": False, "note": "", "slug": None, "name": None,
                  "source": None, "imported_at": None, "symbols": [],
                  "n_sheets": 0, "n_series": 0, "modules": []}

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

    # 品种数据库(本地导入产业库):纯文件读,放在同步段;失败只降级本块
    try:
        from tradingagents.dataflows.variety_db import variety_db_snapshot  # 【调用包】本地品种库快照

        variety_db.update(variety_db_snapshot(code))  # 【调用函数】取关键读数快照(8 模块 × 3 序列)
    except Exception as e:  # 【关键】品种库异常不拖垮看板
        logger.warning("run-input variety_db %s: %s", code, e)
        variety_db["note"] = f"DATA_ERROR: {e}"

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
        "variety_db": variety_db,
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
    # 先扫标题(权重高,命中即返回);再扫正文取**出现 >=2 次**的最长匹配。
    # 【2026-10-08 修复】正文扫描要求 >=2 次: 铂钯研报(#1599)宏观新闻里"原油"
    #   只出现 1 次, 旧逻辑 1 次即命中 ⇒ 误判成 SC 并生成编造的原油观点(SPR/WTI/布伦特)。
    #   标题命中仍 1 次即中: "光伏玻璃周度报告"→FG 这类子品种研报不受影响。
    for kw, code in sorted(_VARIETY_KEYWORD_MAP.items(), key=lambda kv: len(kv[0]), reverse=True):
        if len(kw) < 2:
            continue
        if kw in title:
            return {"variety": code, "direction": "中性", "confidence": 0.4}
        if hay.count(kw) >= 2 and len(kw) > best_len:
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
        ' "report_type":"日报/周报/季报/其它(按研报自述周期;日报=每个交易日发,周报=每周发,季报=按季度发的定期报告如「XX季度运营报告/季报」)",\n'
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
        "被做多的品种记看多、被做空的品种记看空;**同一品种在多个对冲组合中都处于被做空腿"
        "(如标题同时出现\"多PX空PTA\"与\"多PR空PTA\")→ 该品种 direction 判看空,不得判看多**;"
        "标题/小节里的\"成本支撑\"\"供应收缩\"等是涨跌逻辑描述而非方向结论,"
        "不得据此把明确的空头腿翻成看多;仅当正文该品种小节给出明确单边倾向且与价差腿"
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
        # 【2026-10-08 移除 RAG 增强】单篇研报结论必须只基于本篇文本: RAG 拉
        #   同品种历史研报片段会混入其它研报数据, 造成串味(实证 #1599: 铂钯研报
        #   被误判成 SC 后, 结论混入历史原油数据 SPR/WTI/布伦特)。信息不足时
        #   写「研报未披露」, 不引用历史研报补充。
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
            "2b) 研报以对冲/价差形式给方向时(如\"多PX 空PTA\"\"多PR 空PTA\"),该品种段的"
            "方向必须与它在策略中的多空腿一致(被做空腿=看空、被做多腿=看多);同一品种在"
            "多个对冲中都是被做空腿则判看空;已提取结构里的 direction 可能错判了价差腿,"
            "以研报正文的策略表述为准;\"成本支撑\"等逻辑描述不得推翻明确的空头腿。\n"
            f"该品种已提取结构(可能不完整,以研报原文为准):{json.dumps(item, ensure_ascii=False)}\n"
            "---\n研报文本:\n" + text[:12000]
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


# 【功能】周区间工具(2026-09-11 周报优化):周报口径的日期选择从"单日"改为
#           "周起点(周日)",区间 = [周日, 周六]。三处消费(周报总结/单品种总结/
#           逐品种观点周报口径)共用,保证口径一致。
def _week_start(date_str: str) -> str:
    """返回 date_str 所在周的周日(ISO 周日为起点,周六为末):周日起点 = 回退到最近周日。"""
    from datetime import date as _date

    try:
        d = _date.fromisoformat((date_str or "").strip()[:10])
    except (ValueError, TypeError):
        return ""
    # Python weekday(): 周一=0 ... 周日=6;周日为起点 → 回退 (weekday+1) % 7 天。
    days_back = (d.weekday() + 1) % 7
    return (d - __import__("datetime").timedelta(days=days_back)).isoformat()


def _week_range(week_start: str) -> tuple[str, str]:
    """返回 [周起点, 周起点+6] 的 (start, end) 字符串区间(YYYY-MM-DD)。"""
    from datetime import date as _date

    try:
        d = _date.fromisoformat((week_start or "").strip()[:10])
    except (ValueError, TypeError):
        return "", ""
    return d.isoformat(), (d + __import__("datetime").timedelta(days=6)).isoformat()


def _in_week(week_start: str, date_str: str) -> bool:
    """date_str 是否落在 week_start 起 7 天区间内(含边界)。"""
    start, end = _week_range(week_start)
    return bool(start and start <= (date_str or "")[:10] <= end)


# 【功能】季度工具(2026-09-29 季报口径):季报的"日期"从单日改为"季度起点(YYYY-MM-01)",
#           区间 = [Q起点, Q末]。与周报的周起点(_week_start)完全同构 —— 周报归一为周,
#           季报归一为季度;消费方(季报总结/单品种季报总结/观点季度口径)共用同一套口径。
_Q_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "1": 1, "2": 2, "3": 3, "4": 4}
# 季度起点月份:Q1=01, Q2=04, Q3=07, Q4=10
_QUARTER_START_MONTH = {1: 1, 2: 4, 3: 7, 4: 10}


def _quarter_of_title(title: str) -> int:
    """从标题里抠出季度号(1~4);读不到返回 0。

    【关键】优先匹配「Q2 / q2 / 第2季度 / 第二季度」这类**明确季度标识**。
    ⚠️【裸「N季度」也要认 2026-09-29】实盘标题常写成「农业2026年**四季度**策略报告」
    —— **没有"第"字**。旧正则 `第([一二三四1234])季度` 漏掉它,于是回退按发布日
    (9 月 ⇒ Q3)归属,把 Q4 的季报塞进 Q3,并派生出一个"Q4 有数据但点进去空白"的幽灵选项。
    【为什么标题优先】季报标题里的季度是"报告期"语义 ——「2026Q2铁矿季度运营报告」讲的
            是 Q2,即使它 9 月才发布;"海外铅锌矿企业季度运营分析"没有标识,才回退发布日。
    """
    t = title or ""
    m = re.search(r"[Qq]([1-4])(?![0-9])", t)
    if m:
        return int(m.group(1))
    # 先「第N季度」,再无「第」的裸「N季度」——两者都认,裸式在实盘标题里更常见。
    m = re.search(r"(?:第)?([一二三四1234])季度", t)
    if m:
        return _Q_CN_NUM.get(m.group(1), 0)
    return 0


def _year_of_title(title: str) -> int:
    """从标题里抠出**明确的 4 位公历年**(如「农业2026年四季度策略报告」「2026Q2铁矿…」);无则返回 0。

    【为什么需要 2026-09-29】报告期年份也在标题里 ——「2026年四季度」讲的是 2026Q4。
    若只取季度号、年份却用**发布日期**的年份,跨年发布(如 2027-01 发 2026 年四季度报告)
    就会把 2026Q4 错标到发布年。
    【两种写法都认】① `2026年…`;② `2026Q2…`(Q 记号本身就带年份)。
    【防误伤】必须 `(?<![0-9])` 且 20xx 后紧跟「年」或「Q1~Q4」⇒ 合约代码「2601」
    (无后随标识)与日期片段都不会被当成年份。
    """
    t = title or ""
    m = re.search(r"(?<![0-9])(20[0-9]{2})\s*年", t)
    if m:
        return int(m.group(1))
    m = re.search(r"(?<![0-9])(20[0-9]{2})\s*[Qq][1-4]", t)
    return int(m.group(1)) if m else 0


def _quarter_of_date(date_str: str) -> int:
    """按日期算季度号(1~4);非法日期返回 0。"""
    from datetime import date as _date

    try:
        d = _date.fromisoformat((date_str or "").strip()[:10])
    except (ValueError, TypeError):
        return 0
    return (d.month - 1) // 3 + 1


def _quarter_start(date_str: str, title: str = "") -> str:
    """把一篇季报归到"季度起点"(该季度首月 01 日,YYYY-MM-01);无法判定返回 ""。

    【口径】标题优先(报告期语义),标题读不到季度才用发布日期所属季度(与周报
            按发布日归到周起点同构的兜底)。
    """
    from datetime import date as _date

    try:
        d = _date.fromisoformat((date_str or "").strip()[:10])
    except (ValueError, TypeError):
        return ""
    q = _quarter_of_title(title) or _quarter_of_date(date_str)
    if not q:
        return ""
    # 【年份也取标题 2026-09-29】标题声明了报告期就整段采信(年+季),避免跨年发布时
    #   把「2026年四季度」错标到发布年的季度上;标题无年份才回退发布年。
    y = _year_of_title(title) or d.year
    return f"{y:04d}-{_QUARTER_START_MONTH[q]:02d}-01"


def _quarter_range(quarter_start: str) -> tuple[str, str]:
    """返回 [季度起点, 季度末] 的 (start, end) 字符串区间(YYYY-MM-DD)。"""
    from datetime import date as _date

    try:
        d = _date.fromisoformat((quarter_start or "").strip()[:10])
    except (ValueError, TypeError):
        return "", ""
    q = (d.month - 1) // 3 + 1
    start_month = _QUARTER_START_MONTH[q]
    start = _date(d.year, start_month, 1)
    # 季度末 = 下一季度起点前一天
    if q == 4:
        end = _date(d.year, 12, 31)
    else:
        nx = _date(d.year, _QUARTER_START_MONTH[q + 1], 1)
        end = nx - __import__("datetime").timedelta(days=1)
    return start.isoformat(), end.isoformat()


# 【功能】月度工具(2026-10-08 月报口径):月报的"日期"从单日改为"月起点(YYYY-MM-01)",
#           区间 = [月起点, 月末]。与周报(周起点)/季报(季度起点)完全同构 —— 月报总结/
#           单品种月报总结/逐品种月报口径共用同一套口径。


def _month_start(date_str: str) -> str:
    """把一个月报日期归到"月起点"(该月 01 日,YYYY-MM-01);非法日期返回 ""。"""
    from datetime import date as _date

    try:
        d = _date.fromisoformat((date_str or "").strip()[:10])
    except (ValueError, TypeError):
        return ""
    return f"{d.year:04d}-{d.month:02d}-01"


def _month_range(month_start: str) -> tuple[str, str]:
    """返回 [月起点, 月末] 的 (start, end) 字符串区间(YYYY-MM-DD)。"""
    from datetime import date as _date

    try:
        d = _date.fromisoformat((month_start or "").strip()[:10])
    except (ValueError, TypeError):
        return "", ""
    start = _date(d.year, d.month, 1)
    ny, nm = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    end = _date(ny, nm, 1) - __import__("datetime").timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _in_month(month_start: str, date_str: str) -> bool:
    """date_str 是否落在 month_start 所代表的月份区间内(含边界)。"""
    start, end = _month_range(month_start)
    return bool(start and start <= (date_str or "")[:10] <= end)


def _in_quarter(quarter_start: str, date_str: str, title: str = "") -> bool:
    """date_str 是否落在 quarter_start 所代表的季度区间内(含边界)。

    【关键】只用"发布日在区间内"判定会给错误答案:「2026Q2铁矿季度运营报告」9 月发布,
            按日期它落在 Q3 —— 但它是 Q2 季报。因此先看标题里的季度(报告期语义),
            标题与目标季度一致即算命中(其发布日可以不在该区间);标题无季度标识时,
            才回退"发布日期落在区间内"。
    """
    start, end = _quarter_range(quarter_start)
    if not start:
        return False
    ds = (date_str or "")[:10]
    tq = _quarter_of_title(title)
    if tq:
        # 标题已声明报告期:以报告期为准(跨期发布也算命中)。
        # ⚠️年份同样优先取标题(2026-09-29):否则「2026年四季度」跨年发布会对不上。
        ty = _year_of_title(title) or ds[:4]
        return tq == _quarter_of_date(start) and str(ty) == start[:4]
    return start <= ds <= end



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
# 【返回】生效的研报类型('日报'/'周报'/'季报' 或空串,供聚合 JSON 同步使用)。
# 【关键逻辑】LLM 只在给出有效枚举值(日报/周报/季报)时才写回,'其它'不落库(留空待
#           后续自愈),避免把无类型研报错标成日报/周报。
def _self_heal_report_type(db, report_id: int, current: str, structured: dict) -> str:
    cur = (current or "").strip()
    if cur:
        return cur
    rt = str((structured or {}).get("report_type") or "").strip()
    if rt in ("日报", "周报", "季报"):
        # 【收口 2026-09-29】走统一入口:DB + 聚合 JSON 一起写,否则观点总览按类型筛差这一条
        _set_report_type_everywhere(report_id, rt)
        return rt
    return ""


# 【功能】把研报类型写进 DB**并同步到聚合 JSON**(唯一的类型回填入口)。
# 【为什么必须收口】观点总览/QA 读的是聚合 JSON,总结链路读的是 DB。只改 DB 会出现
#           "总结里有、观点总览按同类型筛却查不到"(2026-09-29 实测:存量季报认定的
#           2 条,聚合里 report_type 仍是 '',季报口径整卡空白)。任何回填都必须两边一起。
# 【参数】report_id: 研报主键;rtype: '日报'/'周报'/'季报';codes: 覆盖品种(为空时
#           自动从 DB 行 varieties/variety 取)。
# 【返回】实际生效的类型(非法类型或写库失败返回原值/空)。
# 【关键逻辑】聚合侧是**字段级刷新**(update_research_report_fields),不动其它字段与
#           报告顺序;某品种尚未有该条目时静默跳过(交给 _repair_research_aggregates 补)。
def _set_report_type_everywhere(report_id: int, rtype: str, codes: list[str] | None = None) -> str:
    rt = (rtype or "").strip()
    if rt not in ("日报", "周报", "季报"):
        return ""
    db = get_db()
    with contextlib.suppress(Exception):
        db.update_research_report(report_id, report_type=rt)
    if not codes:
        row = db.get_research_report(report_id) or {}
        codes = [c.strip().upper() for c in str(row.get("varieties") or "").split(",") if c.strip()] \
            or [str(row.get("variety") or "").upper()]
    try:
        from tradingagents.dataflows.research_data import update_research_report_fields  # 【调用包】聚合字段刷新
        for c in codes:
            if c:
                with contextlib.suppress(Exception):
                    update_research_report_fields(c, report_id, {"report_type": rt})
    except Exception as e:  # 聚合侧失败不影响 DB 口径(下次对账会补)
        logger.warning("sync report_type to aggregate failed for %s: %s", report_id, e)
    return rt


# 【功能】把一份研报按品种拆分写各品种聚合 JSON(供 get_research_report / merge_* 读取)。
# 【参数】report_id: 研报主键(作为聚合记录 id);uploaded_at: 上传/发布时间;
#           publish_date: 研报真实发布日期(YYYY-MM-DD,空=未知,消费端回退 uploaded_at);
#           report_type: 研报类型('日报'/'周报'/'季报'/空=未知,前端徽标与每日总结过滤用);
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
                "source": _normalize_source(source),  # 【发行方统一 2026-09-11】
                "uploaded_at": uploaded_at,
                "publish_date": (publish_date or "").strip()[:10],  # 真实发布日期(观点总览日期分组键,缺则消费端回退 uploaded_at)
                "report_type": (report_type or "").strip(),  # 研报类型:日报/周报/季报(空=未知;前端徽标+总结按类型分口径)
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
        if _RAG_WORKER_MODE:  # 【分支】web 主进程:走 RAG 子进程(原生栈隔离)
            n = _rag_call("index_report", timeout_s=1200, report_id=report_id)
        else:
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


def _rag_index_pending(force: bool = False, since_days: int = 0) -> dict:
    """把"已 done 但还没向量"的研报补进 RAG 索引(幂等,可随时重跑)。

    【为什么需要】单篇入库时 _rag_index_report_safely 会即时索引,但以下情况会漏:
      - embedding 模型缺失/损坏期间入库的研报(2026-09-09 真机:模型没装,索引
        全部静默失败,库里 09-07~09-09 的新研报在问答里检索不到);
      - 进程被杀、LLM 链路异常等导致单篇索引失败。
    本函数按"report_id 是否已有向量"增量补齐,是每日/每次采集后的兜底。
    【参数】force: True 时先删旧向量全量重建;since_days: >0 只处理近 N 天入库的。
    【返回】{"done","skipped","failed","total","chunks","reports"}。
    """
    if _RAG_WORKER_MODE:  # 【分支】web 主进程:整段逻辑在 worker 内执行(含 DB 过滤)
        return _rag_call("backfill", timeout_s=7200, force=force, since_days=since_days)

    from tradingagents.rag import is_available, service  # 【调用包】懒导入重依赖

    if not is_available():
        return {"done": 0, "skipped": 0, "failed": 0, "total": 0, "error": "RAG 依赖未就绪"}
    db = get_db()
    rows = [r for r in db.list_research_reports(limit=5000) if r.get("status") == "done"]  # RAG 索引统计: 只数有原文块的行
    if since_days and since_days > 0:
        from datetime import datetime, timedelta

        cutoff = (datetime.now() - timedelta(days=since_days)).strftime("%Y-%m-%d")
        rows = [r for r in rows if (r.get("created_at") or "")[:10] >= cutoff]
    stats = service.backfill_all(rows, force=force)
    status = service.index_status()
    stats.update({"total": len(rows), "chunks": status["chunks"], "reports": status["reports"]})
    logger.info("RAG index pending done: %s", stats)
    return stats


def _rag_delete_vectors_safely(report_id: int) -> None:
    """删除一份研报的全部向量;失败只 warning。"""
    try:
        from tradingagents.rag import is_available  # 【调用包】懒导入(轻量检查)

        if not is_available():
            return
        if _RAG_WORKER_MODE:  # 【分支】web 主进程:走 RAG 子进程
            _rag_call("delete_report", timeout_s=120, report_id=report_id)
        else:
            from tradingagents.rag import service  # 【调用包】懒导入重依赖

            service.delete_report(report_id)
    except Exception as e:
        logger.warning("RAG delete vectors failed for report %s: %s", report_id, e)


def _rag_recent_meta_provider():
    """RAG 保底通道元数据注入(2026-09-22,web 主进程直调路径)。
    与 rag_worker._recent_meta_provider 同语义:SQLite 轻量 4 列,替代每次检索
    全库拉 Chroma metadata(占检索 72% 耗时,随库线性恶化)。失败回退空 dict。
    【2026-09-24 归档】补 summarized 行:research_summaries 每组首篇 rid 的元数据
    (publish_date=组日期, varieties=品种)。否则整月复盘时 9 月上旬已归档的总结
    卡片不进保底通道、被语义池里分数更高的长原文挤出(实测覆盖 09-06 起,前两周全丢)。
    """
    try:
        rows = get_db().list_done_report_meta()
    except Exception:  # noqa: BLE001  注入失败回退 service 旧路径
        return {}
    out: dict[int, dict] = {}
    for r in rows:
        out[int(r["id"])] = {
            "publish_date": r["publish_date"] or "",
            "varieties": r["varieties"] or "",
            "report_type": r["report_type"] or "",
        }
    try:
        for s in get_db().list_research_summaries():
            try:
                rids = json.loads(s.get("report_ids") or "[]")
            except Exception:
                continue
            if not rids:
                continue
            rid0 = int(rids[0])
            var = s.get("variety") or ""
            pd = s.get("publish_date") or ""
            if rid0 in out:
                # 【2026-09-24】同一 rid 可能是多品种研报被拆成多组(TA/PX/PF...)
                # 的首篇 ⇒ varieties 做并集, 否则被后写组覆盖成单个品种,
                # _merge_recent_reports 按品种过滤时会把该 rid 整行滤掉(TA 卡片
                # 进不了保底, 整月复盘 9 月上旬覆盖不到)。
                merged = sorted({x for x in (out[rid0].get("varieties") or "").split(",") if x} | {var})
                out[rid0]["varieties"] = ",".join(merged)
                if not out[rid0].get("publish_date") or pd > out[rid0].get("publish_date", ""):
                    out[rid0]["publish_date"] = pd
            else:
                out[rid0] = {"publish_date": pd, "varieties": var, "report_type": "总结"}
    except Exception:  # noqa: BLE001  summarized 补注失败不影响主通道
        pass
    return out


def _rag_context_for_variety(
    query_text: str,
    variety: str | None = None,
    limit: int = 4,
    exclude_report_id: int | None = None,
) -> str:
    """检索同品种历史研报片段并拼成提示词参考块;未启用/失败返回 ""。"""
    try:
        from tradingagents.rag import is_available  # 【调用包】懒导入(轻量检查)

        if not is_available():
            return ""
        if _RAG_WORKER_MODE:  # 【分支】web 主进程:走 RAG 子进程(冷启动含模型加载,超时放宽)
            return _rag_call(
                "context",
                timeout_s=240,
                query_text=query_text,
                variety=variety,
                limit=limit,
                exclude_report_id=exclude_report_id,
            )
        from tradingagents.rag import service  # 【调用包】懒导入重依赖

        return service.context_for_variety(
            query_text, variety=variety, limit=limit, exclude_report_id=exclude_report_id,
            recent_meta_provider=_rag_recent_meta_provider,
        )
    except Exception:
        return ""


# 【功能】RAG 子进程客户端:把 torch/sentence-transformers/chromadb 原生栈隔离到
#          rag_worker.py 子进程,主进程只做 JSON 行协议收发。worker 崩溃
#          (access violation 等)不再带走 web 服务,下次调用自动重启。
#          【背景】2026-09-10 两天内 web 进程崩 3 次,fault handler 均记录
#          "Windows fatal exception: access violation",崩点在原生层。
# 【模式开关】仅 web 服务主入口(__main__)置 True;采集器等"import web_app"
#          的子进程保持 False,沿用进程内直调(行为与历史版本一致)。
# 【协议】请求 {"id","op","args"} / 响应 {"id","ok","result"|"error"},
#          每行一个 JSON;单 worker 顺序应答,客户端用 RLock 串行发送。
# 【变量】True 时上方各 _rag_* 包装走子进程。
# 【为什么读环境变量 2026-09-14】web_app.py 以脚本方式启动时模块名是 __main__;
#   同一进程内若再有人 `from web_app import ...`(调度器 job,或 /api/research/collect
#   在线程里导入采集器、采集器内部又 `from web_app import ...`),Python 会**再导入
#   一份副本**——那份副本的 __name__ 不是 __main__,拿不到 __main__ 里设的开关,
#   默认值 False 生效 → RAG 在 web 主进程内加载 torch/chromadb,把 09-10 好不容易
#   隔离出去的原生栈又请了回来(09-14 实测主进程确实加载了 chromadb_rust_bindings.pyd
#   与 torch_cpu.dll,随后服务静默停止监听)。改用环境变量做进程级标记,
#   同进程里的任何一份副本都能读到 True。
_RAG_WORKER_MODE = os.environ.get("AGENTSENSE_WEB_SERVER") == "1"
_rag_proc = None  # 【变量】worker 子进程句柄
_rag_req_seq = 0  # 【变量】请求自增 id
_rag_futures: dict[int, "queue.SimpleQueue"] = {}  # 【变量】id → 应答队列
_rag_call_lock = threading.RLock()  # 【锁】串行化 spawn+发送(worker 顺序应答)
_rag_last_ok = 0.0  # 【变量】最近一次成功应答时间戳(卡死 watchdog)


def _rag_json_default(o):
    """numpy 标量/数组兜底序列化(主进程尽量不感知 numpy)。"""
    try:
        import numpy as _np  # noqa: PLC0415  【调用包】延迟导入

        if isinstance(o, _np.generic):
            return o.item()
        if isinstance(o, _np.ndarray):
            return o.tolist()
    except Exception:  # noqa: BLE001
        pass
    return str(o)


def _rag_worker_alive() -> bool:
    return _rag_proc is not None and _rag_proc.poll() is None


def _rag_read_loop():
    """后台线程:持续读 worker 应答行,按 id 分发;EOF 时给等待者发失败。"""
    try:
        for _raw in _rag_proc.stdout:
            line = _raw.strip()
            if not line:
                continue
            try:
                resp = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            q = _rag_futures.pop(resp.get("id"), None)
            if q is not None:
                q.put(resp)
    finally:
        for q in list(_rag_futures.values()):
            q.put({"id": None, "ok": False, "error": "RAG worker 进程已退出"})
        _rag_futures.clear()


def _rag_spawn_worker() -> None:
    global _rag_proc
    script = Path(__file__).resolve().parent / "rag_worker.py"
    err_log = open(script.parent / "rag_worker.err.log", "ab")  # noqa: SIM115 worker 生命周期=进程生命周期
    _rag_proc = subprocess.Popen(
        [sys.executable, str(script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=err_log,
        cwd=str(script.parent),
        text=True,  # 【协议】文本模式(Windows 默认二进制管道,UTF-8 行协议)
        encoding="utf-8",
    )
    threading.Thread(target=_rag_read_loop, daemon=True, name="rag-worker-reader").start()


def _rag_kill(kill: bool = True) -> None:
    """丢弃当前 worker 句柄(kill=True 时强杀进程);下次 _rag_call 会重启。"""
    global _rag_proc
    proc, _rag_proc = _rag_proc, None
    if proc is None:
        return
    if kill and proc.poll() is None:
        with contextlib.suppress(Exception):
            proc.kill()
    with contextlib.suppress(Exception):
        proc.stdin.close()


def _rag_call(op: str, timeout_s: float = 120.0, **args):
    """向 RAG worker 发一个请求并等待应答;失败/超时抛 RuntimeError。

    【超时语义】超时只让本次调用失败,不杀 worker——它可能只是在慢慢跑
    全量重建(force backfill);但若 30 分钟内 worker 从未成功应答过任何
    请求,判定卡死,强杀以便下次调用自动重启。
    """
    global _rag_req_seq, _rag_last_ok
    with _rag_call_lock:
        if not _rag_worker_alive():
            _rag_kill(kill=False)
            _rag_spawn_worker()
        _rag_req_seq += 1
        rid = _rag_req_seq
        q: "queue.SimpleQueue" = queue.SimpleQueue()
        _rag_futures[rid] = q
        payload = json.dumps(
            {"id": rid, "op": op, "args": args}, ensure_ascii=False, default=_rag_json_default
        )
        try:
            _rag_proc.stdin.write(payload + "\n")
            _rag_proc.stdin.flush()
        except Exception as e:  # noqa: BLE001
            _rag_futures.pop(rid, None)
            _rag_kill()
            raise RuntimeError(f"RAG worker 通信失败:{e}") from e
    try:
        resp = q.get(timeout=timeout_s)
    except queue.Empty:
        with contextlib.suppress(Exception):
            _rag_futures.pop(rid, None)
        if _rag_last_ok and time.time() - _rag_last_ok > 1800:
            _rag_kill()
            raise RuntimeError("RAG worker 疑似卡死已强杀,下次调用自动重启") from None
        raise RuntimeError(
            f"RAG worker 响应超时({int(timeout_s)}s,可能正在执行全量重建,请稍后重试)"
        ) from None
    _rag_last_ok = time.time()
    if not resp.get("ok"):
        raise RuntimeError(str(resp.get("error") or "RAG worker 内部错误"))
    return resp.get("result")


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
# 【在跑登记】report_id → 进入处理的单调时刻(monotonic,不受系统时间调整影响)。
# 【为什么需要】研报的 processing 状态是**跨进程**的:采集器(独立子进程)自己 insert
# 再同步处理,它崩溃后行会永远停在 processing,而 web 进程对"这份行是否正被别的进程
# 处理中"一无所知 —— 只看 status 分不清「真在跑」与「僵尸行」。本登记表让 web 侧的
# 僵尸回收线程能精确排除"本进程正在处理"的行;跨进程的"真在跑"则靠 DB 列
# processing_started_at(处理起点)判年龄。两者合起来才不会误杀活跃任务。
_INFLIGHT_REPORTS: dict[int, float] = {}
_INFLIGHT_LOCK = threading.Lock()


def _inflight_enter(report_id: int) -> None:
    """登记"本进程正在处理该研报"(进入时调用)。"""
    with _INFLIGHT_LOCK:
        _INFLIGHT_REPORTS[report_id] = time.monotonic()


def _inflight_exit(report_id: int) -> None:
    """注销登记(处理出口调用,无论成功失败,放在 finally)。"""
    with _INFLIGHT_LOCK:
        _INFLIGHT_REPORTS.pop(report_id, None)


def _inflight_ids() -> set[int]:
    """当前本进程在跑的研报 id 快照(僵尸回收线程用它做"排除")。"""
    with _INFLIGHT_LOCK:
        return set(_INFLIGHT_REPORTS)


class _VarietyPoolSkip(Exception):
    """【正常过滤,非失败】识别出的品种**全部**在当前品种池外 ⇒ 该研报按「已跳过」处理。

    【为什么单独一类】2026-09-22 实测:东证一批 7 条 `status=error` 全是池外品种
    (JD 鸡蛋 / SA 纯碱 / JM,J,CU / TL,TF,T 国债),这是白名单的**预期行为**,却在
    研报库标红、计入"失败"数 ⇒ 真失败被噪声淹没(用户误以为链路坏了)。
    单列一类后落 `status="skipped"`,前端显示灰色「已跳过」且不计入失败数。

    【口径】与"品种闸门"同源:识别出的品种**至少 1 个在池**即放行,见
    `_process_research_report_inner`。⚠️ 下游一律按 `status=='done'` 取数
    (RAG / 聚合 JSON / 各脚本),所以 skipped 天然不进总结与观点链路。
    """


def _process_research_report(report_id: int):
    """【入口包装】登记在跑状态 + 写处理起点 + 兜住**任何**异常,不留 processing 僵尸。

    【2026-09-17 加固】原实现把"读库 + 取品种"放在 try **之外**(见下 inner 函数),
    任一步抛错都会冒出函数、冒出采集器的 `_handle` —— 后果是**已 insert 的研报行
    永久停在 processing**(前端表现就是"一直显示处理中",且没有任何 error 落库,
    连告警都查不出来)。这里把整段纳入兜底,并统一出口。

    【参数】report_id: research_reports 主键。
    【返回】无。状态最终必然推进到 done / error(除非进程被原生崩溃直接终止)。
    """
    _inflight_enter(report_id)
    db = get_db()
    with contextlib.suppress(Exception):
        # 【处理起点】UTC,与库内 created_at 同域(datetime('now') 也是 UTC),可直接比大小。
        # 必须尽力写:它是"这份行到底有没有人在处理"的唯一跨进程判据。
        db.update_research_report(
            report_id,
            processing_started_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        )
    try:
        _process_research_report_inner(report_id)
    except Exception as e:  # noqa: BLE001 - 兜底:绝不让行留在 processing
        logger.exception("Research processing crashed for report %s", report_id)
        with contextlib.suppress(Exception):
            db.update_research_report(
                report_id, status="error", error=f"处理进程异常(未进业务 except): {e}"[:2000]
            )
    finally:
        _inflight_exit(report_id)


def _process_research_report_inner(report_id: int):
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
        # 【品种池】2026-09-09 起只入库 ACTIVE_VARIETIES(21 品种)覆盖的研报;
        # 池外品种研报(如 RB/I)标 error 跳过,不进总结/聚合(存量行保留不删)。
        if not (set(codes_all := [str(v.get("variety", "")).upper() for v in varieties]) & ACTIVE_VARIETIES):
            pool_hint = "、".join(sorted(ACTIVE_VARIETIES))
            # 【2026-09-22】改抛专用异常 ⇒ 落 status="skipped"(灰色「已跳过」),不再
            # 冒充 error:池外过滤是白名单的预期行为,不是链路故障。
            raise _VarietyPoolSkip(
                f"识别品种 {','.join(codes_all)} 均不在当前品种池内(仅支持: {pool_hint}),已跳过入库"
            )
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
    except _VarietyPoolSkip as e:
        # 【正常过滤,非失败】品种全在池外 ⇒ 记 skipped;前端灰色「已跳过」且不计入失败数。
        # 用 logger.info 而非 exception:这是预期行为,不该在日志里留 traceback。
        logger.info("Research %s skipped (variety pool): %s", report_id, e)
        with contextlib.suppress(Exception):
            db.update_research_report(report_id, status="skipped", error=str(e)[:2000])
    except Exception as e:
        logger.exception("Research processing failed for report %s", report_id)
        with contextlib.suppress(Exception):
            db.update_research_report(report_id, status="error", error=str(e)[:2000])


# ── 僵尸研报回收(2026-09-17)────────────────────────────────────────────
# 【要解决什么】研报的 processing 是个**跨进程**的中间态:采集器在独立子进程里
# insert 后同步处理,一旦该进程原生崩溃(access violation 之类,接不住 try/except),
# 行就永久停在 processing —— 前端看到的就是"十几篇一直显示处理中",而且**没有任何
# error 落库、没有告警**(2026-09-15~17 累积 15 篇,直到手动重启服务才被捡起)。
# 原实现只有"服务启动时扫一次",所以只要不重启就无人回收。这里补齐两条:
#   1) `_sweep_stuck_research_reports`:统一回收逻辑(启动扫描 + 周期扫描共用);
#   2) `_start_research_stuck_watchdog`:周期性(默认 2 分钟)自动回收。
# 【判据】"本进程没在跑它"(_inflight_ids)且"处理起点已过阈值"(DB.processing_started_at)
# —— 两个条件同时成立才回收,避免把活跃任务抢成两份并行处理(会双花 LLM 额度)。

_RESEARCH_HEAL_EXECUTOR: ThreadPoolExecutor | None = None
_RESEARCH_HEAL_LOCK = threading.Lock()


def _research_stuck_threshold_minutes() -> float:
    """僵尸判定阈值(分钟):processing 停留超过它即视为处理者已死(可环境变量覆盖)。

    【取值理由】单篇研报端到端 ≈5.5 分钟(LLM 两步占大头),最慢的 RAG 冷启动也在
    15 分钟内;30 分钟给足余量 —— 既不误杀活跃任务,又能在半小时内自愈。
    """
    with contextlib.suppress(Exception):
        return max(5.0, float(os.environ.get("RESEARCH_STUCK_MINUTES", "30")))
    return 30.0


def _research_row_age_minutes(row: dict, now: datetime) -> float:
    """该研报行"停在当前状态"的时长(分钟);时间戳缺失/无法解析返回 +inf(视为陈旧)。

    【关键逻辑】优先 processing_started_at(处理真正的起点),老行/采集器未写时回退
    created_at;库内两者都是 UTC 无时区字符串(SQLite datetime('now')),同域可直接比。
    """
    for key in ("processing_started_at", "created_at", "uploaded_at"):
        raw = (row.get(key) or "").strip()
        if not raw:
            continue
        try:
            ts = datetime.strptime(raw.replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        ts = ts.replace(tzinfo=timezone.utc)
        return max(0.0, (now - ts).total_seconds() / 60.0)
    return float("inf")


def _research_heal_executor() -> ThreadPoolExecutor:
    """惰性创建回收专用线程池(3 并发,低于 LLM 账户并发上限 5)。"""
    global _RESEARCH_HEAL_EXECUTOR
    with _RESEARCH_HEAL_LOCK:
        if _RESEARCH_HEAL_EXECUTOR is None:
            _RESEARCH_HEAL_EXECUTOR = ThreadPoolExecutor(
                max_workers=3, thread_name_prefix="research-heal"
            )
        return _RESEARCH_HEAL_EXECUTOR


def _reprocess_queued_report(rid: int) -> None:
    """线程池任务体:重跑一篇(单篇失败不影响其余)。"""
    try:
        _process_research_report(rid)
    except Exception:  # noqa: BLE001
        logger.warning("stuck reprocess failed for %s", rid, exc_info=True)


def _sweep_stuck_research_reports(*, reason: str, at_startup: bool) -> list[int]:
    """回收"状态卡在中间态、但已无处理者"的研报行并重新入队,返回被回收的 id 列表。

    【参数】reason: 触发来源(留痕用,如 "startup"/"watchdog");
            at_startup: True=服务启动时扫描 —— 启动保护保证同一时刻只有一个实例在监听
                        :5000,本进程尚无任何处理线程,凡停在 processing/pending 的行
                        必是上个进程留下的孤儿,**一律回收**(与 09-14 既定口径一致);
                        False=周期扫描 —— 本进程可能正在处理别的行,必须用"在跑登记
                        + 处理起点年龄"双重排除,否则会把活跃任务抢成两份并行处理。
    【返回】被回收的 report_id 列表(无则空表)。
    【关键逻辑】先统一置回 pending(前端显示"排队中"),再由池逐条认领 —— 认领到哪条
                哪条才变 processing,列表里的"处理中/排队中"与真实并发一一对应。
    """
    db = get_db()
    now = datetime.now(timezone.utc)
    threshold = _research_stuck_threshold_minutes()
    inflight = set() if at_startup else _inflight_ids()
    try:
        rows = db.list_research_reports(None, limit=2000)
    except Exception:  # noqa: BLE001
        logger.warning("research stuck sweep: list failed (reason=%s)", reason, exc_info=True)
        return []
    reclaimed: list[int] = []
    for r in rows:
        rid = r.get("id")
        st = (r.get("status") or "").strip()
        if rid is None or st not in ("processing", "pending"):
            continue
        if rid in inflight:  # 本进程正在处理,绝不碰
            continue
        if not at_startup:
            # 周期扫描只回收 processing:pending 只由本进程的批量导入写入,排队中的行
            # 同样不在在跑登记里,按年龄判会把它抢成两份并行处理(见 09-14 并发口径)。
            if st != "processing":
                continue
            if _research_row_age_minutes(r, now) < threshold:
                continue  # 还年轻:可能正被采集器进程处理中
        reclaimed.append(int(rid))
    if not reclaimed:
        return []
    for rid in reclaimed:
        with contextlib.suppress(Exception):
            db.update_research_report(rid, status="pending")
    ex = _research_heal_executor()
    for rid in reclaimed:
        ex.submit(_reprocess_queued_report, rid)
    logger.warning(
        "research stuck sweep: reclaimed %d report(s) reason=%s at_startup=%s ids=%s",
        len(reclaimed), reason, at_startup, reclaimed,
    )
    print(f"Research stuck sweep ({reason}): re-queued {len(reclaimed)} report(s): {reclaimed[:20]}")
    return reclaimed


def _start_research_stuck_watchdog(interval_seconds: int = 120) -> None:
    """启动僵尸研报的周期回收线程(daemon,随主进程退出)。

    【为什么必须有】启动扫描只覆盖"上一次进程留下的"孤儿;采集器是**独立子进程**,
    它崩溃留下的 processing 行在 web 主进程不重启的情况下**永远无人回收** ——
    实测 09-15~17 累积 15 篇一直显示"处理中"。周期性回收把自愈时间从"下次重启"
    压到 ≤(阈值 + 扫描周期)。
    """
    def _loop():
        while True:
            time.sleep(max(30, interval_seconds))
            try:
                _sweep_stuck_research_reports(reason="watchdog", at_startup=False)
            except Exception:  # noqa: BLE001 - 守护线程绝不能因异常退出
                logger.warning("research stuck watchdog sweep failed", exc_info=True)

    threading.Thread(target=_loop, name="research-stuck-watchdog", daemon=True).start()
    print(
        f"Research stuck watchdog started (interval={interval_seconds}s, "
        f"threshold={_research_stuck_threshold_minutes()}min)"
    )


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


# 【变量】文件夹导入上限(2026-09-30 新增): 单次最多文件数与总字节。
#   为什么单设而不复用 RESEARCH_MAX_SIZE(20MB/单文件): 上传文件夹是**批量**场景,
#   几十篇 PDF 一次传很常见; 限制的是"总量", 单文件仍按 RESEARCH_MAX_SIZE 卡。
FOLDER_UPLOAD_MAX_FILES = 200        # 【变量】单次文件夹上传的最大文件数(防误拖整个盘)
FOLDER_UPLOAD_MAX_TOTAL = 500 * 1024 * 1024  # 【变量】单次总字节上限 500MB


@app.route("/api/research/upload-folder", methods=["POST"])
def api_research_upload_folder():
    """批量上传本地文件夹里的 PDF 研报(浏览器拖拽/选择文件夹), 复用本地导入链路。

    【为什么要这个端点】原先"本地导入"只支持服务端能直读的绝对路径(前端填
    D://研报//xx), 换个电脑/部署方式就用不了, 且用户要自己复制路径。现改为浏览器
    端读取文件夹 → multipart 上传 → 服务端落**唯一临时目录** → 调
    ingest_local_pdfs.ingest_dir(与路径模式**同一个函数**), 处理逻辑零重复。

    【表单】files(必填, 可多个同名键) + org(可选) + varieties(可选, JSON 数组串)。
    【返回】{"status":"collecting","files":N,"message":...}; 失败显式给 error。
    【并发】复用全局 _research_collecting 闸门: 采集/导入同一时刻只跑一个,
            否则两批 LLM 同时打会互相抢并发额度(LLM 并发上限 5)。
    【清理】临时目录在后台线程 finally 里删; 必须**等 ingest_dir 返回**再删 ——
            ingest_dir 是"先全批登记(抽正文+复制原件进库), 再多线程处理", 登记
            阶段就要读这些文件, 提前删会让整批登记失败。
    """
    global _research_collecting
    if _research_collecting:
        return jsonify({"status": "busy", "message": "研报采集正在进行中, 请稍候"}), 409

    files = request.files.getlist("files")
    # 【兼容】某些前端会把单文件放在 file 键(而非 files) —— 一并收, 避免静默 0 篇
    if not files:
        single = request.files.get("file")
        if single:
            files = [single]
    files = [f for f in files if f and f.filename]
    if not files:
        return jsonify({"error": "未收到任何文件(请拖入文件夹或选择 PDF)"}), 400
    if len(files) > FOLDER_UPLOAD_MAX_FILES:
        return jsonify({"error": f"一次最多 {FOLDER_UPLOAD_MAX_FILES} 个文件, 本次 {len(files)} 个"}), 400

    org = (request.form.get("org") or "").strip() or "本地导入"
    # 【品种筛选】前端传 JSON 数组字符串; 解析失败按"未筛选"处理并显式提示,
    # 不静默忽略 —— 否则用户以为筛了品种、实际全量跑。
    requested: set[str] | None = None
    raw_v = request.form.get("varieties")
    if raw_v:
        try:
            parsed = json.loads(raw_v)
            if isinstance(parsed, list) and parsed:
                requested = {str(v).strip().upper() for v in parsed if str(v).strip()}
                unknown = requested - ACTIVE_VARIETIES
                if unknown:
                    return jsonify({"error": f"未知品种代码: {', '.join(sorted(unknown))}"}), 400
                requested = requested or None
        except (ValueError, TypeError):
            return jsonify({"error": "varieties 参数格式错误(应为 JSON 数组)"}), 400

    # 【校验并落盘】逐个查扩展名与体积; 有任何一条不合格就整批拒绝(先整体校验再落盘),
    # 避免"落了一半才发现超限"造成半批脏数据。
    total = 0
    for f in files:
        ext = os.path.splitext(f.filename)[1].lower()
        if ext != ".pdf":
            return jsonify({"error": f"仅支持 PDF, 「{f.filename}」不是({ext or '无扩展名'})"}), 400
        f.stream.seek(0, os.SEEK_END)
        size = f.stream.tell()
        f.stream.seek(0)
        if size <= 0:
            return jsonify({"error": f"「{f.filename}」是空文件"}), 400
        if size > RESEARCH_MAX_SIZE:
            return jsonify({"error": f"「{f.filename}」超过 20MB 单文件上限"}), 400
        total += size
    if total > FOLDER_UPLOAD_MAX_TOTAL:
        return jsonify({"error": f"本次共 {total // 1048576}MB, 超过 {FOLDER_UPLOAD_MAX_TOTAL // 1048576}MB 总上限"}), 400

    import uuid  # 【调用包】唯一临时目录名(与全局笔记一致: 固定名多进程/多请求必撞 WinError 32)
    from werkzeug.utils import secure_filename  # 【调用包】文件名安全化(防路径穿越)

    tmp_dir = Path(tempfile.gettempdir()) / f"agentsense_folder_{uuid.uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    seen = set()   # 【变量】同名文件去重: 拖入多子目录时常有同名 PDF, 后写会覆盖前者
    for f in files:
        safe = secure_filename(f.filename) or "report.pdf"
        if not safe.lower().endswith(".pdf"):
            safe += ".pdf"
        base, ext = os.path.splitext(safe)
        n = 1
        while safe in seen:
            safe = f"{base}_{n}{ext}"
            n += 1
        seen.add(safe)
        try:
            f.save(str(tmp_dir / safe))
            saved += 1
        except OSError as e:
            # 单文件写失败(磁盘满/长路径)不整批失败, 但要记下来并在返回里说明
            logger.warning("Folder upload save failed for %s: %s", f.filename, e)
    if not saved:
        with contextlib.suppress(Exception):
            tmp_dir.rmdir()
        return jsonify({"error": "所有文件写入失败(磁盘空间或路径问题), 请查看服务日志"}), 500

    _research_collecting = True

    def _run():
        global _research_collecting
        from ingest_local_pdfs import ingest_dir  # 【调用包】本地 PDF 导入(与路径模式同一入口)
        try:
            result = ingest_dir(tmp_dir, org, requested or set(), False)
            logger.info("Research folder upload (%d files) done: %s", saved, result)
            if result.get("errors"):
                with contextlib.suppress(Exception):
                    get_db().create_alert(
                        "research_error",
                        "Folder upload partial errors",
                        "; ".join(str(x) for x in result["errors"][:5])[:300],
                    )
        except Exception as e:
            logger.exception("Folder upload ingest failed: %s", e)
            with contextlib.suppress(Exception):
                get_db().create_alert("research_error", "Folder upload failed", str(e)[:300])
        finally:
            # 【必须】ingest_dir 返回后才删: 其"登记阶段"要读原件复制进库
            with contextlib.suppress(Exception):
                shutil.rmtree(tmp_dir, ignore_errors=True)
            _research_collecting = False

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({
        "status": "collecting",
        "files": saved,
        "message": f"已接收 {saved} 个 PDF, 后台登记与提取中(可离开本页)",
    })


@app.route("/api/research/<int:rid>/reprocess", methods=["POST"])
def api_research_reprocess(rid: int):
    """重跑单篇研报的 LLM 处理链路(2026-09-10 新增)。

    【场景】服务被强杀/崩溃导致 processing 僵尸、或 error 的行想重试——
    _process_research_report 幂等,会重新提取文本并覆盖状态(done/error)。
    【返回】{"id": rid, "status": "processing"};404=研报不存在。
    """
    db = get_db()
    report = db.get_research_report(rid)
    if not report:
        return jsonify({"error": f"研报 {rid} 不存在"}), 404
    if not report.get("file_path") or not Path(report["file_path"]).exists():
        return jsonify({"error": f"研报 {rid} 的原文件缺失,无法重跑"}), 400
    threading.Thread(target=_process_research_report, args=(rid,), daemon=True).start()
    return jsonify({"id": rid, "status": "processing", "message": "已重新入队处理"})


# ── 一键修复「观点生成失败」占位(2026-10-08): 研报库卡片按钮 → 后台重跑全部占位研报观点 ────

@app.route("/api/research/reconclude-stuck/status")
def api_research_reconclude_stuck_status():
    """返回当前「观点生成失败」占位研报数(供前端徽标显示 / 按钮可见性)。

    【口径】status='done' 且 conclusion_md 含占位(采集/入库成功但观点生成失败),
    与 scripts/reconclude_research.py 的 _FAIL_MARKER 一致。
    """
    db = get_db()
    rows = db.list_research_reports(None, limit=5000)
    n = sum(1 for r in rows if r.get("status") == "done"
            and "观点生成失败" in (r.get("conclusion_md") or ""))
    return jsonify({"ok": True, "remaining": n})


@app.route("/api/research/reconclude-stuck", methods=["POST"])
def api_research_reconclude_stuck():
    """一键修复: 后台线程重跑所有「观点生成失败」占位研报的观点(2026-10-08 新增)。

    【为什么后台线程】一次可能几十篇,每篇 30-60s,同步跑会挂起请求 → 立即返回启动结果,
    进度用 GET /api/research/reconclude-stuck/status 轮询。
    【口径】status='done' 且 conclusion_md 含占位;逐篇调 reconclude_research_report
    (只重跑观点步骤,不动品种识别/方向/标题)。
    【返回】{"ok", "found", "message"};found=0 时直接返回不启动线程。
    """
    db = get_db()
    rows = db.list_research_reports(None, limit=5000)
    stuck = [r for r in rows if r.get("status") == "done"
             and "观点生成失败" in (r.get("conclusion_md") or "")]
    if not stuck:
        return jsonify({"ok": True, "found": 0, "message": "没有需要修复的研报"})
    ids = [r["id"] for r in stuck]

    def _run():
        for rid in ids:
            try:
                reconclude_research_report(rid)
            except Exception:
                # 单篇失败不阻塞整批;status 接口仍按占位计数反映剩余
                pass

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "found": len(ids),
                    "message": f"已开始修复 {len(ids)} 篇(后台执行,约每篇 30-60s)"})


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
    """自传数据列表(元数据;variety 过滤可选;**只显示当前客户端上传的**)。

    【2026-10-12 增强】对 done 数据集现算信号提炼块(signals: 通用层 + 领域层 +
    render_md 注入块),附加到返回项供前端渲染"列表行内信号小卡"。信号是纯确定性
    计算(不烧 LLM),只在列表请求时现算、不落库 —— 数据更新后信号自动刷新。
    """
    variety = (request.args.get("variety") or "").strip().upper() or None
    datasets = get_db().list_user_datasets(variety, client_tag=_client_tag())
    db = get_db()
    from tradingagents.dataflows.user_signals import build_signals  # 【调用包】信号引擎
    for x in datasets:
        if x.get("status") != "done":
            x["signals"] = None
            continue
        try:
            full = db.get_user_dataset(x["id"]) or {}
            sig = build_signals(full)
            x["signals"] = {k: sig.get(k) for k in
                            ("schema_version", "refiner", "refiner_label",
                             "auto_registered", "as_of", "stale_days",
                             "common", "domain", "readiness", "render_md")}
        except Exception:
            x["signals"] = None
    return jsonify({"datasets": datasets})


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
        r["source"] = _normalize_source(r.get("source") or "")  # 【发行方统一 2026-09-11】
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


# 【发行方名称统一 2026-09-11 用户定】同一公司的各种写法归一为标准短名:
# 国泰君安(期货有限公司/期货研究所/-API/英文写法等) / 华泰(期货/-天玑/英文) / 东证(期货/-繁微)。
# 【注意】"东方期货"是独立公司,不含"东证"子串,不会被误归;未命中规则的发行方原样保留。
_SOURCE_NORMALIZE_RULES: tuple[tuple[str, str], ...] = (
    ("国泰君安", "国泰君安"),
    ("guotai junan", "国泰君安"),
    ("gtja", "国泰君安"),
    ("华泰", "华泰"),
    ("huatai", "华泰"),
    ("东证", "东证"),
    ("东方期货", "东证"),  # 【2026-09-11 用户确认】本系统数据里"东方期货"即东证的另一写法
    ("dongzheng", "东证"),
    # 【同源归并 2026-09-14】扩充覆盖面:同一个机构在库里存在多种写法,
    #   不改会把"一家公司"当成"多家",直接导致观点总表出现「源数 < 观点数」。
    #   实测全库写法:永安期货研究中心 / 永安期货研究中心能化团队 / 永安期货;
    #                中信期货研究所 / 中信期货有限公司 / 中信期货。
    #   注:只用带"期货"的完整键做子串匹配(不用裸"中信"/"永安"),这样
    #       「中信建投期货」之类的第三方机构不会被误并进来。
    ("永安期货", "永安期货"),
    ("中信期货", "中信期货"),
    # 【同源归并 2026-10-14】隆众:同一品种在多个「口径」下重复出现,用户报"不要重复性录入"。
    #   实测库里 `隆众资讯` / `隆众资讯-聚焦` / `隆众资讯-聚乙烯` / `隆众资讯-成品油` 被当成
    #   4 家机构 ⇒ 同一品种在观点总表里出现多行。
    #   成因:采集端只有一个源(热点资讯频道),`account`(类别)是从**标题前缀**派生的
    #   (`[隆众聚焦]`→聚焦、`隆众能化早读`→早读),入库时被拼成 `隆众资讯-<类别>`。
    #   对策:只加这一条归一规则即可 —— 聚合层 `_merge_same_source_items` 本就按
    #   (品种, 归一后机构) 合并成一条,**不需要**新增去重逻辑(内容不丢)。
    #   子串匹配 `隆众资讯` 覆盖全部 `隆众资讯-*` 变体;不误伤其它机构(全库无他者含此串)。
    ("隆众资讯", "隆众资讯"),
    # 【同源归并 2026-10-14 方案 B】公众号渠道 + 机构多写法收敛。
    #   用户报「不要重复性分析录入」并选定**方案 B:只加归一规则、界面显示干净机构名**
    #   （不改 source 写入逻辑 —— 那条会影响所有通道,含手动上传 PDF 的发行方自动识别）。
    #   为什么只加规则就够:聚合层 `_merge_same_source_items` 本就按
    #   (品种, 归一后发行方) 合并;前端观点表格在读时调 `_normalize_source`
    #   ⇒ 加规则**立刻生效且历史行一并覆盖**,零 DB 改动/零 LLM/零重跑。
    #   实测缺口:同一个号「混沌天成研究」在库里裂成 3 种写法
    #   (公众号-混沌天成研究 / 混沌天成研究 / 混沌天成研究院)。
    #   键取法:用 `混沌天成`(短键)。实测 `混沌天成研究院` 是 `混沌天成研究` + "院",
    #     两种键都能命中;但短键还能覆盖 `混沌天成期货` 等未来变体,更稳。
    #   以下 6 个号已由微信读书通道接入,**未来仍会持续入库** ⇒ 预先归一避免再裂。
    ("混沌天成", "混沌天成"),
    ("恒力期货", "恒力期货"),
    ("紫金天风", "紫金天风"),
    ("物产中大", "物产中大"),
    ("中信建投期货", "中信建投"),  # 用带"期货"的完整键:不误伤"中信建投证券"等其它法人
    ("多空逻辑", "多空逻辑"),
    # ⚠️刻意**不**归一的(语义不同,合并会改变含义):
    #   `本地导入-研报暂存`/`永安-研报暂存`/`中信-研报暂存` = 本地导入暂存目录,
    #     不是"机构已确认"的正式来源;`发现报告` = 聚合平台;
    #   `上传`/空 = 兜底值。已核对它们均不含上述任何键 ⇒ 不会被误伤。
)


def _normalize_source(source: str) -> str:
    """发行方名称归一:同一机构的各种写法 → 标准短名;未命中规则的原样返回。

    【口径 2026-09-14】全部已确认机构都用**标准短名**(东证/华泰/国泰君安/永安期货/中信期货)
    —— 分组键与前端显示同口径,这是"源数 == 观点数"的前提。
    【已知局限】短键「华泰」「东证」是子串匹配,会顺带命中「华泰联合证券」「东证资管」
    这类同名集团下的其他机构;本库 done 研报里不存在这些写法,且短键是用户确认口径,故不改。
    """
    s = str(source or "").strip()
    if not s:
        return s
    low = s.lower()
    for key, std in _SOURCE_NORMALIZE_RULES:
        if key in low or key in s:
            return std
    return s


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


# 【衍生策略分列 2026-09-15 用户定】观点对比表把"单边方向"与"衍生策略"分成两列。
# 动机(真实案例):永安 2026-09-13《合成橡胶周报》BR 行方向=看空 —— 来自策略原句
#   "单边偏择机高空",但同一句里还有"期现与月差均可考虑逢低正套""短期多天胶空合成,
#   长期仍以做缩为主"。单个方向标签会把整篇的相对价值/组合思路吃掉,读者只看到"空"。
# 下面的关键词只认**非单边**的衍生策略表述,与方向列互补、不重复。
_DERIV_STRATEGY_KW = re.compile(  # 【常量】衍生策略**动作词**(只认这些,宁缺勿滥)
    r"正套|反套|跨期|跨月|做缩|做扩|买近|卖近|买远|卖远|抛近|抛远"
    r"|套利|期权|买权|卖权"
    # 多X空Y:两侧各 1~6 字,且不许夹「但」与逗号 —— 否则 "偏多但空间有限" 会被误判成
    # "多[但空间]空[有限]"(实测误报)。
    r"|多[^，,、但\s]{1,6}空[^，,、但\s]{1,6}"
    # 英文研报(华泰英文周报)的 operation_advice 是英文原句,补最小一组英文策略词。
    r"|\bspreads?\b|\barbitrage\b",
    re.I,
)
# 【套保单列一条规则】"套保"本身是策略动作,但库里高频出现的 "库存套保压力""库存待套保"
#   是**利空因素**不是建议(实测:国君 SI、东证 SI)。只认前面紧跟动作词的用法。
_DERIV_HEDGE_KW = re.compile(
    r"(卖出|买入|做多|做空|逢低|逢高|轻仓|谨慎|参与|操作|进行|建议|可|宜)\s*套保", re.I
)
# 【为什么刻意不收「月差/价差/跨品种/对冲」】实测两类误报,这个单元格宁可为空:
#   ① 「月差/价差」在国君、东证的结论小节里几乎都是**驱动**不是策略:
#      "因仓单低位+地缘溢价+内外价差走阔 → 推演短期情绪面仍偏强…"被当成策略展示;
#   ② 「对冲」是行情描述:永安 422《天然橡胶&20号胶周报》"综述:中性观望,**驱动对冲**,
#      趋势不明"、"理由:多空驱动对冲、方向不明"。
#    这两类词只在**动作词**(正套/反套/多X空Y…)里出现时才可能被采纳。
# 否定式片段也不是策略("仅期权情绪数据不足以驱动方向性头寸"),下面单独过滤。
_DERIV_STRATEGY_NEG = ("不足以", "无法", "不构成", "不建议", "缺乏", "难以构成")
# 【为什么只读研报自报的策略字段,不扫结论小节】2026-09-15 实测本周期 21 条命中:
#   来自 operation_advice 11 条 + 交易要素行 3 条 (=14 条全部可用),
#   来自结论小节(观点与依据/多空要点/建议权重)7 条 **全是驱动描述噪声**(见上);
#   ⇒ 小节扫描"零收益、全是污染",直接不扫。语义上也更对:交易要素行与
#   operation_advice 是研报**自己声明为策略**的字段,小节是推演过程。


def _row_variety_advice(row: dict, code: str) -> str:
    """DB 行 structured_data 里**该品种**的研报原文操作建议(operation_advice);取不到 → ""。

    【为什么读 structured_data 而不是 DB 列】research_reports 表没有 report_advice 列
            (聚合 JSON 里那份也是从 structured_data 写出去的),直接读这里免一次文件 IO,
            且能按品种精确取到(多品种研报的次品种主意是分开的)。
            旧行可能无 structured_data / varieties 不是对象数组 → 空串兜底。
    """
    try:
        sd = row.get("structured_data") or ""
        data = json.loads(sd) if isinstance(sd, str) else (sd or {})
        if not isinstance(data, dict):
            return ""
        for item in (data.get("varieties") or []):
            if isinstance(item, dict) and str(item.get("variety") or "").upper() == code.upper():
                return str(item.get("operation_advice") or "")
    except Exception:  # 兼容任意历史脏数据:解析失败一律当"未披露"
        return ""
    return ""


def _extract_deriv_strategy(advice: str = "", te: dict | None = None) -> str:
    """研报策略原句/交易要素行 → 「衍生策略」(正套/反套/跨品种/多X空Y/期权/套保…)单元格文本。

    注:括号里的「跨品种/月差」指**片段语义**,不是关键词 —— 关键词只有 _DERIV_STRATEGY_KW
    里的动作词(月差/价差/对冲刻意不收,理由见常量上方注释)。

    【口径 2026-09-15 用户定】观点对比表分两列:方向列只回答"看多/看空/中性",这一列
            补**非单边**的相对价值策略,避免组合思路被一个方向标签吃掉(见上方注释案例)。
    【只读研报自报的策略字段】两个源按优先级取,**首个非空源即返回**(保证单元格短):
              ① 交易要素行的 形态与区间 / 头寸 / 头寸范围(= te,由结论段解析而来);
              ② 研报原文策略原句 operation_advice(信息最全,通常含正套+跨品种)。
            **刻意不扫结论小节**(观点与依据/多空要点/建议权重):实测那 7 条全是
            驱动描述噪声,详见 _DERIV_STRATEGY_KW 上方注释。
    【参数】advice:structured_data 里该品种的 operation_advice(可空);
            te:_parse_trade_elements(该品种结论段) 结果(可空)。
    【返回】str: 「; 」连接的策略片段(≤120 字,超长按句读边界截断);全无 → ""(前端 —)。
    """
    te = te or {}

    def _pick(text: str) -> list[str]:
        out: list[str] = []
        for frag in re.split(r"[；;。\n，,]", re.sub(r"\s+", " ", str(text or ""))):
            frag = frag.strip().strip("#*` ").strip("，,、。.;； ")
            # 「风险:…」是风险披露不是策略;「理由:…」前缀留着读起来别扭,先剥掉(剥空就丢)。
            if frag.startswith("风险"):
                continue
            frag = re.sub(r"^(理由|原因|核心逻辑|单边|策略|操作)\s*[:：]\s*", "", frag).strip()
            if len(frag) < 3 or frag in ("—", "未披露") or "未披露" in frag:
                continue
            if any(n in frag for n in _DERIV_STRATEGY_NEG):
                continue  # "…不足以驱动方向性头寸" 这类否定句不是策略
            if (_DERIV_STRATEGY_KW.search(frag) or _DERIV_HEDGE_KW.search(frag)) and frag not in out:
                out.append(frag)
        return out

    te_text = "；".join(str(te.get(k) or "") for k in ("形态与区间", "头寸", "头寸范围"))
    for source in (te_text, advice):
        hits = _pick(source)
        if hits:
            return _truncate_at_boundary("; ".join(hits), 120)
    return ""


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
        "source": _normalize_source(r.get("source") or ""),  # 【发行方统一 2026-09-11】
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
            report_type(日报/周报/季报,可选,空=全部 —— 各类型结论分开统计)。
    【返回】json: {variety, name, date, dates(可选日期倒序), rows[]}。
    【关键逻辑】方向/置信度/观点均为该品种口径(聚合 JSON 本就是按品种拆的);
                dates 由现有研报当天集合去重降序得出,无研报返回空数组;
                传 report_type 时先按类型过滤再算日期并集(口径与每日/周报总结一致:
                日报=非周报行含未打类型,周报=仅周报行)。
    """
    code = (request.args.get("variety") or "").strip().upper()
    date = (request.args.get("date") or "").strip()[:10]
    rtype = (request.args.get("report_type") or "").strip()  # 空=全部;日报/周报/季报分开统计
    if not code:
        return jsonify({"variety": "", "name": "", "date": "", "dates": [], "rows": []})
    from tradingagents.dataflows.research_data import load_research_data  # 【调用包】聚合 JSON 读取

    data = load_research_data(code) or {}
    reports = data.get("reports") or []
    # 类型口径与每日/周报/季报总结一致:周报=仅周报行;季报=仅季报行;
    # 日报=非周报且非季报行(未打类型的行也进日报口径,否则总结里有、总览筛日报却查不到)。
    if rtype in ("周报", "季报"):
        reports = [r for r in reports if (r.get("report_type") or "").strip() == rtype]
    elif rtype:
        reports = [r for r in reports
                   if (r.get("report_type") or "").strip() not in ("周报", "季报")]

    def _rdate(r: dict) -> str:
        # 日期分组键:聚合记录 publish_date(真实发布日)优先,旧聚合文件无此键回退 uploaded_at
        return (r.get("publish_date") or "").strip()[:10] or str(r.get("uploaded_at") or "")[:10]

    def _rtitle(r: dict) -> str:
        return (r.get("title") or "")

    # 【周期归一 2026-09-29】date 参数为周期键:周报=周起点(周日)、季报=季度起点。
    #   与对应总结/单品种总结同口径 —— 周期报告一期一篇,按周期查看多天聚合。
    r_norm = _rtype_norm(rtype)
    if r_norm:
        dates = sorted({_period_key(_rdate(r), _rtitle(r), r_norm) for r in reports if _rdate(r)}, reverse=True)
        dates = [d for d in dates if d]
    else:
        dates = sorted({_rdate(r) for r in reports if _rdate(r)}, reverse=True)
    if not dates:
        meta = VARIETY_METADATA.get(code, {})
        return jsonify({"variety": code, "name": meta.get("name", code), "date": "", "dates": [], "rows": []})
    if r_norm:
        # 周期口径:date 参数是周期键(或周期内任一天);未传/无效 → 回退最新周期。
        if date:
            sel = _period_key(date, "", r_norm)  # 归一化到周期键
            if sel in dates:
                rows = [r for r in reports if _in_period(sel, _rdate(r), _rtitle(r), r_norm)]
                date = sel
            else:  # 传入日期不在任何已知周期(切换品种残留) → 回退最新周期
                rows = [r for r in reports if _in_period(dates[0], _rdate(r), _rtitle(r), r_norm)]
                date = dates[0]
        else:
            rows = [r for r in reports if _in_period(dates[0], _rdate(r), _rtitle(r), r_norm)]
            date = dates[0]
    elif date in dates:
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


# 【品种展示顺序 2026-09-11 用户定;2026-09-30 加入 P 棕榈油】观点总表/覆盖表按此板块
# 分组排序(一二级菜单):油品 → 能化 → 农产品 → 新能源(池内未列入的品种按元数据顺序追加末尾)。
_VARIETY_SECTORS: dict[str, tuple[str, ...]] = {
    "油品": ("SC", "FU", "LU", "BU"),                              # 油品
    "能化": ("PX", "TA", "EG", "PG", "BR", "EB", "BZ"),            # 能化(TA=PTA 精对苯二甲酸)
    "农产品": ("RU", "NR", "LH", "M", "CJ", "CF", "P"),            # 农产品(P=棕榈油)
    "新能源": ("LC", "SI", "PS"),                                  # 新能源
}
_VARIETY_DISPLAY_ORDER: tuple[str, ...] = tuple(
    code for _sector, codes in _VARIETY_SECTORS.items() for code in codes
)


@app.route("/api/research/aggregate/repair", methods=["POST"])
def api_research_aggregate_repair():
    """聚合 JSON 对账修复(管理用):POST {"dry_run": bool}。

    【功能】扫描全部 done 研报行,聚合 JSON 缺失条目的从 DB 重建(零 LLM)。
            2026-09-11 修"逐品种观点总览与总结不对齐"(周报聚合缺失)。
    """
    body = request.get_json(silent=True) or {}
    result = _repair_research_aggregates(dry_run=bool(body.get("dry_run")))
    return jsonify(result)


def _active_variety_list() -> list[str]:
    """固定顺序的池内品种列表(2026-09-11 用户定:总表/覆盖表每日品种顺序一致)。

    【为什么】ACTIVE_VARIETIES 是 frozenset,迭代顺序随 Python hash seed 每次
            进程启动都不同 → 每天生成的观点总表/覆盖表品种顺序漂移。
    【顺序】用户指定板块分组:油品 → 能化 → 农产品 → 新能源;池内但未列入
            顺序表的品种(未来扩池)按元数据顺序追加在末尾,保证不丢。
    """
    order = [c for c in _VARIETY_DISPLAY_ORDER if c in ACTIVE_VARIETIES]
    tail = [c for c in VARIETY_METADATA if c in ACTIVE_VARIETIES and c not in order]
    return order + tail


@app.route("/api/research/coverage")
def api_research_coverage():
    """研报覆盖表(需求二 2026-09-11):固定列出全部 ACTIVE_VARIETIES 品种。

    【参数】report_type('周报'/空=日报);date(YYYY-MM-DD,可选):
            周报=周起点(周日),日报=单日;缺省取库内最新日期/周起点。
    【返回】{"date", "report_type", "rows":[{code, name, count, has_new(bool)}], "total"}。
    【口径】count = 对应日期范围(周报=周区间 / 日报=单日)内该品种研报数
            (多品种研报按 varieties 拆分);has_new = count > 0。
            与 _build_coverage_table 共用同一统计口径,前端渲染与落盘 md 一致。
    """
    rtype = (request.args.get("report_type") or "").strip()
    date = (request.args.get("date") or "").strip()[:10]
    r_norm = _rtype_norm(rtype)
    db = get_db()
    rows = [r for r in db.list_research_reports(limit=500) if r.get("status") in ("done", "summarized")]  # 【2026-09-24】含 summarized
    if r_norm:
        rows = [r for r in rows if (r.get("report_type") or "").strip() == r_norm]
    else:
        rows = [r for r in rows if (r.get("report_type") or "").strip() not in ("周报", "季报")]

    if r_norm:
        # 周期键列表:库内对应类型研报日期归一 ∪ 已生成总结文件
        dates = sorted(
            {d for d in (_period_key(_report_date(r), (r.get("title") or ""), r_norm) for r in rows) if d},
            reverse=True,
        )
        if date:
            sel = _period_key(date, "", r_norm)
            if sel not in dates and dates:
                sel = dates[0]
        else:
            sel = dates[0] if dates else _period_key(__import__("datetime").date.today().isoformat(), "", r_norm)
        date = sel
        filtered = [r for r in rows
                    if _in_period(date, _report_date(r), (r.get("title") or ""), r_norm)]
    else:
        # 单日列表:库内日报日期 ∪ 已生成每日总结文件
        sdir = _summary_dir("")
        dates = sorted({_report_date(r) for r in rows if _report_date(r)}, reverse=True)
        if sdir.is_dir():
            dates = sorted(set(dates) | {p.stem for p in sdir.glob("????-??-??.md")}, reverse=True)
        if date:
            if date not in dates and dates:
                date = dates[0]
        else:
            date = dates[0] if dates else __import__("datetime").date.today().isoformat()
        filtered = [r for r in rows if _report_date(r) == date]

    name_map = {k: v["name"] for k, v in VARIETY_METADATA.items()}
    counts: dict[str, int] = {}
    for r in filtered:
        rcodes = [c.strip().upper() for c in str(r.get("varieties") or "").split(",") if c.strip()] \
            or [str(r.get("variety") or "").upper()]
        for c in dict.fromkeys(rcodes):
            counts[c] = counts.get(c, 0) + 1
    data_rows = [
        {"code": c, "name": name_map.get(c, ""), "count": counts.get(c, 0),
         "has_new": counts.get(c, 0) > 0}
        for c in _active_variety_list()
    ]
    return jsonify({"date": date, "report_type": rtype, "rows": data_rows, "total": len(data_rows)})


@app.route("/api/research/weekly-coverage")
def api_research_weekly_coverage():
    """兼容旧路由:周报覆盖表(转调 /api/research/coverage 的周报口径)。"""
    rtype = (request.args.get("report_type") or "").strip() or "周报"
    return api_research_coverage_wrapper(
        date=(request.args.get("week_start") or request.args.get("date") or "").strip()[:10],
        rtype=rtype,
    )


def api_research_coverage_wrapper(date: str = "", rtype: str = "") -> dict:
    """coverage 路由的可复用实现(旧路由 /api/research/weekly-coverage 转调)。"""
    rtype = rtype or "周报"
    db = get_db()
    r_norm = _rtype_norm(rtype)
    rows = [r for r in db.list_research_reports(limit=500) if r.get("status") in ("done", "summarized")]  # 【2026-09-24】含 summarized
    if r_norm:
        rows = [r for r in rows if (r.get("report_type") or "").strip() == r_norm]
    else:
        rows = [r for r in rows if (r.get("report_type") or "").strip() not in ("周报", "季报")]
    if r_norm:
        dates = sorted(
            {d for d in (_period_key(_report_date(r), (r.get("title") or ""), r_norm) for r in rows) if d},
            reverse=True,
        )
        if date:
            sel = _period_key(date, "", r_norm)
            if sel not in dates and dates:
                sel = dates[0]
        else:
            sel = dates[0] if dates else _period_key(__import__("datetime").date.today().isoformat(), "", r_norm)
        date = sel
        filtered = [r for r in rows
                    if _in_period(date, _report_date(r), (r.get("title") or ""), r_norm)]
        if r_norm == "周报":
            head_key, count_key = "本周周报数", "本周有新研报"
        else:  # 季报
            head_key, count_key = "本季季报数", "本季有新研报"
    else:
        dates = sorted({_report_date(r) for r in rows if _report_date(r)}, reverse=True)
        if date:
            if date not in dates and dates:
                date = dates[0]
        else:
            date = dates[0] if dates else __import__("datetime").date.today().isoformat()
        filtered = [r for r in rows if _report_date(r) == date]
        head_key, count_key = "当日研报数", "当日有新研报"
    name_map = {k: v["name"] for k, v in VARIETY_METADATA.items()}
    counts: dict[str, int] = {}
    for r in filtered:
        rcodes = [c.strip().upper() for c in str(r.get("varieties") or "").split(",") if c.strip()] \
            or [str(r.get("variety") or "").upper()]
        for c in dict.fromkeys(rcodes):
            counts[c] = counts.get(c, 0) + 1
    data_rows = [
        {"code": c, "name": name_map.get(c, ""), "count": counts.get(c, 0),
         "has_new": counts.get(c, 0) > 0}
        for c in _active_variety_list()
    ]
    return jsonify({
        # 兼容字段:旧前端读 week_start;周期口径下它就是周期键(周起点/季度起点)。
        "week_start": date,
        "date": date, "report_type": rtype,
        "rows": data_rows, "total": len(data_rows),
        "_head": head_key, "_count": count_key,
    })


# ---------------------------------------------------------------------
# 研报每日总结(2026-09-04):把某一天(发布日期优先,缺则回退 uploaded_at 前 10 位)
# 在库全部研报的逐品种分析结论交 LLM 汇总成一份 markdown(各公司观点 / 观点对比与冲突 /
# 可信程度分析 / 综合结论),落盘 ~/.tradingagents/research_daily/{date}.md,
# 供前端"每日总结"卡随时调阅;已生成过的日期直接读文件,不重复烧 LLM。
# ---------------------------------------------------------------------
RESEARCH_DAILY_DIR = Path.home() / ".tradingagents" / "research_daily"
RESEARCH_WEEKLY_DIR = Path.home() / ".tradingagents" / "research_weekly"  # 周报总结独立落盘(2026-09-07 与日报分开)
RESEARCH_QUARTERLY_DIR = Path.home() / ".tradingagents" / "research_quarterly"  # 季报总结独立落盘(2026-09-29)
RESEARCH_MONTHLY_DIR = Path.home() / ".tradingagents" / "research_monthly"  # 月报总结独立落盘(2026-10-08)


# 【功能】研报类型口径工具(2026-09-29 季报引入后统一):
#           '' / '日报' → 单日精确匹配;'周报' → 归到周起点、区间匹配;
#           '季报' → 归到季度起点、区间匹配(标题优先判定报告期)。
# 【为什么统一】季报引入前,各处散落着 `weekly_mode = rtype == '周报'` 的二元判断;
#           再加一个季度维度会让每个判断点都膨胀成三态分支,极易漏改。这里收敛成
#           一组纯函数,所有消费方(总结/单品种/覆盖表/观点总览)都从这里取口径。
def _rtype_norm(rtype: str) -> str:
    """口径归一:'周报'/'季报' 原样;其余(含空、'日报')一律视为日报(单日)口径。"""
    r = (rtype or "").strip()
    return r if r in ("周报", "季报", "月报") else ""


def _period_key(date_str: str, title: str = "", rtype: str = "") -> str:
    """把一个发布日期归到该口径的"周期键":日报=原日期;周报=周起点;季报=季度起点。"""
    r = _rtype_norm(rtype)
    if r == "周报":
        return _week_start(date_str)
    if r == "季报":
        return _quarter_start(date_str, title)
    if r == "月报":
        return _month_start(date_str)
    return (date_str or "")[:10]


def _in_period(period_key: str, date_str: str, title: str = "", rtype: str = "") -> bool:
    """date_str(带标题)是否落在 period_key 所代表的周期内(按口径分派)。"""
    r = _rtype_norm(rtype)
    if r == "周报":
        return _in_week(period_key, date_str)
    if r == "季报":
        return _in_quarter(period_key, date_str, title)
    if r == "月报":
        return _in_month(period_key, date_str)
    return (date_str or "")[:10] == (period_key or "")[:10]


def _period_range(period_key: str, rtype: str = "") -> tuple[str, str]:
    """周期区间(用于文案/覆盖表)。"""
    r = _rtype_norm(rtype)
    if r == "周报":
        return _week_range(period_key)
    if r == "季报":
        return _quarter_range(period_key)
    if r == "月报":
        return _month_range(period_key)
    return (period_key or "")[:10], (period_key or "")[:10]


def _summary_dir(rtype: str = "") -> Path:
    """总结落盘目录:周报 → research_weekly,季报 → research_quarterly,日报(默认) → research_daily。"""
    r = _rtype_norm(rtype)
    if r == "周报":
        return RESEARCH_WEEKLY_DIR
    if r == "季报":
        return RESEARCH_QUARTERLY_DIR
    if r == "月报":
        return RESEARCH_MONTHLY_DIR
    return RESEARCH_DAILY_DIR


def _research_daily_path(date: str, rtype: str = "") -> Path:
    """某周期的总结 md 文件路径(按 _summary_dir 分派目录;date 为该口径的周期键)。"""
    return _summary_dir(rtype) / f"{date}.md"


def _research_rows_for_type(db, rtype: str) -> list[dict]:
    """按周期类型取研报行,供「周期列表 / 周期总结生成 / 品种聚合」三处共用一个口径。

    【为什么必须按类型分档取数 2026-09-29】研报逐日累积,日报每天几十上百条,
    而周报/季报是低频类型(实测 1309 行里周报 316、季报仅 2)。**固定 LIMIT 是按
    上传时间截最新 N 条**,低频类型会被整体挤出窗口:实测 LIMIT=500 只覆盖最近
    8 天 ⇒ 季报 2/2 **全丢**、周报丢 194/316(61%),现象=前端"选季报什么都不
    显示"/季度下拉空,而接口不报错 —— 静默缺陷。
    【分档规则】周报/季报:走 `limit=None` 全表(**低频,必须全量**);
                日报(及其他):保留 LIMIT(量大且天然贴近日,500 条足够;
                历史部分靠每日总结文件并集兜底)。
    【调用方】`_research_daily_dates` / `api_research_daily_generate` /
                `api_research_variety_aggregate` —— 三处口径必须一致,否则
                "列表里有这天、点生成却说没有"这类自相矛盾。
    """
    r = _rtype_norm(rtype)
    if r in ("周报", "季报", "月报"):
        return db.list_research_reports(limit=None)          # 低频:全表
    return db.list_research_reports(limit=500)               # 日报:够用


def _research_daily_dates(db, rtype: str = "") -> list[str]:
    """可用日期并集倒序 = 库内已有 done 结论的对应类型研报日期(发布日优先) ∪ 已生成总结文件的日期。

    【关键】日报口径(默认)排除周报/季报行(每日总结只收日报,2026-09-05 用户定);周报口径
            (rtype='周报')只收周报行;季报口径(rtype='季报')只收季报行 —— 三类日期与
            总结文件各自独立,前端下拉分开(2026-09-07 拆分日报/周报;2026-09-29 加季报)。
    【周期归一】周报的"日期"归到周起点(周日,2026-09-11);季报归到季度起点(该季度首月 1 日,
            2026-09-29)——归一到周期键后做并集;已生成文件的文件名本身即周期键,直接并入。
    """
    r = _rtype_norm(rtype)
    # ⚠️【必须按类型分档取数 2026-09-29】见 _research_rows_for_type 文档:固定 LIMIT
    #   会把低频的周报/季报整体挤出窗口(季报 2/2 全丢 ⇒ 季度下拉空、选季报无显示)。
    _all = _research_rows_for_type(db, rtype)
    if r in ("周报", "季报", "月报"):
        # 周报/季报/月报口径:只收本类型行
        rows = [r_ for r_ in _all if (r_.get("report_type") or "").strip() == r]
    else:
        # 日报口径:排除周报与季报,但**保留未知类型**(report_type='')——历史行多为空,
        #   排除它们会让"每日总结"的日期列表凭空少掉一大片(与 _collect_daily_report_items 同口径)。
        rows = [r_ for r_ in _all if (r_.get("report_type") or "").strip() not in ("周报", "季报")]
    dates = {
        d for d in (_report_date(r_) for r_ in rows)
        if re.match(r"^\d{4}-\d{2}-\d{2}$", d)  # 只认合法日期(剔除空/异常时间戳前缀)
    }
    if r in ("周报", "季报", "月报"):
        # 【周期归一】单日 → 周期键(周报=周起点,季报=季度起点,月报=月起点);
        #   季报还需标题(报告期优先),故按行取标题而不是只对日期做映射。
        dates = {
            k for k in (
                _period_key(
                    _report_date(r_),
                    (r_.get("title") or ""),
                    r,
                )
                for r_ in rows
            ) if k
        }
    sdir = _summary_dir(rtype)
    if sdir.is_dir():
        file_dates = {p.stem for p in sdir.glob("????-??-??.md")}
        if r == "周报":
            # 【周报口径 2026-09-11】旧版周报总结文件可能是单日命名(迁移前),
            # 一并归到所在周起点,与新命名(周起点)统一口径。
            file_dates = {d for d in (_week_start(x) for x in file_dates) if d}
        elif r == "季报":
            # 【季报口径 2026-09-29】同理:季度起点命名,非 1/4/7/10 月 1 日的文件归到季起点。
            file_dates = {d for d in (_quarter_start(x) for x in file_dates) if d}
        elif r == "月报":
            # 【月报口径 2026-10-08】同理:月起点命名,非 1 日的文件归到月起点。
            file_dates = {d for d in (_month_start(x) for x in file_dates) if d}
        dates |= file_dates
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


# ══════════════════════════════════════════════════════════════════
# 同源多篇研报合并 (2026-09-14 用户逐项拍板的口径)
# ══════════════════════════════════════════════════════════════════
#
# 【要解决的问题】某家机构某天可能对同一品种发多篇相关研报(专题+日报 /
#   技术周报+品种周报 / 晨报+点评)。旧行为有两种,都不可取:
#     ① 逐份列出 → 观点总表出现「发行方 4 个、方向 6 个」对不上(用户报障原话);
#     ② 只保留最新一篇(2026-09-14 白天版) → 丢掉其余篇的信息。
#   用户定的口径:**合并成一条,给一个统一的观点与总结**。
#
# 【为什么零额外 LLM 成本也能"统一"】每日/周度总结本来就调一次 LLM、
#   单品种总结每品种调一次,那次调用**已经读到全部材料**。问题只是我们把材料
#   按"篇"喂进去(6 块),于是它就按 6 个观点写。按"机构"合并成 4 块之后,
#   同一次调用就会自然地按机构给统一表述 —— 融合这件事由 LLM 免费完成。

_DUP_TITLE_RATIO = 0.72  # 标题相似度阈值:超过即视为"同一份报告被重复采集"
_DUP_BODY_RATIO = 0.92  # 标题不足以判断时,再用正文前 N 字的相似度兜底
_DUP_BODY_LEN = 600  # 正文比对只取开头(开头最能说明是不是同一篇)
_REDUNDANT_RATIO = 0.90  # 合并文本时的去重阈值(避免同篇双采留下重复要点)


def _title_fingerprint(text: str) -> str:
    """标题/正文 → 判重指纹:剥掉空白、标点与各种书名号装饰,只留实义字符。

    这样《聚烯烃日报：成本支撑偏强…》与《华泰期货聚烯烃日报20260902：成本支撑偏强…》
    的指纹会互相包含,从而被认成同一篇(不同采集链路会给标题加前缀/日期)。
    """
    return re.sub(r"[\s:：、,，。.；;（）()\[\]【】《》「」\-—_/|]+", "", str(text or ""))


_DATE_RUN_RE = re.compile(r"\d{4,}")  # 标题里的日期/报告号(4 位以上数字串)


def _dedup_fingerprint(text: str) -> str:
    """判重专用指纹:标题指纹再删掉 4 位以上的数字串(日期/报告号)。

    【为什么必须再删一次数字】实测最常见的同篇双采形态是
      《聚烯烃日报:成本支撑偏强…》 vs 《华泰期货聚烯烃日报20260902:成本支撑偏强…》——
      日期串插在中间,把原有的包含关系**隔断**(「聚烯烃日报成本支撑偏强」不再是
      「华泰期货聚烯烃日报20260902成本支撑偏强」的子串),相似度也掉到 0.65 左右、
      低于阈值,于是同一篇以两行出现 —— 用户看到的"同一家机构两条重复观点"。
      删掉数字串后包含关系恢复,这一类漏判被消除。
    【安全性】只用于**标题**判重;正文比对仍走 _title_fingerprint(正文里的数字是实义信息)。
    """
    return _DATE_RUN_RE.sub("", _title_fingerprint(text))


def _looks_same_report(title_a: str, title_b: str, text_a: str = "", text_b: str = "") -> bool:
    """两条记录是否为"同一份报告被两条采集链路各收了一遍"。

    【判据(任一命中)】
      ① 判重指纹(_dedup_fingerprint:**已删日期串**)互相包含 —— 典型形态是同一篇被
         补上了机构前缀/日期(日期插在中间会隔断包含关系,故必须先删数字串);
      ② 判重指纹相似度 > _DUP_TITLE_RATIO;
      ③ 正文前 _DUP_BODY_LEN 字相似度 > _DUP_BODY_RATIO(标题差异大时的兜底)。
    【为什么不用"标题严格相等"】标题会被不同采集链路改写,严格相等会大量漏判。
    """
    fa, fb = _dedup_fingerprint(title_a), _dedup_fingerprint(title_b)
    if fa and fb and (fa in fb or fb in fa):
        return True
    if fa and fb and difflib.SequenceMatcher(None, fa, fb).ratio() > _DUP_TITLE_RATIO:
        return True
    ba, bb = _title_fingerprint(text_a)[:_DUP_BODY_LEN], _title_fingerprint(text_b)[:_DUP_BODY_LEN]
    if ba and bb and difflib.SequenceMatcher(None, ba, bb).ratio() > _DUP_BODY_RATIO:
        return True
    return False


def _text_redundant(text_a: str, text_b: str) -> bool:
    """合并要点时判两条文本是否重复(包含关系,或前 200 字高度相似)。"""
    fa, fb = _title_fingerprint(text_a)[:200], _title_fingerprint(text_b)[:200]
    if not fa or not fb:
        return False
    if fa in fb or fb in fa:
        return True
    return difflib.SequenceMatcher(None, fa, fb).ratio() > _REDUNDANT_RATIO


def _truncate_at_boundary(text: str, cap: int) -> str:
    """按句读边界截断(尽量不切断句子),截断时补省略号。"""
    if len(text) <= cap:
        return text
    head = text[:cap]
    cut = max(head.rfind(sep) for sep in "；;。\n")
    return (head[:cut] if cut >= cap // 2 else head).rstrip("；;。 ") + "…"


def _merged_titles_label(it: dict) -> str:
    """合并条目的"篇目标识":一篇 → 《标题》;多篇 → 《标题1》《标题2》(同机构 N 篇已合并)。

    【用途】总结 prompt 的材料抬头。合并后一个条目代表**一家机构**,但 LLM 仍需要知道
    这一块底下原有几篇、分别是什么(才能判断覆盖面并做真正的融合);同时显式写出
    "已合并"能抑制它输出「某机构两篇分别…」这类按篇罗列的表述。
    """
    ts = [str(t.get("title") or "").strip() for t in (it.get("titles") or [])]
    ts = [t for t in ts if t]
    if not ts:
        ts = [str(it.get("title") or "(未命名)")]
    if len(ts) == 1:
        return f"《{ts[0]}》"
    return "".join(f"《{t}》" for t in ts) + f"(同机构 {len(ts)} 篇已合并)"


# 【校验器 2026-09-14】"仍按篇罗列"的语言信号:机构名后面 24 字内出现这些词即视为命中。
_PER_REPORT_PATTERNS: tuple[str, ...] = (
    "分别", "两篇", "二篇", "三篇", "四篇", "多篇", "篇研报", "份研报", "观点不一", "各执",
)


def _detect_per_report_listing(body: str, sources: list[str]) -> list[str]:
    """总结正文里是否仍**按篇罗列**了同一机构(同源合并"没生效"的告警信号)。

    【为什么需要】合并只保证**材料**是"一家机构一块";LLM 仍可能照旧写
      「华泰两篇分别认为…」。这里在落盘前扫一遍,命中即返回机构名,由调用方写 warning
      并在返回值里带 merge_alert(前端可提示「建议重跑」)。
    【刻意只报警不改写正文】改写会误伤正常表述(如"两家机构观点不一"),这违反
      "确定性优先、可解释"的既定口径;而且正文是 LLM 产物,该由 LLM 重跑而不是正则去修。
    """
    hits: list[str] = []
    text = body or ""
    for s in dict.fromkeys(x for x in sources if x and x != "未知"):
        for m in re.finditer(re.escape(s), text):
            win = text[m.end(): m.end() + 24]
            if "合并" in win:
                continue  # 「已合并」是正确表述,不算按篇罗列
            if any(p in win for p in _PER_REPORT_PATTERNS):
                hits.append(s)
                break
    return hits


def _merge_same_source_items(
    recs: list[dict], *, flatten: bool = False, text_cap: int = 0, trade_cap: int = 0
) -> list[dict]:
    """把「同一机构·同一品种」在总结窗口内的多篇研报合并成一条(用户口径 2026-09-14)。

    【两步走】
      ① **同篇双采去重**(用户 2026-09-14 确认"判为同一篇,真去重"):同一份报告被两条
         采集链路各收一遍时,只留**正文最长**的那一版(信息最全),身份(ts/report_id)
         仍用更新的;正文更长的旧版会被吸收进来。
      ② **不同报告合并成一条**:
         - direction:组内**多数票**;**平票取最新**一篇(用户口径);
         - conf_num :组内有值项**均值**(四舍五入);全缺 → None(前端显示 —);
         - text/trade:各篇去重后按**新→旧**拼接(新观点在前);flatten=True 时
           先把换行折成「；」,保证结果能安全放进 markdown 表格单元格;
         - metrics  :按指标名去重,同一指标保留**最新**披露值;
         - report_id:**最新一篇**(前端「原件」主链接);titles/report_ids 收录全部篇目。
    【分组键】(品种代码, 归一后发行方) —— **刻意不含日期**:日报窗口本就是单日;
      周报窗口跨 7 天,若按日分组,同一机构在不同日期发的多篇仍会各占一行,
      「源数 < 观点数」依旧存在(实测 2026-09-06 周有 2 例,其余周为 0)。
    【参数】recs: 逐篇记录,必填 code/source/rdate/ts/rid/order/title/direction/
            conf_num/text;可选 body(判重用,缺省用 text)/trade/metrics。
            flatten: 是否把合并结果压成单行(表格场景传 True)。
            text_cap/trade_cap: 合并后文本的软上限(0=不限);超限按句读边界截断,
            用来给总结 prompt 的材料预算封顶(单篇组本来就在各自预算内,不受影响)。
    【返回】合并后记录列表;每条带 report_id/report_ids/titles/merged_count/dup_dropped。
    """
    groups: dict[tuple, list[dict]] = {}
    for rec in recs:
        key = (str(rec.get("code") or "").upper(), str(rec.get("source") or "未知"))
        groups.setdefault(key, []).append(rec)

    merged_out: list[dict] = []
    for members in groups.values():
        # 组内按"新→旧"排序:后面的方向平票、文本拼接、主链接全都依赖这个次序。
        members = sorted(
            members,
            key=lambda r: (str(r.get("ts") or ""), int(r.get("rid") or 0)),
            reverse=True,
        )

        # ① 同篇双采去重(新→旧扫,与任一已保留篇"像同一篇"即并入)
        kept: list[dict] = []
        dup_dropped = 0
        for rec in members:
            hit = next(
                (
                    k
                    for k in kept
                    if _looks_same_report(
                        k.get("title"), rec.get("title"),
                        k.get("body") or k.get("text"), rec.get("body") or rec.get("text"),
                    )
                ),
                None,
            )
            if hit is None:
                kept.append(rec)
                continue
            dup_dropped += 1
            # 同篇:身份(ts/rid/title)仍用更新的那版,只把更完整的正文吸收进来。
            if len(str(rec.get("text") or "")) > len(str(hit.get("text") or "")):
                hit["text"] = rec.get("text")
                hit["body"] = rec.get("body") or rec.get("text")

        latest = kept[0]

        # ② 方向:多数票;平票取最新(kept 已按新→旧,winners 首个即最新一篇)
        votes: dict[str, int] = {}
        for r in kept:
            d = str(r.get("direction") or "中性")
            votes[d] = votes.get(d, 0) + 1
        top = max(votes.values())
        winners = [r for r in kept if str(r.get("direction") or "中性") in
                   {d for d, n in votes.items() if n == top}]
        direction = str(winners[0].get("direction") or "中性")

        # ③ 置信度:组内有值项均值(缺值不计入)
        vals = [float(r["conf_num"]) for r in kept
                if isinstance(r.get("conf_num"), (int, float))]
        conf_num = (sum(vals) / len(vals)) if vals else None

        # ④ 文本合并:去重后按新→旧拼接(flatten 时折成单行,供表格安全展示)
        def _join_texts(field: str, cap: int, sep: str | None = None) -> str:
            picked: list[str] = []
            for r in kept:
                t = str(r.get(field) or "").strip()
                if not t:
                    continue
                if flatten:
                    t = re.sub(r"\s*\n+\s*", "；", t).strip("； ")
                if not t or t in picked or any(_text_redundant(t, s) for s in picked):
                    continue
                picked.append(t)
                if cap and len("；".join(picked)) >= cap:
                    break
            joined = (sep if sep is not None else ("；" if flatten else "\n\n")).join(picked)
            return _truncate_at_boundary(joined, cap) if cap else joined

        merged = dict(latest)  # 以最新一篇为基底,保留其全部字段
        merged.update({
            "direction": direction,
            "conf_num": conf_num,
            "text": _join_texts("text", text_cap),
            "trade": _join_texts("trade", trade_cap),
            # 【衍生策略 2026-09-15】同机构多篇各有各的衍生策略(如一篇正套、一篇跨品种),
            # 与 trade 同口径按新→旧去重拼接;固定 120 字上限 + 恒用「；」连接
            # (deriv 本身是单行文本,表格单元格里不能出现换行)。
            "deriv": _join_texts("deriv", 120, sep="；"),
            "report_id": latest.get("rid"),
            "report_ids": [r.get("rid") for r in kept],
            "titles": [{"report_id": r.get("rid"), "title": r.get("title")} for r in kept],
            "merged_count": len(kept),
            "dup_dropped": dup_dropped,
            "order": min(int(r.get("order") or 0) for r in kept),
        })
        # metrics:同一指标保留最新披露值(kept 新→旧,首次出现即最新)
        metric_seen: dict[str, dict] = {}
        for r in kept:
            for m in (r.get("metrics") or []):
                lab = str((m or {}).get("label") or "")
                if lab and lab not in metric_seen:
                    metric_seen[lab] = m
        if metric_seen:
            merged["metrics"] = list(metric_seen.values())
        merged_out.append(merged)

    merged_out.sort(key=lambda r: r.get("order") or 0)
    return merged_out


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
            同源合并(2026-09-14):同一机构同一品种的多篇先交给 _merge_same_source_items
            合并成一条(同篇双采去重 + 方向多数票 + 置信度均值 + 要点拼接),这样观点总表
            不会再出现「发行方 4 个、方向 6 个」的对不上。合并的副作用是**每条对应一家机构**,
            因此"研报数"列改用 merged_count 求和(仍是真实篇数,与覆盖表同口径)。
    """
    recs: list[dict] = []
    r_norm = _rtype_norm(rtype)  # ''=日报 / '周报' / '季报'
    name_map = {k: v["name"] for k, v in VARIETY_METADATA.items()}
    agg_cache: dict[str, dict] = {}  # code → {report_id(str): 逐品种置信度}

    for r in rows:
        if r.get("status") not in ("done", "summarized"):  # 【2026-09-24】含 summarized(历史观点保留)
            continue
        rdate = _report_date(r)
        # 【周期归一 2026-09-29】date 参数为该口径的周期键(日报=单日 / 周报=周起点 / 季报=季起点):
        #   周报按周区间匹配、季报按季度区间匹配(标题报告期优先),日报仍精确匹配单日。
        if not _in_period(date, rdate, (r.get("title") or ""), r_norm):
            continue
        # 口径隔离:周报/季报口径只收本类型;日报口径排除周报与季报,但**保留未知类型**
        #   (report_type='')——历史行大量为空,排除它们会让每日总结凭空少掉一片内容。
        rt_row = (r.get("report_type") or "").strip()
        if r_norm:
            if rt_row != r_norm:
                continue
        elif rt_row in ("周报", "季报"):
            continue
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
            advice = _row_variety_advice(r, code)  # 研报原文策略原句(逐品种;旧行无 → "")
            recs.append({
                "code": code,
                # 【发行方归一 2026-09-14】分组键必须用**归一后**的名字:库里同一家公司存在
                # 多种写法(东证期货/上海东证期货有限公司/东证期货-繁微MCP),不归一会把
                # 一家公司当成三家 → 正是「4 个源、6 个观点」的直接成因。
                "source": _normalize_source(str(r.get("source") or "未知")),
                "source_raw": r.get("source") or "未知",
                "rdate": rdate,
                "ts": r.get("uploaded_at") or "",  # 后采集≈后发布,用于"最新一篇"判定
                "rid": r.get("id"),
                "order": len(recs),
                "title": r.get("title") or "(未命名)",
                "direction": te.get("方向") or r.get("direction") or "中性",
                "conf_num": (float(conf) if conf is not None else None),
                "text": opinion,
                "body": seg,  # 判重用的完整正文(标题差异大时按正文兜底判"同篇双采")
                # 【头寸建议列 2026-09-11】交易要素三源合并成一句(与观点总览「头寸」列同口径)。
                # 注:DB 行没有 report_advice 列(它在聚合 JSON 里),这里保持原行为传空,
                # 避免静默改变已生成的「当日观点总表」头寸建议列(要改需一并重跑总结)。
                "trade": _merge_trade_cell(te, str(r.get("report_advice") or "")),
                # 【衍生策略分列 2026-09-15】与方向列互补:只挑非单边的正套/跨品种/月差片段。
                "deriv": _extract_deriv_strategy(advice, te),
            })

    # 【同源合并 2026-09-14】替换旧的"只留最新一篇":同机构同品种的多篇合并成一条统一观点。
    # flatten=True 让要点压成单行(否则换行会撑破 markdown 表格单元格);
    # text_cap=200 给材料预算封顶(21 品种 × 4 机构 × 200 字仍在 payload 28000 以内)。
    items: list[dict] = []
    for rec in _merge_same_source_items(recs, flatten=True, text_cap=200, trade_cap=120):
        items.append({
            "variety": f"{rec['code']}({name_map.get(rec['code'], '')})",
            "report_id": rec.get("report_id"),  # 供单品种总结卡的来源观点对比跳转原件(/api/research/{id}/file)
            "report_ids": rec.get("report_ids") or [],
            "titles": rec.get("titles") or [],  # 合并后该机构全部篇目(材料里全列,便于 LLM 判断覆盖面)
            "merged_count": rec.get("merged_count") or 1,
            "source": rec.get("source") or "未知",
            "source_raw": rec.get("source_raw") or rec.get("source") or "未知",  # 归一前原名(前端 tooltip)
            "title": rec.get("title") or "(未命名)",
            "direction": rec.get("direction") or "中性",
            "confidence": f"{round(float(rec['conf_num']) * 100)}%" if rec.get("conf_num") is not None else "—",
            "opinion": rec.get("text") or "",
            "trade": rec.get("trade") or "",
            # 【衍生策略 2026-09-15】与方向列分列呈现(正套/反套/跨品种/月差/多X空Y);
            # 目前只供前端「来源观点对比表」,不进总结 prompt(进了就得重跑全部总结)。
            "deriv": rec.get("deriv") or "",
        })
    return items


def _build_daily_summary_md(items: list[dict], llm_body: str, generated_at: str) -> str:
    """拼接落盘 md:元信息头(HTML 注释,渲染前剥掉)+ 标题 + LLM 正文。"""
    head = (
        f"<!-- AgentSense 每日总结 | generated_at:{generated_at} "
        f"| reports:{len(items)} -->\n\n"
    )
    return head + llm_body.strip() + "\n"


def _build_coverage_table(date: str, reports: list[dict], rtype: str = "") -> str:
    """覆盖表 markdown(代码拼接,不经 LLM):固定列出全部 ACTIVE_VARIETIES 品种。

    【功能】需求二(2026-09-11 周报 / 2026-09-11 日报扩展):固定 21 品种,每行:
            品种 | 名称 | 计数 | 是否有新研报(✓/—)。列头随口径:
            - 周报口径(date=周起点):「本周周报数 | 本周有新研报」
            - 日报口径(date=单日):「当日研报数 | 当日有新研报」
    【参数】date: 周报=周起点(周日) / 日报=单日;reports: 已按口径过滤的 done 研报行
            (多品种研报按 varieties 字段拆分计数,与 _collect_daily_report_items 同口径;
            池外品种段计数归零不列出额外行);rtype: ''=日报 / '周报'。
    【返回】markdown 表格字符串;无研报行时表格仍在(✓ 列全填 —),保证"固定全部品种"。
    """
    codes = [c for c in _active_variety_list()]  # 固定顺序:ACTIVE_VARIETIES 定义顺序
    name_map = {k: v["name"] for k, v in VARIETY_METADATA.items()}
    counts: dict[str, int] = {}
    for r in reports:
        rcodes = [c.strip().upper() for c in str(r.get("varieties") or "").split(",") if c.strip()] \
            or [str(r.get("variety") or "").upper()]
        for c in dict.fromkeys(rcodes):
            counts[c] = counts.get(c, 0) + 1
    r_ = _rtype_norm(rtype)
    if r_ == "周报":
        head, mark = "| 品种 | 名称 | 本周周报数 | 本周有新研报 |", "| --- | --- | --- | --- |"
        has_new = lambda n: n > 0  # noqa: E731  周报一周一篇,有即"本周有新周报"
    elif r_ == "季报":
        head, mark = "| 品种 | 名称 | 本季季报数 | 本季有新研报 |", "| --- | --- | --- | --- |"
        has_new = lambda n: n > 0  # noqa: E731  季报一季一篇,有即"本季有新季报"
    else:
        head, mark = "| 品种 | 名称 | 当日研报数 | 当日有新研报 |", "| --- | --- | --- | --- |"
        has_new = lambda n: n > 0  # noqa: E731  当日有研报即标 ✓
    lines = [head, mark]
    for c in codes:
        n = counts.get(c, 0)
        lines.append(f"| {c} | {name_map.get(c, '')} | {n} | {'✓' if has_new(n) else '—'} |")
    return "\n".join(lines)


# 兼容别名:周报覆盖表生成(周报口径专用)。
def _build_weekly_coverage_table(week_start: str, reports: list[dict]) -> str:
    return _build_coverage_table(week_start, reports, "周报")


def _parse_llm_views_table(body: str) -> tuple[dict[str, str], dict[str, str]]:
    """从 LLM 生成的"观点总表"markdown 解析观点与头寸两列。

    【返回】(views, trades): {品种代码: 一句话核心观点} 与 {品种代码: 一句话头寸建议}。
    【兼容两种列数】
            - 6 列(2026-09-11 新 prompt): 品种|发行方|方向|置信度|观点|头寸
              → views=倒数第2列, trades=末列;
            - 5 列(旧格式): 品种|发行方|方向|置信度|观点 → views=末列, trades 空。
    【为什么】总表的"一句话核心观点/头寸建议"需保持 LLM 提炼模式(参考 8.30 周报);
            LLM 输出后此处按品种代码归键, 供 _build_daily_views_table 重建总表。
    """
    import re as _re

    views: dict[str, str] = {}
    trades: dict[str, str] = {}
    if not body:
        return views, trades
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 5:
            continue
        m = _re.match(r"^([A-Z0-9]{1,6})(?:\(|$)", cells[0].strip())
        if not m:
            continue
        code = m.group(1)
        # 跳过分隔行(|---|---|...)
        if all(set(c) <= {"-", ":", " "} for c in cells):
            continue
        if len(cells) >= 6:
            opinion, trade = cells[-2], cells[-1]
        else:
            opinion, trade = cells[-1], ""
        opinion = opinion.strip().replace("|", "/")
        trade = trade.strip().replace("|", "/")
        if opinion and opinion not in ("—", "-"):
            views[code] = opinion
        if trade and trade not in ("—", "-"):
            trades[code] = trade
    return views, trades


def _parse_llm_conflict_views(body: str) -> dict[str, str]:
    """从 LLM 的「二、观点对比与冲突」节解析 {品种代码: 一句话观点}(总表观点的兜底源)。

    【为什么】2026-09-11 实测:LLM 经常不按 prompt 输出第一节总表(直接写二节),
            导致 _parse_llm_views_table 解析为空 → 总表回退 opinion 长串。而
            「二」节要求"每个品种一条、一句话写明分歧/共识",LLM 稳定输出且
            覆盖全部品种 → 从二节提取每个品种的观点作总表"一句话核心观点"。
    【格式】2026-09-17 起二节改为表格,但两种写法都兼容:
            - 表格行: `| SC | 多空对立 | 华泰 多(高)—… <br> 国君 空—… | 焦点 | 根源 |`
              → 取第 3 列「各家观点与逻辑」(<br> 换成";"作一句);
            - 列表行(旧格式,兼容历史缓存 md): "- SC原油:…" / "- SC(原油):…" / "- SC:…"
              / 中文名开头: "- 原油:…" / "- 对二甲苯:…"
    """
    import re as _re

    # 中文名 → 代码反查(多别名优先精确名,再按包含匹配)
    name_to_code: dict[str, str] = {}
    for code, meta in VARIETY_METADATA.items():
        nm = str(meta.get("name") or "")
        if nm:
            name_to_code[nm] = code
            name_to_code[nm.upper()] = code

    def _resolve_code(head: str) -> "tuple[str | None, str]":
        """行首/单元格首段 → (品种代码, 剩余观点文本);识别不了返回 (None, '')。"""
        # 先试代码开头: `SC原油:…` / `SC(原油):…` / `SC:…`
        m = _re.match(r"^([A-Z0-9]{1,6})(?:\([^)]*\))?[^\s:：]{0,12}\s*[:：]\s*(.+)$", head)
        if m:
            return m.group(1), m.group(2).strip()
        # 试中文名开头:`原油:…` / `对二甲苯:…`(取最长匹配别名)
        m2 = _re.match(r"^([^\s:：]+?)\s*[:：]\s*(.+)$", head)
        if not m2:
            # 无冒号:表格单元格首列可能是纯代码(`| SC |`)或"代码+中文后缀"(`| SC原油 |`)
            m3 = _re.match(r"^([A-Z0-9]{1,6})(?![A-Za-z0-9])", head)
            if m3:
                return m3.group(1), ""
            nm = head.strip()
            code = name_to_code.get(nm) or name_to_code.get(nm.upper())
            if not code:
                for cname, ccode in name_to_code.items():
                    if len(cname) >= 2 and nm.startswith(cname):
                        return ccode, ""
            return code, ""
        nm, opinion = m2.group(1).strip(), m2.group(2).strip()
        # 精确命中 → 用;否则看是否有别名是开头的子串
        code = name_to_code.get(nm) or name_to_code.get(nm.upper())
        if not code:
            for cname, ccode in name_to_code.items():
                if len(cname) >= 2 and nm.startswith(cname):
                    code = ccode
                    break
        return code, opinion

    views: dict[str, str] = {}
    if not body:
        return views
    in_conflict = False
    for line in body.splitlines():
        ls = line.strip()
        if _re.match(r"^#{2,3}\s*二、", ls):
            in_conflict = True
            continue
        if _re.match(r"^#{2,3}\s*[三四]、", ls):
            in_conflict = False
            continue
        if not in_conflict:
            continue
        # ── 表格行(2026-09-17 新格式): `| 品种 | 分歧状态 | 各家观点与逻辑 | 焦点 | 根源 |`
        if ls.startswith("|") and ls.endswith("|"):
            cells = [c.strip() for c in ls.strip("|").split("|")]
            if len(cells) < 3:
                continue
            if all(set(c) <= {"-", ":", " "} for c in cells):  # 表头分隔行
                continue
            code, _rest = _resolve_code(cells[0])
            if not code:
                continue
            # 观点取「各家观点与逻辑」列(第 3 列);列数不足 3 时退化取末列
            opinion = cells[2] if len(cells) >= 3 else cells[-1]
            opinion = _re.sub(r"\s*<br\s*/?\s*>\s*", "；", opinion)
            opinion = opinion.replace("|", "/").strip()
            if opinion and opinion not in ("—", "-"):
                views[code] = opinion
            continue
        # ── 列表行(旧格式): `- SC:…` / `- 原油:…`
        if not _re.match(r"^[-*]\s+", ls):
            continue
        content = _re.sub(r"^[-*]\s+", "", ls)
        code, opinion = _resolve_code(content)
        if not code:
            continue
        if opinion and opinion not in ("—", "-"):
            views[code] = opinion
    return views


def _repair_research_aggregates(dry_run: bool = False) -> dict:
    """聚合 JSON 对账自愈:done 研报行缺失的品种聚合条目,从 DB 重建(零 LLM)。

    【为什么需要】2026-09-11 发现:周报行(09-07~09-09 入库)处理成功但聚合条目
            缺失(历史版本缺陷,确切时点难考),导致逐品种观点总览周报口径下
            有行无数据、与周报总结不对齐。DB 里结论(conclusion_md 按品种段)、
            方向/置信度/数据点(structured_data.varieties)齐全 → 直接重建。
    【逻辑】1) 遍历全部 done 行,读聚合中该行 id 是否存在于任一品种文件;
            2) 缺失行:按行 varieties 拆品种,逐品种重建条目 upsert(结论取
               `## {code} 结论` 段,方向/置信度/数据点取 structured_data 对应项);
            3) dry_run=True 只统计不写。
    【返回】{"checked", "missing_rows", "repaired_entries", "details"}。
    """
    from tradingagents.dataflows.research_data import (
        RESEARCH_DIR,  # 【调用包】聚合目录单一事实源(勿在调用方硬编码 ~/.tradingagents/...)
        load_research_data,  # 【调用包】读各品种聚合(判 id 是否在)
        update_research_report_fields,  # 【调用包】就地刷新聚合条目字段(类型回填同步)
        upsert_research_report,
    )

    db = get_db()
    rows = [r for r in db.list_research_reports(limit=5000) if r.get("status") in ("done", "summarized")]  # 【2026-09-24】含 summarized(观点总览对账)
    # 【按品种文件精确对账】缺失判定 = 行 varieties 里的某品种,其 {CODE}_research.json
    # 中没有该行 id。(此前用全局 id 集合误判:条目在别的品种文件里也算"已存在",
    # 导致 127/229 等 SC 周报漏补。)
    agg_by_code: dict[str, set[int]] = {}
    # 【类型同步 2026-09-29】同时记录聚合里该 id 的 report_type,用于修"陈旧类型"。
    #   场景:report_type 在 DB 侧被回填/自愈(如存量季报认定、_self_heal_report_type),
    #   聚合 JSON 仍是旧值('')——观点总览读的是聚合,于是按季报/周报筛永远差这一条。
    #   只补缺失条目无法覆盖这种情况,必须按字段刷新。
    agg_types: dict[tuple[str, int], str] = {}
    # 【单一事实源】用 research_data.RESEARCH_DIR(而非再拼一次 ~/.tradingagents/external_data):
    #   两处各写一份路径,一旦目录配置调整就会"对账扫的是 A、写入的是 B",静默失配。
    ext_dir = Path(RESEARCH_DIR)
    for f in ext_dir.glob("*_research.json"):
        code = f.stem.replace("_research", "").upper()
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        ids = set()
        for rec in data.get("reports") or []:
            with contextlib.suppress(Exception):
                rid_rec = int(rec.get("id"))
                ids.add(rid_rec)
                agg_types[(code, rid_rec)] = (rec.get("report_type") or "").strip()
        agg_by_code[code] = ids
    repaired = 0
    retyped = 0
    details: list[str] = []
    missing_rows = 0
    for r in rows:
        rid = int(r["id"])
        row_codes = [c.strip().upper() for c in str(r.get("varieties") or "").split(",") if c.strip()] \
            or [str(r.get("variety") or "").upper()]
        miss_codes = [c for c in row_codes if rid not in agg_by_code.get(c, set())]
        row_type = (r.get("report_type") or "").strip()
        # 【类型陈旧】条目已在聚合里、但聚合的类型与 DB 不一致 → 定向刷新该字段。
        #   只在 DB 有确定类型时刷(DB 空则不动,免得把已有值抹成空)。
        stale_codes = [
            c for c in row_codes
            if rid in agg_by_code.get(c, set())
            and row_type
            and agg_types.get((c, rid), "") != row_type
        ]
        if stale_codes:
            if not dry_run:
                for c in stale_codes:
                    with contextlib.suppress(Exception):
                        if update_research_report_fields(c, rid, {"report_type": row_type}):
                            retyped += 1
            else:
                retyped += len(stale_codes)
            details.append(
                f"行 {rid}({(r.get('title') or '')[:24]}) 类型刷新 → {row_type}({','.join(stale_codes)})"
            )
        if not miss_codes:
            continue
        missing_rows += 1
        # 重建条目:只补缺失的品种(miss_codes),已存在的品种不动
        try:
            structured = json.loads(r.get("structured_data") or "{}")
        except (TypeError, ValueError):
            structured = {}
        vitems = [v for v in (structured.get("varieties") or []) if v.get("variety")]
        codes = [c.strip().upper() for c in str(r.get("varieties") or "").split(",") if c.strip()] \
            or [str(r.get("variety") or "").upper()]
        if vitems:
            by_code = {str(v["variety"]).upper(): v for v in vitems}
            codes = list(dict.fromkeys(codes + list(by_code.keys())))
        else:
            by_code = {}
        conclusions = {
            code: seg
            for code in codes
            if (seg := _daily_variety_segment(r.get("conclusion_md") or "", code)).strip()
        }
        report_type = (r.get("report_type") or "").strip()
        publish_date = (r.get("publish_date") or "").strip()[:10]
        data_point_keys = (
            "spot_price", "basis", "social_inventory", "mill_inventory",
            "warehouse_receipts", "operating_rate", "supply", "demand", "costs",
            "processing_margin", "target_price", "key_events", "rating",
        )
        if not dry_run:
            for code in miss_codes:
                item = by_code.get(code, {})
                upsert_research_report(
                    code,
                    {
                        "id": rid,
                        "title": r.get("title") or "",
                        "source": _normalize_source(r.get("source") or ""),
                        "uploaded_at": r.get("uploaded_at") or "",
                        "publish_date": publish_date,
                        "report_type": report_type,
                        "varieties": codes,
                        "direction": item.get("direction") or r.get("direction") or "中性",
                        "confidence": item.get("confidence") if item.get("confidence") is not None else (r.get("confidence") if code == codes[0] else None),
                        "report_advice": str(item.get("operation_advice") or ""),
                        "conclusion": conclusions.get(code, ""),
                        "data_points": {k: val for k, val in item.items() if k in data_point_keys},
                    },
                )
        repaired += len(miss_codes)
        details.append(f"行 {rid}({(r.get('title') or '')[:24]}) 补 {len(miss_codes)} 品种({','.join(miss_codes)})")
    result = {
        "checked": len(rows),
        "missing_rows": missing_rows,
        "repaired_entries": 0 if dry_run else repaired,
        "retyped_entries": retyped,  # 【类型同步 2026-09-29】聚合 report_type 被刷新成 DB 口径的条目数
        "details": details[:50],
    }
    if dry_run:
        result["repaired_entries"] = repaired  # dry_run 时表示"将要补的数量"
    return result


def _prev_variety_view(code: str, all_rows: list[dict], before_date: str, rtype: str = "") -> dict | None:
    """取该品种在 before_date 之前最近一份 done 对应类型研报的方向/观点/头寸(延续展示用)。

    【为什么】2026-09-11 用户定:当日无新研报的品种,观点不是"—"而是
            「当日无新研报,观点延续{最近日期}(昨日研报观点)」—— 观点有连续性。
    【参数】code: 品种代码;all_rows: 全部研报行(list_research_reports 结果);
            before_date: 严格早于此日期(日报=当日 / 周报=周起点);rtype: 口径。
    【返回】{"date", "direction", "opinion", "trade"} 或 None(历史也无研报)。
    """
    r_norm = _rtype_norm(rtype)
    cands: list[dict] = []
    for r in all_rows:
        if r.get("status") not in ("done", "summarized"):  # 【2026-09-24】含 summarized(延续昨日回溯历史观点)
            continue
        rt = (r.get("report_type") or "").strip()
        # 口径隔离(与总结链路同口径):周期口径只收本类型;日报口径排除周报/季报、保留未知。
        if r_norm:
            if rt != r_norm:
                continue
        elif rt in ("周报", "季报"):
            continue
        rd = _report_date(r)
        if not rd or rd >= before_date:
            continue
        rcodes = [c.strip().upper() for c in str(r.get("varieties") or "").split(",") if c.strip()] \
            or [str(r.get("variety") or "").upper()]
        if code not in rcodes:
            continue
        cands.append(r)
    if not cands:
        return None
    cands.sort(key=lambda r: _report_date(r), reverse=True)
    r = cands[0]
    seg = _daily_variety_segment(r.get("conclusion_md") or "", code)
    te = _parse_trade_elements(seg)
    direction = te.get("方向") or r.get("direction") or "中性"
    opinion = _extract_key_opinion(seg, max_len=150)
    trade = _merge_trade_cell(te, str(r.get("report_advice") or ""))
    return {"date": _report_date(r), "direction": direction, "opinion": opinion, "trade": trade}


def _build_daily_views_table(date: str, items: list[dict], rtype: str = "",
                             llm_views: dict[str, str] | None = None,
                             llm_trades: dict[str, str] | None = None,
                             all_rows: list[dict] | None = None) -> str:
    """代码生成"当日/本周观点总表"(固定全部 ACTIVE_VARIETIES 品种 + 覆盖表列)。

    【功能】需求二扩展(2026-09-11):第一节"当日/本周观点总表"固定 21 品种(无研报也
            列出,观点列为 —),列:
            品种 | 名称 | 发行方 | 方向 | 置信度 | 一句话核心观点 | 当日/本周研报数 | 是否有新研报
    【一句话核心观点】2026-09-11 用户定:使用之前的 LLM 提炼模式(参考 8.30 周报总表
            —— 同一品种多家机构合并、观点是精炼一句话)。llm_views 传 _parse_llm_views_table
            的解析结果时优先用 LLM 观点;解析不到该品种时回退 items 的 opinion 首句
            (保证每行有内容、不编造)。发行方/方向/置信度/研报数来自 items 确定性统计。
    【参数】date: 日报=单日 / 周报=周起点;items: _collect_daily_report_items 产物
            (variety 形如 "SC(原油)");rtype: ''=日报 / '周报';llm_views: 可选,品种→观点。
    【返回】markdown 表格字符串。
    """
    codes = [c for c in _active_variety_list()]
    name_map = {k: v["name"] for k, v in VARIETY_METADATA.items()}
    llm_views = llm_views or {}
    llm_trades = llm_trades or {}  # 【2026-09-14】与 llm_views 对称归一:两者都可缺省(None→空 dict)
    by_code: dict[str, list[dict]] = {}
    for it in items:
        code = (it.get("variety") or "").split("(")[0].strip().upper()
        if code:
            by_code.setdefault(code, []).append(it)
    r_ = _rtype_norm(rtype)
    if r_ == "周报":
        count_head, new_head = "本周研报数", "本周有新研报"
        title = "## 一、本周观点总表"
    elif r_ == "季报":
        count_head, new_head = "本季研报数", "本季有新研报"
        title = "## 一、本季观点总表"
    else:
        count_head, new_head = "当日研报数", "当日有新研报"
        title = "## 一、当日观点总表"

    def _row(c: str) -> str:
        it_list = by_code.get(c, [])
        if not it_list:
            # 【观点延续 2026-09-11 用户定】当日无新研报 → 显示"观点延续{最近日期}(昨日观点)",
            # 方向/头寸也沿用最近一份研报;历史也无研报才显示全 —。
            prev = _prev_variety_view(c, all_rows or [], date, rtype) if all_rows else None
            if prev:
                op = str(prev.get("opinion") or "").strip().replace("\n", " ").replace("|", "/")
                # 取第一句(按句号/分号切),延续文案保持"一句话"观感
                first_sent = re.split(r"[。；;\n]", op)[0].strip() if op else ""
                body_txt = (first_sent or str(prev.get("direction") or "中性"))[:45]
                no_new = {"周报": "本周无新研报", "季报": "本季无新研报"}.get(r_, "当日无新研报")
                view = f"{no_new}，观点延续{prev['date']}（{body_txt}）"
                t = str(prev.get("trade") or "").strip().replace("|", "/")
                trade = (t[:60] + "…") if len(t) > 60 else (t or "—")
                return (
                    f"| {c} | {name_map.get(c, '')} | — | {prev.get('direction') or '中性'} | — | {view} | {trade} | 0 | — |"
                )
            return f"| {c} | {name_map.get(c, '')} | — | — | — | — | — | 0 | — |"
        # 【源数=观点数不变量 2026-09-14】同源合并后每条 item 对应一家机构,三列天然一一对应;
        # 这里仍按归一后名去重兜底,保证即使合并链路回归也不会出现「发行方 4 个、方向 6 个」。
        triples: list[tuple[str, str, str]] = []
        _seen_src: set[str] = set()
        for x in it_list:
            s = _normalize_source(str(x.get("source") or "未知"))
            if s in _seen_src:
                continue  # 合并后不应发生;真发生也只留首条,优先保住三列等长
            _seen_src.add(s)
            triples.append((s, str(x.get("direction") or "中性"), str(x.get("confidence") or "—")))
        sources = " / ".join(t[0] for t in triples)
        dirs = " / ".join(t[1] for t in triples)
        confs = " / ".join(t[2] for t in triples)
        # 一句话核心观点:优先 LLM 提炼(之前模式,总表解析或二节兜底);缺失回退 items opinion 首句。
        view = (llm_views.get(c) or "").strip()
        if view and view != "—" and len(view) > 60:  # LLM 观点超长时截断,保持"一句话"观感
            view = view[:60] + "…"
        if not view or view == "—":
            views_tmp = []
            for x in it_list:
                op = str(x.get("opinion") or "").strip().replace("\n", " ").replace("|", "/")
                views_tmp.append(op[:40] if op else "—")
            view = " ; ".join(v for v in views_tmp if v != "—") or "—"
            if len(view) > 80:
                view = view[:80] + "…"
        # 头寸建议列:优先 LLM 一句话概括(2026-09-11 用户定,不用分号罗列);缺失回退
        # 代码合并(每条前带来源机构名、顿号连接,至少能分清建议归属)。
        trade = (llm_trades.get(c) or "").strip()
        if trade and trade != "—" and len(trade) > 60:
            trade = trade[:60] + "…"
        if not trade or trade == "—":
            trades = []
            for x in it_list:
                t = str(x.get("trade") or "").strip().replace("|", "/")
                if not t or t == "—":
                    continue
                src_name = str(x.get("source") or "").strip()
                label = f"{src_name}:{t}" if src_name else t
                if label not in trades:
                    trades.append(label)
            trade = "，".join(trades) or "—"
            if len(trade) > 80:
                trade = trade[:80] + "…"
        # 【研报数口径 2026-09-14】合并后一个品种行只对应"家数"条材料,但这一列要显示
        # 真实研报**篇数** → 用 merged_count 求和(与覆盖表同口径,不会因合并而缩水)。
        n = sum(int(x.get("merged_count") or 1) for x in it_list)
        return f"| {c} | {name_map.get(c, '')} | {sources} | {dirs} | {confs} | {view} | {trade} | {n} | {'✓' if n > 0 else '—'} |"

    # 【一二级菜单 2026-09-11 用户定】按板块分块:一级=板块(### 标题,前端包折叠菜单),
    # 二级=板块内品种行;池内未列入板块的品种追加"其他"块,保证不丢。
    header = (
        f"| 品种 | 名称 | 发行方 | 方向 | 置信度 | 一句话核心观点 | 头寸建议 | {count_head} | {new_head} |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"
    )
    blocks = [title]
    seen: set[str] = set()
    for sector, sector_codes in _VARIETY_SECTORS.items():
        in_pool = [c for c in sector_codes if c in ACTIVE_VARIETIES]
        if not in_pool:
            continue
        seen.update(in_pool)
        blocks.append(f"### {sector}\n{header}\n" + "\n".join(_row(c) for c in in_pool))
    tail = [c for c in codes if c not in seen]
    if tail:
        blocks.append(f"### 其他\n{header}\n" + "\n".join(_row(c) for c in tail))
    return "\n\n".join(blocks)


def _daily_summary_meta(md: str) -> dict:
    """读 md 头部元信息 → {generated_at, reports};旧文件/缺标 → 空值兜底。"""
    m = re.search(r"<!--.*?generated_at:([^;|]+).*?reports:(\d+).*?-->", md or "")
    return {"generated_at": m.group(1).strip() if m else "", "reports": int(m.group(2)) if m else 0}


def _generate_daily_summary(date: str, force: bool = False, rtype: str = "") -> dict:
    """生成(或复用)某日研报总结;返回 {ok, date, content, generated_at, reports} 或 {ok: False, error}。

    【参数】rtype: 口径('周报'=周报总结,''/日报=每日总结),决定收哪些行与落盘目录。
    """
    r_norm = _rtype_norm(rtype)
    path = _research_daily_path(date, rtype)
    if path.is_file() and not force:  # 已生成过 → 直接读文件,不重复烧 LLM(force 重跑覆盖)
        content = path.read_text(encoding="utf-8")
        meta = _daily_summary_meta(content)
        return {"ok": True, "date": date, "content": content, "cached": True, **meta}

    db = get_db()
    _rows = db.list_research_reports(limit=500)  # 【变量】全量行(观点总表的"延续昨日"回溯用)
    items = _collect_daily_report_items(_rows, date, rtype)
    if not items:
        _kind = {"周报": "周报", "季报": "季报"}.get(r_norm, "日报")
        return {"ok": False, "error": f"{date} 在库没有已完成分析的{_kind}研报"}

    def _titles_label(it: dict) -> str:
        """材料抬头里的篇目标识(合并条目列出全部篇目 + 篇数),模块级实现见 _merged_titles_label。"""
        return _merged_titles_label(it)

    payload = "\n\n".join(
        f"【{i['variety']}】{i['source']}{_titles_label(i)} 方向:{i['direction']}"
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
    kind_title = {"周报": "研报周报总结", "季报": "研报季报总结"}.get(r_norm, "研报每日总结")
    prompt = (
        "你是中国商品期货研究主管。下面是同一天各券商/机构研报的逐品种分析结论摘要。"
        f"请输出一份「{kind_title}」(markdown, 全中文, 不写套话),固定四节:\n"
        "## 一、当日观点总表\n"
        "(markdown 表格,列固定为:品种 | 发行方 | 方向 | 置信度 | 一句话核心观点 | 头寸建议;"
        "同一品种的多家机构合并为一行,发行方/方向/置信度各用「/」分隔且一一对应,"
        "只有一家就只写一个;【同源已合并 2026-09-14】材料中同一机构同一品种的多篇研报"
        "已经合并成一条,因此发行方、方向、置信度三者必须**一家一个、数量完全相等**,"
        "严禁为同一机构重复列出两个方向或两个置信度,严禁出现「某机构两篇分别…」"
        "「某机构三份报告观点不一」这类按篇罗列的表述——同一机构的内部倾向请在"
        "「一句话核心观点」里融合成一句;材料中出现的全部品种每行一个全部列出,"
        "严禁省略、严禁「其余从略/均从略」之类的合并表述;\n"
        "【重点1】「一句话核心观点」请用精炼一句话概括该品种当日各家逻辑的共识或分歧"
        "(如「供增需减兑现、冲高回落,均关注海峡通行与进口回升」),不要罗列数据;\n"
        "【重点2】「头寸建议」必须是一句话概括(不超过 40 字):综合该品种各家研报的"
        "头寸/仓位/操作建议后融成一句,有分歧要点明(如「华泰建议轻仓做多而国泰建议观望」"
        "或「两家一致建议多配 10%~20%」),严禁用分号罗列各家原文、严禁照抄交易要素原文)\n"
        "## 二、观点对比与冲突\n"
        "(markdown 表格,列固定为:品种 | 分歧状态 | 各家观点与逻辑 | 分歧焦点 | 分歧根源;\n"
        "覆盖材料中出现的全部品种,一个品种一行、一行不多一行不少:\n"
        "①「分歧状态」按机构间方向关系选择,必须与该行机构数量自洽:\n"
        "   仅一家机构 → 「单一观点」(严禁写「节奏分歧」「多空对立」——没有对手方,谈不上分歧);\n"
        "   ≥2 家且方向全同(含全部中性) → 「一致看多 / 一致看空 / 一致中性」;\n"
        "   ≥2 家且看多与看空并存 → 「多空对立」;\n"
        "   ≥2 家同向但幅度/时点判断明显不同 → 「节奏分歧」;\n"
        "②「各家观点与逻辑」必须逐家列出,格式:机构 方向(置信度)—一句话核心逻辑,多家之间用 <br> 分行,\n"
        "家数须与总表发行方一一对应,严禁笼统写「各家」「部分机构」或把多家揉成一句;\n"
        "③「分歧焦点」写分歧具体落在哪个变量上(如「投产节奏 vs 需求回补力度」),\n"
        "无分歧的品种此列写共识逻辑一句话;\n"
        "④「分歧根源」点明根源类型:数据口径 / 时间尺度 / 关注权重不同(无分歧写「—」);\n"
        "严禁省略品种、严禁多品种合并成一行)\n"
        "## 三、可信程度分析\n"
        "(按置信度与论据扎实程度给各观点分层:可信度高/中/低,点明哪些依据有具体数字、"
        "哪些只是定性判断;多空明显一边倒时提示拥挤风险)\n"
        "## 四、综合结论与关注要点\n"
        "(汇总当日净倾向,列出未来 1~2 周最值得跟踪的 2~3 个数据/事件)\n"
        "写作要求:只依据下面材料,严禁编造数据;总表与第二节都必须覆盖全部品种,"
        "第二节为表格化逐家对比,总篇幅放宽到 2400 字左右。\n"
    )
    if hist_ctx:
        prompt += (
            "---\n【历史背景参考(其他日期历史研报片段,非当日材料)】\n"
            + hist_ctx
            + "\n第一~三节(总表/对比/可信度)只依据当日材料;上述历史参考仅可在"
            "「四、综合结论与关注要点」中引用并标注发布日期,严禁与当日材料混写。\n"
        )
    # 【周期归一 2026-09-29】date 为周期键:prompt 里给区间而非单日,口径与前端一致。
    if r_norm:
        _start, _end = _period_range(date, r_norm)
        _label = {"周报": "研报周区间(每周日起始)", "季报": "研报季度区间(该季首月 1 日起)"}[r_norm]
        prompt += f"---\n{_label}:{_start} ~ {_end}\n\n{payload[:28000]}"
    else:
        prompt += f"---\n研报日期:{date}\n\n{payload[:28000]}"
    try:
        client = create_llm_client(
            config["llm_provider"],
            config.get("quick_think_llm", config["deep_think_llm"]),
            no_thinking=True,  # 【2026-09-22】研报问答关思考链: 首字 4.1s→0.7s, 有 RAG 上下文支撑质量无损
        )
        result = client.get_llm().invoke(prompt)
        body = str(result.content if hasattr(result, "content") else result).strip()
    except Exception:
        logger.warning("Daily research summary LLM call failed for %s", date, exc_info=True)
        return {"ok": False, "error": "LLM 生成失败,请稍后重试"}
    if not body:
        return {"ok": False, "error": "LLM 返回为空,请重试"}

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    head_md = (
        f"<!-- AgentSense 每日总结 | generated_at:{generated_at} "
        f"| reports:{len(items)} -->\n\n"
    )
    # 【总表重建 2026-09-11】需求二扩展:第一节"当日/本周观点总表"= 代码固定 21 品种
    #           + 覆盖表列(研报数/是否有新研报),其中「一句话核心观点」保持之前 LLM
    #           提炼的模式(参考 8.30 周报总表):从 LLM 输出的总表解析各品种观点,其余
    #           列(发行方/方向/置信度/计数)由 items 确定性统计。LLM 总表段本身不落盘
    #           (避免重复),重建后的表格放在 LLM 正文前。
    import re as _re2

    try:
        _llm_views, _llm_trades = _parse_llm_views_table(body)
        if not _llm_views:  # 【2026-09-11】LLM 常不输出总表 → 兜底从"二、观点对比与冲突"节解析观点
            _llm_views = _parse_llm_conflict_views(body)
        _views_table = _build_daily_views_table(
            date, items, rtype, llm_views=_llm_views, llm_trades=_llm_trades, all_rows=_rows
        )
    except Exception:  # noqa: BLE001 总表失败不影响主总结落盘(回退纯 LLM 正文)
        logger.warning("views table build failed for %s rtype=%s", date, rtype, exc_info=True)
        _views_table = ""
    # 剥离 body 中 LLM 输出的总表段:从「## 一、…总表」到「## 二、」之间的内容丢弃,
    # 保留二~四节(兼容 LLM 用 ## / ### 两种标题级别)。
    _body2 = _re2.sub(
        r"#{2,3}\s*一、[^\n]*总表.*?(?=#{2,3}\s*二、)",
        "",
        body,
        flags=_re2.S,
    ).strip()
    content = head_md + (_views_table + "\n\n" if _views_table else "") + _body2 + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    # 【同源合并校验 2026-09-14】LLM 若仍按篇罗列同一机构 → 记 warning 并回传 merge_alert
    # (只报警不改写正文;前端拿到非空列表可提示"建议重跑",不影响本次落盘)。
    merge_alert = _detect_per_report_listing(body, [str(i.get("source") or "") for i in items])
    if merge_alert:
        logger.warning("daily summary %s still lists same-source reports per report: %s",
                       date, merge_alert)
    return {"ok": True, "date": date, "content": content, "cached": False,
            "generated_at": generated_at, "reports": len(items), "merge_alert": merge_alert}


@app.route("/api/research/daily")
def api_research_daily_list():
    """每日/周报/季报总结:可用周期列表(库内研报周期 ∪ 已生成文件),标注哪些已生成。

    【参数】report_type 查询参数('周报'/'季报' 各自口径,空=日报口径)——三类周期与文件分开。
    """
    rtype = (request.args.get("report_type") or "").strip()
    r_norm = _rtype_norm(rtype)
    db = get_db()
    generated = {p.stem for p in _summary_dir(rtype).glob("????-??-??.md")}
    if r_norm == "周报":
        # 【周报口径 2026-09-11】旧单日命名文件归到周起点,与日期列表口径一致。
        generated = {d for d in (_week_start(x) for x in generated) if d}
    elif r_norm == "季报":
        # 【季报口径 2026-09-29】非季首月 1 日的旧命名文件归到季度起点,口径一致。
        generated = {d for d in (_quarter_start(x) for x in generated) if d}
    return jsonify({
        "dates": [
            {"date": d, "generated": d in generated}
            for d in _research_daily_dates(db, rtype)
        ],
    })


@app.route("/api/research/daily/generate", methods=["POST"])
def api_research_daily_generate():
    """生成(或复用)某周期总结:POST json {date, report_type};LLM 同步调用,前端需 loading 等待。"""
    data = request.get_json(silent=True) or {}
    date = str(data.get("date") or "").strip()[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        return jsonify({"ok": False, "error": "date 参数格式应为 YYYY-MM-DD"}), 400
    rtype = str(data.get("report_type") or "").strip()
    return jsonify(_generate_daily_summary(date, force=bool(data.get("force")), rtype=rtype))


@app.route("/api/research/daily/<date>")
def api_research_daily_get(date: str):
    """读取已生成的某周期总结 md;未生成 → {exists: False}(前端可再点生成)。

    【参数】report_type 查询参数('周报'读 research_weekly,'季报'读 research_quarterly,空读 research_daily)。
    【周期归一】周报:date 为周起点,兼容迁移前的单日旧文件(找该周区间内最早一份);
            季报:date 为季度起点,兼容非季首月命名的旧文件(找该季度区间内最早一份)。
    """
    date = (date or "").strip()[:10]
    rtype = (request.args.get("report_type") or "").strip()
    r_norm = _rtype_norm(rtype)
    path = _research_daily_path(date, rtype)
    if not path.is_file() and r_norm in ("周报", "季报"):
        # 旧版命名兼容:找该周期区间内最早的 .md 文件
        _start, _end = _period_range(date, r_norm)
        sdir = _summary_dir(rtype)
        if _start and sdir.is_dir():
            old = sorted(
                p for p in sdir.glob("????-??-??.md")
                if _start <= p.stem <= _end
            )
            if old:
                path = old[0]
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
    """某周期某品种总结 md 路径(research_variety/{daily|weekly|quarterly}/{date}_{CODE}.md)。"""
    r = _rtype_norm(rtype)
    sub = {"周报": "weekly", "季报": "quarterly"}.get(r, "daily")
    return RESEARCH_VARIETY_DIR / sub / f"{date}_{code}.md"


def _collect_variety_report_items(rows: list[dict], date: str, code: str, rtype: str = "") -> list[dict]:
    """某天涉及 code 的全部 done 对应类型研报 → 逐篇条目(整段结论+四指标,供单品种总结)。

    【与 _collect_daily_report_items 的区别】那边只留 150 字短摘(一天全品种要覆盖面),
            这边保留整段「## {code} 结论」(单品种预算充足,LLM 要梳理完整逻辑链);
            四指标用 _row_research_fund_metrics 取"该品种段"({**r, variety: code}
            保证多品种研报不串到主品种段)。日期/类型/置信度口径与每日总结完全一致。
            同源合并(2026-09-14)与每日总结同口径:同机构多篇先合并成一条(同篇双采去重 +
            方向多数票 + 置信度均值 + 结论段按新→旧拼接 + 四指标按名去重留最新),
            这样 LLM 材料里"一个机构只有一块",它就会按机构给统一观点而不是按篇罗列。
    """
    code = (code or "").strip().upper()
    r_norm = _rtype_norm(rtype)
    name_map = {k: v["name"] for k, v in VARIETY_METADATA.items()}
    agg_cache: dict[str, dict] = {}
    recs: list[dict] = []
    for r in rows:
        if r.get("status") not in ("done", "summarized"):  # 【2026-09-24】含 summarized(历史观点保留)
            continue
        rdate = _report_date(r)
        # 【周期归一 2026-09-29】date 为周期键:周报按周区间、季报按季度区间(标题报告期优先)、
        #   日报仍精确匹配单日。
        if not _in_period(date, rdate, (r.get("title") or ""), r_norm):
            continue
        # 口径隔离:周报/季报口径只收本类型;日报口径排除周报与季报,但保留未知类型行。
        rt_row = (r.get("report_type") or "").strip()
        if r_norm:
            if rt_row != r_norm:
                continue
        elif rt_row in ("周报", "季报"):
            continue
        codes = [c.strip().upper() for c in str(r.get("varieties") or "").split(",") if c.strip()] \
            or [str(r.get("variety") or "").upper()]
        if code not in codes:
            continue
        seg = _daily_variety_segment(r.get("conclusion_md") or "", code)
        te = _parse_trade_elements(seg)
        conf = _variety_agg_conf(code, r.get("id"), agg_cache)
        if conf is None and codes[0] == code:
            conf = r.get("confidence")  # 主品种/单品种回退 DB 行值;次品种取不到不标(—)
        advice = _row_variety_advice(r, code)  # 研报原文策略原句(逐品种;旧行无 → "")
        recs.append({
            "code": code,
            "source": _normalize_source(str(r.get("source") or "未知")),
            "source_raw": r.get("source") or "未知",
            "rdate": rdate,
            "ts": r.get("uploaded_at") or "",
            "rid": r.get("id"),
            "order": len(recs),
            "title": r.get("title") or "(未命名)",
            "direction": te.get("方向") or r.get("direction") or "中性",
            "conf_num": (float(conf) if conf is not None else None),
            "text": seg,
            "body": seg,
            "trade": _merge_trade_cell(te, str(r.get("report_advice") or "")),
            # 【衍生策略 2026-09-15】与方向列分列(正套/反套/跨品种/月差/多X空Y)。同步给
            # 单品种链路:材料里现成有这么一句,后续若要喂进 prompt 可直接取。
            "deriv": _extract_deriv_strategy(advice, te),
            "metrics": _row_research_fund_metrics({**r, "variety": code}, fallback_text=seg),
        })
    # 【同源合并 2026-09-14】保留换行(材料不是表格,多篇结论段之间要留空行便于 LLM 分辨);
    # text_cap 放大到 2000:整段结论本来就长,这里只做"防极端"的兜底截断。
    items: list[dict] = []
    for rec in _merge_same_source_items(recs, text_cap=2000):
        items.append({
            "code": rec.get("code") or code,
            "name": name_map.get(code, ""),
            "source": rec.get("source") or "未知",
            "source_raw": rec.get("source_raw") or rec.get("source") or "未知",
            "title": rec.get("title") or "(未命名)",
            "titles": rec.get("titles") or [],
            "report_id": rec.get("report_id"),
            "report_ids": rec.get("report_ids") or [],
            "merged_count": rec.get("merged_count") or 1,
            "dup_dropped": rec.get("dup_dropped") or 0,
            "direction": rec.get("direction") or "中性",
            "confidence": f"{round(float(rec['conf_num']) * 100)}%" if rec.get("conf_num") is not None else "—",
            "segment": rec.get("text") or "",
            "trade": rec.get("trade") or "",
            "deriv": rec.get("deriv") or "",  # 【2026-09-15】衍生策略(正套/跨品种),与方向列互补
            "metrics": rec.get("metrics") or [],
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
    r_norm = _rtype_norm(rtype)  # 【周期归一 2026-09-29】区间/单日切换
    path = _variety_summary_path(date, code, rtype)
    if path.is_file() and not force:  # 已生成过 → 直接读文件,不重复烧 LLM(force 重跑覆盖)
        content = path.read_text(encoding="utf-8")
        meta = _daily_summary_meta(content)
        return {"ok": True, "date": date, "code": code, "content": content, "cached": True, **meta}

    db = get_db()
    # 【口径一致 2026-09-29】与 _research_daily_dates 同一取数(周报/季报走全表),
    #   否则"列表里有这个周期、点生成却说库里没有"。
    items = _collect_variety_report_items(_research_rows_for_type(db, rtype), date, code, rtype)
    if not items:
        return {"ok": False, "error": f"{date} 在库没有涉及 {code} 的已完成分析研报"}

    payload = "\n\n".join(
        f"【{i['source']}{_merged_titles_label(i)}】方向:{i['direction']}(置信度 {i['confidence']})\n"
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
            metric_lines.append(f"- {i['source']}{_merged_titles_label(i)}: {vals}")
    metrics_block = ("\n\n【当日关键数据备查(研报结构化提取,仅供「当日关键数据」节参考)】\n"
                     + "\n".join(metric_lines)) if metric_lines else ""
    name = {k: v["name"] for k, v in VARIETY_METADATA.items()}.get(code, "")
    # 【周期归一 2026-09-29】date 为周期键:prompt 标题与描述用区间,口径与前端一致。
    if r_norm:
        _start, _end = _period_range(date, r_norm)
        if r_norm == "周报":
            date_label = f"{date} 周({_start}~{_end})"
            date_desc = f"下面是 {_start}~{_end} 这一周(每周日起始)多家机构关于该品种研报的逐品种结论。"
        else:  # 季报
            date_label = f"{date} 季度({_start}~{_end})"
            date_desc = f"下面是 {_start}~{_end} 这一季度(该季首月 1 日起)多家机构关于该品种研报的逐品种结论。"
    else:
        date_label = date
        date_desc = "下面是同一天多家机构关于该品种研报的逐品种结论。"
    # 【RAG 增强】同品种近期历史研报片段作背景参考(非当日材料),与每日总结同款、失败零影响。
    hist_ctx = _rag_context_for_variety(f"{code} {name} 基本面 观点", variety=code, limit=2)
    prompt = (
        f"你是{code}({name})期货研究员。{date_desc}"
        f"请输出一份「{date_label} {code}({name})单品种研报总结」(markdown, 全中文, 不写套话, 500~800 字),"
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
        "写作要求:只依据下面材料,严禁编造;严禁使用 markdown 表格;严禁写套话。"
        "【同源已合并 2026-09-14】材料中同一机构的多篇研报已经合并成一条"
        "(方向为该机构内部多数意见、置信度为组内均值),请**按机构**归纳其统一观点,"
        "严禁按篇逐份复述,也不要写「某机构两篇分别认为…」这类罗列。\n"
    )
    if hist_ctx:
        prompt += (
            "---\n【历史背景参考(其他日期历史研报片段,非当日材料)】\n"
            + hist_ctx
            + "\n上述历史参考仅可在「倾向与跟踪要点」中引用并标注发布日期,"
            "严禁与当日材料混写。\n"
        )
    # 【周期归一 2026-09-29】date 为周期键:prompt 尾部给周期区间,口径与前端一致。
    if r_norm:
        _start, _end = _period_range(date, r_norm)
        _label = {"周报": "研报周区间", "季报": "研报季度区间"}[r_norm]
        _tail = "(每周日起始)" if r_norm == "周报" else "(该季首月 1 日起)"
        prompt += f"---\n{_label}:{_start} ~ {_end}{_tail}\n\n{payload[:28000]}{metrics_block[:4000]}"
    else:
        prompt += f"---\n研报日期:{date}\n\n{payload[:28000]}{metrics_block[:4000]}"
    try:
        client = create_llm_client(
            config["llm_provider"],
            config.get("quick_think_llm", config["deep_think_llm"]),
            no_thinking=True,  # 【2026-09-22】研报问答关思考链: 首字 4.1s→0.7s, 有 RAG 上下文支撑质量无损
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
    # 【同源合并校验 2026-09-14】与每日总结同款:LLM 若仍按篇罗列同一机构 → merge_alert。
    merge_alert = _detect_per_report_listing(body, [str(i.get("source") or "") for i in items])
    if merge_alert:
        logger.warning("variety summary %s %s still lists same-source reports per report: %s",
                       date, code, merge_alert)
    return {"ok": True, "date": date, "code": code, "content": content, "cached": False,
            "generated_at": generated_at, "reports": len(items), "merge_alert": merge_alert}


@app.route("/api/research/variety-summary/<date>")
def api_research_variety_summary_list(date: str):
    """某日单品种总结列表:按当日逐品种条目聚合品种,标注哪些已生成(前端据此自动排队)。

    【返回】{date, report_type, varieties: [{code, name, report_count, directions,
            report_rows(逐份研报 来源/方向/衍生策略/置信度/id, 供来源观点对比表),
            summary_md|None, generated_at, reports(条数, 缓存元信息), cached}]}。
    """
    date = (date or "").strip()[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        return jsonify({"ok": False, "error": "date 参数格式应为 YYYY-MM-DD"}), 400
    rtype = (request.args.get("report_type") or "").strip()
    db = get_db()
    # 品种聚合直接复用每日总结的逐品种条目(同日期/类型/方向口径),按 code 前缀分组;
    # 同时攒逐份研报的来源/方向/置信度,供前端单品种卡渲染"来源观点对比"表(2026-09-09)。
    items = _collect_daily_report_items(_research_rows_for_type(db, rtype), date, rtype)
    groups: dict[str, dict] = {}
    for it in items:
        code = (it.get("variety") or "").split("(")[0].strip().upper()
        if not code:
            continue
        g = groups.setdefault(code, {"code": code, "report_count": 0, "merged_total": 0,
                                     "directions": [], "report_rows": []})
        # 【口径 2026-09-14 同源合并】report_count 现在是"家数"(合并后一行=一家机构),
        # merged_total 是真实研报篇数 → 前端显示「N 家 · 共 M 篇」,两个数都不失真。
        g["report_count"] += 1
        g["merged_total"] += int(it.get("merged_count") or 1)
        d = it.get("direction") or "中性"
        if d not in g["directions"]:
            g["directions"].append(d)
        g["report_rows"].append({
            "report_id": it.get("report_id"),
            # 合并条目:该机构全部篇目(前端"研报"单元格列多个链接)与全部原件 id
            "report_ids": it.get("report_ids") or [],
            "titles": it.get("titles") or [],
            "merged_count": it.get("merged_count") or 1,
            "source": it.get("source") or "未知",
            "source_raw": it.get("source_raw") or it.get("source") or "未知",
            "title": it.get("title") or "(未命名)",
            "direction": d,
            "confidence": it.get("confidence") or "—",
            # 【衍生策略 2026-09-15】来源观点对比表新增一列:只放非单边的正套/反套/
            # 跨品种/月差/多X空Y 片段(单边方向已由 direction 列承载),取不到 → 空(前端 —)。
            "deriv": it.get("deriv") or "",
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


# ---------------------------------------------------------------------------
# 「国君数据」页(2026-09-29 新增): 四源统一快照 + 全池覆盖矩阵
#   与上面两个「数据仓库」卡的区别: 那两张卡各查一个源、只出原始表;
#   本组接口按**品种**把四源聚合成带分位的指标, 并把「无数据」与「接口失败」分开报。
#   聚合逻辑全在 gtja_module.py(可脱离 Flask 单测), 路由只做参数校验 + 序列化。
# ---------------------------------------------------------------------------
@app.route("/api/gtja/overview")
def api_gtja_overview():
    """国君四源统一快照: 基差 + 交易所仓单 + 国君自建库存 + 加工/进口利润。

    【参数】variety(必填); start/end(YYYY-MM-DD, 可选, 缺省近 240 天);
            range_days(可选, 缺省 240)。
    【返回】gtja_module.fetch_overview 的 payload(200)。未配密钥/日期倒置 → ok:False(200)。
    【注意】四源并发 + 短超时(见 gtja_module); 两个全量端点冷启动首次约 6~18s, 之后走 6h 磁盘缓存。
    """
    code = (request.args.get("variety") or "").strip().upper()
    if not code:
        return jsonify({"ok": False, "error": "缺少品种参数(variety)"}), 400
    start = (request.args.get("start") or "").strip()
    end = (request.args.get("end") or "").strip()
    try:
        range_days = int(request.args.get("range_days") or 240)
    except (TypeError, ValueError):
        range_days = 240
    range_days = max(5, min(range_days, 3650))  # 【护栏】防越界成负数/超长拖垮上游
    try:
        import gtja_module  # 【调用包】聚合层(局部导入, 不拖慢主模块加载)

        payload = gtja_module.fetch_overview(code, start, end, range_days)
    except Exception as exc:  # noqa: BLE001 - 聚合层异常按接口错误上报, 不让 500 冒泡
        logger.warning("GTJA overview 失败(%s): %s", code, exc, exc_info=True)
        return jsonify({"ok": False, "error": f"聚合失败: {type(exc).__name__}: {exc}"[:200]}), 200
    return jsonify(payload)


@app.route("/api/gtja/coverage")
def api_gtja_coverage():
    """全池覆盖矩阵: 池内每个品种在四个源上「有数据 / 确定无数据 / 接口失败」。

    【参数】range_days(可选, 缺省 30); varieties(可选, 逗号分隔, 缺省池内全部)。
    【返回】{"ok":True, rows:[{code,name,basis,warehouse,inventory,profit}], summary, sources}。
    【为什么需要】国君基差只覆盖有现货指数的品种(SC/PX/FU/LU/CJ 实测为空),
      先在页面标出来, 用户就不会以为"模块坏了"。
    """
    codes_arg = (request.args.get("varieties") or "").strip()
    if codes_arg:
        codes = [c.strip().upper() for c in codes_arg.split(",") if c.strip()][:60]
    else:
        codes = _active_variety_list()
    try:
        range_days = int(request.args.get("range_days") or 30)
    except (TypeError, ValueError):
        range_days = 30
    range_days = max(5, min(range_days, 3650))
    try:
        import gtja_module

        payload = gtja_module.fetch_coverage(codes, range_days)
    except Exception as exc:  # noqa: BLE001
        logger.warning("GTJA coverage 失败: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": f"聚合失败: {type(exc).__name__}: {exc}"[:200]}), 200
    return jsonify(payload)


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


def _delete_research_original_file(r: dict) -> bool:
    """只删研报原件文件,保留聚合 JSON / RAG 向量 / DB 行(需求三 2026-09-11 原件 14 天清理)。

    【与 _delete_research_report_full 的区别】那边是"整篇删除"(聚合+原件+DB 行+向量全删);
            本函数是"只清原件"——DB 行保留(结论/观点总览/问答仍可用),file_path 置空、
            file_deleted=1 标记,聚合 JSON 与 RAG 向量不动。
    【返回】True=已处理(含原本就无文件/已删过的幂等);False=行无效。
    """
    if not r or not r.get("id"):
        return False
    rid = int(r["id"])
    db = get_db()
    fp = r.get("file_path") or ""
    if fp:
        try:
            Path(fp).unlink(missing_ok=True)
        except OSError as e:
            logger.warning("Failed to delete research original file %s: %s", fp, e)
    # 无论文件是否存在都标记(幂等:重复清理同一行不报错)
    with contextlib.suppress(Exception):
        db.update_research_report(rid, file_path="", file_deleted=1)
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
        # 【2026-09-17】?deep=1 追加"抽样自召回体检" —— chunks/reports 只反映
        # sqlite 侧的块数,证明不了"检得出来"(真机:4648 块里 64% 的向量其实
        # 不在检索索引,状态接口却全绿)。体检拿正文重新编码后检索自身,给召回率。
        deep = (request.args.get("deep") or "").strip().lower() in ("1", "true", "yes")
        try:
            sample_n = int(request.args.get("sample") or 30)
        except ValueError:
            sample_n = 30
        if _RAG_WORKER_MODE:  # 【分支】web 主进程:走 RAG 子进程
            status = _rag_call("status", timeout_s=60)
            health = _rag_call("health", timeout_s=900, sample=sample_n) if deep else None
        else:
            from tradingagents.rag import service  # 【调用包】懒导入重依赖

            status = service.index_status()
            health = service.health_check(sample=sample_n) if deep else None
        body = {"rag_enabled": status["chunks"] > 0, **status}
        if health is not None:
            body["health"] = health
        return jsonify(body)
    except Exception as e:
        logger.warning("RAG status failed: %s", e)
        return jsonify({"rag_enabled": False, "chunks": 0, "reports": 0})


# 【变量】RAG 增量入库并发标志(手动按钮/每日定时/采集后自动触发共用,防重入)。
_rag_indexing = False
# 【变量】最近一次 RAG 增量入库结果(GET 状态回传,前端展示"上次新增 N 份")。
_rag_index_last: dict = {}


# 【功能】把"已入库但还没向量"的新研报补进 RAG 索引(增量、幂等)。
# 【GET】返回 {"indexing": bool, "chunks", "reports", "last": 上次结果};前端按钮据此显示进度。
# 【POST】请求体 {"force": bool(全量重建), "since_days": int(只处理近 N 天,0=全部)};
#         已在跑返回 409;启动成功返回 {"status":"started"}。
# 【关键逻辑】daemon 线程跑 _rag_index_pending(本地 embedding,零 LLM,通常秒级~分钟级),
#           结果写 alert 便于告警中心可见;失败不影响主流程。
@app.route("/api/research/rag/index", methods=["GET", "POST"])
def api_research_rag_index():
    global _rag_indexing
    if request.method == "GET":
        try:
            from tradingagents.rag import is_available  # 【调用包】懒导入(轻量检查)

            if not is_available():
                status = {"chunks": 0, "reports": 0}
            elif _RAG_WORKER_MODE:  # 【分支】web 主进程:走 RAG 子进程
                status = _rag_call("status", timeout_s=60)
            else:
                from tradingagents.rag import service  # 【调用包】懒导入重依赖

                status = service.index_status()
        except Exception:
            status = {"chunks": 0, "reports": 0}
        return jsonify({"indexing": _rag_indexing, **status, "last": _rag_index_last})

    if _rag_indexing:
        return jsonify({"status": "busy", "message": "RAG 索引入库正在进行中,请稍候"}), 409
    body = request.get_json(silent=True) or {}
    force = bool(body.get("force"))
    try:
        since_days = max(0, int(body.get("since_days") or 0))
    except (TypeError, ValueError):
        since_days = 0

    def _run_index():
        global _rag_indexing, _rag_index_last
        try:
            stats = _rag_index_pending(force=force, since_days=since_days)
            _rag_index_last = stats
            if stats.get("error"):
                with suppress(Exception):
                    get_db().create_alert(
                        "rag_index_error", "RAG index skipped",
                        str(stats["error"])[:200], severity="warning",
                    )
            else:
                with suppress(Exception):
                    get_db().create_alert(
                        "rag_index_done", "RAG index updated",
                        f"新索引 {stats['done']} / 跳过 {stats['skipped']} / 失败 {stats['failed']}"
                        f"(共 {stats['total']} 份 done,索引 {stats['chunks']} 片段)",
                        severity="info",
                    )
        except Exception as e:
            logger.exception("RAG index pending failed")
            with suppress(Exception):
                get_db().create_alert(
                    "rag_index_error", "RAG index failed", str(e)[:300], severity="error",
                )
        finally:
            _rag_indexing = False

    _rag_indexing = True
    threading.Thread(target=_run_index, daemon=True).start()  # 【调用函数】后台线程执行(不阻塞响应)
    return jsonify({"status": "started", "message": "新研报入库 RAG 已启动,后台处理中"})


def _conclusion_chunk_note() -> str:
    """【研报结论】块的读取说明(闸门②"身份标注"的提示词侧;单轮/多轮问答共用)。

    【为什么】`tradingagents.rag.chunking.conclusion_text` 会把系统对该篇研报的
    结构化归纳单独做成一条置首片段(见该函数注释的"四道闸门")。这条片段不是研报原文,
    必须在提示词里讲明来源,否则模型会把"系统归纳"当成研报原句照抄给用户 ——
    那正是"RAG 说的和总结说的不一致"的另一面(用户 2026-09-15 反馈)。
    """
    try:
        from tradingagents.rag.chunking import CONCLUSION_MARK  # 【调用包】纯函数模块,零重依赖
    except Exception:  # noqa: BLE001  模块缺失不影响问答
        CONCLUSION_MARK = "【研报结论·系统归纳｜非原文】"
    return (
        f"片段若以「{CONCLUSION_MARK}」开头,那是**系统对该篇研报的结构化归纳**"
        "(含系统识别的方向/策略与结论小节),不是研报原文:回答方向与策略时可以直接采信,"
        "但要说清这是系统归纳;同一篇研报若同时给出原文片段且两者冲突,以原文片段为准。\n"
    )


def _history_block(history: list[dict]) -> str:
    """把多轮对话历史拼成提示词片段(研报问答的"记忆")。

    【为什么需要】此前研报问答是纯单轮:前端历史只用于页面展示,从不回传后端,
            追问"那螺纹钢呢?""它成本端怎么样?"时模型看不到上文,只能瞎猜。
    【参数】history: [{"role":"user"/"assistant","content": str}],已在外层截断/清洗。
    【返回】可直接拼进提示词的字符串;history 为空时返回 ""。
    """
    if not history:
        return ""
    lines = ["【对话历史】(仅供参考上下文,回答只需针对最后一问;与片段冲突时以片段为准)"]
    for h in history:
        who = "用户" if h["role"] == "user" else "助手"
        lines.append(f"{who}: {h['content']}")
    lines.append("")
    return "\n".join(lines)


# 【功能】研报问答:检索研报片段(余弦近邻,可按品种/类型过滤)→ LLM 依据片段作答,
#         返回带编号引用(点击跳 /api/research/<id>/file 原件)。
# 【请求体】{"question"(必填), "variety"?(品种代码过滤), "report_type"?("日报"/"周报"/"季报"),
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
    if not variety:
        variety = _infer_variety_from_question(question)  # 【2026-09-22】问句识别品种兜底
    report_type = str(body.get("report_type") or "").strip() or None
    try:
        top_k = max(1, min(int(body.get("top_k") or 6), 30))  # 【2026-09-24】封顶 12→30(整月复盘)
    except (TypeError, ValueError):
        top_k = 6
    top_k = _llm_choose_top_k(question, variety, None, None, top_k)  # 【2026-09-24】LLM 选检索深度(ask 无日期)
    # 【多轮记忆】前端回传最近若干轮问答(最多 6 轮 = 12 条),用于:
    #   1) 检索查询改写 —— 追问"那螺纹钢呢?"单独检索毫无意义,拼上上文才有效;
    #   2) 提示词里带对话历史 —— 让模型理解指代("它""这个品种""上面那条")。
    raw_history = body.get("history")
    history: list[dict] = []
    if isinstance(raw_history, list):
        for h in raw_history[-12:]:
            if not isinstance(h, dict):
                continue
            role = str(h.get("role") or "").lower()
            content = str(h.get("content") or "").strip()
            if role in ("user", "agent", "assistant") and content:
                history.append({
                    "role": "user" if role == "user" else "assistant",
                    "content": content[:500],
                })
    # 检索用查询:最近一轮用户问题 + 当前问题(历史只取用户侧,避免答案里的措辞带偏检索)
    prior_user_turns = [h["content"] for h in history if h["role"] == "user"][-2:]
    retrieval_query = " ".join([*prior_user_turns, question]).strip() if prior_user_turns else question

    from tradingagents.rag import is_available  # 【调用包】懒导入

    if not is_available():
        return jsonify({
            "ok": False,
            "error": "RAG 未启用:需安装 chromadb 与 sentence-transformers,"
            "并运行 python scripts/backfill_rag_index.py 回填索引",
            "rag_enabled": False,
        })
    try:
        if _RAG_WORKER_MODE:  # 【分支】web 主进程:走 RAG 子进程(冷启动含模型加载,超时放宽)
            hits = _rag_call(
                "retrieve",
                timeout_s=240,
                query=retrieval_query,
                top_k=top_k,
                variety=variety,
                report_type=report_type,
            )
        else:
            from tradingagents.rag import service  # 【调用包】懒导入重依赖

            hits = service.retrieve_hits(
                retrieval_query, top_k=top_k, variety=variety, report_type=report_type,
                recent_meta_provider=_rag_recent_meta_provider,
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
            # 【2026-09-15】kind="conclusion" 的是【研报结论·系统归纳】块(见 chunking.conclusion_text)。
            "kind": h["metadata"].get("kind") or "body",
            "snippet": (h["text"] or "")[:200],
            "score": round(float(h.get("score", 0.0)), 4),
        }
        if include_ctx:
            c["text"] = h["text"] or ""
        citations.append(c)
    if _RAG_WORKER_MODE:  # 【分支】web 主进程:走 RAG 子进程
        context = _rag_call("format_context", timeout_s=60, hits=hits)
    else:
        from tradingagents.rag import service  # 【调用包】懒导入重依赖

        context = service.format_context(hits)
    # 【2026-09-22 数值增强】国君API+akshare 实时数据块(与 QA 多轮同策略)
    data_block = _qa_realtime_data_block(variety, question)  # 【2026-09-22】question 传技术块触发判断
    try:
        client = create_llm_client(
            config["llm_provider"],
            config.get("quick_think_llm", config["deep_think_llm"]),
        )
        from datetime import date as _date  # 【调用包】今天日期(片段里没有"当前时间"概念)

        prompt = (
            "你是中国商品期货研究助理。下面给出用户问题与若干研报片段(带编号,来自不同"
            "日期的历史研报)。请只依据片段内容用中文回答,回答中用 [n] 标注所引用的片段"
            "编号,引用观点/数据时注明其发布日期;片段不足以回答的部分明确说明,严禁编造。\n"
            f"【重要】今天是 {_date.today():%Y-%m-%d};片段的发布日期都只代表该研报的写作时间,"
            "不得把最晚的片段日期当成今天,涉及“最新/近期/当前”时以片段中日期最新的内容为准"
            "并说明该日期。\n"
            "若问题涉及多个方面(如技术面/基本面/宏观面),请逐方面作答;某方面片段未覆盖时"
            "只说明该方面不足,不要因部分缺失而整体拒答。\n"
            "【实时市场数据】块(若存在)是抓取时刻的行情数值,用于核对/补充研报中的数字;"
            "引用数据时注明其来源与日期,与研报片段冲突时两者都要说明。\n"
            + _conclusion_chunk_note()  # 【闸门②】结论块是系统归纳,必须讲明来源
            + (_history_block(history) if history else "")
            + f"用户问题:{question}\n\n"
            f"{context}{data_block}\n\n"
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
_QA_ANSWER_HISTORY_BUDGET = 400  # 改写历史里单条助理回答的预算(聪明截断,见下)
_QA_SELF_REF_MAX_CHARS = 4000  # 自指模式注入作答prompt的上轮回答全文上限
_QA_TERMS_MAX = 50  # 黑话词典注入改写prompt的条目上限(prompt 膨胀防线)


def _shrink_answer_for_history(content: str, budget: int = _QA_ANSWER_HISTORY_BUDGET) -> str:
    """助理回答进改写历史的"聪明截断":保留开头 + 保留标题/列表行(2026-09-13)。

    【为什么】追问常指向上轮回答中后部的"点"(如"那第二点呢"),从头截 budget 字
            会把指代对象截掉,改写器就消解不了指代;markdown 标题行(## ...)通常
            正是"点"的名字,优先保留。
    【策略】超预算时:开头 120 字打底 + 依次收录标题(#/-/*/>)、有序列表(数字.)
            与引用行(单行再截 120),直到预算用尽;不超预算原样返回。
    """
    if len(content) <= budget:
        return content
    head = content[:120]
    kept, remaining = head, budget - len(head)
    for line in content.splitlines()[1:]:
        ls = line.strip()
        if remaining <= 0:
            break
        if ls.startswith(("#", "-", "*", ">")) or re.match(r"^\d+[.、)]", ls):
            chunk = ls[:120]
            kept += "\n" + chunk
            remaining -= len(chunk) + 1
    return kept


# 自指/指代追问检测:命中则把上一轮回答全文作为附加材料块放进作答 prompt
# (混合模式:衔接对话的连续性,幻觉风险用"以研报片段为准"的标注压制)。
_QA_SELF_REF_PAT = re.compile(
    r"第[一二三四五六七八九十百\d]+[点条节段部分]|[上这]面[所说的]|你[刚上前](?:才|面)"
    r"|刚才[所说的]|上一[轮条次个]|前面[所说的]|展开[讲说]|细说|具体说[说下]"
)


def _qa_load_terms(limit: int = _QA_TERMS_MAX) -> list[dict]:
    """拉黑话词典(改写 prompt 注入 / 首轮直替换共用);任何异常返回 []。

    【降级】词典是"可降级增强"——加载失败只损失检索质量增益,绝不拖垮问答链路。
    """
    try:
        return get_db().list_qa_terms(limit=limit)
    except Exception as e:  # noqa: BLE001
        logger.warning("QA terms load failed: %s", e)
        return []


def _qa_apply_terms(question: str, terms: list[dict]) -> tuple[str, list[int]]:
    """首轮黑话直替换:最长词优先做子串替换,返回 (替换后query, 命中条目id列表)。

    【为什么只做首轮】有历史时改写 LLM 会拿着术语映射表做改写(更懂上下文),
            首轮没有 LLM 参与,词典是唯一的人工经验回灌通道。
    【注意】按词长降序替换可缓解"铁矿"误伤"铁矿石";仍可能链式误替换
            (A 的映射里含 B),词由人工录入保证质量,rewritten_query 落库可审计。
    """
    q = question
    hit_ids = []
    for t in sorted(terms, key=lambda x: len(str(x.get("term") or "")), reverse=True):
        term = str(t.get("term") or "").strip()
        mapping = str(t.get("mapping") or "").strip()
        if term and mapping and term != mapping and term in q:
            q = q.replace(term, mapping)
            hit_ids.append(int(t.get("id") or 0))
    return q, [i for i in hit_ids if i]


def _rewrite_query_for_retrieval(
    history: list[dict], question: str, term_lines: list[str] | None = None
) -> str:
    """把追问+历史改写成独立检索 query;失败/无历史返回 ""(调用方回退原问题)。

    【为什么】多轮 RAG 关键步骤:用户追问"那成本端呢?"直接拿去向量检索命中率
            极低,需结合历史补全省略主语/代词成独立 query。
    【降级】history 空(首轮)跳过 LLM 返回 "";改写 LLM 抛异常/产物为空同样
            返回 "" —— 检索质量降一档,但问答链路永不被改写卡死。
            返回值同时就是落库的 rewritten_query("" = 首轮或回退,审计可辨;
            首轮词典替换命中时由调用方覆写为替换后 query,见 api_qa_ask)。
    【词典】term_lines 非空时注入术语映射参考(自进化 P2:口语词→标准检索词回灌)。
    """
    if not history:
        return ""
    try:
        lines = []
        for m in history:
            tag = "用户" if m.get("role") == "user" else "助理"
            content = m.get("content") or ""
            if m.get("role") != "user":
                content = _shrink_answer_for_history(content)  # 聪明截断:保开头+保标题行
            lines.append(f"{tag}:{content}")
        hist_text = "\n".join(lines)[:_QA_REWRITE_CHAR_BUDGET]
        terms_block = ""
        if term_lines:
            terms_block = (
                "术语映射参考(用户口语/黑话→标准检索词,历史或问题中出现时按映射改写):\n"
                + "\n".join(term_lines[:_QA_TERMS_MAX]) + "\n\n"
            )
        prompt = (
            "你是检索查询改写器。根据对话历史,把用户最新问题改写成一个不依赖上下文、"
            "可独立用于向量检索的中文查询。要求:\n"
            "- 补全省略主语与代词(如\"它\"\"这个品种\"→具体品种名),保留最新问题的真实意图;\n"
            "- 若最新问题已切换话题,以最新问题为准,不要强行拼接历史;\n"
            "- 只输出一行查询文本,不要解释、不要加引号。\n\n"
            f"{terms_block}"
            f"对话历史(供参考):\n{hist_text}\n\n"
            f"用户最新问题:{question}\n改写后的检索查询:"
        )
        client = create_llm_client(
            config["llm_provider"],
            config.get("quick_think_llm", config["deep_think_llm"]),
            no_thinking=True,  # 【2026-09-22】改写器同样关思考链: 它是同步阻塞在流式前的一环, 慢则拖累首字
        )
        result = client.get_llm().invoke(prompt)
        rewritten = str(result.content if hasattr(result, "content") else result).strip()
        rewritten = rewritten.splitlines()[0].strip().strip("\"'“”")[:_QA_REWRITE_QUERY_MAX]
        return rewritten
    except Exception as e:
        logger.warning("QA query rewrite failed, fallback to raw question: %s", e)
        return ""


# 【2026-09-24 整月复盘】宽窗口"复盘/总结"意图 → 自动放大检索深度 top_k。
# 背景: 9 月 TA 有 21 个交易日, 但 QA 路由 top_k 默认 6(封顶 12) ⇒ 整月复盘只取到
# 5~9 篇, 覆盖 1/4~1/3 交易日, 无法支撑"长时间结论的总结复盘"。时间分层采样
# (_temporal_sampling)已保证"每段保底取块", 但段数 = min(top_k, 跨度) —— top_k 不够
# 大, 分层也无从施展。这里: 问句含复盘意图词 + 日期跨度 ≥ 14 天时, 把 top_k 放大到
# min(30, 跨度天数), 让整月 21 个交易日都能被分层覆盖到(实测 top30 → 20/21 天)。
_RAG_REVIEW_HINTS = (
    "复盘", "总结", "梳理", "回顾", "整月", "全月", "整周", "整段", "长时间",
    "行情回顾", "走势复盘", "历史行情", "阶段", "演变", "脉络",
)


def _rag_review_top_k(question: str, date_from: str | None, date_to: str | None,
                      base_top_k: int) -> int:
    """宽窗口复盘意图 → 放大 top_k; 其余情况返回 base_top_k(行为不变)。"""
    q = question or ""
    if not any(h in q for h in _RAG_REVIEW_HINTS):
        return base_top_k
    span_days = 0
    if date_from and date_to:
        try:
            span_days = (datetime.strptime(date_to[:10], "%Y-%m-%d")
                         - datetime.strptime(date_from[:10], "%Y-%m-%d")).days
        except ValueError:
            span_days = 0
    if span_days >= 14:  # 宽窗口(≥2 周)才放大; 短窗口/无日期保持原样
        return max(base_top_k, min(30, span_days))
    if not date_from and not date_to:
        return max(base_top_k, 20)  # 无日期但明确复盘意图: 提默认深度(单轮 ask 场景)
    return base_top_k


def _llm_choose_top_k(question: str, variety: str | None, date_from: str | None,
                      date_to: str | None, base_top_k: int) -> int:
    """LLM 判断检索深度 top_k(3~30); 任何失败回退关键词规则→base。

    【为什么】关键词规则(_rag_review_hints)只覆盖固定词("复盘/总结/梳理"),
    而"近一个月供需演变""这段时间涨跌脉络"这类无固定关键词但要求长窗口的问题
    会漏判 ⇒ 检索深度仍 6, 覆盖不足。交给 LLM: 给问题+品种+日期窗口(带 few-shot
    实例), 让它按"检索覆盖面需求"选 3~30。每次问答多 ~1s(quick 模型 no_thinking),
    相对主回答 20~150s 可忽略; 失败自动降级, 绝不阻塞主流程。
    """
    try:
        client = create_llm_client(
            config["llm_provider"],
            config.get("quick_think_llm", config["deep_think_llm"]),
            no_thinking=True,  # 【2026-09-22】关思考链: 路由判断无需推理链
        )
        prompt = (
            "你是期货研报检索参数助手。根据用户问题判断检索多少篇研报片段最合适"
            "(top_k, 整数 3~30):\n"
            "实例:\n"
            "- 问'TA今天库存多少' → 6(单一最新事实)\n"
            "- 问'LU近日情况' → 10(近几天+当日)\n"
            "- 问'近两周供需变化' → 15(中周期演变)\n"
            "- 问'PTA九月行情复盘' → 28(整月覆盖, 需时间均匀采样)\n"
            "规则: 日期范围越大/要求越全面/越要复盘总结, top_k 越大; 单点查询取小值。\n"
            f"品种: {variety or '不限'} | 日期范围: {date_from or '不限'} ~ {date_to or '不限'}\n"
            f"用户问题: {question}\n"
            "只输出一个数字(top_k):"
        )
        result = client.get_llm().invoke(prompt)
        text = str(result.content if hasattr(result, "content") else result)
        m = re.search(r"(\d+)", text)
        if m:
            return max(3, min(30, int(m.group(1))))
    except Exception as e:  # noqa: BLE001  路由失败绝不影响主问答
        logger.warning("LLM top_k 路由失败, 回退关键词规则: %s", e)
    return _rag_review_top_k(question, date_from, date_to, base_top_k)


def _infer_variety_from_question(question: str) -> str | None:
    """从问句识别品种(用户未选品种筛选时的兜底, 2026-09-22)。

    【为什么】"LU近日情况"在**未选品种**时检索拿不到当天的 LU 日报: 不限品种时
    保底通道 min_sim=0.55, LU 日报语义分 0.44~0.49 被门槛挡掉(选品种时才进池)。
    从问句识别出品种后自动过滤, 免去用户必须手选。
    【匹配顺序】①中文名(取最长匹配, 低硫燃料油>燃料油) → ②合约代码(拉丁,
    前后不紧邻字母/数字, 防 CLU/M2501 误匹配)。
    【限定】只在 ACTIVE_VARIETIES(21 品种白名单)内识别; 识别不出返回 None。
    """
    q = (question or "").strip()
    if not q:
        return None
    import re

    from tradingagents.dataflows.commodity_futures import VARIETY_METADATA

    best, best_len = None, 0
    for code in ACTIVE_VARIETIES:
        name = (VARIETY_METADATA.get(code) or {}).get("name") or ""
        if name and name in q and len(name) > best_len:
            best, best_len = code, len(name)
    if best:
        return best
    for code in ACTIVE_VARIETIES:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(code)}(?![A-Za-z0-9])", q):
            return code
    return None


# 【仓单映射 2026-09-22】AgentSense 品种代码 → warrant 模块(四大交易所)索引键。
# 【2026-09-30 修】补两个缺失项(BZ / P), 改后 21 个池品种 21/21 精确命中:
#   - P(棕榈油) → DCE|p: 今日新入池, 有真实仓单(实测 2860 手);
#   - BZ(纯苯)  → DCE|bz: 原注释写「四所均无仓单日报」已过时 —— 大商所日报现含
#     纯苯(当日仓单 0 手, 日减 -525), 真删掉映射会让仓单块静默返回空。
# 【键格式】"交易所|代码", 大小写不可归一: 郑商所品种代码是大写(PTA/PX/CF...),
#   其余三所是小写(m/eg/lc/sc...)。曾想统一 lower() 比较 ⇒ 会漏掉 CZCE 全部品种。
# 【用途】本表是「仓单页品种池筛选」的唯一数据源: 前端不硬编码任何仓单代码,
#   由 overview/history 接口回传的 in_pool 字段驱动(见 _warrant_pool_keys)。
_WARRANT_KEY_MAP = {
    "TA": "CZCE|PTA", "CF": "CZCE|CF", "CJ": "CZCE|CJ", "PX": "CZCE|PX",
    "M": "DCE|m", "EG": "DCE|eg", "EB": "DCE|eb", "PG": "DCE|pg", "LH": "DCE|lh",
    "BZ": "DCE|bz", "P": "DCE|p",
    "LC": "GFEX|lc", "PS": "GFEX|ps", "SI": "GFEX|si",
    "SC": "SHFE|sc", "LU": "SHFE|lu", "FU": "SHFE|fu", "BU": "SHFE|bu",
    "RU": "SHFE|ru", "NR": "SHFE|nr", "BR": "SHFE|br",
}


# 【功能】仓单页「品种池筛选」用的键集合 —— 由 _WARRANT_KEY_MAP 派生(单一数据源)。
# 【返回】{"DCE|bz", "CZCE|PTA", ...}, 与 overview/history 接口的键格式逐字一致。
# 【为什么单独成函数】三处(总览标记 / 历史标记 / 研报仓单块)共用同一份池定义,
#   避免"品种池加了一个品种但仓单页没跟着进池"这类静默不同步。
def _warrant_pool_keys() -> set:
    return set(_WARRANT_KEY_MAP.values())


def _qa_warrant_block(variety: str) -> str:
    """研报问答「仓单块」(2026-09-22): 四大交易所官方仓单(产品级+仓库分布精简)。

    【数据源】warrant 模块(czce/dce/shfe/gfex 每日 16:30 爬取官方仓单日报,
    统一落 data/warrant/out|web)。比 get_futures_inventory 的国君源更新更准
    (实测国君 TA 库存停在 07-10, 而仓单模块今日 16:40 已更新)。
    【内容】①品种级: 最新交易日仓单量 + 日增减 + 单位; ②仓库级: 注册仓库数、
    厂库数、前 3 大仓库。任一步失败返回 ""(不拖垮问答)。
    """
    code = (variety or "").strip().upper()
    key = _WARRANT_KEY_MAP.get(code)
    if not key:
        return ""
    try:
        from warrant import history

        p = (history.get().get("products") or {}).get(key)
        if not p:
            return ""
        qty_map = p.get("qty") or {}
        if not qty_map:
            return ""
        dt = sorted(qty_map.keys())[-1]
        qty = qty_map.get(dt)
        chg = (p.get("change") or {}).get(dt, 0)
        unit = p.get("unit") or ""
        name = p.get("name") or code
        ex_cn = p.get("exchange_cn") or p.get("exchange") or ""
        lines = [f"仓单({ex_cn}官方, {dt}): {name} {qty} {unit}, 日增减 {chg:+g} {unit}"]
        # 仓库分布精简: 总数/厂库数/前 3 大
        try:
            import json
            import os

            det_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "data", "warrant", "web", "detail.json")
            det = json.load(open(det_path, encoding="utf-8")).get("detail", {}).get(key, {})
            whs = det.get("warehouses") or []
            if whs:
                total = len(whs)
                fk = sum(1 for w in whs if (w.get("type") or "") == "厂库")
                top = sorted(whs, key=lambda w: -(w.get("qty") or 0))[:3]
                top_s = "、".join(f"{w.get('name')}({w.get('qty')}{unit})"
                                  for w in top if (w.get("qty") or 0) > 0)
                lines.append(f"注册仓库 {total} 家(厂库 {fk}), 前3大: {top_s or '无'}")
        except Exception:  # noqa: BLE001  仓库分布失败只给产品级
            pass
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001  仓单块失败不影响问答
        logger.warning("QA 仓单块失败(variety=%s): %s", code, e)
        return ""


# 【技术分析触发词 2026-09-22】问句含这些词才注入技术/量化指标块(按需触发, 用户拍板)。
_TECH_QUERY_HINTS = (
    "技术面", "技术分析", "技术指标", "均线", "MA5", "MA10", "MA20", "MA60",
    "RSI", "MACD", "布林", "BOLL", "KDJ", "支撑", "压力位", "压力", "趋势",
    "动量", "超买", "超卖", "ATR", "金叉", "死叉", "多头排列", "空头排列",
    "持仓量", "量化", "波动率", "夏普",
)


def _qa_technical_block(variety: str, question: str = "") -> str:
    """研报问答「技术/量化分析块」(2026-09-22): 基于 akshare 日线手算指标。

    【触发】问句含 _TECH_QUERY_HINTS 任一词才计算(按需触发, 用户拍板);
    未触发/无品种/数据不足(<30 行)返回 ""。
    【指标】纯 numpy/pandas 手写(不装 ta-lib 等新依赖):
    趋势(MA5/10/20/60 排列) / 动量(RSI14, MACD12-26-9, 20日动量) /
    波动(BOLL20-2, ATR14, 年化波动率) / 量化(夏普粗算, 近5日持仓变化) /
    多空计分(5 项 ±1, -5~+5)。
    【口径】数据为 akshare 日线(收盘/持仓), 仅供技术面参考, 非观点来源。
    """
    code = (variety or "").strip().upper()
    if not code or not question or not any(h in question for h in _TECH_QUERY_HINTS):
        return ""
    try:
        import io
        from datetime import date, timedelta

        import pandas as pd

        from tradingagents.dataflows import commodity_futures as cf

        end = date.today().isoformat()
        start = (date.today() - timedelta(days=120)).isoformat()
        s = str(cf.get_futures_price(code, start, end) or "").strip()
        if not s:
            return ""
        df = pd.read_csv(io.StringIO(s))
        if df is None or len(df) < 30:
            return ""
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df["open_interest"] = pd.to_numeric(df["open_interest"], errors="coerce")
        df = df.dropna(subset=["close"]).reset_index(drop=True)
        if len(df) < 30:
            return ""
        close = df["close"]
        latest = close.iloc[-1]
        last_date = str(df["date"].iloc[-1])[:10]

        ma5 = close.rolling(5).mean().iloc[-1]
        ma10 = close.rolling(10).mean().iloc[-1]
        ma20 = close.rolling(20).mean().iloc[-1]
        ma60 = close.rolling(60).mean().iloc[-1]

        # RSI(14) Wilder 平滑
        diff = close.diff()
        up = diff.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        dn = (-diff.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
        rs = up / dn.replace(0, float("nan"))
        rsi = float((100 - 100 / (1 + rs)).iloc[-1]) if not pd.isna(rs.iloc[-1]) else 50.0

        # MACD(12,26,9)
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        dif = ema12 - ema26
        dea = dif.ewm(span=9, adjust=False).mean()
        macd_hist = float((dif - dea).iloc[-1])

        # BOLL(20,2)
        mid = close.rolling(20).mean()
        sd = close.rolling(20).std()
        boll_up = float((mid + 2 * sd).iloc[-1])
        boll_mid = float(mid.iloc[-1])
        boll_low = float((mid - 2 * sd).iloc[-1])

        # ATR(14)
        high = pd.to_numeric(df["high"], errors="coerce")
        low = pd.to_numeric(df["low"], errors="coerce")
        pc = close.shift(1)
        tr = pd.concat([high - low, (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
        atr = float(tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1])

        # 量化: 20日动量 / 年化波动率 / 夏普粗算
        ret = close.pct_change().dropna()
        mom20 = float(close.iloc[-1] / close.iloc[-21] - 1) * 100 if len(close) > 21 else float("nan")
        ann_vol = float(ret.tail(60).std() * (252 ** 0.5) * 100)
        sharpe = float(ret.tail(60).mean() / ret.tail(60).std() * (252 ** 0.5)) if ret.tail(60).std() > 0 else 0.0

        # 持仓变化(近5日)
        oi = df["open_interest"].dropna()
        oi_chg = float(oi.iloc[-1] / oi.iloc[-6] - 1) * 100 if len(oi) > 6 else 0.0

        # 多空计分(5 项 ±1)
        score = 0
        if ma5 > ma10 > ma20:
            score += 1
        elif ma5 < ma10 < ma20:
            score -= 1
        score += 1 if rsi > 50 else -1
        score += 1 if macd_hist > 0 else -1
        score += 1 if latest > ma20 else -1
        score += 1 if latest > boll_mid else -1
        verdict = "偏多" if score >= 2 else ("偏空" if score <= -2 else "中性")

        ma_state = "多头排列" if ma5 > ma10 > ma20 else ("空头排列" if ma5 < ma10 < ma20 else "纠缠")
        lines = [
            f"【技术分析】(akshare 日线, 截至 {last_date}, 纯技术面参考非观点):",
            f"- 均线: MA5={ma5:.0f} MA10={ma10:.0f} MA20={ma20:.0f} MA60={ma60:.0f} "
            f"({ma_state}), 现价 {latest:.0f}",
            f"- RSI(14)={rsi:.1f} ({'超买' if rsi > 70 else ('超卖' if rsi < 30 else '中性')}), "
            f"MACD DIF-DEA={macd_hist:+.1f} ({'红柱偏多' if macd_hist > 0 else '绿柱偏空'})",
            f"- BOLL(20,2): 上轨 {boll_up:.0f} / 中轨 {boll_mid:.0f} / 下轨 {boll_low:.0f}, "
            f"ATR(14)={atr:.0f}",
            f"- 量化: 20日动量 {mom20:+.1f}%, 年化波动率 {ann_vol:.1f}%, 夏普(粗算)={sharpe:.2f}, "
            f"近5日持仓 {oi_chg:+.1f}%",
            f"- 多空计分: {score:+d}/5 ({verdict})",
        ]
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001  技术块失败不影响问答
        logger.warning("QA 技术分析块失败(variety=%s): %s", variety, e)
        return ""


# 【量化面触发词 2026-09-22】问句含这些词才注入量化面分析块(方法论来源:
# qihuo-duokong-logic 技能 —— 库存周期/库存+基差+利润/反转信号)。
_QUANT_QUERY_HINTS = (
    "量化", "库存周期", "基差", "贴水", "升水", "去库", "补库", "被动去库",
    "主动建库", "被动建库", "主动去库", "库存利润", "正套", "反套", "跨期",
    "期限结构", "周期", "套利", "反转", "基差修复", "净多单", "库存高低",
    # 【2026-09-24】交易决策词: "XX 头寸建议"类问法也要触发量化面
    "头寸", "仓位", "建仓", "持仓建议", "减仓", "加仓",
)


def _qa_quant_block(variety: str, question: str = "") -> str:
    """研报问答「量化面分析块」(2026-09-22): 基于 qihuo-duokong-logic 技能方法论。

    【触发】问句含 _QUANT_QUERY_HINTS 任一词才计算; 未触发/无品种返回 ""。
    【方法论来源】用户级技能 qihuo-duokong-logic(仓颉蒸馏《期货多空逻辑》):
      ① 库存周期四象限(被动去库/主动建库/被动建库/主动去库)
      ② 库存+基差+利润交易系统(低库存+深贴水偏多, 高库存+高升水偏空; 矛盾转跨期)
      ③ 反转三信号(价格高低位+基差修复, 持仓大幅下降)
    【数据】基差(国君 API 40 天) / 仓单(四所官方, 日频) / 价格(akshare 120 天)
      / 利润(国君 processProfitData, 2026-09-28 接入, 供三指标共振)。
      ⚠️ 库存用仓单(新鲜)非国君库存(滞后); 利润未被平台覆盖的品种自动降级为"部分共振"。
    """
    code = (variety or "").strip().upper()
    if not code or not question or not any(h in question for h in _QUANT_QUERY_HINTS):
        return ""
    import io
    from datetime import date, timedelta

    import pandas as pd

    lines = []
    cur_b = None  # 基差块可能失败, 库存基差信号用它前判空
    inv_pos_now = None  # 仓单分位"低/高/中"(供 ④ 三指标共振表复用)
    # ── ① 基差(国君 40 天): 最新值/基差率/趋势/分位 ──
    try:
        from tradingagents.dataflows import gtja_api

        df = gtja_api.fetch_basis_df(code, (date.today() - timedelta(days=40)).isoformat(),
                                     date.today().isoformat())
        if df is not None and len(df) >= 2:
            df = df.sort_values("date").reset_index(drop=True)
            basis = pd.to_numeric(df["dom_basis"], errors="coerce").dropna()
            rate = pd.to_numeric(df["dom_basis_rate"], errors="coerce").dropna()
            if len(basis) >= 2:
                cur_b, cur_r = float(basis.iloc[-1]), float(rate.iloc[-1])
                # 基差近 5 日趋势(修复/走扩)
                if len(basis) >= 6:
                    b_5ago = float(basis.iloc[-6])
                    trend = "走强(贴水收窄/升水扩大)" if cur_b > b_5ago else "走弱(升水收窄/贴水扩大)"
                else:
                    trend = "数据不足"
                # 分位: 近 40 日基差相对位置(升贴水状态)
                # 【⚠️ 2026-09-29 修正】原写法 `pos = "高升水" if cur_b > b_hi*0.7 else
                #   ("深贴水" if cur_b < b_lo*0.3 else "中性")` 有两处缺陷:
                #   ① 用 `b_hi*0.7` / `b_lo*0.3` 当阈值 —— 这是把**数值大小**当**比例**用,
                #      只在区间跨 0 且对称时近似成立; 区间全负(基差持续为期货升水)时
                #      `cur_b > b_hi*0.7` 与 `cur_b < b_lo*0.3` 双双为假, 状态恒为"中性"。
                #   ② "升水/贴水"裸词无主体 —— basis = 现货 − 期货, 高处是**现货升水(期货贴水)**,
                #      与用户口语中的"期货升水"方向相反, 极易误读。
                #   现改为**区间内百分位**(与正负号解耦) + 主体词显式标注。
                b_hi, b_lo = float(basis.max()), float(basis.min())
                if b_hi - b_lo > 1e-9:
                    _bpct = (cur_b - b_lo) / (b_hi - b_lo)
                    _lvl = "高位" if _bpct >= 0.7 else ("低位" if _bpct <= 0.3 else "中位")
                    _side = "现货升水(期货贴水)" if cur_b > 0 else ("现货贴水(期货升水)" if cur_b < 0 else "平水")
                    pos = f"{_lvl}·{_side}(区间分位 {_bpct*100:.0f}%)"
                else:
                    pos = "区间无波动(平水)"
                lines.append(
                    f"基差: {cur_b:.0f} 元/吨(基差率 {cur_r*100:.1f}%), 近5日{trend}, "
                    f"40日区间 [{b_lo:.0f}, {b_hi:.0f}] → {pos}"
                )
    except Exception as e:  # noqa: BLE001
        logger.warning("QA 量化块-基差失败(%s): %s", code, e)

    # ── ② 库存周期四象限 + ③ 库存+基差信号(库存=四所仓单) ──
    try:
        from warrant import history

        wh = _WARRANT_KEY_MAP.get(code)
        p = (history.get().get("products") or {}).get(wh) if wh else None
        if p and (p.get("qty") or {}):
            qd = p["qty"]
            dates = sorted(qd.keys())
            series = pd.Series({d: qd[d] for d in dates if qd[d] is not None}).dropna()
            if len(series) >= 3:
                wh_now = float(series.iloc[-1])
                wh_prev = float(series.iloc[-4]) if len(series) >= 4 else float(series.iloc[0])
                wh_chg = (wh_now / wh_prev - 1) * 100 if wh_prev else 0.0
                wh_dir = "升" if wh_chg > 0.5 else ("降" if wh_chg < -0.5 else "平")
                # 价格 20 日方向
                from tradingagents.dataflows import commodity_futures as cf

                ps = str(cf.get_futures_price(code, (date.today() - timedelta(days=40)).isoformat(),
                                              date.today().isoformat()) or "")
                pclose = None
                if ps:
                    pdf = pd.read_csv(io.StringIO(ps))
                    pclose = pd.to_numeric(pdf["close"], errors="coerce").dropna()
                if pclose is not None and len(pclose) >= 20:
                    prc_20ago = float(pclose.iloc[-21])
                    prc_now = float(pclose.iloc[-1])
                    prc_dir = "涨" if prc_now > prc_20ago else "跌"
                    # 四象限
                    phase = {
                        ("涨", "降"): "被动去库(偏多起点)",
                        ("涨", "升"): "主动建库(偏多主升)",
                        ("跌", "升"): "被动建库(偏空起点)",
                        ("跌", "降"): "主动去库(偏空主跌)",
                        ("涨", "平"): "库存持平, 价格上行(观察能否转主动建库)",
                        ("跌", "平"): "库存持平, 价格下行(观察能否转主动去库)",
                    }.get((prc_dir, wh_dir), "数据不足")
                    lines.append(
                        f"库存周期(仓单近5日 {wh_chg:+.1f}% {wh_dir}, 价格20日{prc_dir}): "
                        f"{phase}"
                    )
                    # 库存+基差信号(库存分位 vs 基差状态)
                    # 【⚠️ 方向 2026-09-29 修正】cur_b = 现货 − 期货 ⇒ >0 = **期货贴水**,
                    #   <0 = **期货升水**。方法论「低库存+低利润+深贴水→单边做多」中的
                    #   贴水主体是**期货**。修正前此处写成 low_inv and cur_b < 0(当成贴水),
                    #   与 quant_module._quant_signals 同源错误, 一并纠正。
                    if len(series) >= 10 and cur_b is not None:
                        q_lo, q_hi = float(series.quantile(0.3)), float(series.quantile(0.7))
                        low_inv = wh_now <= q_lo
                        high_inv = wh_now >= q_hi
                        inv_pos_now = "低" if low_inv else ("高" if high_inv else "中")  # 供 ④ 共振表
                        if low_inv and cur_b > 0:
                            lines.append("库存基差信号: 低库存+期货贴水 → 偏多倾向(库存周期方法论)")
                        elif high_inv and cur_b < 0:
                            lines.append("库存基差信号: 高库存+期货升水 → 偏空倾向")
                        elif low_inv and cur_b < 0:
                            lines.append("库存基差信号: 低库存+期货升水(指标矛盾) → 勿单边追, 考虑跨期")
                        elif high_inv and cur_b > 0:
                            lines.append("库存基差信号: 高库存+期货贴水(指标矛盾) → 勿单边追")
    except Exception as e:  # noqa: BLE001
        logger.warning("QA 量化块-库存周期失败(%s): %s", code, e)

    # ── ③ 利润维度(2026-09-28 接入国君 processProfitData): 加工利润 / 进口利润 ──
    # 【为什么】qihuo-duokong-logic 的核心交易系统是"库存+基差+利润"三指标:
    #   基差给客观赔率(贴水做多/升水做空), 库存与利润给主观胜率
    #   (低库存→供不应求易涨; 低利润→亏损不可持续易反弹)。
    #   三指标共振 → 单边; 矛盾 → 转跨期。此前利润维度缺失, 胜率判断只能降级。
    prof_summary = None  # 供 ④ 三指标共振表复用
    try:
        from tradingagents.dataflows import gtja_api  # noqa: F811 - 库存块内已导入, 此处保证独立可用

        pdf = gtja_api.fetch_profit_df(code)
        if pdf is not None and not pdf.empty:
            parts = []
            prof_lows, prof_highs, prof_mids = [], [], []
            for lb, sub in pdf.groupby("label"):
                s = sub.sort_values("date")
                vals = pd.to_numeric(s["profit"], errors="coerce").dropna()
                if len(vals) < 20:
                    continue
                cur = float(vals.iloc[-1])
                # 同比口径 → 该口径全历史分位(方法论文档: 库存/利润用同比高低 + 环比趋势)
                hi, lo = float(vals.max()), float(vals.min())
                q_lo, q_hi = float(vals.quantile(0.3)), float(vals.quantile(0.7))
                pos = "低" if cur <= q_lo else ("高" if cur >= q_hi else "中")
                # 环比趋势(近 5 期)
                if len(vals) >= 6:
                    prev = float(vals.iloc[-6])
                    tr = "↑" if cur > prev * 1.02 else ("↓" if cur < prev * 0.98 else "→")
                else:
                    tr = "→"
                parts.append(f"{lb} {cur:+.0f}({pos}位{tr}, 区间[{lo:.0f},{hi:.0f}])")
                (prof_lows if pos == "低" else prof_highs if pos == "高" else prof_mids).append(lb)
            if parts:
                # 【多数决 2026-09-28】不能因"某个口径在高位"就整体判高: EG 实测 3 个口径中
                #   2 个中位 + 1 个高位, 若按"任一高位即高"会得出"单边做空"的过强结论。
                #   改为**多数口径**定方向; 低/高口径数相等 = 矛盾; 中位占多数 = 无指引。
                n_lo, n_hi = len(prof_lows), len(prof_highs)
                if n_lo > n_hi and n_lo >= len(prof_mids):
                    prof_dir = "低"
                elif n_hi > n_lo and n_hi >= len(prof_mids):
                    prof_dir = "高"
                elif n_lo and n_hi and n_lo == n_hi:
                    prof_dir = "矛盾"
                else:
                    prof_dir = "中"
                prof_summary = {"dir": prof_dir, "lows": prof_lows,
                                "highs": prof_highs, "n_labels": len(parts)}
                lines.append("利润: " + "; ".join(parts))
                # 利润的方向含义(方法论: 低利润→亏损不可持续→易反弹→偏多; 高利润→高供给→偏空)
                if prof_dir == "矛盾":
                    lines.append("利润含义: 多口径方向不一(能化多产品装置常见) → 以综合利润为准, 勿单口径下结论")
                elif prof_dir == "低":
                    lines.append("利润含义: 处历史低位 → 亏损不可持续, 供给端存收缩预期 → 偏多(利润维度)")
                elif prof_dir == "高":
                    lines.append("利润含义: 处历史高位 → 高利润刺激供给增量 → 偏空(利润维度)")
                else:
                    lines.append("利润含义: 处历史中位 → 利润无方向指引(中性)")
    except Exception as e:  # noqa: BLE001
        logger.warning("QA 量化块-利润维度失败(%s): %s", code, e)

    # ── 反转预警: 价格高低位 + 基差修复 + 持仓下降 ──
    try:
        from tradingagents.dataflows import commodity_futures as cf

        ps = str(cf.get_futures_price(code, (date.today() - timedelta(days=120)).isoformat(),
                                      date.today().isoformat()) or "")
        if ps:
            pdf = pd.read_csv(io.StringIO(ps))
            pdf["close"] = pd.to_numeric(pdf["close"], errors="coerce")
            pdf["open_interest"] = pd.to_numeric(pdf["open_interest"], errors="coerce")
            pdf = pdf.dropna(subset=["close"])
            if len(pdf) >= 30:
                close = pdf["close"]
                pct = (float(close.iloc[-1]) - float(close.min())) / (float(close.max()) - float(close.min()) + 1e-9)
                warns = []
                if pct > 0.8:
                    warns.append("价格处120日高位")
                elif pct < 0.2:
                    warns.append("价格处120日低位")
                oi = pdf["open_interest"].dropna()
                if len(oi) >= 6:
                    oi_chg = (float(oi.iloc[-1]) / float(oi.iloc[-6]) - 1) * 100
                    if oi_chg < -10:
                        warns.append(f"持仓近5日大降 {oi_chg:.0f}%")
                if warns:
                    lines.append("反转预警: " + " + ".join(warns) + " → 高位/低位+信号谨慎追单(反转三信号方法论)")
    except Exception as e:  # noqa: BLE001
        logger.warning("QA 量化块-反转失败(%s): %s", code, e)

    # ── ④ 三指标共振决策(方法论核心: 库存+基差+利润 → 四类策略) ──
    # 【四象表出处】qihuo-duokong-logic / cap-inventory-basis-profit.md:
    #   低库存+低利润+深贴水 → 单边做多(高赔率+高胜率)
    #   高库存+高利润+高升水 → 单边做空
    #   低库存+高利润+深贴水 → 跨期正套(指标矛盾, 单边易频繁止损)
    #   高库存+低利润+高升水 → 跨期反套
    # 【为什么单独成块】方法论文档明确"库存利润矛盾时单边频繁止损", 所以冲突时必须
    #   显式给出"转跨期"而不是硬选一个方向。库存取仓单分位(与 ② 同源), 基差取 cur_b。
    try:
        if cur_b is not None and prof_summary is not None and inv_pos_now is not None:
            low_inv, high_inv = inv_pos_now == "低", inv_pos_now == "高"
            # 利润档位: 与 ③ 同一口径(多数决)。"中/矛盾"两边都为假 → 只可能落到"未共振",
            # 不会把矛盾误当成低利润直接推出"单边做多"。
            p_dir = prof_summary.get("dir")
            low_prof, high_prof = p_dir == "低", p_dir == "高"
            p_txt = {"低": "低", "高": "高", "矛盾": "中/矛盾", "中": "中"}.get(p_dir, "不可判定")
            # 【⚠️ 基差方向 2026-09-29 修正】cur_b = dom_basis = **现货 − 期货**(实测量纲钉死,
            #   见 quant_module._quant_signals 处注释)。故:
            #     cur_b > 0 = 现货升水 = **期货贴水** → 顺基差做多方向
            #     cur_b < 0 = 现货贴水 = **期货升水** → 顺基差做空方向
            #   修正前此处写作 discount = cur_b < 0(把"期货升水"当成了贴水), 与上方
            #   cap-inventory-basis-profit「深贴水→单边做多」的正确方向**互为反向**。
            #   注意方法论中的"贴水/升水"主体是**期货**(机器口径), 不是现货, 勿按字面读。
            discounted = cur_b > 0  # 期货贴水(现货升水)
            premium = cur_b < 0     # 期货升水(现货贴水)
            if inv_pos_now in ("低", "高"):
                if low_inv and low_prof and discounted:
                    verdict = "单边做多(三指标共振: 低库存+低利润+深贴水 → 高赔率+高胜率)"
                elif high_inv and high_prof and premium:
                    verdict = "单边做空(三指标共振: 高库存+高利润+高升水)"
                elif low_inv and high_prof and discounted:
                    verdict = "跨期正套(矛盾: 低库存+深贴水看多, 但高利润抑多 → 多近空远, 勿单边)"
                elif high_inv and low_prof and premium:
                    verdict = "跨期反套(矛盾: 高库存+高升水看空, 但低利润托底 → 空近多远, 勿单边)"
                else:
                    # 【部分共振提示 2026-09-28】三指标未齐时不给方向, 但要指出"有几项同向",
                    #   否则用户读到"未共振"会以为三指标都没信号, 丢掉"2 项同向"的有效信息。
                    bull = sum([low_inv, low_prof, discounted])
                    bear = sum([high_inv, high_prof, premium])
                    if max(bull, bear) >= 2:
                        side = "偏多" if bull > bear else "偏空"
                        n_same = max(bull, bear)
                        tail = ", 待第三项确认再加减" if n_same == 2 else ""
                        verdict = f"部分共振({n_same}/3 项{side}) → 无单边优势, 仅可轻仓试{tail}"
                    else:
                        verdict = "指标未共振 → 无单边优势, 观望或降仓"
                lines.append(
                    f"三指标共振表: 库存{inv_pos_now} + 利润({p_txt}) "
                    f"+ 基差({'深贴水(期货)' if discounted else '高升水(期货)' if premium else '平水'}) → {verdict}"
                )
    except Exception as e:  # noqa: BLE001
        logger.warning("QA 量化块-三指标共振失败(%s): %s", code, e)

    if not lines:
        return ""
    head = "【量化面】(方法论: qihuo-duokong-logic 技能; 数据: 国君基差/利润/库存+四所仓单/akshare, "
    head += "仅供量化参考)"
    return "\n" + head + "\n" + "\n".join(lines)


def _qa_realtime_data_block(variety: str, question: str = "") -> str:
    """研报问答「实时数据块」(2026-09-22):RAG 片段之外注入国君API + akshare 数值。

    【为什么】研报片段只有历史数值,问"现在库存/基差/价格多少"会过时或缺数。QA 时
    并发拉 价格(akshare) / 基差·库存·周度观点(国君API) 拼成一块,与片段并列注入
    prompt,并标注"仅用于核对数值,非观点来源"。
    【触发】用户拍板"总是注入":品种已选即注入;未选品种返回 ""(数据全按品种拉)。
    【降级】任一块失败只跳过该块,绝不拖垮问答;全部失败返回 ""(行为等同未开启)。
    【性能】并发 4 路,实测单源 300~900ms,总耗时 ~1s;无缓存(用户要实时核对)。
    """
    if not (variety or "").strip():
        return ""
    code = variety.strip().upper()
    from datetime import date, datetime, timedelta

    end = date.today().isoformat()
    start = (date.today() - timedelta(days=15)).isoformat()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    parts: list[str] = []

    def _prices() -> str:
        from tradingagents.dataflows import commodity_futures as cf  # 【调用包】akshare

        s = str(cf.get_futures_price(code, start, end) or "").strip()
        lines = s.splitlines()
        if not lines:
            return ""
        if len(lines) > 6:
            lines = lines[:1] + lines[-5:]  # 表头 + 最近 5 行
        return "价格(近5交易日, akshare):\n" + "\n".join(lines)

    def _gtja_basis() -> str:
        from tradingagents.dataflows import gtja_api  # 【调用包】国君API

        df = gtja_api.fetch_basis_df(code, start, end)
        if df is None or len(df) == 0:
            return ""
        return "基差(国君API):\n" + df.tail(5).to_string(index=False)

    def _gtja_inv() -> str:
        from tradingagents.dataflows import gtja_api

        df = gtja_api.fetch_inventory_df(code, start, end)
        if df is None or len(df) == 0:
            return ""
        return "库存(国君API):\n" + df.tail(5).to_string(index=False)

    def _gtja_view() -> str:
        from tradingagents.dataflows import gtja_api

        v = gtja_api.fetch_viewpoint(code) or {}
        wk = v.get("weekly") or {}
        st = (wk.get("standpoint") or "").strip()
        rs = (wk.get("reason") or "").strip()
        if not (st or rs):
            return ""
        return f"周度观点(国君API):\n{st or '未披露'}：{rs[:200]}"

    import concurrent.futures

    funcs = {"价格": _prices, "基差": _gtja_basis, "库存": _gtja_inv, "观点": _gtja_view,
             "仓单": lambda: _qa_warrant_block(code),
             "技术面": lambda: _qa_technical_block(code, question),  # 【2026-09-22】按需触发
             "量化面": lambda: _qa_quant_block(code, question)}  # 【2026-09-22】按需触发(技能方法论)
    # 【2026-10-14】总闸从 15s 放宽到 25s：天勤链路修好后「技术面」块改为**真的走天勤**
    #   （约 5s，且建连被 _connect_lock 串行化），15s 在负载高时容易触顶。
    _QA_BLOCK_DEADLINE = 25
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=7)
    futs = {ex.submit(f): name for name, f in funcs.items()}
    try:
        # 【★ 关键】超时**不再掀掉整块**：本块既定语义是"单块失败只跳过"（见下面的
        #   per-block except），而 `as_completed(timeout=)` 抛出的 TimeoutError 会穿透
        #   出去把**已拿到的其余块一起丢掉**，与设计意图相反 —— 实测表现为
        #   `TimeoutError: 1 (of 7) futures unfinished` 让 /api/research/ask 整体失败。
        #   故就地接住：收下已完成的块 + 记一条说明还差几块的 warning。
        for fut in concurrent.futures.as_completed(futs, timeout=_QA_BLOCK_DEADLINE):
            name = futs[fut]
            try:
                blk = fut.result()
                if blk:
                    parts.append(blk)
            except Exception as e:  # noqa: BLE001  单块失败只跳过
                logger.warning("QA 实时数据块 %s 失败(variety=%s): %s", name, code, e)
    except concurrent.futures.TimeoutError:
        _pending = [n for f, n in futs.items() if not f.done()]
        logger.warning("QA 实时数据块总闸超时(%ss)：已收集 %d 块，放弃未完成 %s(variety=%s)",
                       _QA_BLOCK_DEADLINE, len(parts), _pending, code)
    finally:
        # 不能等慢块：`with ThreadPoolExecutor` 退出时是 shutdown(wait=True)，
        # 会**阻塞到所有块做完**，"超时"实际上兜不住延迟（超时后还要再等一次）。
        ex.shutdown(wait=False)
    if not parts:
        return ""
    head = ("【实时市场数据】(来源: 国君API/akshare, 抓取于 "
            f"{ts};仅用于核对数值,非观点来源;与研报片段冲突时两者都要注明)")
    return "\n\n" + head + "\n" + "\n\n".join(parts)


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
            # 【2026-09-15】kind="conclusion" 的是【研报结论·系统归纳】块(见 chunking.conclusion_text),
            # 前端据此加"系统归纳"角标;旧索引没有该键 → 默认 body。
            "kind": h["metadata"].get("kind") or "body",
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
    if not variety:
        variety = _infer_variety_from_question(question)  # 【2026-09-22】问句识别品种兜底
    report_type = str(body.get("report_type") or "").strip() or None
    date_from = str(body.get("date_from") or "").strip() or None
    date_to = str(body.get("date_to") or "").strip() or None
    try:
        top_k = max(1, min(int(body.get("top_k") or 6), 30))  # 【2026-09-24】封顶 12→30(整月复盘)
    except (TypeError, ValueError):
        top_k = 6
    top_k = _llm_choose_top_k(question, variety, date_from, date_to, top_k)  # 【2026-09-24】LLM 选检索深度
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

    # 【隔离适配 2026-09-14】此处不导入 service:web 主进程一律经 _rag_call 走
    #   rag_worker 子进程。若在主进程 import service,torch/chromadb 原生栈会被
    #   拉回主进程 —— 正是 2026-09-10 向量库损坏(access violation)的成因。
    #   采集器等子进程内(_RAG_WORKER_MODE=False)时,下游分支会就地懒导入。

    history = db.get_qa_history_for_rewrite(session_id, max_turns=_QA_REWRITE_TURNS,
                                            max_chars=_QA_REWRITE_CHAR_BUDGET)
    prior_history = history[:-1] if history else []  # 末条是刚插入的当前问题,排除
    # 黑话词典(自进化 P2):改写 prompt 注入映射表;首轮不走改写 LLM,直接子串替换
    terms = _qa_load_terms()
    term_lines = [
        f"{t.get('term')}→{t.get('mapping')}"
        for t in terms if t.get("term") and t.get("mapping")
    ]
    first_turn = not prior_history
    mapped_q, term_hit_ids = question, []
    if first_turn:
        mapped_q, term_hit_ids = _qa_apply_terms(question, terms)
        if term_hit_ids:
            try:
                db.bump_qa_term_hits(term_hit_ids)
            except Exception as e:  # noqa: BLE001  计数失败不影响主链路
                logger.warning("QA term hits bump failed: %s", e)
    t0 = time.monotonic()  # 【2026-09-22】定义在流式分支前: 生成器闭包与下方同步分支共用
    # 【2026-09-22 流式前置】stream 模式把整条链路(改写→检索→数据块→LLM 生成)
    # 全部放进 SSE 生成器: Response 立即返回, 用户点发送 ~1s 内收到状态事件;
    # prep 阶段(改写/检索/数据块)与 LLM 首 token 前的等待, 每 4s 推 heartbeat。
    # 消除"同步 prep + 端点排队吃掉首字(实测 25.6s 无任何事件)"的假卡死。
    if body.get("stream"):
        def _qa_stream_gen():
            import queue as _q
            import threading as _th

            ev_q: _q.Queue = _q.Queue()
            _SENTINEL = object()

            def _pipeline():
                # 【2026-09-22 流式前置】整条链路(改写→检索→数据块→LLM 生成→落库)
                # 全部放后台线程执行, 主生成器统一轮询 ev_q: 4s 无任何事件推 heartbeat。
                # prep 阶段(检索/数据块可能数秒)与 LLM 首 token 前的等待全部可感知,
                # 不再有"同步 prep 吃掉首字 / 端点排队 30~150s 无可见字"的假卡死。
                try:
                    ev_q.put(("status", "改写检索查询…"))
                    with _QA_LLM_SEM:
                        rewritten = _rewrite_query_for_retrieval(prior_history, question, term_lines)
                        if first_turn and term_hit_ids:
                            rewritten = mapped_q  # 首轮词典替换生效:rewritten_query 落替换后 query 供审计
                        search_query = rewritten or question
                        try:
                            if _RAG_WORKER_MODE:  # 【分支】web 主进程:走 RAG 子进程
                                hits = _rag_call(
                                    "retrieve", timeout_s=240,
                                    query=search_query, top_k=top_k, variety=variety,
                                    report_type=report_type, date_from=date_from, date_to=date_to,
                                )
                            else:
                                from tradingagents.rag import service  # 【调用包】懒导入重依赖

                                hits = service.retrieve_hits(
                                    search_query, top_k=top_k, variety=variety,
                                    report_type=report_type, date_from=date_from, date_to=date_to,
                                    recent_meta_provider=_rag_recent_meta_provider,
                                )
                        except Exception as e:  # noqa: BLE001
                            logger.warning("QA stream retrieve failed: %s", e)
                            ev_q.put(("error_msg", "检索失败,请稍后重试"))
                            return
                        if not hits:
                            ev_q.put(("error_msg", "未检索到相关研报片段,试试放宽品种/类型/日期过滤"))
                            return
                        citations = _qa_build_citations(hits)
                        if _RAG_WORKER_MODE:  # 【分支】web 主进程:走 RAG 子进程
                            context = _rag_call("format_context", timeout_s=60, hits=hits)
                        else:
                            from tradingagents.rag import service  # 【调用包】懒导入重依赖

                            context = service.format_context(hits)
                        data_block = _qa_realtime_data_block(variety, question)  # 【2026-09-22】question 传技术块触发判断
                        latest_date = max(
                            (c["publish_date"] for c in citations if c["publish_date"]), default="未知"
                        )
                        prior_answer = next(
                            (m.get("content") or "" for m in reversed(prior_history)
                             if m.get("role") == "assistant" and m.get("content")),
                            "",
                        )
                        extra_block = ""
                        if prior_answer and _QA_SELF_REF_PAT.search(question):
                            extra_block = (
                                "\n【你上一轮的回答(仅供衔接对话;其中的观点/数据若与下方研报片段冲突,"
                                "一律以片段为准)】\n"
                                f"{prior_answer[:_QA_SELF_REF_MAX_CHARS]}\n\n"
                            )
                        client = create_llm_client(
                            config["llm_provider"],
                            config.get("quick_think_llm", config["deep_think_llm"]),
                            no_thinking=True,  # 【2026-09-22】研报问答关思考链: 有 RAG 上下文支撑质量无损
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
                            f"检索片段最新发布日期:{latest_date}\n"
                            "【实时市场数据】块(若存在)是抓取时刻的行情数值,用于核对/补充研报中的"
                            "数字;引用数据时注明其来源与日期,与研报片段冲突时两者都要说明。\n"
                            f"{extra_block}"
                            + _conclusion_chunk_note()  # 【闸门②】结论块是系统归纳,必须讲明来源
                            + f"用户问题:{question}\n\n"
                            f"{context}{data_block}\n\n"
                            "回答(markdown):"
                        )
                        llm = client.get_llm()
                        ev_q.put(("status", f"已检索 {len(citations)} 篇片段,开始生成…"))
                        full: list[str] = []
                        for chunk in llm.stream(prompt):
                            ev_q.put(("chunk", chunk))
                            txt = chunk.content if hasattr(chunk, "content") else chunk
                            if isinstance(txt, list):
                                txt = "".join(str(x) for x in txt)
                            txt = str(txt or "")
                            if txt:
                                full.append(txt)
                        answer_s = "".join(full).strip()
                        latency_s = int((time.monotonic() - t0) * 1000)
                        try:
                            mid_s = db.insert_qa_message(
                                session_id, "assistant", answer_s, citations=citations,
                                retrieved=len(hits), filters=filters,
                                rewritten_query=rewritten or "", latency_ms=latency_s,
                            )["id"]
                            db.update_qa_session(
                                session_id, variety=variety, report_type=report_type,
                                date_from=date_from, date_to=date_to,
                            )
                        except Exception as e2:  # noqa: BLE001  落库失败不阻断流式
                            logger.warning("QA stream persist failed: %s", e2)
                            mid_s = None
                        ev_q.put(("done_result", {
                            "answer": answer_s, "citations": citations,
                            "retrieved": len(hits), "session_id": session_id,
                            "message_id": mid_s,
                        }))
                except Exception as e:  # noqa: BLE001  链路任何一步失败都推 error,不挂空流
                    logger.warning("QA stream pipeline failed: %s", e)
                    ev_q.put(("error_msg", "生成失败,请重试"))
                    return
                ev_q.put(("done_iter", _SENTINEL))

            th = _th.Thread(target=_pipeline, daemon=True)
            th.start()
            try:
                while True:
                    try:
                        kind, obj = ev_q.get(timeout=4)
                    except _q.Empty:
                        yield f"data: {json.dumps({'heartbeat': True}, ensure_ascii=False)}\n\n"
                        continue
                    if kind == "error_msg":
                        yield f"data: {json.dumps({'error': obj}, ensure_ascii=False)}\n\n"
                        return
                    if kind == "done_iter":
                        break
                    if kind == "status":
                        yield f"data: {json.dumps({'status': obj}, ensure_ascii=False)}\n\n"
                        continue
                    if kind == "done_result":
                        yield f"data: {json.dumps({'done': True, **obj}, ensure_ascii=False)}\n\n"
                        return
                    chunk = obj
                    reas = (getattr(chunk, "additional_kwargs", None) or {}).get("reasoning_content")
                    if reas:
                        yield f"data: {json.dumps({'thinking': str(reas)}, ensure_ascii=False)}\n\n"
                    txt = chunk.content if hasattr(chunk, "content") else chunk
                    if isinstance(txt, list):
                        txt = "".join(str(x) for x in txt)
                    txt = str(txt or "")
                    if not txt:
                        continue
                    yield f"data: {json.dumps({'delta': txt}, ensure_ascii=False)}\n\n"
            except GeneratorExit:
                # 【2026-09-22】客户端断开: 后台线程 daemon 随进程收尾, 不阻塞
                return
            return

        return Response(
            stream_with_context(_qa_stream_gen()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    with _QA_LLM_SEM:
        rewritten = _rewrite_query_for_retrieval(prior_history, question, term_lines)
        if first_turn and term_hit_ids:
            rewritten = mapped_q  # 首轮词典替换生效:rewritten_query 落替换后 query 供审计
        search_query = rewritten or question  # ""(首轮/改写失败)回退原问题
        try:
            if _RAG_WORKER_MODE:  # 【分支】web 主进程:走 RAG 子进程(冷启动含模型加载,超时放宽)
                hits = _rag_call(
                    "retrieve",
                    timeout_s=240,
                    query=search_query,
                    top_k=top_k,
                    variety=variety,
                    report_type=report_type,
                    date_from=date_from,
                    date_to=date_to,
                )
            else:
                from tradingagents.rag import service  # 【调用包】懒导入重依赖

                hits = service.retrieve_hits(
                    search_query, top_k=top_k, variety=variety, report_type=report_type,
                    date_from=date_from, date_to=date_to,
                    recent_meta_provider=_rag_recent_meta_provider,
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
    if _RAG_WORKER_MODE:  # 【分支】web 主进程:走 RAG 子进程
        context = _rag_call("format_context", timeout_s=60, hits=hits)
    else:
        from tradingagents.rag import service  # 【调用包】懒导入重依赖

        context = service.format_context(hits)
    # 【2026-09-22 数值增强】国君API+akshare 实时数据块(用户拍板"总是注入";未选品种=空)
    data_block = _qa_realtime_data_block(variety, question)  # 【2026-09-22】question 传技术块触发判断
    # 新鲜度提示:让模型自知材料边界,回答"截至 X 日"
    latest_date = max((c["publish_date"] for c in citations if c["publish_date"]), default="未知")
    # 自指追问(如"那第二点呢/展开讲讲你刚才说的"):上轮回答全文作附加材料块,
    # 仅供衔接;知识仍锚定研报片段,冲突以片段为准(压制"基于模型说话"的幻觉滚雪球)
    prior_answer = next(
        (m.get("content") or "" for m in reversed(prior_history)
         if m.get("role") == "assistant" and m.get("content")),
        "",
    )
    extra_block = ""
    if prior_answer and _QA_SELF_REF_PAT.search(question):
        extra_block = (
            "\n【你上一轮的回答(仅供衔接对话;其中的观点/数据若与下方研报片段冲突,"
            "一律以片段为准)】\n"
            f"{prior_answer[:_QA_SELF_REF_MAX_CHARS]}\n\n"
        )
    try:
        with _QA_LLM_SEM:
            client = create_llm_client(
                config["llm_provider"],
                config.get("quick_think_llm", config["deep_think_llm"]),
                no_thinking=True,  # 【2026-09-22】研报问答关思考链: 首字 4.1s→0.7s
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
                f"检索片段最新发布日期:{latest_date}\n"
                "【实时市场数据】块(若存在)是抓取时刻的行情数值,用于核对/补充研报中的"
                "数字;引用数据时注明其来源与日期,与研报片段冲突时两者都要说明。\n"
                f"{extra_block}"
                + _conclusion_chunk_note()  # 【闸门②】结论块是系统归纳,必须讲明来源
                + f"用户问题:{question}\n\n"
                f"{context}{data_block}\n\n"
                "回答(markdown):"
            )
            llm = client.get_llm()
            # 【2026-09-22】流式分支已前置到函数体开头(Response 立即返回,
            # 改写/检索/数据块/LLM 生成全在 SSE 生成器内,带状态事件与 4s 心跳)。
            result = llm.invoke(prompt)
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


# ── 研报问答·自进化三层(2026-09-14):反馈信号 → 错题本 → 黑话词典 ──
# 阶梯:①qa_messages 补 feedback 列(补"无反馈信号"死穴) ②失败轮自动浮出错题本
# ③口语→检索词词典回灌改写链路(首轮直替换 + 改写 prompt 注入),形成闭环。

@app.route("/api/research/qa/feedback", methods=["POST"])
def api_qa_feedback():
    """给 assistant 消息打 👍/👎(自进化 P0 反馈信号)。

    【入参】{"message_id"必填, "feedback":"up"|"down"}。
    【失败矩阵】message_id 缺失 400;值不合法/消息不存在 → 200 + ok:false(约定同 ask)。
    """
    body = request.get_json(silent=True) or {}
    try:
        message_id = int(body.get("message_id") or 0)
    except (TypeError, ValueError):
        message_id = 0
    if not message_id:
        return jsonify({"error": "message_id 必填"}), 400
    feedback = str(body.get("feedback") or "").strip().lower()
    if feedback not in ("up", "down"):
        return jsonify({"ok": False, "error": "feedback 只支持 up/down"})
    msg = get_db().set_qa_feedback(message_id, feedback)
    if msg is None:
        return jsonify({"ok": False, "error": "消息不存在"})
    return jsonify({"ok": True, "message": msg})


@app.route("/api/research/qa/insights", methods=["GET"])
def api_qa_insights():
    """错题本(自进化 P1):反馈统计 + 失败轮列表(👎/0命中/出错,自动浮出)。"""
    db = get_db()
    stats = db.get_qa_feedback_stats()
    failures = []
    for f in db.get_qa_recent_failures(limit=20):
        failures.append({
            "message_id": f["id"],
            "session_id": f["session_id"],
            "question": f.get("question") or "(未取到提问)",
            "answer": (f.get("content") or "")[:200],  # 面板展示摘要,全量走会话回放
            "retrieved": f.get("retrieved", 0),
            "error": f.get("error") or "",
            "rewritten_query": f.get("rewritten_query") or "",
            "feedback": f.get("feedback") or "",
            "filters": f.get("filters") or {},
            "created_at": f.get("created_at") or "",
        })
    return jsonify({"ok": True, "stats": stats, "failures": failures})


@app.route("/api/research/qa/terms", methods=["GET"])
def api_qa_terms_list():
    """黑话词典列表(按命中次数降序)。"""
    return jsonify({"ok": True, "terms": get_db().list_qa_terms()})


@app.route("/api/research/qa/terms", methods=["POST"])
def api_qa_terms_add():
    """添加/更新词典条目(口语词→检索词);term 已存在视为改映射,不报错。"""
    body = request.get_json(silent=True) or {}
    term = str(body.get("term") or "").strip()
    mapping = str(body.get("mapping") or "").strip()
    if not term or not mapping:
        return jsonify({"ok": False, "error": "term 与 mapping 均必填"})
    if term == mapping:
        return jsonify({"ok": False, "error": "term 与 mapping 不能相同"})
    t = get_db().add_qa_term(term, mapping)
    return jsonify({"ok": True, "term": t})


@app.route("/api/research/qa/terms/<int:tid>", methods=["DELETE"])
def api_qa_terms_delete(tid):
    """删除词典条目;不存在返回 404。"""
    if not get_db().delete_qa_term(tid):
        return jsonify({"error": "词条不存在"}), 404
    return jsonify({"ok": True})


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


# 【功能】手动触发"研报自动接入":华泰天玑 21 品种池日报
#           + 国泰君安云 API 研报(2026-09-04 开通)+ 东证繁微观点(2026-09-08 接入)。
#           (发现报告源 2026-09-09 剔除:三家 API 源已覆盖,重复度高。)
# 【请求体】可选 {"dry_run": true, "source": "all"|"htfc"|"gtja"|"dongzheng"|"local",
#           "varieties": ["SC","TA",...](品种代码白名单,只采指定品种,防止全量
#           采集 LLM 耗时过长;华泰/国君生效;本地导入按文件名别名命中;
#           缺省/空 = 全部品种)};缺省 source=all 三源都跑。
#           source="local" 时另需 "folder"(本地 PDF 目录绝对路径)与可选
#           "org"(机构名,缺省"本地导入",决定存储子目录与入库 source 标签)。
# 【返回】{"status": "started"} / 进行中 409 / source 或品种非法 400 / 异常 500。
# 【关键逻辑】daemon 线程按 source 顺序跑
#           research_collector_htfc.ingest_today(华泰天玑,日报当日+周报近10天,requested=品种集)、
#           research_collector_gtja.ingest_recent(国君云 API,近 2 天,requested=品种集)、
#           research_collector_dongzheng.ingest_recent(东证繁微,研报+动态快评近 3 天,
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
    if source not in ("all", "htfc", "gtja", "dongzheng", "local"):
        return jsonify({"error": "source 须为 all / htfc / gtja / dongzheng / local"}), 400
    # 【本地导入】source=local 时必须带 folder(本地 PDF 目录),org 可选(2026-09-09 从
    # 本机迭代版移植回源机器版;all 不含 local——目录路径按次指定,无固定定时目录)
    local_folder = ""
    local_org = "本地导入"
    if source == "local":
        local_folder = str(body.get("folder") or "").strip()
        local_org = str(body.get("org") or "").strip() or "本地导入"
        if not local_folder or not Path(local_folder).is_dir():
            return jsonify({"error": f"folder 须为已存在的本地目录: {local_folder or '(空)'}"}), 400
    # 【品种筛选】可选 varieties 代码列表,非空时只采指定品种(采集器内部按
    # 品种 tag/代码过滤);非法代码直接 400,避免静默采到 0 篇。
    requested: set[str] | None = None
    raw_varieties = body.get("varieties")
    if isinstance(raw_varieties, list) and raw_varieties:
        # 【品种池】2026-09-09 起校验基准改为 ACTIVE_VARIETIES(21 品种池),
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
                # 【2026-09-14 修复】补上 include_dynamics=True(动态快评与本处
                # days=1 双缺,是"几乎没有东证研报"的主因),口径与调度器一致。
                if requested is None:
                    dz_result = dz_ingest_recent(
                        days=3, dry_run=dry_run, include_dynamics=True, max_reports=60,
                    )
                    logger.info("Research auto-collect (dongzheng) done: %s", dz_result)
            if source == "local":
                from ingest_local_pdfs import ingest_dir  # 【调用包】本地 PDF 导入(懒导入)

                local_result = ingest_dir(
                    Path(local_folder), local_org, requested or set(), dry_run,
                )
                logger.info("Research local import (%s) done: %s", local_folder, local_result)
                if local_result.get("errors"):
                    with suppress(Exception):
                        get_db().create_alert(
                            "research_error",
                            "Local import partial errors",
                            "; ".join(local_result["errors"][:5])[:300],
                            severity="warning",
                        )
            # 【采集后自动入库 RAG】单篇入库时已即时索引,此处兜底补齐:模型缺失/进程
            # 中断期间漏掉的研报(2026-09-09 场景)在此补回;本地 embedding 零 LLM,很快。
            if not dry_run:
                try:
                    _rag_index_pending(since_days=3)  # 【调用函数】只扫近 3 天,避免全库遍历
                except Exception as e:
                    logger.warning("Post-collect RAG index failed: %s", e)
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
             "htfc": "华泰天玑", "gtja": "国君云API", "dongzheng": "东证繁微",
             "local": f"本地导入({local_folder})"}[source]
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
    # 【变量】req_started:流水线启动时刻, 收尾时连同"轮次留痕"一起落盘 ——
    #   留痕里的 ts 是**收尾**时刻, 一轮可能跑几十分钟, 拿它去匹配轮次起点会漂出窗口。
    req_started = datetime.now()

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
        # 【变量】baseline_ids:采集**开始前**各平台已有唯一帖键集合({平台: set})。
        #   收尾阶段与采集后的快照做差, 得到"本轮真实新增"。跨阶段共享故定义在循环外。
        #   None = 基线建立失败, 此时前端会显示"增量不可用"而不是伪造一个 0。
        baseline_ids = None

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
                    # 每个平台一个 batch_collect.py 子进程, 由有界线程池**并行**调度(2026-09-17,
                    # 默认 3 并发); 单平台失败(凭据缺失/超时)记入 errs 后继续, 不影响其他平台。
                    # 一个关键词对应一个 batch_{平台}_{时间戳}.jsonl, 每 4 秒对比一次输出目录,
                    # 有新批次文件就实时推一条 log(完成几个关键词/最新文件多少条);
                    # 若超过 15 秒无新文件, 推一条心跳消息保持"活着"的观感。子进程结束(含
                    # 600 秒超时转异常)后再把最终 stdout/stderr 里关键行推出去。
                    pre_batches = set(glob.glob(str(THINK2_OUTPUT / "batch_*.jsonl")))  # 【变量】采集前已有批次文件集合(用于发现新文件)

                    # 【增量基线】必须在子进程启动**前**扫描完毕:否则本轮新产出的批次
                    # 文件会被算进基线, 增量恒为 0(而页面上看不出任何异常, 只是"永远新增 0 条")。
                    # 全量重放约 2.4 万行/50MB ≈ 0.8 秒, 相对采集耗时(分钟级)可忽略。
                    try:
                        baseline_ids = ut.scan_platform_ids(sorted(pre_batches))
                    except Exception as _be:  # noqa: BLE001
                        baseline_ids = None
                        yield f"data: {json.dumps({'type': 'log', 'msg': f'增量基线建立失败(本轮不显示增量): {_be}'}, ensure_ascii=False)}\n\n"

                    holder = {}  # 【变量】跨线程容器:各平台子进程结果写 holder["res"],异常写 holder["errs"]

                    # 【功能】在后台线程里**并行**跑各平台采集子进程(2026-09-17 起),
                    # 结果/异常统一写进 holder 容器,避免阻塞 SSE 生成器。
                    # 【为什么并行】原先 for 循环串行: 每个平台一个子进程, 一轮墙钟 =
                    #   各平台耗时之和 —— 慢平台(抖音/雪球)会把后面的平台整体拖晚,
                    #   而后端整体也就一个"采集"阶段, 用户只能干等。
                    # 【为什么有界】每个子进程内部各起一个 Chromium, 无限并发会把机器
                    #   打爆; 并发度与定时管道共用同一解析函数(环境变量 + 钳制, 默认 3)。
                    def _run_one_platform(_p, venv_py=venv_py, per_kw=per_kw, since_date=since_date):
                        """跑单平台采集子进程; 返回 CompletedProcess(抛错由 collect_pool 记账)。"""
                        _cmd = [
                            venv_py,
                            "batch_collect.py",
                            "--platform",
                            _p,
                            "--per-kw",
                            str(per_kw),
                            "--turbo",
                            "--no-detail",
                            # 【2026-09-17】两个必须加的旗标:
                            # ① --rotate-keywords: 网页链路原本把整个词池从头跑一遍, 而
                            #    慢平台(抖音 25 词 × ~100s)根本跑不完 ⇒ 每轮都从第 1 个词
                            #    开始, 后面的词被系统性饿死。挂上轮转游标后, 一轮跑到预算
                            #    就用"本轮实际跑完的词数"推进游标, 下次从这里接着跑。
                            # ② --max-minutes 9: 子进程硬超时是 600s, 但采集器默认预算是
                            #    12min —— 预算比超时还长, 意味着**必然被强杀**: 一旦强杀
                            #    连运行报告与退出码都写不出来, 前端只能看到一句 timeout。
                            #    改成 9min(540s)留 60s 给浏览器收尾与报告落盘, 采集器自己
                            #    在关键词边界优雅收尾(数据逐词已落盘, 不丢)。
                            "--rotate-keywords",
                            "--max-minutes",
                            "9",
                        ]
                        if since_date:
                            _cmd.extend(["--since", since_date])
                        return subprocess.run(  # 【调用函数】运行思路2采集脚本 batch_collect.py(单平台)
                            _cmd,
                            cwd=str(THINK2_DIR),
                            capture_output=True,
                            text=True,
                            timeout=600,  # 【变量】单平台采集子进程超时上限 600 秒
                        )

                    def _run_collect(holder=holder):
                        # 【并行调度】与定时管道共用 collect_pool: 同一套并发度解析(默认 3,
                        # 环境变量 AGENTSENSE_COLLECT_CONCURRENCY 可覆盖)、同样的单平台
                        # 异常隔离、同样按请求顺序返回结果 —— 两条链路不会各写一份而跑偏。
                        _by_platform, _errs = collect_pool.run_platforms(
                            platforms, _run_one_platform
                        )
                        # 失败平台保留在结果位(None)以便日志区分"没跑"与"跑了但失败"
                        holder["res"] = [(_p, _by_platform.get(_p)) for _p in platforms]
                        holder["errs"] = [f"{_p}: {_msg}" for _p, _msg in _errs.items()]

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
                    per_ids = {}  # 【变量】per_ids:本平台唯一帖键集合(与基线做差得增量)
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
                                    # 【关键】统一走 ut.post_key(优先 note_id, 退回 url/内容哈希)。
                                    # 旧写法 `d.get("note_id","")` 在字段缺失时会把所有记录塌缩成
                                    # 同一个空串, 去重后总量直接失真 —— 是既有隐患, 一并修掉。
                                    nid = ut.post_key(d)
                                    if nid in total_seen:
                                        continue
                                    total_seen.add(nid)
                                    # platform 字段缺失时按 url 域名兜底推断(2026-07 早期数据缺该字段)
                                    plat = d.get("platform") or _infer_platform(d.get("url", ""))
                                    platform_counts[plat] = platform_counts.get(plat, 0) + 1
                                    per_ids.setdefault(plat, set()).add(nid)
                                    total_posts += 1
                                    # 【日期卫生】同上:不可信日期不参与范围统计,
                                    # 否则这里的"覆盖 X ~ Y"会报出 2104 年。
                                    pt = _sane_publish_date(d.get("publish_time"))  # 【调用函数】脏值 → ""
                                    if pt:
                                        if not earliest_time or pt < earliest_time:
                                            earliest_time = pt
                                        if not latest_time or pt > latest_time:
                                            latest_time = pt
                        except Exception:
                            pass

                    # 【增量】本平台新增 = 采集后集合 − 采集前基线集合。基线不可用时留 {} ,
                    #   前端会显示"增量不可用", 而不是拿 0 冒充"这次没有新数据"。
                    delta = ut.delta_between(baseline_ids, per_ids) if baseline_ids is not None else {}

                    # Send structured summary
                    summary = {
                        "type": "platform_summary",
                        "platforms": platform_counts,
                        "delta": delta,
                        "delta_available": baseline_ids is not None,
                        "total": total_posts,
                        "earliest": earliest_time or "?",
                        "latest": latest_time or "?",
                    }
                    yield f"data: {json.dumps(summary, ensure_ascii=False)}\n\n"

                    # Also as log messages
                    q = "?"
                    yield f"data: {json.dumps({'type': 'log', 'msg': '── 平台数据汇总 ──'}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'type': 'log', 'msg': f'总计: {total_posts} 条 (去重后), 覆盖 {earliest_time or q} ~ {latest_time or q}'}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'type': 'log', 'msg': '本次新增(相对更新前): ' + ('不可用' if baseline_ids is None else ' '.join(f'{ut.PLATFORM_LABELS.get(p, p)} +{n}' for p, n in sorted(delta.items(), key=lambda x: -x[1])))}, ensure_ascii=False)}\n\n"
                    for plat, count in sorted(platform_counts.items(), key=lambda x: -x[1]):
                        pct = f"({count / total_posts * 100:.0f}%)" if total_posts else ""
                        dn = delta.get(plat, 0)
                        yield f"data: {json.dumps({'type': 'log', 'msg': f'  {plat}: 累计 {count} 条 {pct} | 本次新增 {dn} 条'}, ensure_ascii=False)}\n\n"

                    # 【轮次留痕】只用于标注"这次是手动点的"(重建时按时间推断会在
                    #   08:00~08:10 手动更新时误判成定时任务)。写失败不影响流水线。
                    ut.append_recorded_round(THINK2_OUTPUT, {
                        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "started_ts": req_started.strftime("%Y-%m-%d %H:%M:%S"),
                        "source": "manual",
                        "platforms": list(platforms),
                        "delta": delta,
                    })

            except Exception as e:
                yield f"data: {json.dumps({'type': 'log', 'msg': f'ERROR in {key}: {e}'}, ensure_ascii=False)}\n\n"

        yield f"data: {json.dumps({'type': 'complete', 'msg': 'Data update pipeline complete'}, ensure_ascii=False)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# 【功能】各平台增量:按"一次更新一行"给出历次更新各平台新增了几条。
# 【参数】limit=返回最近几轮(默认 20);runs_limit=逐平台明细条数(默认 50)。
# 【返回】{"rounds":[...], "runs":[...], "platforms":[...], "totals":{平台:累计}, ...}
#   · rounds 每项: {start, end, source(manual/scheduled), platforms:{平台:{new,raw,status}},
#                   total_new, total_raw, failed}
#   · new = 真实新增(跨全量历史去重后的净增); raw = 采集命中(含重复的旧帖)
# 【关键逻辑】数字全部由 update_tracker 重放批次文件得出, **不依赖任何采集侧埋点** ——
#   因此服务重启、数据库清空都不会让历史增量丢失。结果按文件指纹缓存,
#   只在有新批次文件时重算(全量重放 2.4 万行 ≈ 0.8 秒)。
@app.route("/api/update_deltas")
def api_update_deltas():
    """Per-update, per-platform collection deltas (net new vs. raw hits)."""
    limit = request.args.get("limit", 20, type=int)
    runs_limit = request.args.get("runs_limit", 50, type=int)
    if not THINK2_OUTPUT or not THINK2_OUTPUT.exists():
        return jsonify({"rounds": [], "runs": [], "platforms": [], "totals": {},
                        "error": "采集输出目录不存在"})
    try:
        data = ut.build_deltas(THINK2_OUTPUT)
    except Exception as e:  # noqa: BLE001
        return jsonify({"rounds": [], "runs": [], "platforms": [], "totals": {},
                        "error": f"增量统计失败: {e}"})

    rounds = data["rounds"][: max(limit, 0)]
    runs = data["runs"][: max(runs_limit, 0)]
    return jsonify({
        "rounds": rounds,
        "runs": runs,
        "platforms": data["platforms"],
        "totals": data["totals"],
        "round_total": len(data["rounds"]),
        "run_total": len(data["runs"]),
        "generated_at": data["generated_at"],
    })


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
        research_times = data.get("research_times", ["08:10", "18:00", "22:00"])
        archive_times = data.get("archive_times", ["09:20"])  # 【2026-09-28】研报归档时点
        start_scheduler(schedule_times=times, research_times=research_times,
                        archive_times=archive_times)
        return jsonify({"status": "started", "schedule_times": times, "research_times": research_times,
                        "archive_times": archive_times})
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
# 【2026-09-24 改密】token → 用户名 映射(登录时记录,登出时移除)。改密码接口需知道
# 当前登录用户是谁, 而 _auth_tokens 只存 token 不绑定用户, 故加此并行映射。
_auth_token_user: dict[str, str] = {}


@app.before_request
def _guard_api_access():
    """【2026-09-24 局域网访问令牌】全局守卫: 所有 /api/* 除 login/status 外均需登录态。

    【为什么】局域网部署(防火墙放行 5000)后, 同网段任何设备都能访问数据接口 ——
    研报库/问答历史/调度控制不应裸奔。复用现有 _auth_tokens 集合 + auth_token cookie
    (HttpOnly, 浏览器同源自动携带, 对 SSE 流式同样生效), 一次性覆盖全部 /api/*,
    无需逐路由挂 _auth_required 装饰器。
    【放行】/api/auth/login 与 /api/auth/status(匿名入口); 非 /api 页面路由放行
    (前端 JS 检测登录态, 未登录弹登录框)。
    【2026-09-29 免登录开关】AGENTSENSE_AUTH_DISABLE=1(放 .env 或环境变量)→ 全站免登录,
    适合本机/可信内网单人使用。前端配合:/api/auth/status 直接回 authenticated=true,
    避免登录遮罩常驻。
    """
    if not request.path.startswith("/api/"):
        return None
    if request.path in ("/api/auth/login", "/api/auth/status"):
        return None
    if os.environ.get("AGENTSENSE_AUTH_DISABLE") == "1":  # 【测试/单机/内网】显式关闭开关
        return None
    token = request.headers.get("X-Auth-Token", "") or request.cookies.get("auth_token", "")
    if token and token in _auth_tokens:
        return None
    return jsonify({"ok": False, "error": "unauthorized"}), 401


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
        _auth_token_user[token] = username  # 【2026-09-24 改密】记录 token→用户名映射
        resp = make_response(jsonify({"status": "ok", "token": token}))
        resp.set_cookie("auth_token", token, max_age=86400 * 30, httponly=True)
        return resp
    return jsonify({"error": "Invalid credentials"}), 401


# 【功能】登出:从内存集合移除 token 并清除 Cookie。
@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    token = request.headers.get("X-Auth-Token", "") or request.cookies.get("auth_token", "")
    _auth_tokens.discard(token)
    _auth_token_user.pop(token, None)  # 【2026-09-24 改密】同步移除映射
    resp = make_response(jsonify({"status": "ok"}))
    resp.delete_cookie("auth_token")
    return resp


# 【功能】检查当前是否已登录(仅看 Cookie 中的 token 是否在内存集合里)。
# 【返回】{"authenticated": true|false}。
@app.route("/api/auth/status")
def api_auth_status():
    # 【2026-09-29 单机/内网免登录】关鉴权时直接回报已登录,前端不弹登录框。
    #   为什么改这里:前端 checkAuth() 只在**非 401** 时才调 hideLogin();
    #   若仍回 {authenticated:false}(HTTP 200),遮罩不会被隐藏,用户还得手点一次登录。
    if os.environ.get("AGENTSENSE_AUTH_DISABLE") == "1":
        return jsonify({"authenticated": True, "auth_disabled": True})
    token = request.cookies.get("auth_token", "")
    return jsonify({"authenticated": token in _auth_tokens})


# 【2026-09-24 局域网】修改当前登录用户密码。
# 【请求体】{"old_password": "...", "new_password": "..."}。
# 【安全】受全局守卫保护(需登录态); 从 _auth_token_user 反查当前用户名, 不信任
#   body 里的 username(防已登录者改他人密码); 校验旧密码 → 更新哈希 → 踢掉除
#   当前会话外的全部旧会话(其他设备的旧 token 立即失效)。
@app.route("/api/auth/change-password", methods=["POST"])
def api_change_password():
    token = request.headers.get("X-Auth-Token", "") or request.cookies.get("auth_token", "")
    if os.environ.get("AGENTSENSE_AUTH_DISABLE") == "1":  # 【与全局守卫一致】关鉴权时不必登录态
        return jsonify({"ok": False, "error": "已关闭登录鉴权(AGENTSENSE_AUTH_DISABLE=1),无需改密"}), 400
    if not token or token not in _auth_tokens:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    username = _auth_token_user.get(token)
    if not username:
        return jsonify({"ok": False, "error": "无法识别当前用户, 请重新登录"}), 401
    data = request.json or {}
    old_pw = str(data.get("old_password") or "")
    new_pw = str(data.get("new_password") or "")
    if not old_pw or not new_pw:
        return jsonify({"ok": False, "error": "旧密码与新密码不能为空"}), 400
    if len(new_pw) < 8:
        return jsonify({"ok": False, "error": "新密码至少 8 位"}), 400
    if new_pw == old_pw:
        return jsonify({"ok": False, "error": "新密码不能与旧密码相同"}), 400
    db = get_db()
    db.ensure_default_user()
    if not db.verify_user(username, old_pw):
        return jsonify({"ok": False, "error": "旧密码不正确"}), 403
    if not db.update_password(username, new_pw):
        return jsonify({"ok": False, "error": "密码更新失败"}), 500
    # 改密成功: 踢掉其他设备会话(仅保留当前), 旧密码已失效
    _auth_tokens.clear()
    _auth_tokens.add(token)
    _auth_token_user.clear()
    _auth_token_user[token] = username
    return jsonify({"ok": True, "status": "ok"})


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
    events = extract_events(variety=variety, days=days)
    # 【2026-09-18】事件卡片同样把 url 渲染成可点链接(前端 loadEvents), 数据源是同一批
    #   batch 记录 —— 雪球存量数据的根相对路径在这里也会点开 404。与 /api/sentiment_posts
    #   共用同一个补全函数, 避免两处各自为政。
    for _e in events:
        _e["url"] = _absolutize_url(_e.get("url", ""), _e.get("platform", ""))
    return jsonify(events)


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
    """打印提示并停留等待回车,再以 code 退出(让用户看清启动保护的原因)。

    【2026-09-14 加固】只在"有交互终端"时才等回车。旧实现无条件调
    input(),而计划任务/守护脚本用 Start-Process 拉起时会分配新控制台,
    input() 于是永久阻塞 → 每次守护脚本误判都会留一个卡死进程(实测 8 组)。
    非 tty(后台/脚本/重定向 stdin)一律直接退出。
    """
    print(message, flush=True)
    interactive = False
    with contextlib.suppress(Exception):
        interactive = bool(sys.stdin is not None and sys.stdin.isatty())
    if interactive:
        with contextlib.suppress(EOFError, KeyboardInterrupt):
            input("    按回车键退出…")
    raise SystemExit(code)


# ── 仓单分析 (warrant) ──────────────────────────────────────────────────────
# 2026-09-14 新增:四家商品交易所(郑商所/大商所/广期所/上期所)仓单日报的展示板块。
# 数据由 warrant_collect.py 采集(data/warrant/web/*.json),本模块只负责读盘 + 切片,
# 不在请求线程里联网;要更新数据走 /api/warrant/refresh 或定时任务。
WARRANT_WEB_DIR = Path(__file__).parent / "data" / "warrant" / "web"  # 【变量】仓单站点数据目录
WARRANT_COLLECT_SCRIPT = Path(__file__).parent / "warrant_collect.py"  # 【变量】采集入口脚本
_WARRANT_CACHE = {"key": None, "data": None}  # 【变量】按 (文件, mtime, 大小) 缓存,避免每请求重复解析
_WARRANT_RUN = {"running": False, "started_at": None, "result": None}  # 【变量】手工刷新任务状态

# 【常量】仓单自动采集时间点。必须与 scheduler.start_scheduler(warrant_times=...) 一致,
# 前端拿它显示"下一次自动采集"。16:40 是 2026-09-21 追加的当日补采(见 scheduler 注释)。
WARRANT_COLLECT_TIMES = ["08:00", "16:40"]
# 【常量】交易所仓单日报「发布窗口」(时, 分): 收盘后陆续发布, 该时刻之后当天日报基本已出。
_WARRANT_PUBLISH_HHMM = (16, 30)


def _expected_warrant_date(now=None):
    """当前时点「理论上已发布」的最近一份仓单日报日期(工作日 + 发布窗口粗判, 不含法定节假日)。

    【为什么需要】仓单日报只在交易日收盘后发布, 所以白天(以及周末)点「采集最新数据」
    拿到的**必然**是上一交易日 —— 这是正确行为, 但前端只显示"数据日期 X"时, 用户会
    读成"采集了但日期怎么不动"(2026-09-21 反馈)。用它算出"按理该有的日期"做对比, 前端
    才能区分两种情况:
        · 数据日期 == 期望日期 → 已是最新, 交易所确实还没发更新的;
        · 数据日期 <  期望日期 → 真落后了, 提示点「采集最新数据」。
    【注意】不含法定节假日, 所以在长假期间会显示"落后", 前端措辞用"可点采集更新"而非报错。
    """
    now = now or datetime.now()
    d = now.date()
    if (now.hour, now.minute) < _WARRANT_PUBLISH_HHMM:
        d -= timedelta(days=1)   # 还没到发布窗口, 今天日报不可能有
    while d.weekday() >= 5:      # 周六/周日交易所不发布
        d -= timedelta(days=1)
    return d


def _warrant_freshness(data_date: str | None) -> dict:
    """把「数据日期 vs 期望日期」翻成前端可用的新鲜度字段。"""
    expected = _expected_warrant_date().isoformat()
    return {"expected": expected,
            "fresh": bool(data_date and data_date >= expected),
            "schedule": list(WARRANT_COLLECT_TIMES),
            "today": datetime.now().strftime("%Y-%m-%d")}


# 【功能】读取仓单站点 JSON(带 mtime 缓存)。文件不存在时返回 None。
# 【参数】name: "overview" | "detail"。
# 【返回】解析后的 dict;读不到返回 None(前端据此显示"尚无数据")。
def _warrant_read(name: str):
    path = WARRANT_WEB_DIR / ("%s.json" % name)
    try:
        st = path.stat()
    except OSError:
        return None
    key = "%s|%d|%d" % (name, int(st.st_mtime), st.st_size)
    if _WARRANT_CACHE.get("key") == key:
        return _WARRANT_CACHE.get("data")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:  # noqa: BLE001 - 数据文件损坏不应让接口 500
        logger.exception("读取仓单数据失败: %s", path)
        return None
    _WARRANT_CACHE["key"] = key
    _WARRANT_CACHE["data"] = data
    return data


# 【功能】仓单总览:交易所汇总 + 全部品种的仓单量与当日增减(供排行榜/筛选)。
# 【返回】{ok, date, generated_at, expected(按理该有的日报日期), fresh(是否已是最新),
#   schedule(自动采集时点), exchanges:[{code,name,unit,unit_mix,brand_support,
#   product_count,total_qty}], products:[{exchange,code,name,unit,last_qty,qty,change,
#   warehouse_count,brand_count,has_brand}], stale}
#   stale=true 表示数据文件缺失或超过 3 天未更新(前端提示"数据可能过期");
#   fresh/expected 用于区分"数据日期不动是因为交易所还没发"与"真的落后了"(2026-09-21)。
@app.route("/api/warrant/overview")
def api_warrant_overview():
    ov = _warrant_read("overview")
    if not ov:
        return jsonify({"ok": False, "error": "尚无仓单数据，请先运行 warrant_collect.py",
                        "exchanges": [], "products": []}), 200
    stale = False
    with contextlib.suppress(Exception):
        d = datetime.strptime(ov.get("date") or "", "%Y-%m-%d").date()
        stale = (datetime.now().date() - d).days > 3
    # 【2026-09-30】给每个品种打 in_pool 标记(是否属于 AgentSense 品种池), 供前端
    # 「品种范围：仅品种池 / 全部品种」筛选。判定键与 _WARRANT_KEY_MAP 逐字一致,
    # 前端不再硬编码任何交易所代码 —— 池子增删只改后端一处。
    pool = _warrant_pool_keys()
    products = []
    for p in ov.get("products") or []:
        q = dict(p)
        q["in_pool"] = ("%s|%s" % (p.get("exchange"), p.get("code"))) in pool
        products.append(q)
    pool_hit = sum(1 for p in products if p["in_pool"])
    return jsonify({"ok": True, "stale": stale,
                    **_warrant_freshness(ov.get("date")),
                    "pool_total": len(pool), "pool_hit": pool_hit,
                    **ov, "products": products})


# 【功能】单品种仓单细则:仓库分布 / 品牌结构 / 仓库厂库占比 / 当日增减结构。
# 【入参】exchange(交易所代码,如 CZCE) + code(品种代码,如 SR)。
# 【返回】{ok, date, exchange, code, brand_support, brand_available, detail:{...}}
#   detail.warehouses[] 按数量降序;detail.brands[] 品牌(无则空);
#   detail.types[] 仓库/厂库;detail.regions[] 地区(上期所);
#   detail.subs[] 分库(大商所);detail.changes{up,down} 当日增减(按仓库);
#   detail.changes_brand 品牌口径增减(有品牌时)。
@app.route("/api/warrant/product")
def api_warrant_product():
    ex = (request.args.get("exchange") or "").strip().upper()
    code = (request.args.get("code") or "").strip()
    if not ex or not code:
        return jsonify({"ok": False, "error": "缺少参数 exchange / code"}), 400
    doc = _warrant_read("detail")
    if not doc:
        return jsonify({"ok": False, "error": "尚无仓单数据"}), 200
    key = "%s|%s" % (ex, code)
    dep = (doc.get("detail") or {}).get(key)
    if dep is None:
        return jsonify({"ok": False, "error": "未找到该品种的仓单数据: %s" % key}), 404
    brand_support = bool((doc.get("brand_support") or {}).get(ex))
    return jsonify({
        "ok": True, "date": doc.get("date"), "exchange": ex, "code": code,
        "brand_support": brand_support,
        "brand_available": bool(dep.get("brands")),
        **dep,
    })


# 【功能】仓单数据采集状态:数据日期 + 最近一次采集结果 + 手工刷新任务是否在跑。
@app.route("/api/warrant/status")
def api_warrant_status():
    ov = _warrant_read("overview") or {}
    last = {}
    with contextlib.suppress(Exception):
        with open(Path(__file__).parent / "data" / "warrant" / "out" / "latest.json",
                  encoding="utf-8") as f:
            last = json.load(f)
    return jsonify({"ok": True, "date": ov.get("date"),
                    "generated_at": ov.get("generated_at"),
                    "last_run": last, "running": _WARRANT_RUN["running"],
                    "started_at": _WARRANT_RUN["started_at"]})


# 【功能】手工触发一次仓单采集(后台线程跑 warrant_collect.py,立即返回)。
# 【入参】可带 ?full=1 走 --force 强制重下;默认复用当日已下载的原始文件。
# 【返回】{ok:true} 已开始;{ok:false} 已在运行中。
@app.route("/api/warrant/refresh", methods=["POST"])
def api_warrant_refresh():
    if _WARRANT_RUN["running"]:
        return jsonify({"ok": False, "error": "已有采集任务在运行中",
                        "started_at": _WARRANT_RUN["started_at"]}), 409
    if not WARRANT_COLLECT_SCRIPT.exists():
        return jsonify({"ok": False, "error": "未找到 warrant_collect.py"}), 500
    force = (request.args.get("full") or "") in ("1", "true", "yes")

    def _worker():
        _WARRANT_RUN["running"] = True
        _WARRANT_RUN["started_at"] = datetime.now().isoformat(timespec="seconds")
        try:
            cmd = [sys.executable, str(WARRANT_COLLECT_SCRIPT)]
            if force:
                cmd.append("--force")
            proc = subprocess.run(cmd, cwd=str(Path(__file__).parent),
                                  capture_output=True, text=True, timeout=900,
                                  encoding="utf-8", errors="replace")
            _WARRANT_RUN["result"] = {"returncode": proc.returncode,
                                      "tail": (proc.stdout or "")[-2000:]}
            # 数据已重新落盘 → 让下一次请求重新解析
            # 【2026-09-21】除站点 JSON 缓存外, 同时显式失效仓单历史时序缓存
            # (history._CACHE 按文件集自动重建是"理论自动", 显式置空保证
            # 「采集最新数据 → 历史走势日期跟着走」必然成立)。
            _WARRANT_CACHE["key"] = None
            with contextlib.suppress(Exception):
                from warrant import history as _whist  # 【延迟导入】仓单历史时序

                _whist._CACHE["key"] = None
        except Exception as e:  # noqa: BLE001
            logger.exception("仓单采集失败")
            _WARRANT_RUN["result"] = {"returncode": -1, "tail": repr(e)}
        finally:
            _WARRANT_RUN["running"] = False

    threading.Thread(target=_worker, daemon=True).start()
    return jsonify({"ok": True, "force": force})


# 【功能】仓单历史时序(分品种 / 分仓库的每日变化曲线数据源)。
# 【入参】可选 exchange + code: 两者都给时额外返回该品种的仓库级时序
#   (warehouses[], 按最新仓单量降序); 不给则只返回品种级, 载荷更小。
# 【返回】{ok, dates:[...], latest(历史最后一天), as_of(采集数据日期),
#          collected_at(站点数据生成时间), ex_latest:{交易所:最新有数日期},
#          expected(按理该有的日报日期), fresh(latest>=expected), schedule(自动采集时点),
#          products:{...}, ex_totals:{...},
#          warehouses:[{name,type,qty:{date:v},change:{date:v}}](仅指定品种时)}
#   as_of / collected_at 来自 overview.json —— 即「采集最新数据」写入的数据日期与
#   生成时间, 前端用它们把历史走势与采集动作显式关联(2026-09-21 修复:此前历史
#   走势不展示采集口径, 早间采集拿到的是上一交易日数据时, 用户会误以为没更新)。
#   ex_latest 逐所给出最新有数日期: 某所采集失败/日报未发布时, 前端能点名是
#   哪一所拖住了日期(如 2026-09-16 广期所缺口), 而不是整条曲线"看起来不动"。
#   响应带 Cache-Control: no-store, 防止浏览器把历史接口缓存住导致采集后不刷新。
#   数据由 warrant/history.py 从 out/ 落盘 JSON 离线聚合(含 CZCE 原始 xlsx 回填),
#   进程内缓存, 采集落盘后文件集变化自动重建(refresh/定时任务还会显式失效)。
@app.route("/api/warrant/history")
def api_warrant_history():
    from warrant import history as warrant_history  # 【延迟导入】避免拖慢 web_app 冷启动
    try:
        hist = warrant_history.get()
    except Exception:  # noqa: BLE001 - 历史聚合失败不应 500 整页
        logger.exception("仓单历史聚合失败")
        resp = jsonify({"ok": False, "error": "仓单历史数据聚合失败",
                        "dates": [], "products": {}, "ex_totals": {}})
        resp.headers["Cache-Control"] = "no-store"
        return resp
    # 每个交易所的「最新有数日期」: 取该所全部品种 qty 键的最大日期。
    # 用品种级数据而非 out 文件名, 是因为文件存在不代表解析出了有效品种行。
    ex_latest: dict = {}
    for _key, p in hist["products"].items():
        ex = p.get("exchange")
        qs = p.get("qty") or {}
        ld = max((d for d in qs if qs.get(d) is not None), default=None)
        if ld and (ex not in ex_latest or ld > ex_latest[ex]):
            ex_latest[ex] = ld
    ov = _warrant_read("overview") or {}
    # 【2026-09-30】历史接口同样按品种池打 in_pool 标记: ③ 历史走势的品种下拉
    # 与 ①/② 用同一套「品种范围」筛选, 必须同源判定, 否则两处列表会不一致。
    _pool = _warrant_pool_keys()
    _hprods = {}
    for _k, _p in (hist.get("products") or {}).items():
        _q = dict(_p)
        _q["in_pool"] = _k in _pool
        _hprods[_k] = _q
    payload = {"ok": True, "dates": hist["dates"],
               "latest": hist["dates"][-1] if hist["dates"] else None,
               "as_of": ov.get("date"), "collected_at": ov.get("generated_at"),
               "ex_latest": ex_latest,
               **_warrant_freshness(hist["dates"][-1] if hist["dates"] else None),
               "pool_total": len(_pool),
               "pool_hit": sum(1 for p in _hprods.values() if p["in_pool"]),
               "products": _hprods, "ex_totals": hist["ex_totals"],
               "backfilled": hist.get("backfilled", [])}
    ex = (request.args.get("exchange") or "").strip().upper()
    code = (request.args.get("code") or "").strip()
    if ex and code:
        dates = hist["dates"]
        whs = hist["warehouses"].get("%s|%s" % (ex, code), {})

        def _latest(w):
            """最新非空仓单量(排序用; 没有任何数据排最后)"""
            for d in reversed(dates):
                v = (w.get("qty") or {}).get(d)
                if v is not None:
                    return v
            return -1

        payload["warehouses"] = [
            {"name": name, "type": w.get("type") or "仓库",
             "qty": w.get("qty") or {}, "change": w.get("change") or {}}
            for name, w in sorted(whs.items(),
                                  key=lambda kv: -_latest(kv[1]))
        ]
    resp = jsonify(payload)
    resp.headers["Cache-Control"] = "no-store"  # 【关键】采集后浏览器必须拿到新曲线
    return resp


# ─── 品种数据库 (2026-09-18 新增; 原「沥青数据库」已泛化为多品种) ───
# 数据源: variety_convert.py 把每个品种的《<品种>数据库.xlsx》离线解析为逐表 JSON。
# 目录: data/variety/varieties.json (品种清单) + data/variety/<品种>/{meta,index,sNN}.json
# 新增品种: python variety_convert.py --src "<品种>数据库-永安.xlsx"
# 本组接口只做文件读取, 不做解析。
_VARIETY_DIR = Path(__file__).parent / "data" / "variety"
_VARIETY_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,23}$")   # 品种目录标识
_SHEET_SLUG_RE = re.compile(r"^s\d{1,3}$")                    # 工作表 slug
_BITUMEN_DIR = _VARIETY_DIR / "bitumen"                        # 兼容旧引用
_BITUMEN_SLUG_RE = _SHEET_SLUG_RE                              # 兼容旧名


def _variety_root():
    """品种数据根目录(读模块全局, 便于测试 monkeypatch)。"""
    return Path(_VARIETY_DIR)


def _read_json_file(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _variety_index_payload(vslug):
    """读某品种的索引 + 元信息 → (payload, http_code)。"""
    base = _variety_root() / vslug
    path = base / "index.json"
    if not os.path.exists(path):
        return ({"ok": False, "variety": vslug, "sheets": [],
                 "error": f"品种 {vslug} 未导入, 请运行 variety_convert.py --src <品种数据库.xlsx>"},
                200)
    try:
        sheets = _read_json_file(path)
    except Exception:  # noqa: BLE001
        logger.exception("品种 %s 索引读取失败", vslug)
        return {"ok": False, "variety": vslug, "sheets": [], "error": "索引读取失败"}, 200
    meta = {}
    mp = base / "meta.json"
    if os.path.exists(mp):
        try:
            meta = _read_json_file(mp)
        except Exception:  # noqa: BLE001
            logger.warning("品种 %s meta.json 读取失败(忽略)", vslug)
    return {"ok": True, "variety": vslug, "meta": meta, "sheets": sheets}, 200


def _variety_sheet_payload(vslug, slug):
    """读某品种某工作表的数据文件 → (payload, http_code)。"""
    path = _variety_root() / vslug / f"{slug}.json"
    if not os.path.exists(path):
        return {"ok": False, "error": f"{vslug}/{slug} 不存在"}, 404
    try:
        return _read_json_file(path), 200
    except Exception:  # noqa: BLE001
        logger.exception("品种数据表 %s/%s 读取失败", vslug, slug)
        return {"ok": False, "error": "数据读取失败"}, 200


@app.route("/api/variety/list")
def api_variety_list():
    """品种清单: [{slug, name, source, imported_at, n_sheets, n_series}]"""
    root = _variety_root()
    path = root / "varieties.json"
    items = []
    if os.path.exists(path):
        try:
            items = _read_json_file(path)
        except Exception:  # noqa: BLE001
            logger.exception("品种清单读取失败")
    elif root.exists():
        # 清单缺失: 按子目录(含 index.json)重建, 兼容手工放置目录
        for d in sorted(root.iterdir()):
            if d.is_dir() and (d / "index.json").exists():
                m = {}
                mp = d / "meta.json"
                if mp.exists():
                    try:
                        m = _read_json_file(mp)
                    except Exception:  # noqa: BLE001
                        m = {}
                items.append({"slug": d.name, "name": m.get("name", d.name),
                              "source": m.get("source"), "imported_at": m.get("imported_at"),
                              "n_sheets": m.get("n_sheets"), "n_series": m.get("n_series")})
    return jsonify({"ok": True, "varieties": items})


@app.route("/api/variety/<vslug>/index")
def api_variety_index(vslug):
    """某品种的模块索引: [{slug, name, type, group, n_series, date_min, date_max, units}]"""
    if not _VARIETY_SLUG_RE.match(vslug or ""):
        return jsonify({"ok": False, "error": "非法品种标识"}), 400
    return jsonify(_variety_index_payload(vslug)[0])


@app.route("/api/variety/<vslug>/sheet/<slug>")
def api_variety_sheet(vslug, slug):
    """某品种某工作表数据(前端按需加载)。slug 形如 s01, 白名单校验防目录穿越。"""
    if not _VARIETY_SLUG_RE.match(vslug or ""):
        return jsonify({"ok": False, "error": "非法品种标识"}), 400
    if not _SHEET_SLUG_RE.match(slug or ""):
        return jsonify({"ok": False, "error": "非法 slug"}), 400
    payload, code = _variety_sheet_payload(vslug, slug)
    return jsonify(payload), code


# ── 旧路由别名(2026-09-18 前的前端/脚本): 固定指向 bitumen 品种 ──
@app.route("/api/bitumen/index")
def api_bitumen_index():
    return jsonify(_variety_index_payload("bitumen")[0])


@app.route("/api/bitumen/sheet/<slug>")
def api_bitumen_sheet(slug):
    if not _SHEET_SLUG_RE.match(slug or ""):
        return jsonify({"ok": False, "error": "非法 slug"}), 400
    payload, code = _variety_sheet_payload("bitumen", slug)
    return jsonify(payload), code


# ═══════════════════════════════════════════════════════════════════════════════
# 外文研报 → 中文编译 PDF（2026-09-24 新增「外文研报」tab 的服务端）
# -------------------------------------------------------------------------------
# 【做什么】用户上传外文研报 PDF → 后台线程把它编译成中文本 A4 PDF
#   （封面 / 核心结论 / 结论与逻辑支撑 / 关键数据速查 / 内部关联解读 / 全文翻译 / 附录），
#   前端拿 job id 轮询进度，完成后下载。
# 【为什么是"任务制"】全文翻译一份 20 页外文报告要 5-15 分钟，同步请求必然超时，
#   所以 upload 只登记任务并立即返回，进度靠 /api/foreign/job/<id> 轮询。
# 【路由风格】与 /api/research/upload 一致：校验 → 落盘 → 起后台线程 → 立即返回 id。
# 【鉴权】本组路由命中全局 before_request 守卫（/api/* 需登录态），无需另加装饰器。
# -------------------------------------------------------------------------------

FOREIGN_ALLOWED_EXTS = {".pdf"}  # 【变量】外文研报只接受 PDF(图片型/扫描件需先 OCR,见 foreign_report)
_FOREIGN_JID_RE = re.compile(r"^[0-9a-f]{6,32}$")  # 【变量】任务 id 白名单(防路径穿越)


def _foreign_public(job: dict) -> dict:
    """把内部任务字典裁成前端可见字段（不外泄服务器文件路径）。"""
    return {
        "id": job.get("id"),
        "filename": job.get("filename"),
        "title": job.get("title"),
        "org": job.get("org"),
        "status": job.get("status"),
        "stage": job.get("stage"),
        "stage_label": job.get("stage_label"),
        "progress": job.get("progress", 0),
        "message": job.get("message"),
        "error": job.get("error"),
        "log": job.get("log") or [],
        "warnings": job.get("warnings") or [],
        "created_at": job.get("created_at"),
        "finished_at": job.get("finished_at"),
        "n_pages": job.get("n_pages"),
        "pages": job.get("pages"),
        "chars": job.get("chars"),
        "chunks": job.get("chunks"),
        "out_name": job.get("out_name"),
        "has_pdf": bool(job.get("out_pdf") and Path(job["out_pdf"]).exists()),
        "options": job.get("options") or {},
    }


@app.route("/api/foreign/upload", methods=["POST"])
def api_foreign_upload():
    """上传外文研报 PDF：校验 → 落盘 → 登记编译任务 → 立即返回 {id}。

    【表单】file 必填（.pdf）；include_images / include_insight 可选（"0" 表示关闭）。
    【返回】{"id": 任务id, "status": "running"}；前端据 id 轮询 /api/foreign/job/<id>。
    """
    import foreign_report  # 【调用包】外文研报编译管线(局部导入,避免拖慢主模块加载)

    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "缺少上传文件"}), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in FOREIGN_ALLOWED_EXTS:
        return jsonify({"error": f"不支持的文件类型 {ext or '(无扩展名)'}，本功能只接受 PDF"}), 400

    f.stream.seek(0, os.SEEK_END)
    size = f.stream.tell()
    f.stream.seek(0)
    if size > foreign_report.MAX_UPLOAD_BYTES:
        return jsonify({"error": f"文件超过 {foreign_report.MAX_UPLOAD_BYTES // 1048576}MB 上限"}), 400
    if size < 1024:
        return jsonify({"error": "文件过小，疑非有效 PDF"}), 400

    from werkzeug.utils import secure_filename  # 【调用包】文件名安全化(仅用于展示名)

    display = secure_filename(f.filename) or "foreign_report.pdf"
    if not display.lower().endswith(".pdf"):
        display += ".pdf"
    upload_dir = foreign_report.UPLOAD_DIR
    upload_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = upload_dir / f"{secrets.token_hex(8)}.pdf"
    f.save(str(pdf_path))

    def _flag(name: str, default: bool = True) -> bool:
        raw = (request.form.get(name) or "").strip().lower()
        if raw == "":
            return default
        return raw not in ("0", "false", "no", "off")

    options = {
        "include_images": _flag("include_images", True),
        "include_insight": _flag("include_insight", True),
    }
    try:
        jid = foreign_report.start_job(pdf_path, display, options)
    except Exception as e:
        logger.warning("foreign report start_job failed", exc_info=True)
        with contextlib.suppress(Exception):
            pdf_path.unlink()
        return jsonify({"error": f"任务登记失败：{e}"}), 500
    return jsonify({
        "id": jid,
        "status": "running",
        "message": "已接收，正在后台编译（全文翻译较耗时，可离开本页稍后回来看）",
    })


@app.route("/api/foreign/jobs")
def api_foreign_jobs():
    """近期编译任务列表（时间倒序），供页面历史区展示。"""
    import foreign_report

    try:
        limit = max(1, min(100, int(request.args.get("limit", 30))))
    except Exception:
        limit = 30
    return jsonify({"jobs": foreign_report.list_jobs(limit)})


@app.route("/api/foreign/job/<jid>")
def api_foreign_job(jid: str):
    """查询单个任务进度（前端每 5s 轮询一次）。404 = 任务不存在。"""
    import foreign_report

    if not _FOREIGN_JID_RE.match(jid or ""):
        return jsonify({"error": "非法任务 id"}), 400
    job = foreign_report.get_job(jid)
    if not job:
        return jsonify({"error": "任务不存在"}), 404
    resp = jsonify(_foreign_public(job))
    resp.headers["Cache-Control"] = "no-store"  # 进度必须每次现取,不能被浏览器缓存
    return resp


@app.route("/api/foreign/download/<jid>")
def api_foreign_download(jid: str):
    """下载/预览编译稿 PDF。?inline=1 走内联预览，否则作附件下载。"""
    import foreign_report

    if not _FOREIGN_JID_RE.match(jid or ""):
        return jsonify({"error": "非法任务 id"}), 400
    job = foreign_report.get_job(jid)
    if not job:
        return jsonify({"error": "任务不存在"}), 404
    out = job.get("out_pdf")
    if not out or not Path(out).exists():
        return jsonify({"error": "编译稿尚未生成或已被清理"}), 404
    inline = (request.args.get("inline") or "") in ("1", "true", "yes")
    return send_file(
        out,
        mimetype="application/pdf",
        as_attachment=not inline,
        download_name=job.get("out_name") or "外文研报_中文编译.pdf",
    )


@app.route("/api/foreign/job/<jid>", methods=["DELETE"])
def api_foreign_delete(jid: str):
    """删除任务：清理状态、产物 PDF 与上传的原始文件。"""
    import foreign_report

    if not _FOREIGN_JID_RE.match(jid or ""):
        return jsonify({"error": "非法任务 id"}), 400
    ok = foreign_report.delete_job(jid)
    return jsonify({"ok": ok, "id": jid})


# 主入口:以脚本方式运行时启动调度器 + Waitress 生产服务器。
# · 调度器启动失败只打印警告,不影响看板启动。
# · waitress.serve 以 8 个线程监听 0.0.0.0:5000,channel_timeout=600 秒。
if __name__ == "__main__":
    # 【RAG 进程隔离】web 服务主进程启用 RAG worker 模式:torch/chromadb 原生栈
    # 全部在 rag_worker.py 子进程内加载推理,worker 崩溃不再带走 web 服务
    # (2026-09-10 两天 4 次 access violation 后的根治)。采集器等 import 本模块
    # 的子进程不受此影响,保持进程内直调。必须在调度器/线程启动前设置。
    # 【2026-09-14】同时打进程级环境变量:同进程内被二次导入的 web_app 副本
    # (调度器 job / 采集器里的 `from web_app import ...`)据此也走 worker 模式,
    # 否则那份副本默认 False,会把 torch/chromadb 拉回主进程。
    os.environ["AGENTSENSE_WEB_SERVER"] = "1"
    _RAG_WORKER_MODE = True

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
            # 开盘前研报自动接入(日盘 09:00 前 + 夜盘 21:00 前) + 22:00 夜盘补充采集
            # (2026-09-10 新增 22:00:夜盘开盘后补一轮,错峰 HTFC 22:15/GTJA 22:30/DZ 22:40/RAG 23:15)
            research_times=["08:10", "18:00", "22:00"],
            # 仓单采集(2026-09-21 需求:每天定时采集并更新整个「仓单分析」板块)
            #   08:00 = 用户指定;16:40 = 追加当日收盘后补采(四所日报约 16:00 起发布,
            #   只跑早间会让当天数据迟一天进看板)。启动时若当天已过时点却漏采会自动补跑。
            warrant_times=["08:00", "16:40"],
            # 公众号采集+入库(2026-09-21 需求:定时采「多空逻辑」等号的每日新文并入研报库)
            #   采集 08:30/18:30 = 用户指定,零登录「合集」通道游标增量,**不占 LLM**;
            #   入库 09:05/19:05 = **刻意后移 35 分钟**,避开研报三源的 LLM 并发窗口
            #   (research_times 会错峰成 htfc 08:25/18:15、gtja 08:40/18:30、dz 08:50/18:40,
            #    账户并发上限 5;入库要逐篇走 LLM,同一时刻起跑必然 429)。
            wechat_collect_times=["08:30", "18:30"],
            wechat_ingest_times=["09:05", "19:05"],
            # 研报归档(2026-09-28:每日增量把 >14 天研报总结化→RAG→软删;09:20 避开
            # 早间研报 LLM 并发 08:10-09:05 + 公众号 09:05 入库, 不抢并发配额)
            archive_times=["09:20"],
            # 隆众资讯(2026-10-14 新增):采集 08:20/17:10(**免登录 HTTP,不占 LLM**);
            # 入库 10:00/20:00 —— 刻意避开研报三源窗口(08:25~09:45 / 18:15~19:35),
            # 账户并发上限 5,研报三源已占 2~3 路,入库逐篇走 LLM 必须错开。
            oilchem_collect_times=["08:20", "17:10"],
            oilchem_ingest_times=["10:00", "20:00"],
        )
        print("Scheduler started: daily 08:00/18:00, research 08:10/18:00/22:00, "
              "warrant 08:00/16:40, wechat collect 08:30/18:30 + ingest 09:05/19:05, "
              "oilchem collect 08:20/17:10 + ingest 10:00/20:00")
    except Exception as e:
        print(f"Scheduler not started: {e}")

    # 【僵尸回收·启动扫描】服务被强杀/崩溃时,正在 LLM 处理的研报状态会永远停在
    # processing,前端表现为"文档处理停了好久"(2026-09-10 实测:重启杀进程留下 3 条)。
    # 【2026-09-14 补 pending】本地批量导入是"先全批登记(status=pending) → 再并行
    # 处理",进程在阶段二途中被杀时,队列里**尚未被 worker 认领**的 pending 行同样是
    # 孤儿(研报库显示排队中却再也不动),所以两类状态一起扫。
    # 【2026-09-17 抽出】逻辑抽到 _sweep_stuck_research_reports(启动/周期共用),
    # 并在这里追加**周期回收线程** —— 原实现只在启动时扫一次,而采集器是独立子进程,
    # 它崩溃留下的孤儿行在"服务不重启"的情况下永远无人回收(09-15~17 累积 15 篇)。
    try:
        _sweep_stuck_research_reports(reason="startup", at_startup=True)
    except Exception as e:
        print(f"Stuck processing sweep skipped: {e}")
    _start_research_stuck_watchdog()

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

    # 【启动自愈 2026-09-11】聚合缺失对账:done 行在聚合 JSON 缺条目的从 DB 重建
    # (零 LLM),修"逐品种观点总览与总结不对齐"(周报聚合缺失)。与孤儿清扫互补:
    # 清扫管"聚合多 DB 少",本步管"DB 有聚合缺"。
    try:
        _rep = _repair_research_aggregates(dry_run=False)
        if _rep.get("missing_rows"):
            print(
                f"Aggregate repair: {_rep['missing_rows']} rows / {_rep['repaired_entries']} entries rebuilt"
            )
    except Exception as e:
        print(f"Aggregate repair skipped: {e}")

    from waitress import serve  # 【调用包】生产级 WSGI 服务器(多线程托管 Flask)

    # 【2026-09-29】启动即可见鉴权模式:否则"免登录"与"登录坏了"从现象上无法区分。
    if os.environ.get("AGENTSENSE_AUTH_DISABLE") == "1":
        print("Auth: DISABLED (AGENTSENSE_AUTH_DISABLE=1) — 所有 /api/* 免登录")
    else:
        print("Auth: ENABLED (登录鉴权生效;如需免登录在 .env 设 AGENTSENSE_AUTH_DISABLE=1)")
    print("FuturesMind Dashboard: http://localhost:5000")
    serve(app, host="0.0.0.0", port=5000, threads=8, channel_timeout=600)
