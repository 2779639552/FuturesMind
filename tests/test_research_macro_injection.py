"""研报宏观事件注入链路测试:宏观/情绪分析师节点把 research_macro_context 的
返回确定性前置到系统提示(2026-09-04)。全 mock——不触 LLM/网络/真实数据。

覆盖: 有事件注入到位 / 无事件不注入 / 情绪节点同通道注入。
"""

import tradingagents.agents.analysts.commodity_analysts as ca
import tradingagents.agents.analysts.sentiment_analyst as sa

_INJECTED = "# RESEARCH 宏观事件(近3天)\n- 中东战事升级(利多x2)"


class _FakeLLM:
    """占位 LLM:节点只把它透传给 _run_tool_loop(已被替换),不真正调用。"""


def _patch_loop(monkeypatch, module, captured):
    """替换模块内 _run_tool_loop:捕获初始消息(系统提示在其中),返回固定报告。"""

    def _fake(llm, tools, initial_messages, **kwargs):
        captured.append(initial_messages[0].content)
        return "FAKE REPORT"

    monkeypatch.setattr(module, "_run_tool_loop", _fake)


_STATE = {"trade_date": "2026-09-04", "company_of_interest": "SC", "messages": []}


def test_macro_node_injects_research_events(monkeypatch):
    captured = []
    monkeypatch.setattr(ca, "research_macro_context", lambda sym: _INJECTED)
    _patch_loop(monkeypatch, ca, captured)
    ca.create_commodity_macro_analyst(_FakeLLM())(_STATE)
    assert len(captured) == 1
    assert "RESEARCH 宏观事件" in captured[0]
    assert "中东战事升级" in captured[0]
    # 注入块位于提示最前部:先于框架第 0 节指引(模板会包一层前言,不能断言 startswith)
    assert captured[0].index(_INJECTED) < captured[0].index("Research Report Macro Events")


def test_macro_node_no_events_no_injection(monkeypatch):
    captured = []
    monkeypatch.setattr(ca, "research_macro_context", lambda sym: "")
    _patch_loop(monkeypatch, ca, captured)
    ca.create_commodity_macro_analyst(_FakeLLM())(_STATE)
    assert len(captured) == 1
    # 框架第 0 节指引文字里含 "RESEARCH 宏观事件" 字样,须断言注入块标记头,不是裸短语
    assert "# RESEARCH 宏观事件" not in captured[0]
    assert "中东战事升级" not in captured[0]


def test_sentiment_node_injects_research_events(monkeypatch):
    captured = []
    monkeypatch.setattr(sa, "research_macro_context", lambda sym: _INJECTED)
    _patch_loop(monkeypatch, sa, captured)
    sa.create_commodity_sentiment_analyst(_FakeLLM())(_STATE)
    assert len(captured) == 1
    assert "RESEARCH 宏观事件" in captured[0]
    assert "中东战事升级" in captured[0]


def test_macro_node_prompt_mentions_section_zero(monkeypatch):
    """框架第 0 节(研报事件使用指引)始终在提示中,无事件时也教 LLM 如实说明。"""
    captured = []
    monkeypatch.setattr(ca, "research_macro_context", lambda sym: "")
    _patch_loop(monkeypatch, ca, captured)
    ca.create_commodity_macro_analyst(_FakeLLM())(_STATE)
    assert "Research Report Macro Events" in captured[0]
    assert "不要编造" in captured[0]
