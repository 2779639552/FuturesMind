"""Tests for the multi-platform daily pipeline in scheduler.py.

The daily pipeline collects from weibo / xueqiu / zhihu / eastmoney_guba / xhs /
douyin in a **bounded thread pool** (2026-09-17 起并行, 默认 3 并发; 此前为串行),
and one platform's failure (e.g. missing cookie) must be logged and skipped
without stopping the rest.

2026-09-15: 平台数 4 → 6(xhs/douyin 此前根本不在采集计划内),且采集参数
按平台分档(PLATFORM_COLLECT_PROFILES):各平台单关键词真实产出上限与风控耐受度
差异极大,原「一律 per_kw=15 + --turbo」既把小红书限到一半,又给抖音最激进档。

2026-09-17: 并发化后**平台调用顺序不再可断言**(完成顺序不稳定),因此:
  - 断言"六个平台全被调用过 + 各自参数正确"(集合语义), 而不是调用顺序;
  - 另加一条 test_pipeline_collects_platforms_in_parallel 用峰值并发证明"真的并行",
    避免把"看起来并行"当成并行(串行代码也能通过集合断言)。

No subprocess is spawned and no database is touched — `get_db`,
`_run_platform_collection` and `subprocess.run` are all patched.
"""

import threading
import time
from unittest.mock import MagicMock, patch

from scheduler import (
    DEFAULT_COLLECT_PROFILE,
    PLATFORM_COLLECT_PROFILES,
    _ChildResult,
    _run_daily_pipeline,
)

from tests.test_batch_collect_channel_health import bc  # noqa: E402  # 【变量】batch_collect 模块别名(词池/预算常量)
DEFAULT_KEYWORDS_DOUYIN = bc.DEFAULT_KEYWORDS_DOUYIN  # 【变量】抖音词池(测试断言轮数上限用)

_PLATFORMS = ("weibo", "xueqiu", "zhihu", "eastmoney_guba", "xhs", "douyin")


def _run(platform_callable, aggregate_returncode=0, aggregate_stdout="Pipeline OK: 47 JSONs"):
    """跑一次 _run_daily_pipeline,返回 (mock_db, mock_platform_collector)。

    【2026-09-17】聚合子进程的 mock **必须显式给 returncode**。
    旧写法 `return_value=MagicMock()` 让 `proc.returncode` 是个 MagicMock 对象,
    于是"聚合失败也照写 pipeline_complete"那个缺陷在测试里永远暴露不出来
    (旧代码根本就没读这个字段 —— 返回值被直接丢弃)。现在管道用 returncode
    判定成败,契约才变得可测:给 0 = 聚合脚本成功。
    【2026-09-24】调度器已统一走 `_capture_run`(09-21 无控制台父进程修复: 子进程
    stdout 重定向到文件再读回, `subprocess.run` 不再直接出现在调用点)。
    patch 锚点同步到 `scheduler._capture_run`, 返回 `_ChildResult(stdout, stderr, rc)`
    —— 与实现同构, 否则 mock 的 proc 不会真写文件、stdout 断言必失。
    """
    mock_db = MagicMock()
    mock_run = MagicMock(side_effect=platform_callable)
    proc = _ChildResult(aggregate_stdout, "", aggregate_returncode)
    with patch("scheduler.get_db", return_value=mock_db), patch(
        "scheduler._run_platform_collection", mock_run
    ), patch("scheduler._capture_run", return_value=proc):
        _run_daily_pipeline()
    return mock_db, mock_run


