"""Tests for `tradingagents.dataflows.dongzheng_api`(2026-09-08 东证繁微 MCP 客户端)。

网络层打桩:urlopen 返回假 SSE/JSON 流,验证 streamable-http 解析与错误路径;
工具层验证结果提取优先级(structuredContent > text content JSON)。
"""

import io
import json

import pytest

import tradingagents.dataflows.dongzheng_api as dz


class _FakeResp(io.BytesIO):
    """最小 urlopen 返回对象(with 协议 + read)。"""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


@pytest.fixture()
def _no_token(monkeypatch):
    monkeypatch.delenv("FIONA_MCP_TOKEN", raising=False)


@pytest.mark.unit
def test_rpc_parses_sse_and_json(_no_token, monkeypatch):
    """响应自适应:SSE(data: 行)与裸 JSON 都能解析。"""
    sse = 'event: message\r\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":1}}\r\n\r\n'
    monkeypatch.setattr(dz.urllib.request, "urlopen",
                        lambda req, timeout: _FakeResp(sse.encode("utf-8")))
    assert dz._rpc("viewpoint", "tools/list", {}) == {"ok": 1}

    monkeypatch.setattr(dz.urllib.request, "urlopen",
                        lambda req, timeout: _FakeResp(b'{"jsonrpc":"2.0","id":1,"result":{"ok":2}}'))
    assert dz._rpc("viewpoint", "tools/list", {}) == {"ok": 2}


@pytest.mark.unit
def test_rpc_sends_token_header_when_set(_no_token, monkeypatch):
    """配置 FIONA_MCP_TOKEN 时请求须带 Bearer 头(匿名端点兼容)。"""
    monkeypatch.setenv("FIONA_MCP_TOKEN", "tok-123")
    seen = {}

    def fake_urlopen(req, timeout):
        seen["auth"] = req.headers.get("Authorization")
        return _FakeResp(b'{"jsonrpc":"2.0","id":1,"result":{}}')

    monkeypatch.setattr(dz.urllib.request, "urlopen", fake_urlopen)
    dz._rpc("viewpoint", "tools/list", {})
    assert seen["auth"] == "Bearer tok-123"


@pytest.mark.unit
def test_rpc_raises_on_rpc_error(_no_token, monkeypatch):
    monkeypatch.setattr(dz.urllib.request, "urlopen", lambda req, timeout: _FakeResp(
        b'{"jsonrpc":"2.0","id":1,"error":{"code":-32000,"message":"boom"}}'))
    with pytest.raises(RuntimeError, match="boom"):
        dz._rpc("viewpoint", "tools/list", {})


@pytest.mark.unit
def test_rpc_raises_on_network_error(_no_token, monkeypatch):
    def boom(req, timeout):
        raise OSError("conn refused")

    monkeypatch.setattr(dz.urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError, match="请求失败"):
        dz._rpc("viewpoint", "tools/list", {})


@pytest.mark.unit
def test_tools_call_prefers_structured_content(_no_token, monkeypatch):
    monkeypatch.setattr(dz, "_rpc", lambda ep, m, p: {"isError": False, "structuredContent": {"items": [1]}})
    assert dz._tools_call("viewpoint", "x", {}) == {"items": [1]}


@pytest.mark.unit
def test_tools_call_falls_back_to_text_json(_no_token, monkeypatch):
    monkeypatch.setattr(dz, "_rpc", lambda ep, m, p: {
        "isError": False,
        "content": [{"type": "text", "text": json.dumps({"items": [2]})}],
    })
    assert dz._tools_call("viewpoint", "x", {}) == {"items": [2]}


@pytest.mark.unit
def test_tools_call_raises_on_iserror(_no_token, monkeypatch):
    monkeypatch.setattr(dz, "_rpc", lambda ep, m, p: {"isError": True})
    with pytest.raises(RuntimeError, match="isError"):
        dz._tools_call("viewpoint", "x", {})


@pytest.mark.unit
def test_fetch_dynamics_builds_date_args(_no_token, monkeypatch):
    """start/end/limit 落进工具参数;返回 items 列表。"""
    captured = {}

    def fake_call(endpoint, name, args):
        captured.update(args=args, name=name)
        return {"items": [{"source_id": 1}], "total": 1}

    monkeypatch.setattr(dz, "_tools_call", fake_call)
    items = dz.fetch_dynamics("2026-09-07", "2026-09-08", limit=5, offset=2)
    assert items == [{"source_id": 1}]
    assert captured["name"] == "viewpoint_search_dynamics"
    assert captured["args"] == {"limit": 5, "offset": 2,
                                "start_date": "2026-09-07", "end_date": "2026-09-08"}


@pytest.mark.unit
def test_fetch_views_builds_freq_args(_no_token, monkeypatch):
    captured = {}

    def fake_call(endpoint, name, args):
        captured.update(args=args, name=name)
        return {"items": []}

    monkeypatch.setattr(dz, "_tools_call", fake_call)
    assert dz.fetch_views(freq="yearly", limit=7) == []
    assert captured["args"] == {"freq": "yearly", "limit": 7, "offset": 0}
