"""研报问答微信 Bot — 独立进程,公众号测试号通道。

只做三件事:收消息 → HTTP 调 web_app 现有 /api/research/qa/* → 回消息。
不导入 web_app、不直连 chroma/SQLite(web_app 保持唯一数据入口)。

微信回调必须 5 秒内应答:请求线程内只做签名校验 + XML 解析并被动回复 ACK,
QA 检索作答在后台 daemon 线程跑,最终答案经客服消息接口异步推送。

指令:帮助 / 新对话 / 状态 / 赞 / 踩(接通 web 端 QA 反馈信号),其余一律当问题。
"""

import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from xml.etree import ElementTree as ET

import requests
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request

load_dotenv()

logger = logging.getLogger("wechat_bot")

# ---------------------------------------------------------------------------
# 配置(.env;缺 APPID/SECRET 时被动回复仍可用,仅客服消息推送禁用)
# ---------------------------------------------------------------------------
WECHAT_TOKEN = os.environ.get("WECHAT_TOKEN", "")
WECHAT_APPID = os.environ.get("WECHAT_APPID", "")
WECHAT_APPSECRET = os.environ.get("WECHAT_APPSECRET", "")
BOT_PORT = int(os.environ.get("WECHAT_BOT_PORT", "5001"))
QA_BASE_URL = os.environ.get("WECHAT_QA_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
# 逗号分隔 openid 白名单;为空 = 允许所有关注者
_ALLOWED_OPENIDS = {
    s.strip() for s in os.environ.get("WECHAT_ALLOWED_OPENIDS", "").split(",") if s.strip()
}

QA_HTTP_TIMEOUT = 1000        # 覆盖 web_app 侧最坏 300s×重试 的作答耗时
FEEDBACK_HTTP_TIMEOUT = 30
CHUNK_BYTE_LIMIT = 1000       # 客服消息文本按字节限长:分片以 UTF-8 字节计,1000B 留足余量
PROCESSING_TIMEOUT_S = 900    # 与 QA 最坏耗时对齐,防忙碌标志卡死

STATE_PATH = Path(__file__).resolve().parent / "data" / "wechat_bot_state.json"
_WX_API = "https://api.weixin.qq.com"

WELCOME_TEXT = (
    "你好,我是研报问答助手~\n"
    "直接发送问题即可检索研报库作答,例如:螺纹钢近期供需怎么看\n\n"
    "发送「帮助」查看全部指令。"
)
HELP_TEXT = (
    "研报问答助手·用法\n"
    "· 直接发问题,如:螺纹钢近期供需怎么看\n"
    "· 支持追问,自动结合上文检索\n"
    "· 新对话 - 开启新一轮会话\n"
    "· 状态 - 查看当前会话\n"
    "· 赞/踩 - 评价上一条回答(帮我们改进)\n"
    "· 回答通常 1~2 分钟,引用来源附在末尾"
)
ACK_TEXT = "已收到,正在检索研报库,请稍候…(通常 1~2 分钟)"
BUSY_TEXT = "上一个问题还在检索中,请稍候,回答送达后可继续提问~"
DENY_TEXT = "您暂无使用权限,请联系管理员"
UNSUPPORTED_TEXT = "暂仅支持文字提问,请直接输入您想问的问题"
OFFLINE_TEXT = "研报服务暂时无法连接,请稍后再试"
NO_PRIOR_ANSWER_TEXT = "还没有可评价的回答~"
FEEDBACK_ACK_TEXT = "已收到反馈,谢谢!"
NEW_SESSION_TEXT = "已开启新对话,请直接提问~"

# ---------------------------------------------------------------------------
# Markdown → 纯文本(微信文本消息不渲染 markdown)
# ---------------------------------------------------------------------------
_FENCE_RE = re.compile(r"```[^\n]*\n.*?\n\s*```", re.S)
_INLINE_CODE_RE = re.compile(r"`([^`]*)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.S)
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)\s]*\)")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*(.+?)\s*#*\s*$")
_TABLE_SEP_RE = re.compile(r"^\s{0,3}\|?[\s:|-]*-[\s:|-]*\|")
_TABLE_ROW_RE = re.compile(r"^\s{0,3}\|(.+)\|?\s*$")
_HR_RE = re.compile(r"^\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*$")