def test_pipeline_collects_all_six_platforms():
    mock_db, mock_run = _run(lambda platform, **kwargs: None)

    assert mock_run.call_count == len(_PLATFORMS)
    # 【2026-09-17 并行化】不再断言调用顺序(并行完成顺序不稳定), 断言集合语义
    attempted = [c.args[0] for c in mock_run.call_args_list]
    assert sorted(attempted) == sorted(_PLATFORMS)
    for c in mock_run.call_args_list:
        platform = c.args[0]
        per_kw, mode, max_kw = PLATFORM_COLLECT_PROFILES.get(platform, DEFAULT_COLLECT_PROFILE)
        assert c.kwargs.get("per_kw") == per_kw
        assert c.kwargs.get("mode") == mode
        assert c.kwargs.get("max_keywords") == max_kw
        # 回溯窗口统一 30 天(过滤发生在采集之后,不增加耗时)
        assert c.kwargs.get("since_days") == 30

    # pipeline_started + pipeline_complete both logged (aggregate step succeeded)
    alerts = [a.args[0] for a in mock_db.create_alert.call_args_list]
    assert "pipeline_started" in alerts
    assert "pipeline_complete" in alerts


def test_pipeline_collects_platforms_in_parallel():
    """用**峰值并发**证明真的并行 —— 串行代码同样能通过"六个平台都被调用"的断言。

    2026-09-17: 串行时一轮墙钟 = 各平台耗时之和(抖音单独就吃满 12min 预算),
    排在后面的平台被整体拖晚。这里给每个平台 0.25s 并统计同时在跑的数量:
    默认并发 3 ⇒ 峰值应在 [2, 3];若退化成串行(peak==1)或失去上界(peak>3)都会失败。
    """
    state = {"lock": threading.Lock(), "active": 0, "peak": 0}

    def tracked(platform, **kwargs):
        with state["lock"]:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        try:
            time.sleep(0.25)
            return {"platform": platform, "status": "ok", "notes": 1}
        finally:
            with state["lock"]:
                state["active"] -= 1

    _run(tracked)

    assert state["peak"] >= 2, "峰值并发只有 1 ⇒ 采集实际上仍是串行"
    assert state["peak"] <= 3, f"并发度失去上界(会把 Chromium 实例数打爆): peak={state['peak']}"


def test_douyin_uses_safe_mode_and_is_throttled():
    """抖音必须走慢档且减关键词 —— 软限流冷却 45min 不恢复,不能用 turbo 硬撞。

    2026-09-24 全量 25 词试采后回 13 词/轮(全量需 ~37min 超预算截断 + 2 词限流,
    13 词/轮 + 06:00 早轮两段互补覆盖全部词)。上限必须 ≤ 词池。
    """
    _, mock_run = _run(lambda platform, **kwargs: None)

    douyin = [c for c in mock_run.call_args_list if c.args[0] == "douyin"]
    assert len(douyin) == 1
    assert douyin[0].kwargs.get("mode") == "safe", "抖音必须慢档(软限流敏感), 禁 turbo"
    max_kw = douyin[0].kwargs.get("max_keywords")
    assert max_kw, "抖音必须限制每轮关键词数量以降载"
    assert 0 < max_kw <= len(DEFAULT_KEYWORDS_DOUYIN), f"每轮 {max_kw} 词 > 词池"


def test_platform_failure_does_not_stop_pipeline():
    def flaky(platform, **kwargs):
        if platform == "xueqiu":
            raise RuntimeError("cookie expired")

    mock_db, mock_run = _run(flaky)

    # all platforms attempted despite xueqiu raising
    assert mock_run.call_count == len(_PLATFORMS)

    # a collection_error alert was written for the failed platform
    error_calls = [
        c for c in mock_db.create_alert.call_args_list if c.args[0] == "collection_error"
    ]
    assert len(error_calls) == 1
    assert error_calls[0].args[1] == "xueqiu pipeline error"
    assert error_calls[0].kwargs.get("severity") == "error"

    # the pipeline still ran the aggregate + completed alert
    alerts = [a.args[0] for a in mock_db.create_alert.call_args_list]
    assert "pipeline_complete" in alerts


def test_aggregate_failure_logs_pipeline_error():
    # If the aggregate/backtest/generate subprocess fails, the pipeline must log
    # pipeline_error (not pipeline_complete) without crashing the scheduler.
    mock_db = MagicMock()
    with patch("scheduler.get_db", return_value=mock_db), patch(
        "scheduler._run_platform_collection", return_value=None
    ), patch("scheduler.subprocess.run", side_effect=TimeoutError("aggregate slow")):
        _run_daily_pipeline()
    alert_types = [a.args[0] for a in mock_db.create_alert.call_args_list]
    assert "pipeline_error" in alert_types
    assert "pipeline_complete" not in alert_types


