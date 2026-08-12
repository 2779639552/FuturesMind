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
#   3) 主页面:     / 、/test
#   4) 分析工具:   /api/run_analysis(SSE 流式) + /api/progress 轮询 +
#                  /api/pause /api/resume /api/stop /api/feedback,
#                  /api/analysis_results、/api/backtest、/api/history、/api/compare
#   5) 报告导出:   /api/report/<文件>/pdf、/api/report/<文件>/md
#   6) 配置:       /api/config
#   7) 数据更新:   /api/update_data(SSE 流水线)
#   8) 数据库 / 调度器 / 鉴权: /api/db/*、/api/scheduler/*、/api/auth/*
#   9) 分析接口:   /api/analysis/*(异常、背离、领先滞后、作者、事件、排名、跨平台)
#   10) 自选:      /api/watchlist
#   11) 模拟交易:  /api/trading/*(20+ 条策略路由,含风控与多策略对比)
#   12) Agent 验证: /api/batch_backtest/*、/api/agent_validation/*
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

import glob
import io
import json
import os
import re
import secrets
import sys
import threading
import time
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    jsonify,
    make_response,
    render_template_string,
    request,
    send_file,
    stream_with_context,
)

load_dotenv()

# 把本文件所在目录加入模块搜索路径,使同目录下的 commodity_demo / database /
# signal_analyzer / tradingagents 等模块无需安装即可被 import。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import contextlib  # noqa: E402

from langchain_core.messages import HumanMessage  # noqa: E402

from commodity_demo import build_commodity_graph  # noqa: E402

