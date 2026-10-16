"""六平台采集并行化的回归测试 (2026-09-17)。

被锁定的真实缺陷
----------------
1) 【串行】`_run_daily_pipeline` 与网页"一键更新"原先都是 `for 平台: subprocess.run`
   串行执行, 一轮墙钟 = 各平台耗时之和。实测抖音单独就吃满 12min 预算、雪球 safe
   档更长, 排在后面的平台被整体拖晚, 前面的平台一超时就直接吃掉后面的时间。
   修复: 抽出 collect_pool, 两条链路共用同一套"有界并行 + 顺序还原 + 异常隔离"。

2) 【不能无界】每个采集子进程内部各起一个 Chromium(Playwright 平台), 无界并发
   会把内存打满, 反而让每个平台都变慢甚至超时 ⇒ 默认 3、硬上限 6。

3) 【顺序必须稳定】并行完成顺序不确定, 若直接把 futures 的完成顺序当结果顺序,
   告警文案与前端日志会随机抖动, 事后无法与历史记录比对 ⇒ 必须按调用方给定顺序返回。

测试手法: 不启动任何子进程、不联网、不碰数据库 —— worker 全是本地 sleep/计数。
"""

import threading
import time

from collect_pool import (
    DEFAULT_CONCURRENCY,
    ENV_VAR,
    MAX_CONCURRENCY,
    resolve_concurrency,
    run_platforms,
)

_PLATFORMS = ["weibo", "xueqiu", "zhihu", "eastmoney_guba", "xhs", "douyin"]


# ──────────────────────────────────────────────────────────────────────────
# 并发度解析: 手滑的环境变量既不能变成 0 并发, 也不能把机器打爆
# ──────────────────────────────────────────────────────────────────────────


def test_concurrency_defaults_to_three_without_env():
    assert resolve_concurrency(6, env={}) == DEFAULT_CONCURRENCY == 3


def test_concurrency_reads_valid_env_value():
    assert resolve_concurrency(6, env={ENV_VAR: "2"}) == 2


def test_concurrency_falls_back_on_garbage_env():
    for bad in ("abc", "", "  ", "2.5", "None"):
        assert resolve_concurrency(6, env={ENV_VAR: bad}) == DEFAULT_CONCURRENCY, bad


def test_concurrency_falls_back_when_below_one():
    """0/负数不是"串行"的同义词: 0 并发会让采集什么也不做, 属于配置事故。"""
    for bad in ("0", "-3"):
        assert resolve_concurrency(6, env={ENV_VAR: bad}) == DEFAULT_CONCURRENCY, bad


def test_concurrency_is_clamped_to_hard_cap():
    """意图明确要"更多"时钳到上限, 而不是回退默认。"""
    assert resolve_concurrency(6, env={ENV_VAR: "99"}) == MAX_CONCURRENCY == 6


def test_concurrency_never_exceeds_platform_count():
    assert resolve_concurrency(2, env={ENV_VAR: "6"}) == 2
    assert resolve_concurrency(0, env={ENV_VAR: "6"}) == 1


# ──────────────────────────────────────────────────────────────────────────
# 并行执行: 真的并行 / 有界 / 顺序稳定 / 异常隔离
# ──────────────────────────────────────────────────────────────────────────


def _tracking_worker(delay, state):
    """返回一个 worker: sleep + 记录"同时在跑"的峰值。"""

    def _work(platform):
        with state["lock"]:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        try:
            time.sleep(delay)
            return f"{platform}-ok"
        finally:
            with state["lock"]:
                state["active"] -= 1

    return _work


def test_run_platforms_is_actually_parallel():
    """6 平台 × 0.25s, 并发 3 ⇒ 墙钟应≈2 波(0.5s), 串行会是 1.5s。"""
    state = {"lock": threading.Lock(), "active": 0, "peak": 0}
    started = time.monotonic()
    results, errors = run_platforms(_PLATFORMS, _tracking_worker(0.25, state), concurrency=3)
    elapsed = time.monotonic() - started

    assert not errors and len(results) == len(_PLATFORMS)
    assert state["peak"] >= 2, "峰值并发只有 1 ⇒ 实际上还是串行"
    assert state["peak"] <= 3, f"并发度超界: peak={state['peak']}"
    # 串行需要 1.5s; 给足余量, 只要求明显快于串行
    assert elapsed < 1.5 * 0.75, f"没有加速: elapsed={elapsed:.2f}s"