def _strip_md_inline(s):
    s = _INLINE_CODE_RE.sub(r"\1", s)
    s = _BOLD_RE.sub(lambda m: m.group(1) or m.group(2) or "", s)
    s = _ITALIC_RE.sub(r"\1", s)
    s = _LINK_RE.sub(r"\1", s)
    return s


def md_to_plain(text):
    """标题→【】,去粗体/斜体/代码围栏/表格分隔行/水平线,链接留文字,压缩空行。"""
    if not text:
        return ""
    s = _FENCE_RE.sub("", text)
    out = []
    for line in s.split("\n"):
        if _TABLE_SEP_RE.match(line) or _HR_RE.match(line):
            continue
        m = _HEADING_RE.match(line)
        if m:
            line = f"【{_strip_md_inline(m.group(1))}】"
        else:
            m2 = _TABLE_ROW_RE.match(line)
            if m2:
                cells = [c.strip() for c in m2.group(1).split("|")]
                line = " | ".join(c for c in cells if c)
            else:
                line = _strip_md_inline(line)
        out.append(line.rstrip())
    s = "\n".join(out)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def split_chunks(text, limit=CHUNK_BYTE_LIMIT):
    """按 UTF-8 字节数打包空行段落到 limit 内;超长段落按行切,再不行硬切(不切破多字节字符)。"""
    text = (text or "").strip()
    if not text:
        return []

    def _blen(s):
        return len(s.encode("utf-8"))

    if _blen(text) <= limit:
        return [text]
    chunks = []
    current = ""
    for para in text.split("\n\n"):
        para = para.strip("\n").rstrip()
        if not para:
            continue
        candidate = f"{current}\n\n{para}" if current else para
        if _blen(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        while _blen(para) > limit:
            raw = para.encode("utf-8")[:limit]
            cut = raw.rfind(b"\n")  # 尽量在行界断
            if cut <= 0:
                cut = limit
            piece = None
            while cut > 0:  # 回退到可解码边界,不切破多字节字符
                try:
                    piece = raw[:cut].decode("utf-8")
                    break
                except UnicodeDecodeError:
                    cut -= 1
            if piece is None:
                break
            chunks.append(piece.strip())
            para = para[len(piece):].strip()
        if para:
            current = para
    if current:
        chunks.append(current)
    return chunks


def format_citations_footer(citations, top=3):
    """引用来源尾注,取前 top 条;空引用返回空串。"""
    if not citations:
        return ""
    lines = ["", "———", "引用来源:"]
    for c in citations[:top]:
        title = (c.get("title") or "").strip() or "未命名报告"
        date = (c.get("publish_date") or "").strip()
        lines.append(f"[{c.get('no', '')}] {title} ({date})" if date else f"[{c.get('no', '')}] {title}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 状态文件(openid→会话映射 + 最后一条可评价回答;原子写,单进程内加锁)
# ---------------------------------------------------------------------------
_STATE_LOCK = threading.Lock()


def _empty_state():
    return {"sessions": {}, "last_message": {}}


def _load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return _empty_state()
    except (OSError, ValueError):
        logger.warning("状态文件损坏,已重置: %s", STATE_PATH)
        return _empty_state()
    if not isinstance(data, dict):
        return _empty_state()
    data.setdefault("sessions", {})
    data.setdefault("last_message", {})
    return data


def _save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_name(STATE_PATH.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)


def _update_state(mutate):
    with _STATE_LOCK:
        state = _load_state()
        mutate(state)
        _save_state(state)


def get_or_create_session(openid):
    """返回已映射的会话 id;无映射返回 0(web_app 侧 ask 会自动建会话)。"""
    with _STATE_LOCK:
        entry = _load_state()["sessions"].get(openid)
    return int(entry.get("session_id", 0)) if entry else 0


def record_session(openid, session_id, title):
    def _m(state):
        entry = state["sessions"].setdefault(openid, {"session_id": 0, "title": "", "turns": 0})
        entry["session_id"] = int(session_id or 0)
        entry["title"] = title
        entry["turns"] = int(entry.get("turns", 0)) + 1

    _update_state(_m)


def reset_session(openid):
    def _m(state):
        state["sessions"].pop(openid, None)

    _update_state(_m)


def clear_session_mapping(openid):
    """「会话不存在」自愈路径:清掉失效映射,下次提问自动新建。"""
    reset_session(openid)


def record_last_message(openid, message_id):
    def _m(state):
        state["last_message"][openid] = {"message_id": int(message_id), "ts": time.time()}

    _update_state(_m)


def get_last_message(openid):
    with _STATE_LOCK:
        return _load_state()["last_message"].get(openid)


# ---------------------------------------------------------------------------
# MsgId 去重(微信超时重试会重复推送同一条消息)+ 每用户忙碌标志
# ---------------------------------------------------------------------------
_MSGID_SEEN = OrderedDict()
_MSGID_LOCK = threading.Lock()
_MSGID_CAP = 256


def is_duplicate_msgid(msg_id):
    if not msg_id:
        return False
    key = str(msg_id)
    with _MSGID_LOCK:
        if key in _MSGID_SEEN:
            _MSGID_SEEN.move_to_end(key)
            return True
        _MSGID_SEEN[key] = True
        while len(_MSGID_SEEN) > _MSGID_CAP:
            _MSGID_SEEN.popitem(last=False)
        return False


_PROCESSING = {}
_PROCESSING_LOCK = threading.Lock()


def try_acquire(openid):
    """每用户同时在途一个问题;标志带 PROCESSING_TIMEOUT_S 过期自愈。"""
    now = time.time()
    with _PROCESSING_LOCK:
        start = _PROCESSING.get(openid)
        if start and now - start < PROCESSING_TIMEOUT_S:
            return False
        _PROCESSING[openid] = now
        return True


def release(openid):
    with _PROCESSING_LOCK:
        _PROCESSING.pop(openid, None)


# ---------------------------------------------------------------------------
# QA HTTP 客户端(薄封装,测试在此处打桩,绝不打真网络)
# ---------------------------------------------------------------------------
def _qa_ask(question, session_id=0):
    try:
        resp = requests.post(
            f"{QA_BASE_URL}/api/research/qa/ask",
            json={"question": question, "session_id": session_id},
            timeout=QA_HTTP_TIMEOUT,
        )
        return resp.json()
    except (requests.RequestException, ValueError):
        logger.exception("QA API 调用失败: %s", QA_BASE_URL)
        return {"ok": False, "error": "qa_unreachable"}


def _qa_feedback(message_id, feedback):
    try:
        resp = requests.post(
            f"{QA_BASE_URL}/api/research/qa/feedback",
            json={"message_id": message_id, "feedback": feedback},
            timeout=FEEDBACK_HTTP_TIMEOUT,
        )
        return resp.ok
    except requests.RequestException:
        logger.exception("feedback 上报失败: message_id=%s", message_id)
        return False


# ---------------------------------------------------------------------------
# access_token 缓存 + 客服消息推送(异步答案的唯一出口)
# ---------------------------------------------------------------------------
_TOKEN_CACHE = {"token": "", "expires_at": 0.0}
_TOKEN_LOCK = threading.Lock()


def get_access_token(force_refresh=False):
    if not WECHAT_APPID or not WECHAT_APPSECRET:
        return None
    now = time.time()
    with _TOKEN_LOCK:
        if not force_refresh and _TOKEN_CACHE["token"] and now < _TOKEN_CACHE["expires_at"]:
            return _TOKEN_CACHE["token"]
    try:
        resp = requests.get(
            f"{_WX_API}/cgi-bin/token",
            params={"grant_type": "client_credential",
                    "appid": WECHAT_APPID, "secret": WECHAT_APPSECRET},
            timeout=15,
        )
        data = resp.json()
    except (requests.RequestException, ValueError):
        logger.exception("获取 access_token 失败")
        return None
    token = data.get("access_token")
    if not token:
        logger.error("获取 access_token 失败: %s", data)
        return None
    with _TOKEN_LOCK:
        _TOKEN_CACHE["token"] = token
        _TOKEN_CACHE["expires_at"] = time.time() + int(data.get("expires_in", 7200)) - 300
    return token


def push_customer_text(openid, content):
    token = get_access_token()
    if not token:
        logger.error("无 access_token,推送失败(未配置 APPID/APPSECRET?)")
        return False
    payload = {"touser": openid, "msgtype": "text", "text": {"content": content}}
    # requests 的 json= 默认 ensure_ascii=True,微信会原样显示 \uXXXX 转义并按转义后长度判超长
    # → 必须手发原始 UTF-8 JSON
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    for attempt in (1, 2):
        try:
            resp = requests.post(
                f"{_WX_API}/cgi-bin/message/custom/send",
                params={"access_token": token}, data=body,
                headers={"Content-Type": "application/json; charset=utf-8"}, timeout=15,
            )
            data = resp.json()
        except (requests.RequestException, ValueError):
            logger.exception("客服消息推送失败 openid=%s", openid)
            return False
        errcode = data.get("errcode", 0)
        if errcode == 0:
            return True
        # token 失效:强刷一次再试
        if errcode in (40001, 42001) and attempt == 1:
            token = get_access_token(force_refresh=True)
            if not token:
                return False
            continue
        logger.error("客服消息推送失败 errcode=%s errmsg=%s", errcode, data.get("errmsg"))
        return False
    return False


# ---------------------------------------------------------------------------
# 后台线程池(daemon;_wait_workers 供测试同步)
# ---------------------------------------------------------------------------
_WORKERS = []
_WORKERS_LOCK = threading.Lock()


def spawn_worker(fn, *args):
    def _run():
        try:
            fn(*args)
        except Exception:
            logger.exception("后台任务异常")

    t = threading.Thread(target=_run, daemon=True)
    with _WORKERS_LOCK:
        _WORKERS.append(t)
    t.start()
    return t


def _wait_workers(timeout=15):
    with _WORKERS_LOCK:
        threads = list(_WORKERS)
    for t in threads:
        t.join(timeout=timeout)
    with _WORKERS_LOCK:
        _WORKERS[:] = [t for t in _WORKERS if t.is_alive()]


def _friendly_error(error):
    error = error or "未知错误"
    if error == "qa_unreachable":
        return OFFLINE_TEXT
    return f"查询失败:{error}\n请稍后重试或换个问法"


def handle_question(openid, question):
    """异步 QA 全流程:ask →(失效会话自愈)→ md 转换 → 分片推送。"""
    try:
        session_id = get_or_create_session(openid)
        result = _qa_ask(question, session_id)
        # 网页端删掉会话导致映射失效 → 清映射、从零重试一次
        if not result.get("ok") and "会话不存在" in str(result.get("error", "")):
            clear_session_mapping(openid)
            result = _qa_ask(question, 0)
        if not result.get("ok"):
            push_customer_text(openid, _friendly_error(result.get("error")))
            return
        answer = (result.get("answer") or "").strip()
        if not answer:
            # ok:true + 空 answer = 未检索到命中(web_app 约定),error 里带说明
            push_customer_text(openid, result.get("error") or "研报库中暂未检索到相关内容,换个问法试试~")
            return
        message_id = result.get("message_id")
        if message_id:
            record_last_message(openid, message_id)  # 接通「赞/踩」反馈
        record_session(openid, result.get("session_id", 0), question[:30])
        plain = md_to_plain(answer) + format_citations_footer(result.get("citations"))
        chunks = split_chunks(plain)
        for i, chunk in enumerate(chunks, 1):
            text = f"({i}/{len(chunks)}) {chunk}" if len(chunks) > 1 else chunk
            if not push_customer_text(openid, text):
                # 末路兜底(如 45002 超长):推截断版,至少让用户拿到开头结论
                logger.error("答案推送失败,走截断兜底 openid=%s chunk=%s/%s", openid, i, len(chunks))
                push_customer_text(openid, text[:400] + "\n…(内容过长已截断,完整回答见网页端)")
                break
    finally:
        release(openid)


# ---------------------------------------------------------------------------
# 微信协议层
# ---------------------------------------------------------------------------
def check_signature(token, signature, timestamp, nonce):
    if not token or not signature:
        return False
    digest = hashlib.sha1("".join(sorted([token, timestamp, nonce])).encode("utf-8")).hexdigest()
    return digest == signature


def parse_wechat_xml(raw):
    """扁平化为 {子标签: 文本};非 XML 返回 None。"""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None
    return {child.tag: (child.text or "") for child in root}


def build_text_reply(from_user, to_user, content):
    """被动回复文本消息:收发方互换(CDATA 内无需转义,但 ']]>' 需规避)。"""
    content = content.replace("]]>", "]] >")
    return (
        "<xml>"
        f"<ToUserName><![CDATA[{from_user}]]></ToUserName>"
        f"<FromUserName><![CDATA[{to_user}]]></FromUserName>"
        f"<CreateTime>{int(time.time())}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{content}]]></Content>"
        "</xml>"
    )


_COMMANDS = {
    "帮助": "help", "help": "help", "命令": "help", "菜单": "help",
    "新对话": "new", "new": "new", "重新开始": "new", "换话题": "new",
    "状态": "status", "当前会话": "status", "status": "status",
    "赞": "up", "👍": "up",
    "踩": "down", "👎": "down",
}


def match_command(text):
    """识别指令;非指令返回 ("qa", 原文)。拉丁指令不区分大小写。"""
    key = (text or "").strip()
    if not key:
        return "", ""
    for candidate in (key, key.lower()):
        if candidate in _COMMANDS:
            return _COMMANDS[candidate], key
    return "qa", key


def _status_text(openid):
    with _STATE_LOCK:
        entry = _load_state()["sessions"].get(openid)
    if not entry:
        return "暂无进行中的对话,直接提问即可~"
    return (f"当前会话:{entry.get('title') or '(无标题)'}\n"
            f"已轮数:{entry.get('turns', 0)}\n发送「新对话」可开启新会话")


app = Flask(__name__)


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True, "port": BOT_PORT, "qa_base_url": QA_BASE_URL})


