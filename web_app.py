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
    run_adaptive_sentiment,
    run_contrarian_sentiment,
    run_donchian_strategy,
    run_momentum_adaptive,
    run_momentum_strategy,
    run_simulated_trading,
    run_strategy_comparison,
    run_trailing_strategy,
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

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))
config = DEFAULT_CONFIG.copy()
set_config(config)

# ── Dynamic paths ────────────────────────────────────────────────────────────

# User sentiment dir (real collected data). Falls back to bundled repo samples
# (data/external_data) when no user data exists yet — lets a fresh clone browse.
_USER_SENTIMENT_DIR = Path(os.path.expanduser("~/.tradingagents/external_data"))
_REPO_SENTIMENT_DIR = Path(__file__).parent / "data" / "external_data"
SENTIMENT_DIR = (
    _USER_SENTIMENT_DIR
    if any(_USER_SENTIMENT_DIR.glob("*_sentiment.json"))
    else _REPO_SENTIMENT_DIR
)

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


def get_page_template():
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        return f.read()


# ── Sector mapping (dynamic from VARIETY_METADATA) ────────────────────────────


def _get_sector(code: str) -> str:
    """Get sector name dynamically from VARIETY_METADATA."""
    meta = VARIETY_METADATA.get(code, {})
    return meta.get("sector_cn", "其他")


# ── Progress Tracker (thread-safe, adapted from astock-ref) ────────────────────

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


class ProgressTracker:
    """Thread-safe mutable state container for analysis progress."""

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

    def resume(self) -> bool:
        with self._lock:
            if not self.is_paused or self.stop_requested:
                return False
            self.is_paused = False
            self._pause_event.set()
            return True

    def request_stop(self) -> bool:
        with self._lock:
            if not self.is_running or self.is_complete or self.error or self.stop_requested:
                return False
            self.stop_requested = True
            self.is_paused = False
            self._pause_event.set()
            return True

    def wait_if_paused(self):
        self._pause_event.wait()

    def mark_stage_active(self, stage_id: str):
        with self._lock:
            if self.stop_requested:
                return
            self.current_stage = stage_id

    def mark_stage_done(self, stage_id: str, report=""):
        with self._lock:
            if self.stop_requested:
                return
            if stage_id not in self.completed_stages:
                self.completed_stages.append(stage_id)
            if report:
                self.stage_reports[stage_id] = report[:3000]
            self.current_stage = ""

    def mark_complete(self, final_state: dict, rating=None):
        with self._lock:
            self.final_state = final_state
            self.rating = rating
            self.is_running = False
            self.is_complete = True
            self.is_paused = False
            self.stop_requested = False
            self._pause_event.set()

    def mark_error(self, err: str):
        with self._lock:
            self.error = err
            self.is_running = False
            self.is_paused = False
            self.stop_requested = False
            self._pause_event.set()

    def update_stats(self, llm=0, tool=0, tok_in=0, tok_out=0):
        with self._lock:
            if self.stop_requested:
                return
            self.llm_calls = llm
            self.tool_calls = tool
            self.tokens_in = tok_in
            self.tokens_out = tok_out

    @property
    def elapsed(self) -> float:
        return time.time() - self.start_time

    def stage_status(self, stage_id: str) -> str:
        with self._lock:
            if stage_id in self.completed_stages:
                return "done"
            if stage_id == self.current_stage:
                return "active"
            return "pending"

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


# Global tracker instance (one analysis at a time)
_tracker: ProgressTracker | None = None

# ── Config persistence ──────────────────────────────────────────────────────

CONFIG_PATH = Path(os.path.expanduser("~/.tradingagents/web_config.json"))


