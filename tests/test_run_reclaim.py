# -*- coding: utf-8 -*-
"""僵尸运行回收的护栏（2026-10-23）。

【要解决什么】`_active_runs` 原先只看 is_running / is_complete / error 三个标记。
一旦分析线程卡在 LLM 调用里（timeout=300 × max_retries=2，最坏 900s）或者收尾路径
被异常跳过，is_running 就永远是 True ⇒ **名额被永久占着**，后来的人一律 429。
这才是并发上限只能设 3 的真正原因：不是机器扛不住，是"死一个就少一个坑"。

【三级判据（本文件逐条钉住）】
  ① 线程已死但状态仍 running   → 立即回收（确定）
  ② 运行超过 1 小时硬上限      → 回收（确定）
  ③ 连续 15 分钟无任何进展     → 先标 stalled（**仍占名额**），再等 5 分钟才强制回收

【为什么第 ③ 条要分两步】`update_stats` 是长节点内部唯一的心跳源。极端慢的单次调用
可能十分钟没有心跳。一检测到就释放名额，会把正常分析误杀成"失败"——先观察一轮，
比误杀便宜。
"""
from __future__ import annotations

import time

import pytest

import web_app
from web_app import ProgressTracker


class _DeadThread:
    """线程已退出。"""

    def is_alive(self) -> bool:  # noqa: D102
        return False


class _AliveThread:
    """线程还活着（可能卡住，也可能只是慢）。"""

    def is_alive(self) -> bool:  # noqa: D102
        return True


def _mk(symbol: str = "TA", *, thread=None, idle: float = 0.0, elapsed: float = 0.0) -> ProgressTracker:
    t = ProgressTracker(symbol=symbol, trade_date="2026-10-23", stages=[])
    t.is_running = True
    t._stage_reports = {}
    t._final_rating = None
    now = time.time()
    t.start_time = now - elapsed
    t.last_progress_at = now - idle
    t._thread = thread
    return t


@pytest.fixture(autouse=True)
def _clean_registry():
    with web_app._RUNS_LOCK:
        web_app._RUNS.clear()
        web_app._LATEST_RUN_ID = None
    yield
    with web_app._RUNS_LOCK:
        web_app._RUNS.clear()
        web_app._LATEST_RUN_ID = None


# ---------------------------------------------------------------------------
# 1. 判据 ①：线程已死但状态还是 running
# ---------------------------------------------------------------------------
class TestDeadThread:
    def test_reclaims_immediately(self):
        rid = web_app._register_run(_mk("TA", thread=_DeadThread()))
        actions = web_app._reclaim_stale_runs()

        t = web_app._resolve_tracker(rid)
        assert t.error, "死线程必须被标记错误，否则名额永远不释放"
        assert t.is_running is False
        assert t.stop_requested is True, "要请求线程退出，防止它回来继续写状态"
        assert actions and actions[0]["action"] == "dead_thread"

    def test_no_longer_counted_as_active(self):
        web_app._register_run(_mk("TA", thread=_DeadThread()))
        assert web_app._active_runs() == [], "回收后不能再算作在跑"

    def test_alive_thread_is_not_reclaimed(self):
        """线程还活着、刚有进展 —— 绝不能动。"""
        rid = web_app._register_run(_mk("TA", thread=_AliveThread(), idle=10, elapsed=60))
        web_app._reclaim_stale_runs()
        t = web_app._resolve_tracker(rid)
        assert not t.error, "正常跑着的分析被误杀"
        assert t.is_running is True


# ---------------------------------------------------------------------------
# 2. 判据 ②：硬上限
# ---------------------------------------------------------------------------
class TestMaxDuration:
    def test_reclaims_beyond_hard_cap(self, monkeypatch):
        monkeypatch.setattr(web_app, "MAX_RUN_SECONDS", 3600)
        rid = web_app._register_run(
            _mk("TA", thread=_AliveThread(), idle=5, elapsed=3601)
        )
        web_app._reclaim_stale_runs()
        t = web_app._resolve_tracker(rid)
        assert t.error and "硬上限" in t.error, t.error

    def test_does_not_touch_under_hard_cap(self, monkeypatch):
        monkeypatch.setattr(web_app, "MAX_RUN_SECONDS", 3600)
        rid = web_app._register_run(
            _mk("TA", thread=_AliveThread(), idle=5, elapsed=3599)
        )
        web_app._reclaim_stale_runs()
        assert not web_app._resolve_tracker(rid).error


