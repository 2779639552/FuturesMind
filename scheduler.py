"""FuturesMind Scheduler — Automated collection with APScheduler."""

# =============================================================================
# 【模块角色】
#   scheduler.py 是项目的"定时任务调度中心",基于 APScheduler 的后台调度器
#   (BackgroundScheduler)在后台按预设时间自动执行数据采集与处理流程,让整个
#   系统无需人工操作也能每天自动运转。
#
#   主要任务:
#     1. 每日定时管道(_run_daily_pipeline):每天 08:00 与 18:00 各执行一次,
#        先跑微博采集(batch_collect.py 子进程),再做情感聚合、回测与情感
#        JSON 重新生成。
#     2. 健康检查(_health_check):每 30 分钟一次,检测最近的采集是否有失败,
#        若有则在告警中心写入一条 warning 告警。
#     3. 平台采集(_run_platform_collection):单个平台的采集任务,通过启动
#        batch_collect.py 子进程实现,结果与异常都会写入数据库并生成告警。
#
#   所有任务都通过数据库(get_db())记录状态与告警,便于 Web 前端展示;
#   start_scheduler() 返回的调度器对象由调用方(通常是 web_app/main)持有。
# =============================================================================

import os  # 【调用包】路径/环境变量操作(定位虚拟环境解释器)
import subprocess  # 【调用包】启动子进程(采集/管道脚本)
import sys  # 【调用包】当前解释器路径定位(复用同一虚拟环境)
from contextlib import suppress  # 【调用包】忽略指定异常的上下文管理器(解析条数容错)
from datetime import datetime  # 【调用包】当前时间/日期生成

from apscheduler.schedulers.background import BackgroundScheduler  # 【调用包】APScheduler 后台调度器(守护线程运行)
from apscheduler.triggers.cron import CronTrigger  # 【调用包】cron 表达式触发器(定时/周期任务)

from database import get_db  # 【调用包】数据库访问(记录采集状态与告警)
from path_utils import resolve_think2_dir  # 【调用包】路径集中管理(定位思路2 数据目录)

THINK2_DIR = resolve_think2_dir()  # 【变量】数据工作目录(采集/管道子进程的 cwd)

# ── Global scheduler ──────────────────────────────────────────────

_scheduler: BackgroundScheduler | None = None  # 【变量】全局调度器单例(启动后持有,供停止/重启)