def _load_web_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_web_config(cfg: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ── Routes ─────────────────────────────────────────────────────────────────

# ── Live Price ────────────────────────────────────────────────────────────────

PRICE_CACHE_FILE = Path(__file__).parent / "live_prices_cache.json"


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


@app.route("/")
def index():
    resp = make_response(render_template_string(get_page_template()))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/test")
def test_page():
    path = Path(__file__).parent / "test_trading.html"
    return render_template_string(path.read_text(encoding="utf-8"))


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


@app.route("/api/sentiment/<variety>")
def api_sentiment(variety):
    """Get sentiment data for a variety with time range metadata."""
    path = SENTIMENT_DIR / f"{variety}_sentiment.json"
    if not path.exists():
        return jsonify({"error": "No sentiment data"}), 404
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

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


@app.route("/api/run_analysis", methods=["POST"])
def api_run_analysis():
    """Run full analysis with SSE streaming — reports, debate, synthesis, rating."""
    global _tracker
    data = request.json or {}
    symbol = data.get("symbol", "RB").upper()
    trade_date = data.get("date", "2026-07-14")

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

    stages = (
        PIPELINE_STAGES
        if include_sentiment
        else [s for s in PIPELINE_STAGES if s["id"] != "sentiment"]
    )

    _tracker = ProgressTracker(symbol=symbol, trade_date=trade_date, stages=stages)
    _tracker.is_running = True

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

    def run_analysis():
        global _tracker
        try:
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
            _tracker.mark_error(str(e))

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


@app.route("/api/pause", methods=["POST"])
def api_pause():
    global _tracker
    if _tracker and _tracker.pause():
        return jsonify({"status": "paused", "progress": _tracker.to_dict()})
    return jsonify({"status": "not_running"}), 400


@app.route("/api/resume", methods=["POST"])
def api_resume():
    global _tracker
    if _tracker and _tracker.resume():
        return jsonify({"status": "resumed", "progress": _tracker.to_dict()})
    return jsonify({"status": "not_paused"}), 400


@app.route("/api/stop", methods=["POST"])
def api_stop():
    global _tracker
    if _tracker and _tracker.request_stop():
        return jsonify({"status": "stopping", "progress": _tracker.to_dict()})
    return jsonify({"status": "not_running"}), 400


@app.route("/api/progress")
def api_progress():
    """Get current analysis progress."""
    global _tracker
    if _tracker:
        d = _tracker.to_dict()
        d["rating"] = getattr(_tracker, "_final_rating", None)
        return jsonify(d)
    return jsonify({"is_running": False, "is_complete": False})


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


@app.route("/api/report/<path:filename>/md")
def api_report_md(filename):
    """Download a report as Markdown."""
    safe_name = os.path.basename(filename)
    fpath = REPORT_DIR / safe_name
    if not fpath.exists():
        return jsonify({"error": "Not found"}), 404

    return send_file(fpath, mimetype="text/markdown", as_attachment=True, download_name=safe_name)


# ── Config endpoint ───────────────────────────────────────────────────────


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
                    result = subprocess.run(
                        cmd,
                        cwd=str(THINK2_DIR),
                        capture_output=True,
                        text=True,
                        timeout=600,
                    )
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
# ═══════════════════════════════════════════════════════════════════


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


@app.route("/api/db/alerts")
def api_db_alerts():
    limit = request.args.get("limit", 50, type=int)
    unack = request.args.get("unacknowledged", 0, type=int)
    alerts = get_db().get_alerts(limit=limit, unacknowledged_only=bool(unack))
    return jsonify(alerts)


@app.route("/api/db/alerts/<int:alert_id>/ack", methods=["POST"])
def api_ack_alert(alert_id):
    get_db().acknowledge_alert(alert_id)
    return jsonify({"status": "ok"})


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


@app.route("/api/scheduler/stop", methods=["POST"])
def api_scheduler_stop():
    try:
        from scheduler import stop_scheduler

        stop_scheduler()
        return jsonify({"status": "stopped"})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# ── P0: Auth ──────────────────────────────────────────────────────


def _auth_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        token = request.headers.get("X-Auth-Token", "") or request.cookies.get("auth_token", "")
        if not token or token not in _auth_tokens:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)

    return wrapper


_auth_tokens: set[str] = set()


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


@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    token = request.headers.get("X-Auth-Token", "") or request.cookies.get("auth_token", "")
    _auth_tokens.discard(token)
    resp = make_response(jsonify({"status": "ok"}))
    resp.delete_cookie("auth_token")
    return resp


@app.route("/api/auth/status")
def api_auth_status():
    token = request.cookies.get("auth_token", "")
    return jsonify({"authenticated": token in _auth_tokens})


# ═══════════════════════════════════════════════════════════════════
# P1: Analysis endpoints
# ═══════════════════════════════════════════════════════════════════


@app.route("/api/analysis/anomalies/<variety>")
def api_anomalies(variety):
    threshold = request.args.get("threshold", 2.0, type=float)
    result = detect_anomalies(variety, threshold_std=threshold)
    return jsonify({"variety": variety, "anomalies": result, "count": len(result)})


@app.route("/api/analysis/divergence/<variety>")
def api_divergence(variety):
    result = compute_divergence(variety)
    if result is None:
        return jsonify({"error": "No data"}), 404
    return jsonify(result)


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


