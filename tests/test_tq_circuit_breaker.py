# -*- coding: utf-8 -*-
"""天勤「失败熔断」回归护栏（2026-10-14）

【为什么需要】熔断是为修「分钟 K 线自动刷新失效」而加的**行为**，不是重构：
  没有它，天勤不可用时每次取数都要抢全局串行锁 + 等 ~6s 重试，批量取数
  （技术面总览 21 品种 × 4 周期 = 84 次）会串行占锁 8 分钟以上，把同一把锁
  后面的 K 线刷新拖到 35~50s，而刷新周期只有 30s ⇒ 用户看到「K 线不更新」。

【断言要点】不只是"熔断后返回 None"，更要断言**没有再去抢锁/重试**
  —— 否则"返回 None 但内部照样等 6s"会让护栏假绿（本项目栽过这类坑）。
  判据：熔断期内连接函数（`_run_isolated_from_logging_hijack`）**调用次数为 0**。
"""
from __future__ import annotations

import json

import pytest

from tradingagents.dataflows import tq_source


class _FakeSSLError(Exception):
    """模拟真实的连不上 auth 主机。"""


@pytest.fixture
def breaker(monkeypatch):
    """把熔断状态复位，并让 configured() 为真（否则 fetch 会在第一步就返回）。"""
    tq_source.reset_circuit()
    monkeypatch.setenv("TQ_USER", "u")
    monkeypatch.setenv("TQ_PASS", "p")
    monkeypatch.setattr(tq_source, "configured", lambda: True)
    yield tq_source
    tq_source.reset_circuit()


def _patch_connect(monkeypatch, fail: bool, calls: list):
    """替换建连入口；fail=True 时抛 SSLError，否则返回一个哑对象。"""

    def _fake(fn):
        calls.append(1)
        if fail:
            raise _FakeSSLError("HTTPSConnectionPool(host='auth.shinnytech.com')")
        class _Api:
            def close(self):
                pass
        return _Api()

    monkeypatch.setattr(tq_source, "_run_isolated_from_logging_hijack", _fake)


# ══════════════════════════════════════════════════════════════
# 一、熔断状态机
# ══════════════════════════════════════════════════════════════

def test_circuit_closed_initially(breaker):
    st = breaker.circuit_state()
    assert st["open"] is False
    assert st["streak"] == 0


def test_circuit_opens_after_threshold_failures(breaker, monkeypatch):
    """连续失败达阈值 ⇒ 熔断开启。"""
    calls: list = []
    _patch_connect(monkeypatch, fail=True, calls=calls)
    for _ in range(breaker.circuit_state()["threshold"]):
        assert breaker.fetch_minute_bars("BR", "1", 10) is None
    st = breaker.circuit_state()
    assert st["open"] is True, "连续失败达阈值后熔断未开启"
    assert st["cooldown_remaining"] > 0


def test_open_circuit_does_not_touch_connect(breaker, monkeypatch):
    """★核心断言：熔断期内**不再尝试建连**（不抢锁、不重试）。

    只断言"返回 None"是不够的 —— 内部照样等 6s 重试也能返回 None，
    那正是要修的缺陷本身，必须是"一次都没去连"。
    """
    calls: list = []
    _patch_connect(monkeypatch, fail=True, calls=calls)
    for _ in range(breaker.circuit_state()["threshold"]):
        breaker.fetch_minute_bars("BR", "1", 10)
    n_after_open = len(calls)

    # 熔断已开：再调用 5 次，建连次数必须**一个都不增加**
    for _ in range(5):
        assert breaker.fetch_minute_bars("BR", "1", 10) is None
    assert len(calls) == n_after_open, (
        "熔断期内仍去建连了（%d -> %d）—— 没有真正跳过抢锁/重试" % (n_after_open, len(calls))
    )


def test_open_circuit_sets_error_message(breaker, monkeypatch):
    """熔断期返回时须写 last_error，便于前端"数据源边界"如实告知。"""
    calls: list = []
    _patch_connect(monkeypatch, fail=True, calls=calls)
    for _ in range(breaker.circuit_state()["threshold"]):
        breaker.fetch_minute_bars("BR", "1", 10)
    breaker.fetch_minute_bars("BR", "1", 10)
    assert "熔断" in (breaker.last_error() or ""), "熔断期未写 last_error"


