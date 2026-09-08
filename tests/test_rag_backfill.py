"""研报处理链路 → RAG 自动索引挂点测试(_process_research_report 成功/失败路径)。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import web_app  # noqa: E402


class _FakeLLMClient:
    def get_llm(self):
        return object()  # _llm_extract_structured / _llm_opinion_conclusion 已打桩, llm 不被真调


class _FakeDB:
    def __init__(self, row):
        self._row = dict(row) if row else None

    def get_research_report(self, report_id):
        return dict(self._row) if self._row else None

    def update_research_report(self, report_id, **fields):
        if self._row:
            self._row.update(fields)


def _row(status="done"):
    return {"id": 55, "status": status, "variety": "RB", "title": "旧标题",
            "source": "旧来源", "filename": "a.pdf", "file_path": "x.pdf",
            "publish_date": "", "report_type": "", "uploaded_at": "2026-09-08 10:00:00"}


@pytest.fixture
def hooked_process(monkeypatch):
    """打桩 _process_research_report 全部外部依赖,返回索引钩子调用记录。"""
    calls = []
    monkeypatch.setattr(web_app, "_rag_index_report_safely", lambda rid: calls.append(rid))
    monkeypatch.setattr(web_app, "_extract_report_text", lambda fp: ("甲" * 100, False))
    monkeypatch.setattr(web_app, "_llm_extract_structured", lambda llm, sel, text: {
        "report_title": "新标题", "publisher": "华泰期货", "publish_date": "2026-09-07",
        "varieties": [{"variety": "RB", "direction": "看多", "confidence": 0.6}],
    })
    monkeypatch.setattr(
        web_app, "_llm_opinion_conclusion",
        lambda llm, text, varieties, report_id=None: {"RB": "## 观点与依据\n测试结论"},
    )
    monkeypatch.setattr(web_app, "_ingest_backfill_fund_metrics", lambda varieties, text: None)
    monkeypatch.setattr(web_app, "_write_research_aggregates", lambda *a, **k: None)
    monkeypatch.setattr(web_app, "_self_heal_publish_date", lambda db, rid, cur, sd: "2026-09-07")
    monkeypatch.setattr(web_app, "_self_heal_report_type", lambda db, rid, cur, sd: "日报")
    return calls


def test_process_success_triggers_rag_index(hooked_process, monkeypatch):
    calls = hooked_process
    monkeypatch.setattr(web_app, "get_db", lambda: _FakeDB(_row()))
    web_app._process_research_report(55)
    assert calls == [55]


def test_process_failure_skips_rag_index(hooked_process, monkeypatch):
    """空文本 → 研报判失败 → 不应触发索引钩子。"""
    calls = hooked_process
    monkeypatch.setattr(web_app, "get_db", lambda: _FakeDB(_row()))
    monkeypatch.setattr(web_app, "_extract_report_text", lambda fp: ("", False))
    web_app._process_research_report(55)
    assert calls == []


def test_process_missing_report_skips_rag_index(hooked_process, monkeypatch):
    calls = hooked_process
    monkeypatch.setattr(web_app, "get_db", lambda: _FakeDB(None))
    web_app._process_research_report(999)
    assert calls == []