# ---------------------------------------------------------------------------
# 3. 判据 ③：无进展（两段式，不能一步到底）
# ---------------------------------------------------------------------------
class TestStaleHeartbeat:
    def test_below_threshold_untouched(self, monkeypatch):
        monkeypatch.setattr(web_app, "STALE_HEARTBEAT_SECONDS", 900)
        rid = web_app._register_run(_mk("TA", thread=_AliveThread(), idle=899, elapsed=1000))
        web_app._reclaim_stale_runs()
        t = web_app._resolve_tracker(rid)
        assert not t.error and t.is_running, "14 分钟无进展还可能是慢调用，不能杀"

    def test_first_detection_only_marks_stalled(self, monkeypatch):
        """★ 关键：第一次只标 stalled，**名额仍然占着**。"""
        monkeypatch.setattr(web_app, "STALE_HEARTBEAT_SECONDS", 900)
        rid = web_app._register_run(_mk("TA", thread=_AliveThread(), idle=901, elapsed=1000))

        actions = web_app._reclaim_stale_runs()
        t = web_app._resolve_tracker(rid)
        assert t.stalled_at is not None, "应记录卡死起点"
        assert not t.error, "第一次观察不能直接判死"
        assert t.is_running is True
        assert any(a["action"] == "stall_detected" for a in actions)
        assert [r for r, _ in web_app._active_runs()] == [rid], "stalled 仍应占名额"

    def test_force_after_grace(self, monkeypatch):
        monkeypatch.setattr(web_app, "STALE_HEARTBEAT_SECONDS", 900)
        monkeypatch.setattr(web_app, "STALE_FORCE_SECONDS", 300)
        rid = web_app._register_run(_mk("TA", thread=_AliveThread(), idle=901, elapsed=1000))
        web_app._reclaim_stale_runs()  # 第一次：标 stalled

        # 把 stalled_at 拨回 301 秒前，模拟"宽限期已过"
        t = web_app._resolve_tracker(rid)
        t.stalled_at = time.time() - 301
        web_app._reclaim_stale_runs()

        assert t.error and "卡死" in t.error, t.error
        assert web_app._active_runs() == []

    def test_progress_resets_stall(self, monkeypatch):
        """只要有新进展，stalled 标记必须清掉（否则会被"累计"误杀）。"""
        monkeypatch.setattr(web_app, "STALE_HEARTBEAT_SECONDS", 900)
        rid = web_app._register_run(_mk("TA", thread=_AliveThread(), idle=901, elapsed=1000))
        web_app._reclaim_stale_runs()
        t = web_app._resolve_tracker(rid)
        assert t.stalled_at is not None

        t.update_stats(llm=t.llm_calls + 1)  # 心跳
        assert t.stalled_at is None, "有新进展后必须清除卡死标记"
        assert time.time() - t.last_progress_at < 1


# ---------------------------------------------------------------------------
# 4. 已完成的运行不受影响 + 不能被"复活"
# ---------------------------------------------------------------------------
class TestCompletedRuns:
    def test_completed_run_ignored(self):
        t = _mk("TA", thread=_DeadThread(), elapsed=9999)
        t.mark_complete({}, rating=None)
        rid = web_app._register_run(t)
        web_app._reclaim_stale_runs()
        assert web_app._resolve_tracker(rid).error is None

    def test_mark_complete_cannot_overwrite_error(self):
        """被回收的线程如果后来从 LLM 调用里回来了，不许把 error 洗成 complete。"""
        t = _mk("TA", thread=_DeadThread())
        t.reclaim("卡死回收")
        t.mark_complete({"final_trade_decision": "x"})
        assert t.error == "卡死回收", "回收结论被收尾覆盖了 —— 用户会看到'成功'的半截结果"
        assert t.is_complete is False


# ---------------------------------------------------------------------------
# 5. 端到端：回收后名额真的腾出来了
# ---------------------------------------------------------------------------
class TestCapacityFreed:
    @pytest.fixture(autouse=True)
    def _no_real_thread(self, monkeypatch):
        """禁止真的起分析线程，否则这条测试会跑一次完整分析、烧 LLM 调用。"""

        class _NoThread:
            def __init__(self, *a, **k):
                pass

            def start(self):
                pass

        monkeypatch.setattr(web_app.threading, "Thread", _NoThread)

    def test_new_request_allowed_after_reclaim(self, monkeypatch):
        monkeypatch.setattr(web_app, "MAX_PARALLEL_ANALYSES", 1)
        # 占满：一个死线程的僵尸运行
        web_app._register_run(_mk("TA", thread=_DeadThread()))

        c = web_app.app.test_client()
        r0 = c.get("/api/progress")
        assert r0.status_code == 200

        r = c.post("/api/run_analysis", json={"symbol": "RB", "date": "2026-10-23"})
        assert r.status_code == 200, (
            f"僵尸占满名额后新请求被拒（{r.status_code}），说明回收没在容量判断前跑"
        )

    def test_429_reports_stalled_stage(self, monkeypatch):
        """卡住的分析在 429 里要看得出来是在跑还是卡住，不能只报时长。"""
        monkeypatch.setattr(web_app, "MAX_PARALLEL_ANALYSES", 1)
        t = _mk("TA", thread=_AliveThread(), idle=901, elapsed=1000)
        t.current_stage = "technical_analyst"
        web_app._register_run(t)

        c = web_app.app.test_client()
        r = c.post("/api/run_analysis", json={"symbol": "RB", "date": "2026-10-23"})
        assert r.status_code == 429
        active = r.get_json()["active"]
        assert active[0]["current_stage"] == "technical_analyst"
