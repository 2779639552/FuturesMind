"""研报问答流式输出测试(2026-09-22):/api/research/qa/ask?stream=true。

检索与 LLM 全打桩,DB 用内存 fake —— 不碰真库、不打网络。
断言:SSE 事件序列(delta 增量 / done 收尾含 citations)、完整回答落库。
"""
import sys
import json
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import web_app  # noqa: E402


class _Chunk:
    def __init__(self, t, reasoning=None):
        self.content = t
        self.additional_kwargs = {"reasoning_content": reasoning} if reasoning else {}


class _StreamLLM:
    def stream(self, prompt):
        yield _Chunk("", "正在推理 PTA 库存与基差")  # 思考段(空 content, 走 thinking 事件)
        for t in ["这是", "流式", "回答"]:
            yield _Chunk(t)

    def invoke(self, prompt):
        return _Chunk("完整回答")


class _FakeClient:
    def __init__(self, *a, **k):
        pass

    def get_llm(self):
        return _StreamLLM()


class _FakeDB:
    def __init__(self):
        self.inserted = []

    def get_qa_session(self, sid):
        return None

    def create_qa_session(self, **kw):
        return {"id": 7}

    def insert_qa_message(self, sid, role, content, **kw):
        self.inserted.append({"role": role, "content": content, **kw})
        return {"id": 99}

    def update_qa_session(self, *a, **kw):
        pass

    def get_qa_history_for_rewrite(self, *a, **kw):
        return []

    def list_qa_terms(self, *a, **kw):
        return []

    def bump_qa_term_hits(self, *a, **kw):
        pass


def _fake_hits():
    return [{
        "metadata": {"report_id": 1, "title": "测试研报", "publish_date": "2026-09-20",
                     "variety": "TA", "chunk_index": 0, "kind": "body"},
        "text": "PTA 供需偏紧",
        "score": 0.8,
    }]


@pytest.fixture
def qa_env(monkeypatch):
    fake_db = _FakeDB()
    monkeypatch.setattr(web_app, "get_db", lambda: fake_db)
    monkeypatch.setattr(web_app, "create_llm_client", lambda *a, **k: _FakeClient())
    monkeypatch.setattr(web_app, "_rag_call", lambda op, **kw: _fake_hits() if op == "retrieve" else "【片段】")
    monkeypatch.setattr(web_app, "_qa_realtime_data_block", lambda variety, question="": "")
    monkeypatch.setattr(web_app, "_RAG_WORKER_MODE", True)  # 走 _rag_call mock,不碰真库
    client = web_app.app.test_client()
    return client, fake_db


def test_qa_ask_stream_events(qa_env):
    client, fake_db = qa_env
    resp = client.post("/api/research/qa/ask", json={
        "question": "PTA 库存如何",
        "variety": "TA",
        "stream": True,
    })
    assert resp.status_code == 200
    assert "text/event-stream" in (resp.content_type or "")
    body = resp.data.decode("utf-8")
    # status 事件:链路阶段状态(流式前置后 Response 立即返回,prep 阶段可见)
    assert '"status": "改写检索查询…"' in body
    assert '"status": "已检索 1 篇片段,开始生成…"' in body
    # thinking 事件:思考段(reasoning_content)先于正文推送
    assert '"thinking": "正在推理 PTA 库存与基差"' in body
    # delta 事件:流式增量按序出现
    assert '"delta": "这是"' in body
    assert '"delta": "流式"' in body
    assert '"delta": "回答"' in body
    # done 事件:完整回答 + citations
    done_ev = [l for l in body.split("\n\n") if l.startswith("data: ") and '"done"' in l]
    assert done_ev, "缺少 done 事件"
    done = json.loads(done_ev[0][len("data: "):])
    assert done["answer"] == "这是流式回答"
    assert done["citations"] and done["citations"][0]["report_id"] == 1
    assert done["message_id"] == 99
    assert done["session_id"] == 7
    # 完整回答已落库(assistant 轮)
    assert any(m["role"] == "assistant" and m["content"] == "这是流式回答" for m in fake_db.inserted)


def test_qa_ask_stream_fallback_json(qa_env):
    """不带 stream 参数 → 走旧 JSON 契约(invoke 完整回答)。"""
    client, fake_db = qa_env
    resp = client.post("/api/research/qa/ask", json={
        "question": "PTA 库存如何",
        "variety": "TA",
    })
    assert resp.status_code == 200
    d = resp.get_json()
    assert d["ok"] is True
    assert d["answer"] == "完整回答"
    assert not (resp.content_type or "").startswith("text/event-stream")


def test_qa_ask_stream_heartbeat_on_slow_model(monkeypatch):
    """首字前超 4s 无事件 → 推 heartbeat 事件(2026-09-22 端点排队兜底)。"""
    import time

    class _SlowChunk:
        def __init__(self, t):
            self.content = t

    class _SlowStreamLLM:
        def stream(self, prompt):
            time.sleep(5)  # 模拟端点排队: 首字前 5s 无任何事件
            yield _SlowChunk("迟到的回答")

        def invoke(self, prompt):
            return _SlowChunk("完整回答")

    class _SlowClient:
        def __init__(self, *a, **k):
            pass

        def get_llm(self):
            return _SlowStreamLLM()

    fake_db = _FakeDB()
    monkeypatch.setattr(web_app, "get_db", lambda: fake_db)
    monkeypatch.setattr(web_app, "create_llm_client", lambda *a, **k: _SlowClient())
    monkeypatch.setattr(web_app, "_rag_call", lambda op, **kw: _fake_hits() if op == "retrieve" else "【片段】")
    monkeypatch.setattr(web_app, "_qa_realtime_data_block", lambda variety, question="": "")
    monkeypatch.setattr(web_app, "_RAG_WORKER_MODE", True)
    client = web_app.app.test_client()
    resp = client.post("/api/research/qa/ask", json={
        "question": "PTA 库存如何",
        "variety": "TA",
        "stream": True,
    })
    body = resp.data.decode("utf-8")
    assert '"heartbeat": true' in body, "慢模型首字前应推 heartbeat"
    assert '"delta": "迟到的回答"' in body
    assert '"done"' in body