def _run_platform_collection(platform: str, per_kw: int = 15, since_days: int = 7):
    """运行单个平台的采集任务并记录日志。

    【功能】调用 batch_collect.py 子进程采集指定平台(如 weibo)的帖子,
            把任务开始/结束、条数、失败原因写入数据库并视情况生成告警。
    【参数】
        platform : 平台名,如 "weibo"/"zhihu"。
        per_kw   : 每个关键词采集条数上限,默认 15。
        since_days: 采集回溯天数,默认 7 天(只采近 7 天内容)。
    【返回】无。
    【关键逻辑】
            - 先 start_collection 落一条 running 日志,拿到 log_id。
            - 用子进程跑 batch_collect.py(带 --turbo --no-detail 提速)。
            - 从子进程输出里解析 "Total notes:" 得到采集条数。
            - 返回码非 0 且非 "Interrupted" 视为失败,写 error 日志并告警;
              成功但条数 < 10 时告警"低数据量"。
            - 15 分钟超时(subprocess timeout=900)与异常均会记录并告警。
    """
    db = get_db()  # 【调用函数】取当前线程数据库实例(写采集日志/告警)
    log_id = db.start_collection(platform, keywords_count=30)  # 【调用函数】写一条 running 采集日志,返回 log_id 供后续更新

    try:
        since_date = (
            datetime.now().replace(hour=0, minute=0, second=0)
            - __import__("datetime").timedelta(days=since_days)
        ).strftime("%Y-%m-%d")  # 【变量】since_date:回溯起点日期(YYYY-MM-DD,只采近 N 天)

        # 复用当前 Python 解释器所在的虚拟环境,避免依赖系统 Python
        venv_py = os.path.join(os.path.dirname(sys.executable), "python")  # 【变量】venv_py:当前虚拟环境解释器路径(复用环境,避免依赖系统 Python)
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

        result = subprocess.run(  # 【调用函数】启动 batch_collect.py 子进程采集指定平台帖子(15 分钟超时)
            cmd,
            cwd=str(THINK2_DIR) if THINK2_DIR.exists() else ".",
            capture_output=True,
            text=True,
            timeout=900,  # 15 min timeout:采集任务最长运行 15 分钟,超时视为失败
        )

        # 解析子进程输出,从中提取采集到的帖子条数("Total notes: N")
        # Parse output for post count
        output = (result.stdout or "") + (result.stderr or "")  # 【变量】output:子进程输出(合并 stdout+stderr,供解析条数)
        posts_count = 0  # 【变量】posts_count:解析出的采集条数(默认 0)
        for line in output.split("\n"):
            if "Total notes:" in line:
                with suppress(Exception):
                    posts_count = int(line.split("Total notes:")[1].strip().split()[0])

        if result.returncode != 0 and "Interrupted" not in output:  # 【变量】result.returncode:子进程退出码(0=成功);"Interrupted" 视为用户中断不算失败
            db.finish_collection(log_id, posts_count, error=output[-500:])  # 【调用函数】更新采集日志为失败(error 取输出末尾 500 字符)
            db.create_alert(
                "collection_failed",  # 【调用函数】写入"采集失败"告警
                f"{platform} collection failed",
                output[-300:] or "Unknown error",
                severity="error",
            )
        else:
            db.finish_collection(log_id, posts_count)  # 【调用函数】更新采集日志为成功

            # Check if posts count is too low
            if posts_count < 10:
                db.create_alert(
                    "low_data",  # 【调用函数】写入"低数据量"告警(条数 < 10)
                    f"{platform} low posts",
                    f"Only {posts_count} posts collected",
                    severity="warning",
                )

    except subprocess.TimeoutExpired:
        db.finish_collection(log_id, 0, error="Timeout (15 min)")  # 【调用函数】超时:记 0 条并把日志标记为超时
        db.create_alert(
            "collection_timeout",  # 【调用函数】写入"采集超时"告警
            f"{platform} timeout",
            "Collection exceeded 15 minutes",
            severity="error",
        )
    except Exception as e:
        db.finish_collection(log_id, 0, error=str(e)[:500])  # 【调用函数】异常:记 0 条并写错误日志
        db.create_alert("collection_error", f"{platform} error", str(e)[:300], severity="error")  # 【调用函数】写入"采集异常"告警


def _run_daily_pipeline():
    """每日全量处理管道:微博采集 → 聚合 → 回测 → 重新生成情感 JSON。

    【功能】每天定时执行的主流程:先采集微博,再跑情感聚合、权重回测与
            情感 JSON 文件重生成,最后写入完成/失败告警。
    【参数】无。
    【返回】无。
    【关键逻辑】
            - 开始前写 pipeline_started 告警。
            - 微博(weibo)被注释为"fast, stable",是首选采集源。
            - 聚合+回测+生成 通过一段内嵌 Python 脚本(-c)在 THINK2_DIR 子进程
              中执行,依次调用 trend_aggregator.aggregate、backtest_weights.run_all、
              generate_tradingagents_sentiment 生成各品种情感 JSON。
            - 整体 5 分钟超时(300 秒);异常只记告警,不影响调度器继续运行。
    """
    db = get_db()  # 【调用函数】取数据库实例(写管道事件告警)
    db.create_alert(
        "pipeline_started",  # 【调用函数】写入"管道启动"信息告警
        "Daily pipeline started",
        f"Automated pipeline at {datetime.now():%H:%M}",
        severity="info",
    )

    # 先采集微博(速度快、接口稳定,故作为默认来源)
    # Collect from weibo (fast, stable)
    _run_platform_collection("weibo", per_kw=15, since_days=7)

    # Aggregate + backtest + regenerate
    try:
        venv_py = os.path.join(os.path.dirname(sys.executable), "python")  # 【变量】venv_py:虚拟环境解释器路径(用于执行内嵌脚本)
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
        subprocess.run(  # 【调用函数】在数据目录子进程执行聚合+回测+情感 JSON 生成脚本(5 分钟超时)
            [venv_py, "-c", script],
            cwd=str(THINK2_DIR) if THINK2_DIR.exists() else ".",
            capture_output=True,
            text=True,
            timeout=300,
        )
        db.create_alert(
            "pipeline_complete",  # 【调用函数】写入"管道完成"信息告警
            "Daily pipeline complete",
            f"Generated results at {datetime.now():%H:%M}",
            severity="info",
        )
    except Exception as e:
        db.create_alert("pipeline_error", "Pipeline failed", str(e)[:300], severity="error")  # 【调用函数】写入"管道失败"告警


