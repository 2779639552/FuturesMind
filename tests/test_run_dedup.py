# -*- coding: utf-8 -*-
"""同品种互斥（409 duplicate）的护栏（2026-10-23）。

【要解决什么】同品种 + 同交易日 + 同模块配置的分析已经在跑时，再点一次「开始分析」
就是**纯重复**：LLM 推理不共享，取数缓存只省几秒，结果是 token 双花、时间一点不省。

【为什么不按品种一刀切】同品种不同交易日、完整档 vs 轻量档，是**不同的分析**，
必须允许同时跑 —— 只按品种拦会把正常用法挡掉。

【为什么返回 409 而不是 429】429 是"没名额了，你等着"，409 是"这件事已经在做，
直接加入"——两者前端处置完全不同：409 要接管已有 run 继续轮询，不能报错停下。
"""
from __future__ import annotations

import pytest

import web_app
from web_app import ProgressTracker


def _modules_for(payload: dict) -> dict:
    """按后端同一条路径算出模块签名，避免测试自己猜。"""
    opts = web_app.resolve_run_options(payload.get("symbol", "RB"), payload)
    return {
        "sentiment": opts["include_sentiment"],
        "debate": opts["include_debate"],
        "synthesis": opts["include_synthesis"],
        "scenario": opts["include_scenario"],
    }


def _running(symbol: str, date: str, payload: dict | None = None, modules: dict | None = None) -> ProgressTracker:
    t = ProgressTracker(symbol=symbol, trade_date=date, stages=web_app.PIPELINE_STAGES)
    t.is_running = True
    t.run_modules = modules if modules is not None else _modules_for(payload or {"symbol": symbol, "date": date})
    t.current_stage = "technical_analyst"
    return t


@pytest.fixture(autouse=True)
def _clean():
    with web_app._RUNS_LOCK:
        web_app._RUNS.clear()
        web_app._LATEST_RUN_ID = None
    yield
    with web_app._RUNS_LOCK:
        web_app._RUNS.clear()
        web_app._LATEST_RUN_ID = None


@pytest.fixture(autouse=True)
def _no_real_thread(monkeypatch):
    """禁止真的起分析线程 —— 否则这些测试会跑完整分析、烧 LLM 调用。"""

    class _NoThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

    monkeypatch.setattr(web_app.threading, "Thread", _NoThread)


def _post(payload: dict):
    return web_app.app.test_client().post("/api/run_analysis", json=payload)


class TestDuplicate:
    def test_same_symbol_date_modules_rejected(self):
        payload = {"symbol": "TA", "date": "2026-10-23"}
        rid = web_app._register_run(_running("TA", "2026-10-23", payload))

        r = _post(payload)
        assert r.status_code == 409, r.get_json()
        d = r.get_json()
        assert d["error"] == "duplicate"
        assert d["run"] == rid, "必须交回已有 run，前端才能接管它的进度页"
        assert "已切到那次运行" in d["message"]

    def test_body_carries_stages_and_modules(self):
        """409 里必须带 stages/modules —— 前端是走同一段代码重绘进度条的。"""
        payload = {"symbol": "TA", "date": "2026-10-23"}
        web_app._register_run(_running("TA", "2026-10-23", payload))

        d = _post(payload).get_json()
        assert isinstance(d.get("stages"), list) and d["stages"], "缺 stages，前端进度条会空"
        assert set(d["modules"]) == {"sentiment", "debate", "synthesis", "scenario"}

    def test_different_date_allowed(self):
        web_app._register_run(_running("TA", "2026-10-22"))
        r = _post({"symbol": "TA", "date": "2026-10-23"})
        assert r.status_code == 200, "不同交易日是不同分析，不能拦"

    def test_different_modules_allowed(self):
        """完整档在跑时，用户仍可以起一个轻量档做对比。"""
        web_app._register_run(_running("TA", "2026-10-23", {"symbol": "TA", "date": "2026-10-23"}))
        r = _post({
            "symbol": "TA", "date": "2026-10-23",
            "include_debate": False, "include_synthesis": False,
            "include_scenario": False, "include_sentiment": "exclude",
        })
        assert r.status_code == 200, "配置不同就是不同分析"

    def test_different_symbol_allowed(self):
        web_app._register_run(_running("TA", "2026-10-23"))
        assert _post({"symbol": "RB", "date": "2026-10-23"}).status_code == 200

    def test_finished_run_is_not_duplicate(self):
        """跑完的不能拦 —— 否则同一天想重跑一次都做不到。"""
        t = _running("TA", "2026-10-23", {"symbol": "TA", "date": "2026-10-23"})
        t.mark_complete({}, rating=None)
        web_app._register_run(t)
        assert _post({"symbol": "TA", "date": "2026-10-23"}).status_code == 200