# ──────────────────────────────────────────────────────────────────────────
# 2026-09-17: 管道收尾的两处静默失效
# ──────────────────────────────────────────────────────────────────────────


def test_aggregate_nonzero_exit_logs_pipeline_error_not_complete():
    """聚合子进程**退出码非 0** 时必须报 pipeline_error,不得报 complete。

    【锁定的缺陷 2026-09-17】旧实现 `subprocess.run(...)` 的返回值**没有赋值**,
    聚合/回测/生成脚本哪怕报错,也照样写下 `Daily pipeline complete`(severity=info)
    —— 用户看到"管道完成",而产物其实没更新。这与 `start_collection` 的 lastrowid
    坑同属"静默失效":告警说成功、东西没做。
    """
    mock_db, _ = _run(lambda platform, **kwargs: None, aggregate_returncode=1,
                      aggregate_stdout="")

    alert_types = [a.args[0] for a in mock_db.create_alert.call_args_list]
    assert "pipeline_error" in alert_types
    assert "pipeline_complete" not in alert_types
    err = [a for a in mock_db.create_alert.call_args_list if a.args[0] == "pipeline_error"][0]
    assert "exit=1" in err.args[2], err.args[2]


def test_pipeline_complete_reports_per_platform_outcome():
    """管道完成告警必须带上"几个平台成功 / 哪几个异常"。

    旧实现只写 "Generated results at HH:MM" —— 六个平台里即便有平台被 blocked,
    用户从告警里也完全看不出来(实测 09-17 那轮 xhs=blocked,而 complete 只说成功)。
    """
    def mixed(platform, **kwargs):
        if platform == "xhs":
            return {"platform": "xhs", "status": "blocked", "notes": 0}
        if platform == "zhihu":
            return {"platform": "zhihu", "status": "empty", "notes": 0}
        return {"platform": platform, "status": "ok", "notes": 12}

    mock_db, _ = _run(mixed)

    done = [a for a in mock_db.create_alert.call_args_list if a.args[0] == "pipeline_complete"]
    assert len(done) == 1
    msg = done[0].args[2]
    assert "4/6" in msg, msg                       # ok 的平台有 4 个
    assert "xhs=blocked" in msg, msg               # 失败平台必须点名
    assert "zhihu=empty" in msg, msg               # empty 不等于 ok,要能区分
    assert "Pipeline OK: 47 JSONs" in msg, msg     # 产物凭据(聚合脚本最后一行)


def test_pipeline_complete_takes_aggregate_stdout_tail_as_artifact_proof():
    """产物摘要取聚合脚本 stdout 的**最后一行**,证明 JSON 确实生成了。"""
    mock_db, _ = _run(lambda platform, **kwargs: {"status": "ok", "notes": 3},
                      aggregate_stdout="noise\nPipeline OK: 51 JSONs\n")

    done = [a for a in mock_db.create_alert.call_args_list if a.args[0] == "pipeline_complete"][0]
    assert "Pipeline OK: 51 JSONs" in done.args[2]
    assert "6/6" in done.args[2], done.args[2]


def test_pipeline_collects_platform_results_even_when_collector_raises():
    """采集器直接抛错时,该平台也要被记进汇总(否则"失败"会从报告里消失)。"""
    def boom(platform, **kwargs):
        if platform == "weibo":
            raise RuntimeError("collector exploded")
        return {"platform": platform, "status": "ok", "notes": 5}

    mock_db, _ = _run(boom)

    done = [a for a in mock_db.create_alert.call_args_list if a.args[0] == "pipeline_complete"][0]
    assert "5/6" in done.args[2], done.args[2]
    assert "weibo=error" in done.args[2], done.args[2]