@app.route("/api/analysis/leadlag/<variety>")
def api_leadlag(variety):
    max_lag = request.args.get("max_lag", 5, type=int)
    result = analyze_lead_lag(variety, max_lag=max_lag)
    if result is None:
        return jsonify({"error": "Insufficient data"}), 404
    return jsonify(result)


@app.route("/api/analysis/authors")
def api_authors():
    limit = request.args.get("limit", 20, type=int)
    return jsonify(get_top_authors(limit=limit))


@app.route("/api/analysis/events")
def api_events():
    variety = request.args.get("variety", "")
    days = request.args.get("days", 7, type=int)
    return jsonify(extract_events(variety=variety, days=days))


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


@app.route("/api/analysis/ranking")
def api_ranking():
    return jsonify(get_all_variety_scores())


@app.route("/api/analysis/crossplatform/<variety>")
def api_crossplatform(variety):
    result = analyze_cross_platform(variety)
    if result is None:
        return jsonify({"error": "No data"}), 404
    return jsonify(result)


# ═══════════════════════════════════════════════════════════════════
# P2: Watchlist
# ═══════════════════════════════════════════════════════════════════


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
# ═══════════════════════════════════════════════════════════════════


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
    )
    # Save trades to DB
    db = get_db()
    for t in result.get("recent_trades", []):
        db.save_trade_signal(t["variety"], t["entry"], 0, t["dir"], 0, horizon)
    return jsonify(result)


@app.route("/api/trading/contrarian", methods=["POST"])
def api_trading_contrarian():
    data = request.json or {}
    result = run_contrarian_sentiment(
        variety=data.get("variety", ""),
        horizon=data.get("horizon", 3),
        trend_window=data.get("trend_window", 5),
    )
    return jsonify(result)


@app.route("/api/trading/adaptive_sentiment", methods=["POST"])
def api_trading_adaptive_sentiment():
    data = request.json or {}
    result = run_adaptive_sentiment(
        variety=data.get("variety", ""),
        horizon=data.get("horizon", 3),
        trend_window=data.get("trend_window", 5),
    )
    return jsonify(result)


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
                r = run_trailing_strategy(variety=variety, signal_threshold=0.2, max_holding=10)
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
                r = run_adaptive_sentiment(variety=variety)
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
                r = run_contrarian_sentiment(variety=variety)
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
                r = run_momentum_adaptive(variety=variety)
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


@app.route("/api/trading/momentum_strat", methods=["POST"])
def api_trading_momentum():
    data = request.json or {}
    result = run_momentum_strategy(variety=data.get("variety", ""))
    return jsonify(result)


@app.route("/api/trading/momentum_adaptive", methods=["POST"])
def api_trading_momentum_adaptive():
    data = request.json or {}
    result = run_momentum_adaptive(
        variety=data.get("variety", ""),
        lookback=data.get("lookback", 5),
        hold=data.get("hold", 3),
        trend_window=data.get("trend_window", 5),
    )
    return jsonify(result)


@app.route("/api/trading/donchian", methods=["POST"])
def api_trading_donchian():
    data = request.json or {}
    result = run_donchian_strategy(variety=data.get("variety", ""))
    return jsonify(result)


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
    )
    return jsonify(result)


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
    )
    return jsonify(result)


@app.route("/api/trading/stats")
def api_trading_stats():
    return jsonify(get_db().get_trade_stats())


@app.route("/api/trading/signals")
def api_trading_signals():
    variety = request.args.get("variety", "")
    limit = request.args.get("limit", 100, type=int)
    return jsonify(get_db().get_trade_signals(variety=variety or None, limit=limit))


# ── Main ──────────────────────────────────────────────────────────────────

# ═══════════════════════════════════════════════════════════════════
# Batch Backtest: Agent vs Sentiment direction accuracy
# ═══════════════════════════════════════════════════════════════════

_batch_state = {"running": False, "results": [], "total": 0, "done": 0}


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

    t = threading.Thread(target=run_batch, daemon=True)
    t.start()

    return jsonify({"status": "started", "total": len(varieties), "run_agent": run_agent})


@app.route("/api/batch_backtest/status")
def api_batch_status():
    return jsonify(_batch_state)


# ═══════════════════════════════════════════════════════════════════
# Agent Validation: full-pipeline direction/score/confidence test
# ═══════════════════════════════════════════════════════════════════

_val_state = {"running": False, "results": [], "total": 0, "done": 0, "date": ""}


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

    t = threading.Thread(target=run_validation, daemon=True)
    t.start()

    return jsonify({"status": "started", "total": len(varieties), "date": trade_date})


@app.route("/api/agent_validation/status")
def api_validation_status():
    return jsonify(_val_state)


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
