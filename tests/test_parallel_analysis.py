# -*- coding: utf-8 -*-
"""分析并行的护栏（2026-10-23）。

【为什么改】原来 `_tracker` 是进程级单例，内网第二个人点「开始分析」会直接接管
第一个人的进度页 —— 两人都在跑、结果互相覆盖、LLM 调用翻倍。

现在：
  · 每次分析有自己的 tracker，`run_id` 标识；
  · 接口用 `?run=` 定位，**不带 run 时回退到最近一次**（旧页面/旧脚本不受影响）；
  · 最多 `MAX_PARALLEL_ANALYSES` 个并发，超过返回 429 并列出在跑哪几个。

【本文件守什么】
  1. 注册表能正确区分"在跑"与"已完成"
  2. 超过上限返回 429，且 429 里带得动"谁在跑、跑了多久"
  3. 不同 run 的进度互不串（这是并行的核心）
  4. 不带 run 参数仍能读到最近一次（向后兼容，旧页面不能崩）
  5. 已完成运行会被淘汰，不会无限涨内存
"""
from __future__ import annotations

import threading
import time

import pytest

import web_app
from web_app import ProgressTracker


def _mk(symbol: str = "TA", running: bool = True) -> ProgressTracker:
    t = ProgressTracker(symbol=symbol, trade_date="2026-10-23", stages=[])
    t.is_running = running
    t._stage_reports = {}
    t._final_rating = None
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
# 1. 注册表
# ---------------------------------------------------------------------------
class TestRegistry:
    def test_register_returns_unique_ids(self):
        a = web_app._register_run(_mk("TA"))
        b = web_app._register_run(_mk("RB"))
        assert a and b and a != b, "run_id 必须唯一"
        assert web_app._LATEST_RUN_ID == b, "最近一次应指向后注册的"

    def test_resolve_by_id_and_fallback(self):
        a = web_app._register_run(_mk("TA"))
        b = web_app._register_run(_mk("RB"))
        assert web_app._resolve_tracker(a).symbol == "TA"
        assert web_app._resolve_tracker(b).symbol == "RB"
        assert web_app._resolve_tracker(None).symbol == "RB", "不带 run 应回退到最近一次"
        assert web_app._resolve_tracker("nope") is None, "未知 id 必须返回 None 而不是最近一次"

    def test_active_only_counts_running(self):
        a = web_app._register_run(_mk("TA", running=True))
        b = web_app._register_run(_mk("RB", running=True))
        c = web_app._register_run(_mk("CF", running=False))
        web_app._resolve_tracker(c).is_complete = True
        active = {rid for rid, _ in web_app._active_runs()}
        assert active == {a, b}, "已完成的运行不该算进并发数"

    def test_finished_runs_evicted(self):
        """保留最近 _KEEP_FINISHED_RUNS 个已完成，其余淘汰。"""
        ids = []
        for i in range(web_app._KEEP_FINISHED_RUNS + 4):
            t = _mk("V%d" % i, running=False)
            t.is_complete = True
            ids.append(web_app._register_run(t))
        with web_app._RUNS_LOCK:
            alive = set(web_app._RUNS)
        assert len(alive) == web_app._KEEP_FINISHED_RUNS, "已完成运行没有被淘汰，内存会一直涨"
        assert ids[-1] in alive, "最新的必须保留"
        assert ids[0] not in alive, "最旧的应被淘汰"