@app.route("/wechat", methods=["GET", "POST"])
def wechat():
    """微信回调入口:GET=URL 验证(echostr 原样回),POST=消息/事件推送。"""
    args = request.args
    if not check_signature(WECHAT_TOKEN, args.get("signature", ""),
                           args.get("timestamp", ""), args.get("nonce", "")):
        return "invalid signature", 403
    if request.method == "GET":
        return Response(args.get("echostr", ""), mimetype="text/plain")
    return _handle_callback(request.get_data())


def _reply_xml(content):
    return Response(content, mimetype="application/xml")


def _handle_callback(data):
    msg = parse_wechat_xml(data)
    if msg is None:
        return "success"
    msg_type = (msg.get("MsgType") or "").strip()
    openid = msg.get("FromUserName") or ""
    self_id = msg.get("ToUserName") or ""

    if msg_type == "event":
        if (msg.get("Event") or "").strip() == "subscribe":
            return _reply_xml(build_text_reply(openid, self_id, WELCOME_TEXT))
        return "success"
    if msg_type != "text":
        return _reply_xml(build_text_reply(openid, self_id, UNSUPPORTED_TEXT))

    content = (msg.get("Content") or "").strip()
    if not content:
        return "success"
    if _ALLOWED_OPENIDS and openid not in _ALLOWED_OPENIDS:
        return _reply_xml(build_text_reply(openid, self_id, DENY_TEXT))
    if is_duplicate_msgid(msg.get("MsgId")):
        return "success"

    cmd, _arg = match_command(content)
    if cmd == "help":
        return _reply_xml(build_text_reply(openid, self_id, HELP_TEXT))
    if cmd == "new":
        reset_session(openid)
        return _reply_xml(build_text_reply(openid, self_id, NEW_SESSION_TEXT))
    if cmd == "status":
        return _reply_xml(build_text_reply(openid, self_id, _status_text(openid)))
    if cmd in ("up", "down"):
        entry = get_last_message(openid)
        if not entry:
            return _reply_xml(build_text_reply(openid, self_id, NO_PRIOR_ANSWER_TEXT))
        spawn_worker(_qa_feedback, entry["message_id"], cmd)
        return _reply_xml(build_text_reply(openid, self_id, FEEDBACK_ACK_TEXT))

    # 其余一律当提问:忙碌则拒绝,否则后台跑 QA、5 秒内先 ACK
    if not try_acquire(openid):
        return _reply_xml(build_text_reply(openid, self_id, BUSY_TEXT))
    spawn_worker(handle_question, openid, content)
    return _reply_xml(build_text_reply(openid, self_id, ACK_TEXT))


if __name__ == "__main__":
    from waitress import serve

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logger.info(
        "wechat_bot 启动: port=%s qa=%s allowlist=%s push_credentials=%s",
        BOT_PORT, QA_BASE_URL, bool(_ALLOWED_OPENIDS),
        bool(WECHAT_APPID and WECHAT_APPSECRET),
    )
    serve(app, host="0.0.0.0", port=BOT_PORT, threads=16, channel_timeout=30)
