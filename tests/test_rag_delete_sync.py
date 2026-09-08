"""删除研报 → RAG 向量同步清理挂点测试(_delete_research_report_full)。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import web_app  # noqa: E402

# conftest autouse 会把 web_app._rag_delete_vectors_safely 换成 no-op;
# 模块导入期(测试运行前)先保存真挂点,供"异常隔离"测试还原使用。
_REAL_RAG_DELETE = web_app._rag_delete_vectors_safely


class _FakeDB:
    def __init__(self, rows):
        self._rows = {r["id"]: dict(r) for r in rows}
        self.deleted = []

    def get_research_report(self, report_id):
        return self._rows.get(report_id)

    def delete_research_report(self, report_id):
        self.deleted.append(report_id)
        self._rows.pop(report_id, None)

    def list_research_reports(self, limit=1000):
        return [dict(r) for r in self._rows.values()]


@pytest.fixture
def isolated_delete(monkeypatch):
    """隔离 _delete_research_report_full 的外部副作用,返回删除钩子调用记录。"""
    calls = []
    db = _FakeDB([
        {"id": 7, "varieties": "RB,HC", "variety": "RB", "file_path": None},
        {"id": 8, "varieties": "", "variety": "CU", "file_path": None},
    ])
    monkeypatch.setattr(web_app, "get_db", lambda: db)
    import tradingagents.dataflows.research_data as rd

    monkeypatch.setattr(rd, "remove_research_report", lambda *a, **k: None)
    monkeypatch.setattr(rd, "sweep_orphan_reports", lambda *a, **k: 0)
    monkeypatch.setattr(web_app, "_rag_delete_vectors_safely", lambda rid: calls.append(rid))
    return calls, db


def test_delete_triggers_rag_vector_cleanup(isolated_delete):
    calls, _db = isolated_delete
    assert web_app._delete_research_report_full(7) is True
    assert calls == [7]


def test_delete_nonexistent_report_no_hook(isolated_delete):
    calls, _db = isolated_delete
    assert web_app._delete_research_report_full(999) is False
    assert calls == []


def test_delete_survives_rag_cleanup_error(monkeypatch, isolated_delete):
    """RAG 清理抛错不允许影响删除主流程(还原真挂点,在 service 层抛错验证隔离)。"""
    calls, _db = isolated_delete
    import tradingagents.rag as rag_pkg
    import tradingagents.rag.service as rag_service

    monkeypatch.setattr(web_app, "_rag_delete_vectors_safely", _REAL_RAG_DELETE)
    monkeypatch.setattr(rag_pkg, "is_available", lambda: True)

    def _boom(report_id):
        calls.append(report_id)
        raise RuntimeError("chroma down")

    monkeypatch.setattr(rag_service, "delete_report", _boom)
    assert web_app._delete_research_report_full(7) is True
    assert calls == [7]