# New imports for v2.6
from database import get_db  # noqa: E402
from path_utils import resolve_think2_dir  # noqa: E402
from signal_analyzer import (  # noqa: E402
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
from tradingagents.dataflows.commodity_futures import (  # noqa: E402
    VARIETY_METADATA,
    get_futures_price,
)
from tradingagents.dataflows.config import set_config  # noqa: E402
from tradingagents.dataflows.evolution_memory import get_evolution_context  # noqa: E402
from tradingagents.dataflows.sentiment_data import load_sentiment_data  # noqa: E402
from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402
from tradingagents.llm_clients import create_llm_client  # noqa: E402

# ------------------------------------------------------------------
# Flask 应用初始化
# · secret_key 优先取环境变量 FLASK_SECRET_KEY,否则随机生成(重启即失效)。
# · config 从 DEFAULT_CONFIG 复制一份,并同步给 tradingagents.dataflows.config,
#   供后续构建 Agent 图 / 调用 LLM 时读取统一配置。
# ------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))
config = DEFAULT_CONFIG.copy()
set_config(config)

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

THINK2_OUTPUT = THINK2_DIR / "output" if THINK2_DIR else None
THINK2_TRENDS = THINK2_OUTPUT / "trends" if THINK2_OUTPUT else None

LOG_DIR = Path(os.path.expanduser("~/.tradingagents/logs"))
REPORT_DIR = LOG_DIR

# Template path (reload on every request for live editing)
TEMPLATE_PATH = Path(__file__).parent / "web_template.html"


# 【功能】读取前端模板 web_template.html 的完整内容。
# 【关键】每次调用都重新读盘,配合"每次请求都读模板"的设计实现模板热改。
def get_page_template():
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        return f.read()


# ── Sector mapping (dynamic from VARIETY_METADATA) ────────────────────────────


# 【功能】根据品种代码从 VARIETY_METADATA 取中文板块名(如"黑色系")。
# 【参数】code: 品种代码(如 "RB")。
# 【返回】板块中文名;查不到时返回 "其他"。
def _get_sector(code: str) -> str:
    """Get sector name dynamically from VARIETY_METADATA."""
    meta = VARIETY_METADATA.get(code, {})
    return meta.get("sector_cn", "其他")


# ── Progress Tracker (thread-safe, adapted from astock-ref) ────────────────────

# 分析流水线的 10 个阶段定义。id 供前后端对位, name 为中文显示名, icon 为图标。
# 这些阶段与 commodity_demo 构建的 Agent 图节点一一对应:
# 技术 → 基本面 → 宏观 → 情绪 → 多方立论 → 空方反驳 → 多方反驳 → 辩论裁决 → 综合研判 → 情景分析。
PIPELINE_STAGES = [
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
_tracker: ProgressTracker | None = None

# ── Config persistence ──────────────────────────────────────────────────────

CONFIG_PATH = Path(os.path.expanduser("~/.tradingagents/web_config.json"))


# 【功能】读取前端界面配置(主题 / LLM 模型等)。文件不存在时返回空 dict。
# 【关键】配置持久化在用户主目录 ~/.tradingagents/web_config.json,重启不丢失。
def _load_web_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


# 【功能】把前端界面配置写回 ~/.tradingagents/web_config.json(自动建目录、UTF-8 缩进)。
def _save_web_config(cfg: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ── Routes ─────────────────────────────────────────────────────────────────

# ── Live Price ────────────────────────────────────────────────────────────────

PRICE_CACHE_FILE = Path(__file__).parent / "live_prices_cache.json"


# 【功能】启动一个后台守护线程,周期性调用 warm_cache.py 刷新实时行情缓存文件。
# 【关键逻辑】
#   · 交易时段判定(北京时间): 周一~周五 且 (8~15点 或 21~23点)。
#   · 交易时段每 300 秒(5 分钟)刷新,非交易时段每 1800 秒(30 分钟)。
#   · 子进程执行超时 120 秒;任何异常都吞掉,避免后台线程崩溃。
def _start_price_cache_updater():
    """Background thread: refresh price cache every 5 min during trading hours."""
    import subprocess as _sp
    import sys as _sys
    import threading as _th
    import time as _time

    def _refresh():
        while True:
            try:
                now = _time.localtime()
                # Trading hours: Mon-Fri 8:30-15:30, 21:00-23:30 (Beijing time)
                is_weekday = now.tm_wday < 5
                hour = now.tm_hour
                is_trading = is_weekday and ((8 <= hour <= 15) or (21 <= hour <= 23))
                interval = 300 if is_trading else 1800  # 5min during trading, 30min otherwise

                _sp.run(
                    [_sys.executable, str(Path(__file__).parent / "warm_cache.py")],
                    capture_output=True,
                    timeout=120,
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
        from price_fetcher import update_price_files

        result = update_price_files(varieties)
        return jsonify({"updated": len(result), "details": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# 【功能】渲染看板主页面。get_page_template() 读 web_template.html,再经
#   render_template_string 注入(模板里的 Jinja2 变量会被替换为实际数据)。
# 【关键】显式禁用浏览器缓存(no-cache),保证每次刷新都拿到最新模板与数据。
@app.route("/")
def index():
    resp = make_response(render_template_string(get_page_template()))
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


# 【功能】获取品种日线价格,供前端画折线 / K 线。
# 【参数】days=回看天数(默认 180,强制限制在 30~730)。
# 【返回】{"_meta": {price_start, price_end, data_points}, "prices": [{date, close}, ...]}。
# 【关键】get_futures_price() 返回 CSV 文本;这里逐行解析,只取日期与收盘价(第 5 列),
#   并只返回最后 max(days,120) 个点。
@app.route("/api/price/<variety>")
def api_price(variety):
    """Get price data for charts. Query param: days (default 180)."""
    days = request.args.get("days", 180, type=int)
    days = max(30, min(days, 730))  # Clamp 30-730
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    result = get_futures_price(variety, start_date, end_date)
    data = []
    for line in result.strip().split("\n"):
        if not line or line.startswith("#") or not line[0].isdigit():
            continue
        parts = line.split(",")
        if len(parts) >= 5:
            data.append({"date": parts[0].strip(), "close": float(parts[4])})
    # Return with meta
    meta = {}
    if data:
        meta = {
            "price_start": data[0]["date"],
            "price_end": data[-1]["date"],
            "data_points": len(data),
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

    # Price data
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    price_result = get_futures_price(variety, start_date, end_date)
    price_map = {}
    for line in price_result.strip().split("\n"):
        if not line or line.startswith("#") or not line[0].isdigit():
            continue
        parts = line.split(",")
        if len(parts) >= 5:
            price_map[parts[0].strip()] = float(parts[4])

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
        **sent_meta,
    }

    return jsonify({"_meta": meta, "overlay": overlay})


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


# 【功能】列出最近 20 份已保存的分析报告(commodity_*.md)。
# 【返回】[{symbol, filename, size, time, path}, ...]。
@app.route("/api/history")
def api_history():
    """Get past analysis reports."""
    reports = []
    if REPORT_DIR.exists():
        for f in sorted(REPORT_DIR.glob("commodity_*.md"), reverse=True)[:20]:
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
    price_result = get_futures_price(variety, date, end_dt.strftime("%Y-%m-%d"))
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
        m = re.search(rf"## {sec}\n(.*?)(?=\n## |\n---\n|\Z)", content, re.DOTALL)
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
        include_sentiment = load_sentiment_data(symbol) is not None

    # 阶段列表:不带情绪分析时,过滤掉 "sentiment" 阶段,进度条也随之少一段。
    stages = (
        PIPELINE_STAGES
        if include_sentiment
        else [s for s in PIPELINE_STAGES if s["id"] != "sentiment"]
    )

    _tracker = ProgressTracker(symbol=symbol, trade_date=trade_date, stages=stages)
    _tracker.is_running = True

    # Agent 节点名 → 前端阶段 id 的映射,用于把图节点执行进度映射为进度条阶段。
    stage_map = {
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
    def run_analysis():
        global _tracker
        try:
            # 构建 LangGraph 多分析师图;enable_feedback=False 表示本轮不要求用户反馈。
            app_graph, _ = build_commodity_graph(
                config, enable_feedback=False, include_sentiment=include_sentiment
            )
            evo_ctx = get_evolution_context(symbol)
            initial_state = {
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
            for chunk in app_graph.stream(initial_state, stream_mode="updates"):
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
                        from tradingagents.dataflows.evolution_memory import (
                            store_prediction,
                        )

                        store_prediction(
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
    evo_ctx = get_evolution_context(symbol)
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
            config.get("quick_think_llm", config["deep_think_llm"]),
        )
        llm = client.get_llm()
        result = llm.invoke(debate_prompt)
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
    from fpdf import FPDF

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
        total = len(steps)

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

                    import subprocess

                    venv_py = os.path.join(os.path.dirname(sys.executable), "python")
                    cmd = [
                        venv_py,
                        "batch_collect.py",
                        "--platform",
                        platforms[0],
                        "--per-kw",
                        str(per_kw),
                        "--turbo",
                        "--no-detail",
                    ]
                    if since_date:
                        cmd.extend(["--since", since_date])

                    # 【关键逻辑】采集子进程在后台线程里跑, 主生成器只负责轮询推进度,
                    # 避免整条 SSE 流被 subprocess.run 阻塞住(前端"1/7 采集数据"看起来像卡住)。
                    # 一个关键词对应一个 batch_{平台}_{时间戳}.jsonl, 每 4 秒对比一次输出目录,
                    # 有新批次文件就实时推一条 log(完成几个关键词/最新文件多少条);
                    # 若超过 15 秒无新文件, 推一条心跳消息保持"活着"的观感。子进程结束(含
                    # 600 秒超时转异常)后再把最终 stdout/stderr 里关键行推出去。
                    pre_batches = set(glob.glob(str(THINK2_OUTPUT / "batch_*.jsonl")))
                    holder = {}

                    def _run_collect():
                        try:
                            holder["res"] = subprocess.run(
                                cmd,
                                cwd=str(THINK2_DIR),
                                capture_output=True,
                                text=True,
                                timeout=600,
                            )
                        except Exception as e:  # noqa: BLE001
                            holder["err"] = str(e)

                    th = threading.Thread(target=_run_collect, daemon=True)
                    th.start()

                    start_ts = time.time()
                    last_count = 0
                    last_report_ts = start_ts
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
                    if "err" in holder:
                        yield f"data: {json.dumps({'type': 'log', 'msg': f'collect 阶段出错: {holder['err']}'}, ensure_ascii=False)}\n\n"
                    else:
                        result = holder["res"]
                        lines = (result.stdout or "").split("\n") + (result.stderr or "").split("\n")
                        for line in lines:
                            if any(
                                kw in line.lower()
                                for kw in ["complete", "total notes", "done", "error"]
                            ):
                                yield f"data: {json.dumps({'type': 'log', 'msg': line.strip()[:200]}, ensure_ascii=False)}\n\n"

                elif key == "fix_fans":
                    yield f"data: {json.dumps({'type': 'log', 'msg': 'Parsing fan count strings...'}, ensure_ascii=False)}\n\n"
                    if not THINK2_DIR or not THINK2_DIR.exists():
                        continue
                    sys.path.insert(0, str(THINK2_DIR))
                    from platforms.weibo_adapter import _parse_fans_count

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
                                    d["author_fans"] = _parse_fans_count(raw)
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
                    from trend_aggregator import aggregate

                    paths = sorted(glob.glob(str(THINK2_OUTPUT / "batch_*.jsonl")))
                    result = aggregate(paths)
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
                    from backtest_weights import run_all

                    result_b = run_all(min_points=10, horizons=[1, 3, 5])
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
                    from generate_tradingagents_sentiment import (
                        OUTPUT_DIR as GEN_OUTPUT,
                        generate_sentiment_json,
                        load_trends_data,
                    )

                    varieties, index, global_weights = load_trends_data(THINK2_TRENDS)
                    GEN_OUTPUT.mkdir(parents=True, exist_ok=True)
                    gen_count = 0
                    for vname in sorted(varieties.keys()):
                        output = generate_sentiment_json(
                            vname, varieties[vname], index, global_weights
                        )
                        if output is None:
                            continue
                        if output["data"]["social_sentiment"]["total_posts_analyzed"] < min_notes:
                            continue
                        sym = output["variety"]
                        with open(GEN_OUTPUT / f"{sym}_sentiment.json", "w", encoding="utf-8") as f:
                            json.dump(output, f, ensure_ascii=False, indent=2)
                        gen_count += 1
                    yield f"data: {json.dumps({'type': 'log', 'msg': f'Generated {gen_count} varieties (min_notes={min_notes})'}, ensure_ascii=False)}\n\n"

                elif key == "update_price":
                    yield f"data: {json.dumps({'type': 'log', 'msg': 'Fetching latest prices via AKShare...'}, ensure_ascii=False)}\n\n"
                    if THINK2_DIR and THINK2_DIR.exists():
                        import subprocess

                        venv_py = os.path.join(os.path.dirname(sys.executable), "python")
                        result = subprocess.run(
                            [venv_py, "price_fetcher.py"],
                            cwd=str(THINK2_DIR),
                            capture_output=True,
                            text=True,
                            timeout=120,
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
                                    plat = d.get("platform", "?")
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
    db = get_db()
    return jsonify(
        {
            "platforms": db.get_platform_stats(),
            "total_posts": db.get_total_posts(),
            "collection_history": db.get_collection_history(10),
            "unacknowledged_alerts": db.get_unacknowledged_count(),
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
        from scheduler import _scheduler

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
        from scheduler import start_scheduler

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
        from scheduler import stop_scheduler

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
_auth_tokens: set[str] = set()


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
# · 综合类:run / contrarian / adaptive_sentiment / apply_risk(风控) / multi_compare / compare
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
    result = run_simulated_trading(
        variety=variety,
        horizon=horizon,
        signal_threshold=threshold,
        start_date=data.get("start_date", "2025-01-01"),
        end_date=data.get("end_date", ""),
    )
    # Save trades to DB
    db = get_db()
    for t in result.get("recent_trades", []):
        db.save_trade_signal(t["variety"], t["entry"], 0, t["dir"], 0, horizon)
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
    result = run_contrarian_sentiment(
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
    result = run_adaptive_sentiment(
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

    from signal_analyzer import _load_price as _lpr, apply_risk_management as _arm

    px_data = _lpr(variety)
    prices = px_data.get("prices", []) if px_data else []
    result = _arm(trades_raw, prices, stop_loss, trail_stop)
    return jsonify({"trades": result})


# 【功能】一次运行多个策略,返回各自的累计收益曲线(PnL),便于前端横向对比。
# 【请求体】{"strategies": ["fixed","trailing",...], "variety": ..., "horizon": 3,
#             "start_date": ..., "end_date": ...}
# 【返回】{"curves": {策略名: [累计收益,...]}, "stats": {策略名: {trades,win_rate,total_pnl,label,...}},
#           "dates": 公共交易日轴, "price_curve": 价格归一化曲线(%)}
# 【关键逻辑】
#   · 逐策略调用对应回测函数;先把"入场日 → 当日 PnL"映射出来(strategy_pnls),
#     再按公共日期轴累加,得到可对齐比较的曲线。
#   · 某个策略抛异常不影响其它策略,错误信息写入 stats[策略名].error。
#   · price_curve 以起始收盘价为基准做百分比归一化。
@app.route("/api/trading/multi_compare", methods=["POST"])
def api_trading_multi():
    """Run multiple strategies and return all PnL curves in one response."""
    data = request.json or {}
    strategies = data.get("strategies", ["fixed", "trailing"])
    variety = data.get("variety", "")
    horizon = data.get("horizon", 3)
    start_date = data.get("start_date", "2025-01-01")
    end_date = data.get("end_date", "2026-07-21")
    data.get("stop_loss", 0)
    data.get("trail_stop", 0)

    result = {"curves": {}, "stats": {}, "dates": [], "price_curve": []}
    # Per-strategy PnL deltas: {strategy: {entry_date: pnl}}
    strategy_pnls = {}

    for s in strategies:
        try:
            if s == "fixed":
                r = run_simulated_trading(
                    variety=variety, horizon=horizon, signal_threshold=0.2, start_date=start_date
                )
                trades = r.get("recent_trades", [])
                pnl_map = {}
                for t in trades:
                    d = str(t.get("entry", ""))
                    p = float(t.get("pnl", 0))
                    pnl_map[d] = pnl_map.get(d, 0) + p
                strategy_pnls[s] = pnl_map
                result["stats"]["fixed"] = {
                    "trades": r.get("total_trades", 0),
                    "win_rate": r.get("win_rate", 0),
                    "total_pnl": round(sum(pnl_map.values()), 2),
                    "label": "情绪固定",
                    "advanced_metrics": r.get("advanced_metrics", {}),
                }

            elif s == "trailing":
                r = run_trailing_strategy(
                    variety=variety,
                    signal_threshold=0.2,
                    max_holding=10,
                    start_date=start_date,
                    end_date=end_date,
                )
                trades = r.get("recent_trades", [])
                pnl_map = {}
                for t in trades:
                    d = str(t.get("entry", ""))
                    p = float(t.get("pnl", 0))
                    pnl_map[d] = pnl_map.get(d, 0) + p
                strategy_pnls[s] = pnl_map
                result["stats"]["trailing"] = {
                    "trades": r.get("total_trades", 0),
                    "win_rate": r.get("win_rate", 0),
                    "total_pnl": round(sum(pnl_map.values()), 2),
                    "label": "情绪反转",
                    "advanced_metrics": r.get("advanced_metrics", {}),
                }

            elif s == "adaptive_sent":
                r = run_adaptive_sentiment(
                    variety=variety, start_date=start_date, end_date=end_date
                )
                c = r.get("curves", {}).get("adaptive", [])
                dates = r.get("dates", [])
                pnl_map = {}
                if len(dates) == len(c):
                    prev = 0
                    for i, d in enumerate(dates):
                        delta = c[i] - prev
                        prev = c[i]
                        if delta != 0:
                            pnl_map[d] = round(delta, 2)
                strategy_pnls[s] = pnl_map
                result["stats"]["adaptive_sent"] = {
                    "trades": r.get("adaptive", {}).get("trades", 0),
                    "win_rate": r.get("adaptive", {}).get("win_rate", 0),
                    "total_pnl": round(c[-1], 2) if c else 0,
                    "label": "自适应",
                    "advanced_metrics": r.get("adaptive", {}).get("advanced_metrics", {}),
                }

            elif s == "contrarian":
                r = run_contrarian_sentiment(
                    variety=variety, start_date=start_date, end_date=end_date
                )
                c = r.get("curves", {}).get("contrarian", [])
                dates = r.get("dates", [])
                pnl_map = {}
                if len(dates) == len(c):
                    prev = 0
                    for i, d in enumerate(dates):
                        delta = c[i] - prev
                        prev = c[i]
                        if delta != 0:
                            pnl_map[d] = round(delta, 2)
                strategy_pnls[s] = pnl_map
                result["stats"]["contrarian"] = {
                    "trades": r.get("contrarian", {}).get("trades", 0),
                    "win_rate": r.get("contrarian", {}).get("win_rate", 0),
                    "total_pnl": round(c[-1], 2) if c else 0,
                    "label": "逆情绪",
                    "advanced_metrics": r.get("contrarian", {}).get("advanced_metrics", {}),
                }
            elif s == "momentum_ad":
                r = run_momentum_adaptive(variety=variety, start_date=start_date, end_date=end_date)
                c = r.get("curves", {}).get("adaptive", [])
                dates = r.get("dates", [])
                pnl_map = {}
                if len(dates) == len(c):
                    prev = 0
                    for i, d in enumerate(dates):
                        delta = c[i] - prev
                        prev = c[i]
                        if delta != 0:
                            pnl_map[d] = round(delta, 2)
                strategy_pnls[s] = pnl_map
                result["stats"]["momentum_ad"] = {
                    "trades": r.get("adaptive", {}).get("trades", 0),
                    "win_rate": r.get("adaptive", {}).get("win_rate", 0),
                    "total_pnl": round(c[-1], 2) if c else 0,
                    "label": "动量+自适应",
                    "advanced_metrics": r.get("adaptive", {}).get("advanced_metrics", {}),
                }
        except Exception as e:
            result["stats"][s] = {"error": str(e)[:100]}

    # Build price curve and common date grid
    common_dates = []
    if variety:
        from signal_analyzer import _load_price as _lp2

        pdata = _lp2(variety)
        if pdata:
            prices = pdata.get("prices", [])
            if prices:
                base = float(prices[0]["close"])
                px_curve = []
                for px in prices:
                    d = str(px["date"])[:10]
                    if start_date <= d <= end_date:
                        px_curve.append(round((float(px["close"]) - base) / base * 100, 2))
                        common_dates.append(d)
                result["price_curve"] = px_curve

    # Build aligned curves: iterate common dates, accumulate PnL for each strategy
    if common_dates:
        result["dates"] = common_dates
        for s in strategy_pnls:
            pnl_map = strategy_pnls[s]
            cum = 0
            aligned = []
            for d in common_dates:
                if d in pnl_map:
                    cum += pnl_map[d]
                aligned.append(round(cum, 2))
            result["curves"][s] = aligned
            # Update total_pnl
            if s in result["stats"]:
                result["stats"][s]["total_pnl"] = round(cum, 2)
    else:
        result["dates"] = []

    return jsonify(result)


# 【功能】动量策略(纯价格,追涨杀跌)。参数 variety/start_date/end_date。
# 【返回】策略结果 + today_signal。
@app.route("/api/trading/momentum_strat", methods=["POST"])
def api_trading_momentum():
    data = request.json or {}
    result = run_momentum_strategy(
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
    result = run_momentum_adaptive(
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
    result = run_donchian_strategy(
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
    result = run_ma_cross_strategy(
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
    result = run_ma_cross_sent_strategy(
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
    result = run_macd_strategy(
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
    result = run_macd_sent_strategy(
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
    result = run_rsi_strategy(
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
    result = run_rsi_sent_strategy(
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
    result = run_bollinger_strategy(
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
    result = run_bollinger_sent_strategy(
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
    result = run_turtle_strategy(
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
    result = run_turtle_sent_strategy(
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
    result = run_atr_strategy(
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
    result = run_atr_sent_strategy(
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
    result = run_trailing_strategy(
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
    result = run_strategy_comparison(
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
_batch_state = {"running": False, "results": [], "total": 0, "done": 0}


# 【功能】仅用情绪数据做即时方向预测(不跑 Agent 图,速度很快,用于批量对比)。
# 【返回】{"direction": "BULL"|"BEAR"|"HOLD", "score": 最新情绪分} 或 None(无数据)。
# 【关键】取 trade_date 当天或之前最后一期 avg_score: >0.05 判看多,<-0.05 判看空,否则中性。
def _predict_sentiment_only(variety: str, trade_date: str) -> dict:
    """Get instant sentiment-based direction prediction."""
    from signal_analyzer import _load_sentiment

    sent = _load_sentiment(variety)
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
    from signal_analyzer import _load_price as _lp

    price_data = _lp(variety)
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
        include_sentiment = load_sentiment_data(symbol) is not None
        app_graph, _ = build_commodity_graph(
            config, enable_feedback=False, include_sentiment=include_sentiment
        )
        evo_ctx = get_evolution_context(symbol)
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
        for chunk in app_graph.stream(state, stream_mode="updates"):
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
        from signal_analyzer import _load_price as _lp

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
_val_state = {"running": False, "results": [], "total": 0, "done": 0, "date": ""}


# 【功能】对单个品种跑完整 Agent 流水线,同时更新 _val_state["current_stage"] 供前端看进度。
# 【返回】{variety, rating, confidence, score, agent_dir, actual_dir, actual_pct,
#           correct, dir_strength, elapsed} 或 {variety, error}。
# 【关键逻辑】把 RATING 文本映射为方向:含"看多/BULL"→BULL,"看空/BEAR"→BEAR,否则 HOLD;
#   dir_strength = score-5(看多时越大越强)或 5-score(看空时越大越坚定)。
def _validate_one_variety(variety: str, trade_date: str, config: dict) -> dict:
    """Run full Agent pipeline with per-stage progress tracking."""
    global _val_state
    try:
        include_sentiment = load_sentiment_data(variety) is not None
        app_graph, _ = build_commodity_graph(
            config, enable_feedback=False, include_sentiment=include_sentiment
        )
        evo_ctx = get_evolution_context(variety)
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
        for chunk in app_graph.stream(state, stream_mode="updates"):
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
        from signal_analyzer import _load_price as _lpv

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
        from scheduler import start_scheduler

        start_scheduler(schedule_times=["08:00", "18:00"])
        print("Scheduler started: daily at 08:00, 18:00")
    except Exception as e:
        print(f"Scheduler not started: {e}")

    from waitress import serve

    print("FuturesMind Dashboard: http://localhost:5000")
    serve(app, host="0.0.0.0", port=5000, threads=8, channel_timeout=600)
