# -*- coding: utf-8 -*-
"""读书通道「失败自动修复」的边界（2026-10-19）。

分两层，回答"下次出问题会不会自愈"：
  ① 采集器内：会话自动续期（启动 ×2 + 请求级 -2012 续期重试）—— 见 test_wechat_weread_cover.py
  ② 调度层  ：失败后按类型处理 —— transient 隔 60s 自动重试一次；credentials 必须扫码、不重试。
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _src() -> str:
    return (ROOT / "scheduler.py").read_text(encoding="utf-8")


@pytest.mark.unit
def test_failure_kind_splits_credentials_from_transient():
    """行为判据：退出码 3 = 长期凭据失效（重试无意义）；其余算瞬时。"""
    from scheduler import _weread_failure_kind

    assert _weread_failure_kind("weread 退出码 3") == "credentials"
    assert _weread_failure_kind("weread 退出码 1") == "transient"
    assert _weread_failure_kind("weread 子进程失败: timed out") == "transient"
    assert _weread_failure_kind("") == "transient"


@pytest.mark.unit
def test_only_transient_failures_are_retried():
    """★ 只有 transient 才重试；credentials 直接告警（否则会把"需扫码"掩盖成"重试过"）。"""
    s = _src()
    # 窗口要**包含首调用**（它在 `_retried = 0` 之前），否则只数到重试那一次
    i = s.index("w_out, w_err = _run_weread()")
    j = s.index("w_collected, _w_src", i)
    seg = s[i:j]
    assert 'if w_err and _weread_failure_kind(w_err) == "transient":' in seg, \
        "重试条件不是按失败类型判定"
    assert "time.sleep(60)" in seg, "没有退避等待（立刻重试容易撞同一个瞬时故障）"
    assert seg.count("_run_weread()") == 2, "没有真正执行第二次采集（窗口内 %d 次）" % seg.count("_run_weread()")
    # 重试后必须重新取时点，否则读的是第一次的结果文件
    assert "_w_since = datetime.now().timestamp()" in seg, \
        "★ 重试前没重置 _w_since ⇒ 统计会读到上一轮的旧结果"


@pytest.mark.unit
def test_credentials_failure_escalates_and_points_to_qr():
    """长期凭据失效：按 error 级别告警，并明确"必须扫码"。"""
    s = _src()
    i = s.index('if _weread_failure_kind(w_err) == "credentials":')
    seg = s[i:i + 600]
    assert 'severity="error"' in seg, "凭据失效仍是 warn ⇒ 会被当成可自愈，实际要人工扫码"
    assert "_weread_login.mjs" in seg, "没有给出可执行的下一步（扫码脚本）"
    assert "必须扫码" in seg, "文案没说明「重试救不了」"


@pytest.mark.unit
def test_transient_alert_says_no_qr_needed():
    """瞬时失败：告警要说明「已自动续期并重试」，避免用户又去扫码。"""
    s = _src()
    i = s.index('if _weread_failure_kind(w_err) == "credentials":')
    j = s.index("severity=", i)
    seg = s[j:j + 700]
    assert "已自动续期并重试" in seg, "瞬时失败没说明已自动处理"
    assert "无需扫码" in seg, "没告诉用户这类不需要扫码"


@pytest.mark.unit
def test_collector_exit_code_3_marks_unrecoverable_session():
    """契约：采集器续期两次都失败时必须以 3 退出（调度靠它区分类型）。"""
    src = (ROOT / "wechat_weread_collector.py").read_text(encoding="utf-8")
    i = src.index("if not renew_session():")
    seg = src[i:i + 700]
    assert "return 3" in seg, "续期两次失败没有以 3 退出 ⇒ 调度会把「需扫码」当瞬时故障重试"

@pytest.mark.unit
def test_ingest_is_mutually_exclusive(monkeypatch):
    """★★ 入库互斥：锁被占用时**不能**再跑一遍（否则同一批会被两路各烧一次 LLM）。"""
    import scheduler as S

    called = []
    monkeypatch.setattr(S, "_run_wechat_ingest_impl", lambda *a, **k: called.append(1))
    monkeypatch.setattr(S, "_safe_alert", lambda *a, **k: None)
    monkeypatch.setattr(S, "get_db", lambda: None)

    assert S._INGEST_LOCK.acquire(blocking=False)
    try:
        S._run_wechat_ingest("t")
        assert called == [], "锁被占用时仍执行了入库 ⇒ 并发会重复跑 LLM"
    finally:
        S._INGEST_LOCK.release()

    S._run_wechat_ingest("t")
    assert called == [1], "锁空闲时没有执行入库"


@pytest.mark.unit
def test_collect_triggers_ingest_only_when_new_articles():
    """★ 有新文才触发入库；两个通道的新文都要计入。"""
    s = _src()
    # 窗口从 `_new_total` 起（它在完成告警**之前**计算），到入库函数定义前
    i = s.index("_new_total = max(0, int(collected or 0))")
    j = s.index("def _run_wechat_ingest", i)
    seg = s[i:j]
    assert "max(0, int(collected or 0)) + max(0, int(w_collected or 0))" in seg, \
        "新文总数没把两个通道都算上"
    assert "if _new_total > 0:" in seg, "没有按「是否有新文」决定是否触发"
    assert "_run_wechat_ingest(tag)" in seg, "有新文却没有触发入库"
    assert "立即触发入库" in seg, "完成告警没说明已自动触发入库"

@pytest.mark.unit
def test_ingest_is_mutually_exclusive(monkeypatch):
    """★★ 入库互斥：锁被占用时**不能**再跑一遍（否则同一批会被两路各烧一次 LLM）。"""
    import scheduler as S

    called = []
    monkeypatch.setattr(S, "_run_wechat_ingest_impl", lambda *a, **k: called.append(1))
    monkeypatch.setattr(S, "_safe_alert", lambda *a, **k: None)
    monkeypatch.setattr(S, "get_db", lambda: None)

    assert S._INGEST_LOCK.acquire(blocking=False)
    try:
        S._run_wechat_ingest("t")
        assert called == [], "锁被占用时仍执行了入库 ⇒ 并发会重复跑 LLM"
    finally:
        S._INGEST_LOCK.release()

    S._run_wechat_ingest("t")
    assert called == [1], "锁空闲时没有执行入库"
