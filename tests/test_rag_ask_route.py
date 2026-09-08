"""研报问答路由(POST /api/research/ask 与 GET /api/research/rag/status)测试。

检索层与 LLM 全部打桩;仿 test_research_daily_summary 的 monkeypatch 风格。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tradingagents.rag as rag_pkg  # noqa: E402
import tradingagents.rag.service as rag_service  # noqa: E402
import web_app  # noqa: E402


def _hit(report_id=42, variety="RB", chunk_index=0, text="螺纹钢库存去化。", score=0.83):
    return {
        "id": f"{report_id}:{chunk_index}",
        "text": text,
        "metadata": {
            "report_id": report_id,
            "chunk_index": chunk_index,
            "chunk_total": 3,
            "variety": variety,
            "varieties": variety,
            "publish_date": "2026-09-07",
            "report_type": "日报",
            "title": f"报告{report_id}",
            "source": "华泰期货",
            "direction": "看多",
            "text_truncated": False,
        },
        "score": score,
    }


class _FakeLLMClient:
    def get_llm(self):
        class _L:
            def invoke(self, prompt):
                self.last_prompt = prompt

                class _R:
                    content = "库存去化支撑价格 [1]。"

                return _R()

        return _L()


@pytest.fixture
def api():
    return web_app.app.test_client()


# ---------------------------------------------------------------------------
# POST /api/research/ask
# ---------------------------------------------------------------------------
def test_ask_requires_question(api):
    resp = api.post("/api/research/ask", json={"question": "   "})
    assert resp.status_code == 400


def test_ask_disabled_when_deps_missing(api, monkeypatch):
    monkeypatch.setattr(rag_pkg, "is_available", lambda: False)
    resp = api.post("/api/research/ask", json={"question": "螺纹钢怎么看?"})
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["ok"] is False and body["rag_enabled"] is False


def test_ask_success_with_citations(api, monkeypatch):
    captured = {}

    def _fake_retrieve(question, top_k=6, variety=None, report_type=None, exclude_report_id=None):
        captured.update(question=question, top_k=top_k, variety=variety)
        return [_hit(), _hit(report_id=43, variety="CU", chunk_index=1, score=0.71)]

    monkeypatch.setattr(rag_pkg, "is_available", lambda: True)
    monkeypatch.setattr(rag_service, "retrieve_hits", _fake_retrieve)
    monkeypatch.setattr(web_app, "create_llm_client", lambda *a, **k: _FakeLLMClient())
    resp = api.post(
        "/api/research/ask",
        json={"question": "螺纹钢库存怎么看", "variety": "rb", "top_k": 99},
    )
    body = resp.get_json()
    assert resp.status_code == 200 and body["ok"] is True and body["rag_enabled"] is True
    assert body["retrieved"] == 2
    assert "[1]" in body["answer"]
    assert captured["variety"] == "RB"  # 品种参数归一大写
    assert captured["top_k"] == 12  # top_k 钳制到上限
    c1 = body["citations"][0]
    assert c1["no"] == 1 and c1["report_id"] == 42 and c1["title"] == "报告42"
    assert c1["publish_date"] == "2026-09-07" and c1["variety"] == "RB"
    assert c1["snippet"].startswith("螺纹钢库存去化")
    assert c1["score"] == 0.83
    assert body["citations"][1]["no"] == 2
    # format_context 是真函数:编号与标题应进入喂给 LLM 的材料
    # (invoke 侧没断言 prompt,这里补一道格式化产物检查)
    ctx = rag_service.format_context([_hit()])
    assert "[1]" in ctx and "报告42" in ctx


def test_ask_include_context_flag(api, monkeypatch):
    """include_context=true 才随响应返回片段全文(评测脚本用);默认响应保持轻量。"""

    def _fake_retrieve(question, top_k=6, variety=None, report_type=None, exclude_report_id=None):
        return [_hit(text="完整片段" * 50)]

    monkeypatch.setattr(rag_pkg, "is_available", lambda: True)
    monkeypatch.setattr(rag_service, "retrieve_hits", _fake_retrieve)
    monkeypatch.setattr(web_app, "create_llm_client", lambda *a, **k: _FakeLLMClient())
    # 默认:只有 snippet,无 text
    body = api.post("/api/research/ask", json={"question": "螺纹钢"}).get_json()
    assert "text" not in body["citations"][0] and body["citations"][0]["snippet"]
    # 打开:全文返回
    body = api.post(
        "/api/research/ask", json={"question": "螺纹钢", "include_context": True}
    ).get_json()
    assert body["citations"][0]["text"].startswith("完整片段")


def test_ask_no_hits(api, monkeypatch):
    monkeypatch.setattr(rag_pkg, "is_available", lambda: True)
    monkeypatch.setattr(rag_service, "retrieve_hits", lambda *a, **k: [])
    resp = api.post("/api/research/ask", json={"question": "任意"})
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["ok"] is True and body["retrieved"] == 0 and body["citations"] == []
    assert "未检索到" in body["error"]


def test_ask_retrieve_failure_degrades(api, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("chroma down")

    monkeypatch.setattr(rag_pkg, "is_available", lambda: True)
    monkeypatch.setattr(rag_service, "retrieve_hits", _boom)
    resp = api.post("/api/research/ask", json={"question": "任意"})
    body = resp.get_json()
    assert resp.status_code == 200 and body["ok"] is False and body["rag_enabled"] is True


def test_ask_llm_failure_keeps_citations(api, monkeypatch):
    monkeypatch.setattr(rag_pkg, "is_available", lambda: True)
    monkeypatch.setattr(rag_service, "retrieve_hits", lambda *a, **k: [_hit()])

    def _boom(*a, **k):
        raise RuntimeError("429")

    monkeypatch.setattr(web_app, "create_llm_client", _boom)
    resp = api.post("/api/research/ask", json={"question": "任意"})
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["ok"] is False
    assert len(body["citations"]) == 1  # 检索结果仍返回,前端可展示
    assert "生成回答失败" in body["error"]


# ---------------------------------------------------------------------------
# GET /api/research/rag/status
# ---------------------------------------------------------------------------
def test_rag_status_enabled(api, monkeypatch):
    monkeypatch.setattr(rag_pkg, "is_available", lambda: True)
    monkeypatch.setattr(rag_service, "index_status", lambda: {"chunks": 5, "reports": 2})
    body = api.get("/api/research/rag/status").get_json()
    assert body == {"rag_enabled": True, "chunks": 5, "reports": 2}


def test_rag_status_disabled_empty_index(api, monkeypatch):
    monkeypatch.setattr(rag_pkg, "is_available", lambda: True)
    monkeypatch.setattr(rag_service, "index_status", lambda: {"chunks": 0, "reports": 0})
    body = api.get("/api/research/rag/status").get_json()
    assert body["rag_enabled"] is False and body["chunks"] == 0


def test_rag_status_deps_missing(api, monkeypatch):
    monkeypatch.setattr(rag_pkg, "is_available", lambda: False)
    body = api.get("/api/research/rag/status").get_json()
    assert body == {"rag_enabled": False, "chunks": 0, "reports": 0}
