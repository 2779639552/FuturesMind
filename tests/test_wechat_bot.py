"""微信研报问答 Bot(wechat_bot.py)测试。

web_app QA API 与微信客服接口全部打桩(monkeypatch 模块薄封装),
绝不打真网络;状态文件落到 tmp_path 隔离 data/。
"""

import hashlib
import sys
import types
from collections import OrderedDict
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import wechat_bot as wb  # noqa: E402

TOKEN = "testtoken"
OPENID = "oUSER1"
SELF_ID = "gh_test"


def _sig(token, ts="111", nonce="abc"):
    return hashlib.sha1("".join(sorted([token, ts, nonce])).encode()).hexdigest()


class _Recorder:
    def __init__(self):
        self.calls = []


def _ok_ask_payload():
    return {
        "ok": True,
        "session_id": 77,
        "message_id": 901,
        "answer": "## 核心观点\n**成本支撑**较强,建议偏多 [1]。",
        "citations": [{"no": 1, "report_id": 5, "title": "黑色日报",
                       "publish_date": "2026-09-12", "snippet": "", "score": 0.9}],
        "rewritten_query": "",
        "rag_enabled": True,
    }


@pytest.fixture
def bot(monkeypatch, tmp_path):
    monkeypatch.setattr(wb, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(wb, "WECHAT_TOKEN", TOKEN)
    monkeypatch.setattr(wb, "_ALLOWED_OPENIDS", set())
    # 模块级可变状态:每测试清零
    monkeypatch.setattr(wb, "_MSGID_SEEN", OrderedDict())
    wb._PROCESSING.clear()
    rec = types.SimpleNamespace(asks=_Recorder(), feedbacks=_Recorder(), pushes=_Recorder())

    def fake_ask(question, session_id=0):
        rec.asks.calls.append((question, session_id))
        return _ok_ask_payload()

    def fake_feedback(message_id, feedback):
        rec.feedbacks.calls.append((message_id, feedback))
        return True

    def fake_push(openid, content):
        rec.pushes.calls.append((openid, content))
        return True

    monkeypatch.setattr(wb, "_qa_ask", fake_ask)
    monkeypatch.setattr(wb, "_qa_feedback", fake_feedback)
    monkeypatch.setattr(wb, "push_customer_text", fake_push)
    monkeypatch.setattr(wb, "get_access_token", lambda force_refresh=False: None)
    yield rec
    wb._PROCESSING.clear()
    wb._wait_workers(timeout=5)


@pytest.fixture
def api(bot):
    return wb.app.test_client()


def _xml_body(content, openid=OPENID, msg_id="1001", msg_type="text"):
    content_part = f"<![CDATA[{content}]]>" if msg_type == "text" else ""
    return (
        f"<xml><ToUserName><![CDATA[{SELF_ID}]]></ToUserName>"
        f"<FromUserName><![CDATA[{openid}]]></FromUserName>"
        f"<CreateTime>1700000000</CreateTime>"
        f"<MsgType><![CDATA[{msg_type}]]></MsgType>"
        f"<Content>{content_part}</Content>"
        f"<MsgId>{msg_id}</MsgId></xml>"
    ).encode()


def _event_body(event, openid=OPENID):
    return (
        f"<xml><ToUserName><![CDATA[{SELF_ID}]]></ToUserName>"
        f"<FromUserName><![CDATA[{openid}]]></FromUserName>"
        f"<CreateTime>1700000000</CreateTime>"
        f"<MsgType><![CDATA[event]]></MsgType>"
        f"<Event><![CDATA[{event}]]></Event></xml>"
    ).encode()


def _post(api, body, ts="111", nonce="abc", sig=None):
    return api.post("/wechat", data=body, query_string={
        "signature": sig or _sig(TOKEN, ts, nonce), "timestamp": ts, "nonce": nonce})


def _reply_content(resp):
    root = ET.fromstring(resp.data)
    return root.findtext("Content") or ""


# ---------------------------------------------------------------------------
# 签名与 echo 验证
# ---------------------------------------------------------------------------
def test_check_signature_ok():
    assert wb.check_signature("t", _sig("t"), "111", "abc") is True


def test_check_signature_tampered():
    assert wb.check_signature("t", _sig("other"), "111", "abc") is False


def test_get_echo_verification(api):
    resp = api.get("/wechat", query_string={
        "signature": _sig(TOKEN), "timestamp": "111", "nonce": "abc", "echostr": "ECHO123"})
    assert resp.status_code == 200 and resp.data == b"ECHO123"


def test_get_bad_signature_403(api):
    resp = api.get("/wechat", query_string={
        "signature": "bad", "timestamp": "111", "nonce": "abc", "echostr": "X"})
    assert resp.status_code == 403


def test_post_bad_signature_403(api, bot):
    resp = _post(api, _xml_body("问题"), sig="bad")
    assert resp.status_code == 403 and not bot.asks.calls


# ---------------------------------------------------------------------------
# 事件与非文本消息
# ---------------------------------------------------------------------------
def test_subscribe_event_welcome_reply(api, bot):
    resp = _post(api, _event_body("subscribe"))
    content = _reply_content(resp)
    assert "研报问答助手" in content
    # 收发方互换:ToUserName=粉丝 openid
    root = ET.fromstring(resp.data)
    assert root.findtext("ToUserName") == OPENID
    assert root.findtext("FromUserName") == SELF_ID


def test_unsubscribe_event_ignored(api, bot):
    resp = _post(api, _event_body("unsubscribe"))
    assert resp.data == b"success"


def test_non_text_message_fallback(api, bot):
    resp = _post(api, _xml_body("", msg_type="image", msg_id="2001"))
    assert "仅支持文字" in _reply_content(resp)


def test_malformed_xml_returns_success(api, bot):
    resp = _post(api, b"not-xml")
    assert resp.data == b"success"


# ---------------------------------------------------------------------------
# 提问主链路
# ---------------------------------------------------------------------------
def test_text_happy_path(api, bot):
    resp = _post(api, _xml_body("螺纹钢怎么看", msg_id="3001"))
    assert "已收到" in _reply_content(resp)  # 5 秒内被动 ACK
    wb._wait_workers(timeout=5)
    assert bot.asks.calls == [("螺纹钢怎么看", 0)]  # 首问自动建会话
    pushed = "\n".join(c for _, c in bot.pushes.calls)
    assert "成本支撑" in pushed and "【核心观点】" in pushed  # md 已转纯文本
    assert "引用来源" in pushed and "黑色日报" in pushed
    # 状态落盘
    entry = wb.get_or_create_session(OPENID)
    assert entry == 77
    assert wb.get_last_message(OPENID)["message_id"] == 901


def test_followup_uses_recorded_session(api, bot):
    wb.record_session(OPENID, 77, "旧问题")
    _post(api, _xml_body("那成本端呢", msg_id="3002"))
    wb._wait_workers(timeout=5)
    assert bot.asks.calls == [("那成本端呢", 77)]


def test_duplicate_msgid_suppressed(api, bot):
    body = _xml_body("螺纹钢怎么看", msg_id="3003")
    _post(api, body)
    _post(api, body)  # 微信重试 → 抑制
    wb._wait_workers(timeout=5)
    assert len(bot.asks.calls) == 1


def test_allowlist_rejection(api, bot, monkeypatch):
    monkeypatch.setattr(wb, "_ALLOWED_OPENIDS", {"oFRIEND"})
    resp = _post(api, _xml_body("问题", msg_id="3004"))
    assert "权限" in _reply_content(resp)
    wb._wait_workers(timeout=5)
    assert not bot.asks.calls


def test_busy_guard_rejects_second_question(api, bot):
    wb._PROCESSING[OPENID] = __import__("time").time()  # 预置在途
    resp = _post(api, _xml_body("第二个问题", msg_id="3005"))
    assert "还在检索中" in _reply_content(resp)
    wb._wait_workers(timeout=5)
    assert not bot.asks.calls


def test_empty_text_ignored(api, bot):
    resp = _post(api, _xml_body("   ", msg_id="3006"))
    assert resp.data == b"success"
    wb._wait_workers(timeout=5)
    assert not bot.asks.calls


# ---------------------------------------------------------------------------
# 指令
# ---------------------------------------------------------------------------
def test_help_command(api, bot):
    resp = _post(api, _xml_body("帮助", msg_id="4001"))
    assert "新对话" in _reply_content(resp)
    wb._wait_workers(timeout=5)
    assert not bot.asks.calls


def test_help_latin_case_insensitive(api, bot):
    resp = _post(api, _xml_body("HELP", msg_id="4002"))
    assert "新对话" in _reply_content(resp)


def test_new_dialogue_command_resets_session(api, bot):
    wb.record_session(OPENID, 77, "旧问题")
    resp = _post(api, _xml_body("新对话", msg_id="4003"))
    assert "已开启新对话" in _reply_content(resp)
    assert wb.get_or_create_session(OPENID) == 0


def test_status_command_shows_session_info(api, bot):
    wb.record_session(OPENID, 7, "螺纹钢怎么看")
    resp = _post(api, _xml_body("状态", msg_id="4004"))
    content = _reply_content(resp)
    assert "螺纹钢怎么看" in content and "1" in content


def test_status_command_without_session(api, bot):
    resp = _post(api, _xml_body("状态", msg_id="4005"))
    assert "暂无进行中的对话" in _reply_content(resp)


def test_upvote_calls_feedback(api, bot):
    wb.record_last_message(OPENID, 901)
    resp = _post(api, _xml_body("赞", msg_id="4006"))
    assert "已收到反馈" in _reply_content(resp)
    wb._wait_workers(timeout=5)
    assert bot.feedbacks.calls == [(901, "up")]


def test_downvote_without_prior_answer(api, bot):
    resp = _post(api, _xml_body("踩", msg_id="4007"))
    assert "还没有可评价的回答" in _reply_content(resp)
    wb._wait_workers(timeout=5)
    assert not bot.feedbacks.calls


# ---------------------------------------------------------------------------
# 异步管线异常路径
# ---------------------------------------------------------------------------
def test_qa_unreachable_maps_offline(bot, monkeypatch):
    # _qa_ask 网络异常时返回 qa_unreachable → handle_question 应推 OFFLINE 文案
    monkeypatch.setattr(wb, "_qa_ask",
                        lambda q, session_id=0: {"ok": False, "error": "qa_unreachable"})
    wb.handle_question(OPENID, "螺纹钢怎么看")
    assert bot.pushes.calls[-1][1] == wb.OFFLINE_TEXT


def test_stale_session_retries_fresh(api, bot, monkeypatch):
    calls = bot.asks.calls
    orig_ok = _ok_ask_payload()

    def fake_ask(question, session_id=0):
        calls.append((question, session_id))
        if session_id == 5:  # 失效映射
            return {"ok": False, "error": "会话不存在"}
        return orig_ok

    monkeypatch.setattr(wb, "_qa_ask", fake_ask)
    wb.record_session(OPENID, 5, "旧问题")
    resp = _post(api, _xml_body("螺纹钢怎么看", msg_id="5001"))
    assert "已收到" in _reply_content(resp)
    wb._wait_workers(timeout=5)
    assert calls[0] == ("螺纹钢怎么看", 5)
    assert calls[1] == ("螺纹钢怎么看", 0)
    # 自愈后重新记录新会话
    assert wb.get_or_create_session(OPENID) == 77


def test_no_hits_error_pushed(api, bot, monkeypatch):
    bot.pushes.calls.clear()

    def fake_ask(question, session_id=0):
        bot.asks.calls.append((question, session_id))
        return {"ok": True, "session_id": 1, "message_id": 1,
                "answer": "", "citations": [], "error": "未检索到相关研报"}

    monkeypatch.setattr(wb, "_qa_ask", fake_ask)
    _post(api, _xml_body("冷门品种", msg_id="5002"))
    wb._wait_workers(timeout=5)
    assert len(bot.pushes.calls) == 1
    assert "未检索到" in bot.pushes.calls[0][1]


def test_generation_failure_pushes_friendly_error(api, bot, monkeypatch):
    def fake_ask(question, session_id=0):
        bot.asks.calls.append((question, session_id))
        return {"ok": False, "error": "生成失败(超时)", "message_id": 9}

    monkeypatch.setattr(wb, "_qa_ask", fake_ask)
    _post(api, _xml_body("螺纹钢", msg_id="5003"))
    wb._wait_workers(timeout=5)
    assert "查询失败" in bot.pushes.calls[0][1]


# ---------------------------------------------------------------------------
# 纯函数单测
# ---------------------------------------------------------------------------
def test_split_chunks_short():
    assert wb.split_chunks("短回答") == ["短回答"]


def test_split_chunks_default_limit_is_byte_safe():
    text = "字" * 1000  # 3000 UTF-8 字节,超默认 1000B
    chunks = wb.split_chunks(text)
    assert all(len(c.encode("utf-8")) <= wb.CHUNK_BYTE_LIMIT for c in chunks)
    assert "".join(chunks) == text  # 不丢字


def test_split_chunks_long_packaging():
    paras = [f"para{i} " + "x" * 280 for i in range(5)]  # ASCII,字节=字符
    text = "\n\n".join(paras)
    chunks = wb.split_chunks(text, limit=300)
    assert all(len(c) <= 300 for c in chunks)
    assert "".join(chunks) == text.replace("\n\n", "")
    assert all(c.strip() for c in chunks)


def test_split_chunks_huge_single_paragraph():
    text = "a" * 4000
    chunks = wb.split_chunks(text, limit=1500)
    assert len(chunks) == 3
    assert "".join(chunks) == text


def test_split_chunks_no_multibyte_split():
    text = "螺纹" * 500  # 3000B 单段,硬切不得切破汉字
    chunks = wb.split_chunks(text, limit=1000)
    assert len(chunks) > 1
    for c in chunks:
        c.encode("utf-8")  # 能编码 = 没有残缺字节
    assert "".join(chunks) == text


def test_split_chunks_empty():
    assert wb.split_chunks("") == []
    assert wb.split_chunks(None) == []


def test_md_to_plain_transforms():
    md = (
        "## 核心观点\n"
        "**多头**与*空头*并存\n"
        "| 品种 | 方向 |\n|---|---|\n| 螺纹钢 | 偏多 |\n"
        "[链接](http://example.com)\n"
        "---\n"
        "- 列表项\n"
        "```python\ncode()\n```"
    )
    plain = wb.md_to_plain(md)
    assert "【核心观点】" in plain
    assert "多头" in plain and "**" not in plain and "*" not in plain
    assert "|---|" not in plain and "品种 | 方向" in plain
    assert "链接" in plain and "http" not in plain
    assert "code()" not in plain and "```" not in plain
    assert "- 列表项" in plain


def test_citations_footer_top3():
    cites = [{"no": i, "title": f"报告{i}", "publish_date": "2026-09-12"} for i in range(1, 6)]
    footer = wb.format_citations_footer(cites, top=3)
    assert footer.count("报告") == 3 and "引用来源" in footer
    assert wb.format_citations_footer([]) == ""


def test_build_text_reply_escapes_and_swaps():
    xml = wb.build_text_reply("fan", "gh", "a<b>&c")
    root = ET.fromstring(xml)
    assert root.findtext("ToUserName") == "fan"
    assert root.findtext("FromUserName") == "gh"
    assert root.findtext("Content") == "a<b>&c"


def test_get_access_token_caches(monkeypatch):
    calls = []

    class _Resp:
        @staticmethod
        def json():
            return {"access_token": "TK", "expires_in": 7200}

    def fake_get(*a, **k):
        calls.append(1)
        return _Resp()

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(wb, "WECHAT_APPID", "wxid")
    monkeypatch.setattr(wb, "WECHAT_APPSECRET", "sec")
    monkeypatch.setattr(wb, "_TOKEN_CACHE", {"token": "", "expires_at": 0.0})
    assert wb.get_access_token() == "TK"
    assert wb.get_access_token() == "TK"
    assert len(calls) == 1  # 第二次走缓存


def test_healthz(api):
    resp = api.get("/healthz")
    assert resp.get_json()["ok"] is True
