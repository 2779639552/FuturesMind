"""注入块「超时 ≠ 无数据」与「结论块排在读数块之前」的护栏（2026-10-22）。

【为什么需要这组测试】
2026-10-22 体检发现两个静默缺陷，原有 32 个视图/注入测试全部是绿的：

  1. **超时阈值低于冷启动**：`analyst_views.WAIT_SECONDS=8.0`，而 `_compute` 实测 16.17s；
     `analysis_bundle.WAIT_SECONDS=6.0`，而 `fetch_overview` 冷启动 6~18s。
     技术分析师是图里第一个节点（建图 1.54s 后到达）⇒ 价格轴视图**必然**拿不到。
  2. **拿不到时静默返回空串**：日志、提示词里都没有痕迹，等于把「取数失败」
     表现成「本来就没有这一节」—— 违反项目三态纪律。

原有测试抓不到，是因为它们只断言「有数据时块存在」，从没断言「没数据时**必须说出来**」，
也从没盯过超时常量与真实冷启动的关系（冷启动一变慢，常量就失效，测试仍绿）。

本文件改盯三件事：
  · 超时常量必须 ≥ 15s（低于此值等于没给冷启动留余量）；
  · 超时 → 必须出显式降级块 + warning，且**不能**与「确定无数据」混为一谈；
  · 提示词里结论块（价格轴视图 / 三指标策略）必须排在读数块（方法论）之前。
"""
from __future__ import annotations

import io
import re
import time
from pathlib import Path

import pytest

import tradingagents.dataflows.analysis_bundle as A
import tradingagents.dataflows.analyst_views as V

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# 1. 超时常量必须覆盖冷启动
# ---------------------------------------------------------------------------
def test_wait_seconds_covers_cold_start():
    """实测 `_compute` 冷启动 16.17s / `fetch_overview` 6~18s。

    阈值低于冷启动 = 每次冷缓存都必然拿不到块。这个断言就是那条「护栏」——
    数据源哪天变慢，只要有人想把 20 改回 8，这里会红。
    """
    assert V.WAIT_SECONDS >= 15, (
        f"analyst_views.WAIT_SECONDS={V.WAIT_SECONDS}s 低于实测冷启动 16.17s —— "
        "技术分析师（图里第一个节点）会必然拿不到价格轴视图"
    )
    assert A.WAIT_SECONDS >= 15, (
        f"analysis_bundle.WAIT_SECONDS={A.WAIT_SECONDS}s 低于 fetch_overview 冷启动 6~18s"
    )


# ---------------------------------------------------------------------------
# 2. 超时 → 显式降级块（不能静默）
# ---------------------------------------------------------------------------
def _slow_compute(sym):
    time.sleep(1.5)
    return {"code": sym, "votes": [], "tech_snapshot": {}}


def test_technical_view_timeout_is_explicit(monkeypatch):
    monkeypatch.setattr(V, "WAIT_SECONDS", 0.2)
    monkeypatch.setattr(V, "_compute", _slow_compute)
    V.clear_cache()

    out = V.technical_view("TA")
    assert "超时" in out, "超时必须写出来，不能返回空串让模型以为本来就没有这一节"
    assert "未纳入" in out
    assert "不是「无信号」" in out, "要明确区分「缺席」与「中性/无信号」"
    assert "仅价格轴" in out, "降级块也要带轴隔离声明"


def test_fundamental_view_timeout_is_explicit(monkeypatch):
    monkeypatch.setattr(V, "WAIT_SECONDS", 0.2)
    monkeypatch.setattr(V, "_compute", _slow_compute)
    V.clear_cache()

    out = V.fundamental_view("TA")
    assert "超时" in out and "未纳入" in out
    assert "基差/库存/利润三轴" in out


def test_timeout_emits_warning(monkeypatch, caplog):
    """超时以前零日志 —— 线上块长期缺席也查不到痕迹。"""
    monkeypatch.setattr(V, "WAIT_SECONDS", 0.2)
    monkeypatch.setattr(V, "_compute", _slow_compute)
    V.clear_cache()

    with caplog.at_level("WARNING"):
        V._snapshot("TA")
    assert any("首调超时" in r.message for r in caplog.records), "超时必须留下 warning 日志"


def test_gtja_timeout_is_explicit(monkeypatch):
    monkeypatch.setattr(A, "WAIT_SECONDS", 0.05)
    monkeypatch.setattr(A, "build_block", lambda s: time.sleep(0.4) or "SLOW")
    A.clear_cache()

    out = A.gtja_context("RB")
    assert "超时" in out and "未纳入" in out
    assert "不是「无数据」" in out, "降级块不能说成「无数据」，更不能写 0"


