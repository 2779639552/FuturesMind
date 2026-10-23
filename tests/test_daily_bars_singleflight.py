# -*- coding: utf-8 -*-
"""日线缓存单飞（single-flight）的护栏（2026-10-23）。

【要解决什么】`_daily_bars_cached` 原来只加了"保护字典"的锁，**不合并并发请求**。
做分析并行后实测：3 路同时分析同一品种 ⇒ 3 次天勤握手（每次 5.4s）+ 3 次国君请求。
国君有熔断，批量取数会把交互式请求拖死；天勤侧并发也会放大 SSL 抖动。

【本文件守什么】
  1. 同一 key 的 N 个并发调用，上游**只被打一次**（这是单飞的定义）
  2. 所有调用者都拿到同一份数据（不能有人拿到空）
  3. leader 抛异常时，等待者**不会拿到空数据**（宁可重取，也不能静默变空）
  4. 不同 key（不同品种/天数）互不阻塞
  5. 单飞结束必须清理信号表 —— 否则下一次同 key 调用会白等 30 秒
"""
from __future__ import annotations

import threading
import time

import pytest

import quant_module as Q


def _bars(n: int = 40, base: float = 100.0) -> list[dict]:
    return [
        {"date": "2026-09-%02d" % (i % 28 + 1), "open": base + i, "high": base + i + 1,
         "low": base + i - 1, "close": base + i, "volume": 1000, "oi": 500}
        for i in range(n)
    ]


class TestSingleFlight:
    def test_concurrent_same_key_hits_upstream_once(self, monkeypatch):
        """★ 核心断言：6 个线程同时要同一份日线，上游只能被打一次。"""
        calls = {"n": 0}
        gate = threading.Event()

        def slow_fetch(code, days):
            calls["n"] += 1
            gate.wait(2.0)  # 拖住第一个，确保其余线程在它取完前到达
            return _bars()

        monkeypatch.setattr(Q, "_load_daily_bars", slow_fetch)

        results: list[list[dict]] = []
        errors: list[Exception] = []

        def worker():
            try:
                results.append(Q._daily_bars_cached("SFTEST", 250))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        time.sleep(0.2)  # 让所有线程都进到"等待 leader"的位置
        gate.set()
        for t in threads:
            t.join(5)

        assert not errors, errors
        assert calls["n"] == 1, f"上游被打了 {calls['n']} 次，单飞没生效"
        assert len(results) == 6
        assert all(len(r) == 40 for r in results), "有调用者拿到空/残缺数据"
        assert all(r is results[0] or r == results[0] for r in results)

    def test_releases_inflight_after_success(self, monkeypatch):
        """单飞信号必须在取完后清掉，否则下一次同 key 调用要白等。"""
        monkeypatch.setattr(Q, "_load_daily_bars", lambda c, d: _bars())
        Q._daily_bars_cached("SFTEST2", 250)
        assert "dbars:SFTEST2:250" not in Q._INFLIGHT, "信号表没清理"

    def test_leader_failure_does_not_return_empty(self, monkeypatch):
        """leader 抛异常时，等待者必须自己重取 —— 绝不能因此返回空数据。"""
        state = {"n": 0}
        gate = threading.Event()

        def flaky(code, days):
            state["n"] += 1
            if state["n"] == 1:
                gate.wait(2.0)
                raise RuntimeError("天勤 SSL 抖动")
            return _bars()

        monkeypatch.setattr(Q, "_load_daily_bars", flaky)

        out: list[object] = []

        def leader():
            try:
                Q._daily_bars_cached("SFTEST3", 250)
            except Exception as exc:  # noqa: BLE001
                out.append(exc)

        def follower():
            out.append(Q._daily_bars_cached("SFTEST3", 250))

        t1 = threading.Thread(target=leader)
        t1.start()
        time.sleep(0.15)
        t2 = threading.Thread(target=follower)
        t2.start()
        gate.set()
        t1.join(5)
        t2.join(5)

        follower_result = [x for x in out if isinstance(x, list)]
        assert follower_result and len(follower_result[0]) == 40, (
            "leader 失败后等待者拿到了空数据（应当自己重取）"
        )
        assert any(isinstance(x, Exception) for x in out), "leader 的异常应如实抛出"

    def test_different_keys_do_not_block(self, monkeypatch):
        """不同品种必须能并行取数，不能被同一把锁串起来。"""
        seen: list[str] = []
        lock = threading.Lock()

        def fetch(code, days):
            with lock:
                seen.append(code)
            time.sleep(0.15)
            return _bars()

        monkeypatch.setattr(Q, "_load_daily_bars", fetch)

        t0 = time.time()
        threads = [
            threading.Thread(target=Q._daily_bars_cached, args=(c, 250))
            for c in ("SFA", "SFB", "SFC")
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        elapsed = time.time() - t0

        assert sorted(seen) == ["SFA", "SFB", "SFC"]
        assert elapsed < 0.4, f"三个不同品种被串行化了（{elapsed:.2f}s）"

    def test_cache_hit_does_not_enter_inflight(self, monkeypatch):
        """已有缓存时不该创建单飞条目（否则会给并发读加无谓的锁竞争）。"""
        monkeypatch.setattr(Q, "_load_daily_bars", lambda c, d: _bars())
        Q._daily_bars_cached("SFTEST4", 250)
        Q._INFLIGHT.clear()
        Q._daily_bars_cached("SFTEST4", 250)  # 命中内存缓存
        assert Q._INFLIGHT == {}, "命中缓存却创建了单飞条目"