# ---------------------------------------------------------------------------
# 2. 并发上限
# ---------------------------------------------------------------------------
class TestCapacity:
    def _post(self):
        c = web_app.app.test_client()
        return c.post("/api/run_analysis", json={"symbol": "TA", "date": "2026-10-23"})

    @pytest.fixture(autouse=True)
    def _no_real_thread(self, monkeypatch):
        """禁止真的起分析线程 —— 否则这条测试会实际跑一次完整分析、烧 LLM 调用。

        【怎么发现的】第一版没禁，测试 1.37 秒就"通过"了：后台线程是 daemon，
          pytest 退出时被一起杀掉，看起来一切正常，实际可能已经发出了 LLM 请求。
        """
        class _NoThread:
            def __init__(self, *a, **k):
                pass

            def start(self):
                pass

        monkeypatch.setattr(web_app.threading, "Thread", _NoThread)

    def test_rejects_when_full(self, monkeypatch):
        """塞满上限后，再请求必须 429，且要带上"谁在跑"。"""
        monkeypatch.setattr(web_app, "MAX_PARALLEL_ANALYSES", 2)
        # 直接放置 2 个"在跑"的 tracker
        for _ in range(2):
            web_app._register_run(_mk("TA", running=True))

        r = self._post()
        assert r.status_code == 429, r.status_code
        body = r.get_json()
        assert body["error"] == "busy"
        assert "上限 2" in body["message"] or "已有 2 个" in body["message"], body["message"]
        assert len(body["active"]) == 2, "应列出正在跑的分析"
        assert {"run", "symbol", "elapsed", "current_stage"} <= set(body["active"][0])

    def test_allows_below_limit(self, monkeypatch):
        monkeypatch.setattr(web_app, "MAX_PARALLEL_ANALYSES", 3)
        web_app._register_run(_mk("TA", running=True))
        r = self._post()
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body.get("run"), "启动成功必须返回 run_id"
        # 新登记的那次应可在注册表里查到
        assert web_app._resolve_tracker(body["run"]) is not None


# ---------------------------------------------------------------------------
# 3. 接口隔离与向后兼容
# ---------------------------------------------------------------------------
class TestEndpoints:
    def test_progress_is_per_run(self):
        a = web_app._register_run(_mk("TA"))
        b = web_app._register_run(_mk("RB"))
        web_app._resolve_tracker(a).mark_stage_done("technical")
        web_app._resolve_tracker(b).mark_stage_done("fundamental")

        c = web_app.app.test_client()
        da = c.get("/api/progress?run=" + a).get_json()
        db = c.get("/api/progress?run=" + b).get_json()
        assert da["symbol"] == "TA" and db["symbol"] == "RB"
        assert "technical" in da["completed_stages"]
        assert "technical" not in db["completed_stages"], "两个运行的状态串了"
        assert da["run"] == a and db["run"] == b

    def test_progress_without_run_falls_back_to_latest(self):
        web_app._register_run(_mk("TA"))
        b = web_app._register_run(_mk("RB"))
        d = web_app.app.test_client().get("/api/progress").get_json()
        assert d["symbol"] == "RB" and d["run"] == b, "旧页面（不带 run）必须仍能用"

    def test_results_are_per_run(self):
        a = web_app._register_run(_mk("TA", running=False))
        b = web_app._register_run(_mk("RB", running=False))
        ta = web_app._resolve_tracker(a)
        ta.is_complete = True
        ta._stage_reports = {"synthesis": "RATING: 偏多 | CONFIDENCE: 中 | SCORE: 7"}
        tb = web_app._resolve_tracker(b)
        tb.is_complete = True
        tb._stage_reports = {"synthesis": "RATING: 偏空 | CONFIDENCE: 高 | SCORE: 3"}

        c = web_app.app.test_client()
        ra = c.get("/api/analysis_results?run=" + a).get_json()
        rb = c.get("/api/analysis_results?run=" + b).get_json()
        assert "偏多" in ra["reports"]["synthesis"]
        assert "偏空" in rb["reports"]["synthesis"], "结果按 run 取串了"

    def test_stop_targets_only_given_run(self):
        a = web_app._register_run(_mk("TA"))
        b = web_app._register_run(_mk("RB"))
        r = web_app.app.test_client().post("/api/stop?run=" + a)
        assert r.status_code == 200
        assert web_app._resolve_tracker(a).stop_requested is True
        assert web_app._resolve_tracker(b).stop_requested is False, "不该停掉别人的分析"

    def test_pause_unpause_specific_run(self):
        a = web_app._register_run(_mk("TA"))
        b = web_app._register_run(_mk("RB"))
        c = web_app.app.test_client()
        assert c.post("/api/pause?run=" + a).status_code == 200
        assert web_app._resolve_tracker(a).is_paused is True
        assert web_app._resolve_tracker(b).is_paused is False
        assert c.post("/api/resume?run=" + a).status_code == 200
        assert web_app._resolve_tracker(a).is_paused is False

    def test_results_not_ready_before_complete(self):
        rid = web_app._register_run(_mk("TA", running=True))
        d = web_app.app.test_client().get("/api/analysis_results?run=" + rid).get_json()
        assert d == {"ready": False}