# ---------------------------------------------------------------------------
# 3. 「确定无数据」仍然是静默的（三态不能混）
# ---------------------------------------------------------------------------
def test_no_data_is_still_silent(monkeypatch):
    """无在途计算 + 空快照 = 确定无覆盖 → 不注入，且**不能**冒出超时块。"""
    monkeypatch.setattr(V, "_snapshot", lambda s, **kw: {})
    V.clear_cache()
    assert V.technical_view("TA") == ""
    assert V.fundamental_view("TA") == ""


def test_build_failure_is_cached_not_retried(monkeypatch):
    """构建异常要写缓存（空串 = 本次确定拿不到），否则每次调用都重试上游。"""
    calls = {"n": 0}

    def _boom(sym):
        calls["n"] += 1
        raise RuntimeError("upstream down")

    monkeypatch.setattr(A, "build_block", _boom)
    A.clear_cache()
    assert A.gtja_context("RB") == ""
    assert A.gtja_context("RB") == ""
    assert calls["n"] == 1, f"构建异常后被重试了 {calls['n']} 次，应只跑一次"


# ---------------------------------------------------------------------------
# 4. 预热必须先于建图（与 1.54s 建图并行）
# ---------------------------------------------------------------------------
def test_warm_starts_before_graph_build():
    src = io.open(ROOT / "web_app.py", encoding="utf-8").read()
    start = src.index("def run_analysis():")
    body = src[start:start + 8000]

    i_gtja = body.find("_gtja_warm")
    i_views = body.find("_views_warm")
    m_build = re.search(r"build_commodity_graph\(", body)
    assert m_build, "run_analysis 里找不到 build_commodity_graph 调用"

    assert i_gtja != -1 and i_views != -1, "预热线程不见了"
    assert i_gtja < m_build.start(), "国君四源预热必须在建图之前启动（否则第一个节点等不到）"
    assert i_views < m_build.start(), "量化视图预热必须在建图之前启动"


# ---------------------------------------------------------------------------
# 5. 结论块必须排在读数块之前
# ---------------------------------------------------------------------------
def _capture(factory_name: str, monkeypatch, mod):
    cap = {}

    def _fake_loop(llm, tools, initial_messages, **_kw):
        cap["prompt"] = initial_messages[0].content
        return "REPORT"

    monkeypatch.setattr(mod, "_run_tool_loop", _fake_loop)
    node = getattr(mod, factory_name)(llm=object())
    node({"trade_date": "2026-10-22", "company_of_interest": "TA",
          "messages": [], "past_context": ""})
    return cap.get("prompt", "")


def test_technical_prompt_puts_view_before_methodology(monkeypatch):
    """所有注入都是 `块 + system_message` ⇒ 最后 prepend 的排最前。

    原实现把价格轴视图 prepend 在方法论之前，结果被 1802 字方法论压到 34.5% 处，
    实测引用率「加权分/价格轴票」零命中。现在视图必须排在方法论**前面**。
    """
    from tradingagents.agents.analysts import commodity_analysts as mod

    monkeypatch.setattr(mod, "technical_view", lambda s: "【技术面量化 · TEST（仅价格轴）】\n◆ 加权 +0.33")
    monkeypatch.setattr(mod, "technical_methodology_context", lambda s: "【方法论读数 · TEST】\n◆ ADXR 31.7")
    monkeypatch.setattr(mod, "variety_db_context", lambda s: "")

    p = _capture("create_commodity_technical_analyst", monkeypatch, mod)
    assert p.index("【技术面量化 · TEST") < p.index("【方法论读数 · TEST"), (
        "价格轴视图仍排在方法论后面 —— 结论被读数淹没，引用率会继续掉"
    )


def test_fundamental_prompt_puts_view_before_methodology(monkeypatch):
    from tradingagents.agents.analysts import commodity_analysts as mod

    monkeypatch.setattr(mod, "fundamental_view", lambda s: "【基本面量化 · TEST（三轴）】\n◆ 三指标策略")
    monkeypatch.setattr(mod, "gtja_context", lambda s: "【国君四源 · TEST】\n◆ 基差 550")
    monkeypatch.setattr(mod, "fundamental_methodology_context", lambda s: "【方法论读数 · TEST】\n◆ 四象")
    monkeypatch.setattr(mod, "variety_db_context", lambda s: "【品种数据库 · TEST】")

    p = _capture("create_commodity_fundamental_analyst", monkeypatch, mod)
    assert p.index("【基本面量化 · TEST") < p.index("【国君四源 · TEST") < p.index("【方法论读数 · TEST"), (
        "预期顺序：结论（三指标策略）→ 读数（四源）→ 方法论"
    )
