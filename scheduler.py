"""FuturesMind Scheduler — Automated collection with APScheduler."""

import os
import subprocess
import sys
from contextlib import suppress
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from database import get_db
from path_utils import resolve_think2_dir

THINK2_DIR = resolve_think2_dir()

# ── Global scheduler ──────────────────────────────────────────────

_scheduler: BackgroundScheduler | None = None


def _run_platform_collection(platform: str, per_kw: int = 15, since_days: int = 7):
    """Run collection for one platform and log to DB."""
    db = get_db()
    log_id = db.start_collection(platform, keywords_count=30)

    try:
        since_date = (
            datetime.now().replace(hour=0, minute=0, second=0)
            - __import__("datetime").timedelta(days=since_days)
        ).strftime("%Y-%m-%d")

        venv_py = os.path.join(os.path.dirname(sys.executable), "python")
        cmd = [
            venv_py,
            "batch_collect.py",
            "--platform",
            platform,
            "--per-kw",
            str(per_kw),
            "--turbo",
            "--no-detail",
            "--since",
            since_date,
        ]

        result = subprocess.run(
            cmd,
            cwd=str(THINK2_DIR) if THINK2_DIR.exists() else ".",
            capture_output=True,
            text=True,
            timeout=900,  # 15 min timeout
        )

        # Parse output for post count
        output = (result.stdout or "") + (result.stderr or "")
        posts_count = 0
        for line in output.split("\n"):
            if "Total notes:" in line:
                with suppress(Exception):
                    posts_count = int(line.split("Total notes:")[1].strip().split()[0])

        if result.returncode != 0 and "Interrupted" not in output:
            db.finish_collection(log_id, posts_count, error=output[-500:])
            db.create_alert(
                "collection_failed",
                f"{platform} collection failed",
                output[-300:] or "Unknown error",
                severity="error",
            )
        else:
            db.finish_collection(log_id, posts_count)

            # Check if posts count is too low
            if posts_count < 10:
                db.create_alert(
                    "low_data",
                    f"{platform} low posts",
                    f"Only {posts_count} posts collected",
                    severity="warning",
                )

    except subprocess.TimeoutExpired:
        db.finish_collection(log_id, 0, error="Timeout (15 min)")
        db.create_alert(
            "collection_timeout",
            f"{platform} timeout",
            "Collection exceeded 15 minutes",
            severity="error",
        )
    except Exception as e:
        db.finish_collection(log_id, 0, error=str(e)[:500])
        db.create_alert("collection_error", f"{platform} error", str(e)[:300], severity="error")


def _run_daily_pipeline():
    """Full daily pipeline: weibo + zhihu → aggregate → backtest → regenerate."""
    db = get_db()
    db.create_alert(
        "pipeline_started",
        "Daily pipeline started",
        f"Automated pipeline at {datetime.now():%H:%M}",
        severity="info",
    )

    # Collect from weibo (fast, stable)
    _run_platform_collection("weibo", per_kw=15, since_days=7)

    # Aggregate + backtest + regenerate
    try:
        venv_py = os.path.join(os.path.dirname(sys.executable), "python")
        script = """
import json, sys, glob
from pathlib import Path
OUTPUT = Path('.') / 'output'
TRENDS = OUTPUT / 'trends'
sys.path.insert(0, '.')
from trend_aggregator import aggregate
paths = sorted(glob.glob(str(OUTPUT / 'batch_*.jsonl')))
result = aggregate(paths)
from backtest_weights import run_all
result_b = run_all(min_points=10, horizons=[1, 3, 5])
from generate_tradingagents_sentiment import load_trends_data, generate_sentiment_json, OUTPUT_DIR as GEN_OUTPUT
varieties, index, global_weights = load_trends_data(TRENDS)
GEN_OUTPUT.mkdir(parents=True, exist_ok=True)
gen_count = 0
for vname in sorted(varieties.keys()):
    output = generate_sentiment_json(vname, varieties[vname], index, global_weights)
    if output is None or output['data']['social_sentiment']['total_posts_analyzed'] < 2: continue
    with open(GEN_OUTPUT / f'{output[\"variety\"]}_sentiment.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    gen_count += 1
print(f'Pipeline OK: {gen_count} JSONs')
        """
        subprocess.run(
            [venv_py, "-c", script],
            cwd=str(THINK2_DIR) if THINK2_DIR.exists() else ".",
            capture_output=True,
            text=True,
            timeout=300,
        )
        db.create_alert(
            "pipeline_complete",
            "Daily pipeline complete",
            f"Generated results at {datetime.now():%H:%M}",
            severity="info",
        )
    except Exception as e:
        db.create_alert("pipeline_error", "Pipeline failed", str(e)[:300], severity="error")


def start_scheduler(schedule_times: list[str] = None):
    """Start the background scheduler.

    Args:
        schedule_times: List of cron-style times, e.g. ["08:00", "18:00"].
                        Default: ["08:00", "18:00"]
    """
    global _scheduler

    if schedule_times is None:
        schedule_times = ["08:00", "18:00"]

    _scheduler = BackgroundScheduler(daemon=True)

    for time_str in schedule_times:
        hour, minute = time_str.split(":")
        _scheduler.add_job(
            _run_daily_pipeline,
            CronTrigger(hour=int(hour), minute=int(minute)),
            id=f"daily_{time_str}",
            name=f"Daily pipeline {time_str}",
        )

    _scheduler.start()

    # Also run a health check every 30 minutes
    _scheduler.add_job(
        _health_check,
        CronTrigger(minute="*/30"),
        id="health_check",
        name="Health check",
    )

    # Initialize default user
    get_db().ensure_default_user()

    return _scheduler


def _health_check():
    """Periodic health check — detect anomalies."""
    db = get_db()

    # Check if any recent collection failed
    recent = db.get_collection_history(limit=5)
    failures = [r for r in recent if r.get("status") == "error"]
    if failures:
        db.create_alert(
            "health_warning",
            "Recent collection failures detected",
            f"{len(failures)} failures in last 5 runs",
            severity="warning",
        )


def stop_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