def test_run_platforms_concurrency_one_is_serial():
    """并发度 1 必须真的串行:同样工作量,串行耗时应显著长于并行。

    【2026-09-30 修 flaky】原断言是 `elapsed >= 0.9`,而 6 平台 × 0.15s 的**理论值
      恰好就是 0.9s** —— 零余量。Windows 的 time.sleep 精度约 15.6ms,加上线程调度
      与计时抖动,实测在 0.89~0.91 之间来回跳 ⇒ 即使无人干扰也会随机变红(实测单跑
      5 次:2 绿 3 红)。这种"必然 flaky"的断言会让每次回归结论都不可信。
    【改法】换成**相对判据**:同一批工作分别用并发 1 与并发 6 跑一遍,要求串行耗时
      至少是并行的 3 倍(实测比值约 6,余量 2 倍)。既不依赖绝对时钟精度,又完整保留
      原意图 —— "并发 1 若退化成并行",两次耗时接近,比值≈1 ⇒ 照样红。
    """
    state = {"lock": threading.Lock(), "active": 0, "peak": 0}
    started = time.monotonic()
    run_platforms(_PLATFORMS, _tracking_worker(0.15, state), concurrency=1)
    serial = time.monotonic() - started
    assert state["peak"] == 1

    state2 = {"lock": threading.Lock(), "active": 0, "peak": 0}
    started = time.monotonic()
    run_platforms(_PLATFORMS, _tracking_worker(0.15, state2), concurrency=6)
    parallel = time.monotonic() - started

    assert serial >= parallel * 3, (
        f"并发度 1 未比并发 6 明显慢(可能退化成并行): "
        f"serial={serial:.2f}s parallel={parallel:.2f}s ratio={serial / max(parallel, 1e-6):.1f}"
    )


def test_run_platforms_preserves_requested_order():
    """完成顺序与请求顺序相反时, 结果仍必须按请求顺序排列。"""
    delays = {"weibo": 0.30, "xueqiu": 0.24, "zhihu": 0.18,
              "eastmoney_guba": 0.12, "xhs": 0.06, "douyin": 0.01}

    def worker(platform):
        time.sleep(delays[platform])
        return platform.upper()

    results, errors = run_platforms(_PLATFORMS, worker, concurrency=6)

    assert not errors
    assert list(results) == _PLATFORMS, "结果顺序被完成顺序影响了(告警/日志会随机抖动)"
    assert results["douyin"] == "DOUYIN"


def test_run_platforms_isolates_single_platform_failure():
    """单平台抛错只记 errors, 其余平台照跑 —— 某平台 Cookie 失效是常态。"""

    def worker(platform):
        if platform == "xhs":
            raise RuntimeError("cookie expired")
        return platform

    results, errors = run_platforms(_PLATFORMS, worker, concurrency=3)

    assert set(results) == set(_PLATFORMS) - {"xhs"}
    assert list(errors) == ["xhs"]
    assert "cookie expired" in errors["xhs"]


def test_run_platforms_handles_empty_platform_list():
    results, errors = run_platforms([], lambda p: p, concurrency=3)
    assert results == {} and errors == {}


# ──────────────────────────────────────────────────────────────────────────
# 定时管道侧: _collect_concurrency 与 collect_pool 同源
# ──────────────────────────────────────────────────────────────────────────


def test_scheduler_concurrency_delegates_to_collect_pool(monkeypatch):
    from scheduler import _collect_concurrency

    monkeypatch.delenv(ENV_VAR, raising=False)
    assert _collect_concurrency(6) == DEFAULT_CONCURRENCY

    monkeypatch.setenv(ENV_VAR, "4")
    assert _collect_concurrency(6) == 4

    monkeypatch.setenv(ENV_VAR, "0")
    assert _collect_concurrency(6) == DEFAULT_CONCURRENCY