def test_success_resets_streak_and_clears_circuit(breaker, monkeypatch):
    """建连成功 ⇒ 计数清零、熔断解除（否则网络恢复后仍被挡着）。

    ⚠️ 不能用 `reset_circuit()` 来"半开"—— 那会把 streak 一起清零，
    于是"成功是否清零"这条断言被前置动作兜住，护栏变成**永真**
    （变异自检实测：把 `_record_ok()` 删掉，用例照样绿）。
    正确做法：只把**冷却截止时间**拨到过去（模拟冷却自然到期），保留 streak。
    """
    calls: list = []
    _patch_connect(monkeypatch, fail=True, calls=calls)
    for _ in range(breaker.circuit_state()["threshold"]):
        breaker.fetch_minute_bars("BR", "1", 10)
    st0 = breaker.circuit_state()
    assert st0["open"] is True and st0["streak"] >= 1

    # 模拟"冷却到期"：只拨时间，**不动 streak**（这才逼近真实半开状态）
    breaker._COOLDOWN_UNTIL = 0.0
    assert breaker.circuit_state()["streak"] >= 1, "前置条件：streak 应仍 >0"

    # 网络恢复
    _patch_connect(monkeypatch, fail=False, calls=calls)
    breaker.fetch_minute_bars("BR", "1", 10)
    st = breaker.circuit_state()
    assert st["open"] is False, "建连成功后熔断未解除"
    assert st["streak"] == 0, "建连成功后失败计数未清零（网络恢复仍会被挡）"


def test_reset_circuit_clears(breaker, monkeypatch):
    calls: list = []
    _patch_connect(monkeypatch, fail=True, calls=calls)
    for _ in range(breaker.circuit_state()["threshold"]):
        breaker.fetch_minute_bars("BR", "1", 10)
    assert breaker.circuit_state()["open"] is True
    breaker.reset_circuit()
    st = breaker.circuit_state()
    assert st["open"] is False and st["streak"] == 0 and st["cooldown_remaining"] == 0


# ══════════════════════════════════════════════════════════════
# 二、三个取数入口都受熔断保护（漏一个就有一条链路照样被拖慢）
# ══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("entry", ["daily", "minute", "realtime"])
def test_all_fetch_entries_respect_circuit(breaker, monkeypatch, entry):
    calls: list = []
    _patch_connect(monkeypatch, fail=True, calls=calls)
    for _ in range(breaker.circuit_state()["threshold"]):
        breaker.fetch_minute_bars("BR", "1", 10)
    n0 = len(calls)

    if entry == "daily":
        assert breaker.fetch_daily_bars("BR", 10) is None
    elif entry == "minute":
        assert breaker.fetch_minute_bars("BR", "1", 10) is None
    else:
        assert breaker.fetch_realtime(["BR"]) is None
    assert len(calls) == n0, "%s 入口未受熔断保护（仍去建连）" % entry


# ══════════════════════════════════════════════════════════════
# 三、阈值可用环境变量覆盖（否则测试要真等 2 分钟）
# ══════════════════════════════════════════════════════════════

def test_threshold_and_cooldown_are_env_overridable():
    """阈值/冷却期可由环境变量覆盖（否则测试要真等 2 分钟）。

    ⚠️ **不能在本进程里 `importlib.reload(tq_source)`** —— reload 会重建模块级对象
    （新的 `_connect_lock`、新函数对象），而别的模块可能已 `from tq_source import X`
    持有旧引用 ⇒ **身份错乱，污染同一 pytest 会话里的其它文件**
    （实测：加上 reload 用例后，组合跑会多出 1 个无关文件失败）。
    正确做法：**起子进程**设环境变量后 import，进程退出即无残留。
    """
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    code = (
        "import json;from tradingagents.dataflows import tq_source as t;"
        "print('##RESULT##' + json.dumps(t.circuit_state()))"
    )
    env = {"TQ_FAIL_THRESHOLD": "1", "TQ_COOLDOWN": "5"}
    import os
    full_env = dict(os.environ)
    full_env.update(env)
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=str(root), env=full_env, timeout=120)
    assert p.returncode == 0, "子进程失败：%s" % (p.stderr or "")[-300:]
    # ⚠️ 不能取"最后一行"：tqsdk 首次导入会往 stdout 打免责声明/提示（实测），
    #    行序不可控。用哨兵前缀精确取，避免解析到噪声行。
    line = next((ln for ln in p.stdout.splitlines() if ln.startswith("##RESULT##")), None)
    assert line, "子进程未输出结果标记。stdout=%r" % p.stdout[-300:]
    st = json.loads(line[len("##RESULT##"):])
    assert st["threshold"] == 1, "TQ_FAIL_THRESHOLD 未生效：%s" % st
    assert abs(st["cooldown_seconds"] - 5) < 0.01, "TQ_COOLDOWN 未生效：%s" % st