def start_scheduler(schedule_times: list[str] = None):
    """启动后台调度器(每日定时管道 + 30 分钟健康检查)。

    【功能】创建并启动 APScheduler 后台调度器,注册每日管道任务与健康检查任务,
            同时初始化默认管理员账号。
    【参数】schedule_times: 每日执行管道的时间点列表,如 ["08:00","18:00"];
            默认 ["08:00","18:00"]。
    【返回】BackgroundScheduler: 已启动的调度器对象(全局 _scheduler)。
    【关键逻辑】
            - 每个时间点注册一个 CronTrigger 触发的 _run_daily_pipeline 任务。
            - 另注册 _health_check 任务,每 30 分钟(minute="*/30")运行一次。
            - 启动后立即调用 get_db().ensure_default_user() 确保管理员存在。
            - daemon=True:调度器作为守护线程运行,不阻塞主进程退出。

    Start the background scheduler.

    Args:
        schedule_times: List of cron-style times, e.g. ["08:00", "18:00"].
                        Default: ["08:00", "18:00"]
    """
    global _scheduler

    if schedule_times is None:
        schedule_times = ["08:00", "18:00"]  # 【变量】默认执行时间点(早 8 点/晚 6 点各跑一次管道)

    _scheduler = BackgroundScheduler(daemon=True)  # 【调用函数】创建后台调度器(守护线程,不阻塞主进程退出)

    for time_str in schedule_times:
        hour, minute = time_str.split(":")
        _scheduler.add_job(  # 【调用函数】注册每日管道任务(CronTrigger 按时间点触发)
            _run_daily_pipeline,
            CronTrigger(hour=int(hour), minute=int(minute)),
            id=f"daily_{time_str}",
            name=f"Daily pipeline {time_str}",
        )

    _scheduler.start()  # 【调用函数】启动调度器(任务开始按时触发)

    # Also run a health check every 30 minutes
    _scheduler.add_job(  # 【调用函数】注册健康检查任务(每 30 分钟)
        _health_check,
        CronTrigger(minute="*/30"),
        id="health_check",
        name="Health check",
    )

    # Initialize default user
    get_db().ensure_default_user()  # 【调用函数】确保默认管理员账号存在(首次启动自动创建)

    return _scheduler


def _health_check():
    """周期性健康检查——发现采集异常并告警。

    【功能】每 30 分钟检查最近 5 次采集任务,若存在失败记录则写入健康告警。
    【参数】无。
    【返回】无。
    【关键逻辑】取最近 5 条 collection_log,筛选 status=='error' 的条数,
                只要有一处失败就告警,提示用户关注。
    """
    db = get_db()  # 【调用函数】取数据库实例(查最近采集历史)

    # Check if any recent collection failed
    recent = db.get_collection_history(limit=5)  # 【调用函数】取最近 5 次采集日志
    failures = [r for r in recent if r.get("status") == "error"]  # 【变量】failures:最近采集中的失败记录列表
    if failures:
        db.create_alert(
            "health_warning",  # 【调用函数】写入"健康检查"告警
            "Recent collection failures detected",
            f"{len(failures)} failures in last 5 runs",
            severity="warning",
        )


def stop_scheduler():
    """停止后台调度器。

    【功能】关闭全局调度器并置空引用,通常在应用退出/重启时调用。
    【参数】无。
    【返回】无。
    【关键逻辑】shutdown(wait=False) 不等待任务结束,立即返回,避免阻塞退出。
    """
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)  # 【调用函数】关闭调度器(不等待进行中任务,立即返回)
        _scheduler = None
