#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""公众号采集 · 微信读书通道（2026-10-14 落地）

============================== 为什么需要这个通道 ==============================
机构号（中信期货研究 / 混沌天成研究 / 中信建投期货研究 等）**不建合集**，
所以 wechat_album_collector.py 的「合集接口」通道对它们完全不适用（空数组）。
而微信公众号对未登录抓取是封闭的 —— 但**微信读书收录了这些号**（可加入书架），
于是存在一条合法的旁路：

    GET https://weread.qq.com/web/mp/articles?bookId=MP_WXS_xxx&offset=N    ← 文章清单
    GET https://weread.qq.com/web/mp/content?reviewId=<bid>_<originalId>    ← 正文 + 原文直链

这两个接口**不是**官方 Agent API（那个的 17 个接口都列不出公众号文章，实测），
而是微信读书网页版自己用的私域接口，需要**网页登录态 cookie**（wr_vid/wr_skey/wr_rt），
扫码一次即可（见 probe/_weread_login.mjs）。

============================== 关键事实（实测，别踩） ==============================
1. **回包是两层结构，文章藏在第二层**：
     顶层 {reviews[], clearAll, synckey}
     reviews[i]            = 一次「群发」（多篇一起推），有 createTime/subCount
     reviews[i].subReviews[j].review.mpInfo = 单篇文章的元数据！
       · title      标题
       · content    摘要（如"铜、铝、锌、锡、镍、不锈钢、碳酸锂"）
       · time       发布时间戳
       · originalId 文章原始 id
       · readNum/likeNum/pic_url
   实测一个号 offset=0 返回 20 个「群发」，展开后是多篇（subCount 之和）。
   ⚠️ 只读 reviews[i] 会**漏掉子文章**，必须展开 subReviews。

2. **正文接口回包 3.5MB**，正文有两处可提，且有**原文永久直链**：
     · content_noencode: '...'   → \x0a\x22 转义的原文（首选，抗 DOM 改版）
     · id="js_content"           → HTML（回退）
     · https://mp.weixin.qq.com/s/<token>  → 原文直链（回包内自带）

3. **必须带 cookie**，且**不能用官方 API Key 顶替**（实测伪装成 wr_skey 头也返回
   `{"errCode":-2010,"errMsg":"用户不存在"}`）。
   ★ `wr_skey` 只是 **90 分钟**的会话密钥（`Max-Age=5400`）；`wr_vid`/`wr_rt` 是 360 天长期凭据。
     **每次采集前必须调 `POST /web/login/renewal` 续期**，否则 12 小时一轮的调度必然
     撞 `-2012 登录超时`（2026-10-19 定案：这不是"登录过期"，重扫码也只能管 90 分钟）。

4. 与 wechat_album_collector **共用同一个 inbox**（见下方 F_INBOX）⇒ 下游
   `wechat_ingest.py` 与 scheduler 的 wechat_ingest 任务**零改动**即可消费。

============================== 用法 ==============================
  python wechat_weread_collector.py --discover            # 从书架发现公众号并注册
  python wechat_weread_collector.py --list                # 看注册表 + cookie 状态
  python wechat_weread_collector.py                       # 默认 --daily 增量追踪
  python wechat_weread_collector.py --backfill 20         # 每号回填最近 20 篇
  python wechat_weread_collector.py --account 混沌天成研究 --backfill 5
  python wechat_weread_collector.py --no-content          # 只列清单不抓正文（快）
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import io
import json
import os
import random
import re
import sys
import threading
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent

# ── 状态目录 ────────────────────────────────────────────────────────────
# WEREAD_DIR: 本通道自己的状态（cookie/注册表/水位线）
# WECHAT_DIR: **与合集通道共用**（inbox 必须一致，否则下游 ingest 收不到）
WEREAD_DIR = Path(os.path.expanduser(
    (os.environ.get("AGENTSENSE_WEREAD_DIR") or "").strip())
    or (Path.home() / ".tradingagents" / "weread"))
WECHAT_DIR = Path(os.path.expanduser(
    (os.environ.get("AGENTSENSE_WECHAT_DIR") or "").strip())
    or (Path.home() / ".tradingagents" / "wechat"))

F_COOKIES = WEREAD_DIR / "cookies.json"
F_REGISTRY = WEREAD_DIR / "mp_accounts.json"
F_STATE = WEREAD_DIR / "mp_seen.json"
F_RESULT = WEREAD_DIR / "mp_collect_result.json"
F_TXT = WEREAD_DIR / "mp_collect_result.txt"

# ★ 关键：追加到**与合集通道同一个** inbox，下游 wechat_ingest 原样消费
F_INBOX = WECHAT_DIR / "wechat_inbox.jsonl"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
BASE = "https://weread.qq.com"
PAGE_SIZE = 20          # 实测接口固定返回 20 个「群发」
MIN_BODY_CHARS = 200    # 与 ingest.py 同阈值：正文不足则标 text_ok=False

_log: list[str] = []


def say(s: str = "") -> None:
    print(s, flush=True)
    _log.append(s)


# ============================================================ cookie / 会话
# 【2026-10-19 实测定案】`wr_skey` 是**服务端会话密钥，Max-Age=5400（90 分钟）**；
#   `wr_vid` / `wr_rt` 是长期凭据（360 天，且每次续期都被再延到 360 天）。
#   旧实现只带静态 cookie、从不续期 ⇒ 12 小时一轮的调度**必然**在下一轮撞
#   `-2012 登录超时`。这不是"登录过期"——重扫码也只能管 90 分钟。
#   修法：采集前调 `POST /web/login/renewal`，并把服务端下发的新 cookie 写回。
_RENEWAL_PATH = "/web/login/renewal"
_SESSION = None


def _read_json_tolerant(p: Path, tries: int = 3, delay: float = 0.2):
    """读 JSON：先重试拿**最新版**，仍失败再回滚 `.bak`；都救不回来才报错。

    【为什么顺序是"先重试后回滚"】重试能覆盖"写入瞬间读到 0 字节"的毫秒级窗口——
      这时 `.bak` 是**上一版**，直接回滚会丢掉刚写进去的最新数据。
      只有重试也读不到，才认定为真损坏，交给 `atomic_io.load_json` 自愈。
    """
    last: Exception | None = None
    for _ in range(max(1, tries)):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            last = exc
            time.sleep(delay)

    # ── 真损坏：从 .bak 回滚（并修好主文件）──
    healed: list[str] = []
    try:
        from tradingagents.atomic_io import load_json as _atomic_load

        return _atomic_load(
            p, on_heal=lambda _path, why: healed.append(why),
        )
    except Exception:  # noqa: BLE001 - 落到下面统一报错
        pass

    raise SystemExit(
        f"✘ 读取失败：{p}\n"
        f"   {type(last).__name__}: {str(last)[:120]}\n"
        "   文件可能是空文件/半截 JSON（并发写或历史损坏），且 .bak 回滚也失败。"
        "确认后删除该文件并重跑扫码：node probe/_weread_login.mjs")


def _load_cookie_list() -> list[dict]:
    if not F_COOKIES.exists():
        raise SystemExit(
            f"✘ 未找到 cookie：{F_COOKIES}\n"
            "  先跑一次扫码登录：node probe/_weread_login.mjs")
    j = _read_json_tolerant(F_COOKIES)
    cks = j.get("cookies") or []
    names = {c.get("name") for c in cks}
    # ★ 续期只认长期凭据（实测：把 wr_skey 整个删掉仍能续期成功）
    #   ⇒ 不再把 wr_skey 当必要条件，否则"密钥过期"会被误报成"未登录、去扫码"。
    if not {"wr_vid", "wr_rt"} <= names:
        raise SystemExit(
            "✘ cookie 缺少长期凭据（需要 wr_vid + wr_rt）\n"
            f"  文件：{F_COOKIES}\n"
            "  重跑：node probe/_weread_login.mjs")
    return cks


def _session() -> requests.Session:
    """惰性创建并**全程复用** Session。

    ★ 必须复用：续期拿到的 `wr_skey` 存在 Session 的 cookie jar 里，
      每次新建 Session 会让新密钥带不到后续请求（自动续期静默失效）。
    """
    global _SESSION
    if _SESSION is None:
        s = requests.Session()
        for c in _load_cookie_list():
            s.cookies.set(c.get("name"), str(c.get("value")),
                          domain=c.get("domain") or ".weread.qq.com",
                          path=c.get("path") or "/")
        _SESSION = s
    return _SESSION


def _headers() -> dict:
    # ★ Cookie 由 Session 统一携带（续期会改写它）。这里**不要再拼 Cookie 头**，
    #   否则旧密钥会覆盖新密钥，自动续期看起来"成功"但下一次请求照样 -2012。
    return {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
            "Referer": BASE + "/"}


def _persist_session_cookies() -> None:
    """把 Session 里的新 cookie 写回 cookies.json（供下次运行/其他工具复用）。"""
    j = _load_json(F_COOKIES, {}) or {}
    old = {c.get("name"): c for c in (j.get("cookies") or []) if c.get("name")}
    out, seen = [], set()
    for c in _session().cookies:
        if c.domain and "weread" not in c.domain:
            continue
        item = dict(old.get(c.name) or {})
        item.update({"name": c.name, "value": c.value,
                     "domain": c.domain or item.get("domain") or ".weread.qq.com",
                     "path": c.path or "/"})
        if c.expires:
            item["expires"] = int(c.expires)
        out.append(item)
        seen.add(c.name)
    for n, c in old.items():          # 保住 Session 未覆盖的条目（如 wr_fp）
        if n not in seen:
            out.append(c)
    j["cookies"] = out
    now = _dt.datetime.now().isoformat(timespec="seconds")
    j.setdefault("savedAt", now)
    j["renewedAt"] = now
    _save_json(F_COOKIES, j)
    _mark_disk_synced()


# ── 会话密钥轮换：服务端可能在任何响应里换掉 wr_skey ──────────────────────────
#   requests.Session 会把 Set-Cookie 收进内存 jar，但**磁盘副本不会自动更新**
#   ⇒ 下次进程启动又拿旧值（这正是"重登后 1.5 小时又失效"的直接成因）。
#   这里每发一次请求就比对一次指纹，变了立刻写回。
_DISK_FP = None


def _jar_value(name: str) -> str:
    for c in _session().cookies:
        if c.name == name and (not c.domain or "weread" in c.domain):
            return str(c.value)
    return ""


def _jar_fingerprint() -> str:
    parts = sorted("%s=%s@%s" % (c.name, c.value, c.domain or "") for c in _session().cookies)
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()


def _disk_fingerprint() -> str:
    try:
        j = json.loads(F_COOKIES.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ""
    parts = sorted("%s=%s@%s" % (c.get("name"), c.get("value"), c.get("domain") or "")
                   for c in (j.get("cookies") or []))
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()


def _mark_disk_synced() -> None:
    global _DISK_FP
    _DISK_FP = _jar_fingerprint()


def _sync_cookies_if_changed() -> bool:
    """检测服务端是否轮换了会话 cookie；有变化就写回文件。

    返回 True 表示发生了轮换并已落盘。
    """
    global _DISK_FP
    if _DISK_FP is None:
        _DISK_FP = _disk_fingerprint()
    if _jar_fingerprint() == _DISK_FP:
        return False
    old_skey = (_disk_json_skey() or "")[:6]
    _persist_session_cookies()          # 内部会 _mark_disk_synced()
    new_skey = _jar_value("wr_skey")
    if new_skey and new_skey[:6] != old_skey:
        say("   ↻ 检测到 wr_skey 被服务端轮换，已写回 cookies.json（旧 %s… → 新 %s…）"
            % (old_skey or "-", new_skey[:6]))
    else:
        say("   ↻ 检测到 cookie 变化，已写回 cookies.json")
    return True


def _disk_json_skey() -> str:
    try:
        j = json.loads(F_COOKIES.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ""
    for c in (j.get("cookies") or []):
        if c.get("name") == "wr_skey":
            return str(c.get("value") or "")
    return ""


def renew_session(*, quiet: bool = False) -> bool:
    """续期会话密钥。成功返回 True，并把新 cookie 写回文件。

    【为什么不扫码也能恢复】`wr_rt` 是刷新令牌，只要它还在，
      `/web/login/renewal` 就能换出新的 `wr_skey`。
      实测：连续调用幂等；缺 `wr_skey`、缺 `Origin` 头都能成功。
    """
    try:
        r = _session().post(BASE + _RENEWAL_PATH, headers=_headers(), timeout=30)
        d = r.json()
    except Exception as e:  # noqa: BLE001
        if not quiet:
            say("   ⚠ 续期请求异常：%s" % str(e)[:120])
        return False
    try:
        ok = int(d.get("succ") or 0) == 1
    except (TypeError, ValueError):
        ok = False
    if not ok:
        if not quiet:
            say("   ⚠ 续期未成功：%s（连续失败 ⇒ 长期凭据也失效，需重新扫码）" % str(d)[:120])
        return False
    _persist_session_cookies()
    return True


# ── 错误码分层（2026-10-21 止血）─────────────────────────────────────────
#   ★ 这三条码**不是一回事**，混在一起就会误判：
#       -2010 / -2012 = 会话不可用（过期/未登录）→ 本地续期**有可能**救回来；
#       -2041         = 会话有效、但**这一条接口**拒绝 → 续期救不了，重试更救不了。
#   实测证据（2026-10-21，同一套 cookie）：
#       /web/shelf/sync  → 200，416 本真实书（匿名是 -2010/0 本）⇒ 确为登录态
#       /web/mp/cover    → 200，正常返回最新一篇
#       /web/mp/articles → 恒 -2041（换 2 个号、3 组参数、5 种请求头都一样）
#       /web/mp/articles（匿名）→ -2010
#   ⇒ 上一版把 -2041 也当"长期凭据失效"，让用户去扫码，那是**误报**。
_SESSION_CODES = frozenset({-2010, -2012})
_ENDPOINT_REFUSED = frozenset({-2041})
_AUTH_CODES = _SESSION_CODES | _ENDPOINT_REFUSED   # 兼容既有调用点


class AuthError(RuntimeError):
    """会话真的不可用（续期后仍被拒）—— 这类才值得让用户重新扫码。"""


class EndpointRefused(RuntimeError):
    """接口在有登录态的前提下拒绝服务。

    【为什么不许继承 SystemExit】SystemExit 会**穿透** collect_account 的
      `except Exception`，把整轮采集打断（实测：第一个号失败后其余 5 个号连试都没试）。
      这里用普通异常，让上层能"单号降级、继续下一个号"。
    """


# ── 自动续期预算 ──
#   第一版没有任何预算：`_get` 每次撞鉴权码就再续期一次，而 `fetch_articles` 自己还重试 4 次
#   ⇒ 一轮能续十几次。对账号只会更糟（越续越像机器人）。
_MAX_AUTO_RENEWS = 1
_AUTO_RENEWS = 0
_RENEW_LOCK = threading.Lock()


def _auto_renew() -> bool:
    """领取一次自动续期额度；用完就不再续（每进程最多 `_MAX_AUTO_RENEWS` 次）。"""
    global _AUTO_RENEWS
    with _RENEW_LOCK:
        if _AUTO_RENEWS >= _MAX_AUTO_RENEWS:
            return False
        _AUTO_RENEWS += 1
        return True


def _auth_code(r: requests.Response):
    """只在**小回包**上解析 errCode（正文接口回 3.5MB HTML，解析会白炸一次）。"""
    if len(r.content) > 4096:
        return None
    try:
        d = r.json()
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(d, dict):
        return None
    code = d.get("errCode")
    if code is None:
        code = d.get("errcode")
    try:
        return int(code)
    except (TypeError, ValueError):
        return None


def _get(path: str, params: dict, timeout: int = 40) -> requests.Response:
    """统一请求入口：**只有会话过期**才自动续期并重试一次。

    -2041 刻意不在这里处理：它不是会话问题，续期与重试都无效，
      只会把账号往风控里推。交给 `fetch_articles` 抛 `EndpointRefused`，由上层降级。
    """
    r = _session().get(BASE + path, params=params, headers=_headers(), timeout=timeout)
    code = _auth_code(r)
    if code in _SESSION_CODES and _auto_renew() and renew_session(quiet=True):
        # ★ `_auto_renew()` 只是额度闸，**不是**续期动作本身。
        #   第一版把它写在原来 `renew_session(...)` 的位置上，等于"扣了额度却不续期"
        #   —— 真撞 -2012 时会被判成 AuthError（测试 test_2012_renews_at_most_once 抓到）。
        say("   ↻ 会话已过期（errCode=%s），续期后重试一次" % code)
        r = _session().get(BASE + path, params=params, headers=_headers(), timeout=timeout)
    # ★ 每次请求后都检查：服务端可能在这个响应里顺手轮换了 wr_skey
    _sync_cookies_if_changed()
    return r



# ============================================================ 注册表/水位线
def _load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _save_json(p: Path, obj) -> None:
    """原子写 JSON（临时文件 + fsync + os.replace）**并保留上一版 `.bak`**。

    【为什么必须原子（2026-10-23 根因）】旧实现 `io.open(p, "w")` 会**先把文件截断为
      0 字节**再写内容。在这个窗口内任何并发读都会拿到空文件/半截 JSON，`json.loads`
      抛 `JSONDecodeError: Expecting value: line 1 column 1 (char 0)`；而该异常在采集器
      里没有被捕获 ⇒ 整轮以**退出码 1** 结束，`mp_collect_result.json` 不写，
      调度器只能读到上一轮的旧 `new`。

    实测证据：10-22 18:30 与 10-23 08:30 **连续两轮**都是退出码 1（失败后 60s 自动重试
    也失败）；而人工复跑 86 秒、26 篇、退出码 0 —— 说明数据源与凭据都正常，
    坏的就是这个"写一半被读到"的窗口。

    实现统一走 `tradingagents.atomic_io.save_json`（与项目其他落盘同一份逻辑）。
    """
    from tradingagents.atomic_io import save_json as _atomic_save

    _atomic_save(p, obj, indent=2, backup=True)


def load_registry() -> dict:
    return _load_json(F_REGISTRY, {"accounts": [], "updated": ""})


def load_state() -> dict:
    return _load_json(F_STATE, {"accounts": {}, "updated": ""})


# ============================================================ 书架发现
def discover_from_shelf() -> list[dict]:
    """从 /web/shelf/sync 里把公众号条目（bookId 以 MP_WXS_ 开头）挖出来。

    ★ 前提：用户已在微信读书 App / 网页把号**加入书架**。
      微信读书把公众号存成 `MP_WXS_<数字>`，author 恰为 "公众号"。

    ⚠️ **路径必须是 `/web/shelf/sync`**（2026-10-14 实测）：
       · `/web/shelf/sync`  ✅ 返回 JSON（books[] 含 MP_WXS_ 条目）
       · `/shelf/sync`       ❌ 私域下不是 JSON（那是 Agent Gateway 的接口名，
                                走 gateway 要 API Key，与本通道 cookie 体系不同）
       · `/api/shelf/sync`   ❌ 404
       · `/web/shelf`        ❌ 返回的是 HTML 页面
    """
    r = _get("/web/shelf/sync", {}, timeout=60)
    try:
        d = r.json()
    except Exception as e:  # noqa: BLE001
        raise SystemExit(
            f"✘ /web/shelf/sync 非 JSON（HTTP {r.status_code}, {len(r.content)} bytes）："
            f"{str(e)[:80]}\n  多半是 cookie 失效 → 重跑 node probe/_weread_login.mjs")
    if d.get("errcode") or d.get("errCode"):
        raise SystemExit(f"✘ /web/shelf/sync 失败：{d}")
    out = []
    for b in (d.get("books") or []):
        bid = str(b.get("bookId") or "")
        if bid.startswith("MP_WXS_") or b.get("author") == "公众号":
            out.append({"name": b.get("title") or bid, "book_id": bid,
                        "cover": b.get("cover") or "",
                        "intro": b.get("intro") or ""})
    return out


# ============================================================ 文章清单
def expand_articles(payload: dict, bid: str) -> list[dict]:
    """把 `/web/mp/articles` 的回包**展开成扁平文章列表**。纯函数，便于单测。

    ⚠️ 回包是**两层**结构，这是最容易做错的地方（2026-10-14 实测）：
         {reviews[]}                       ← 第一层：一次「群发」
         reviews[i].subReviews[j].review   ← 第二层：才是单篇文章
         reviews[i].subReviews[j].review.mpInfo = {title, content, time, originalId, ...}
       **只读 reviews[i] 会漏掉子文章** —— 一次群发常见 3~8 篇（subCount 字段）。
       实测「混沌天成研究」offset=0 的 20 个群发展开后是 105 篇文章。
    """
    flat: list[dict] = []
    for rv in (payload.get("reviews") or []):
        push_t = int(rv.get("createTime") or 0)
        for sr in (rv.get("subReviews") or []):
            inner = sr.get("review") or {}
            mp = inner.get("mpInfo") or {}
            oid = str(mp.get("originalId") or "")
            if not oid:
                continue
            flat.append({
                "original_id": oid,
                "review_id": str(inner.get("reviewId") or sr.get("reviewId")
                                 or f"{bid}_{oid}"),
                "title": (mp.get("title") or "").strip(),
                "summary": (mp.get("content") or "").strip(),
                "time": int(mp.get("time") or push_t or 0),
                "push_time": push_t,
                "mp_rank": int(inner.get("mpRank") or 0),
                "read_num": int(mp.get("readNum") or 0),
                "like_num": int(mp.get("likeNum") or 0),
                "pic_url": mp.get("pic_url") or "",
                "mp_name": mp.get("mp_name") or "",
            })
    return flat


def fetch_articles(bid: str, offset: int, retries: int = 4) -> list[dict]:
    """取一页「群发」并展开成扁平文章列表（**主通道**）。

    【2026-10-19 更正】10-16 曾判定本接口"已被平台废弃（恒返 -2041）"——**那是误判**。
      真因是同一个 `wr_skey` 90 分钟过期问题：续期后本接口正常返回
      （实测 offset=0 → 101 篇、offset=20 → 109 篇、两页零重叠）。
      ⇒ 恢复为取清单的主通道；`/web/mp/cover` 降级为兜底。

    保留原说明：该接口偶发**静默空回包**（HTTP 200 但 reviews 为空）⇒ 退避重试。
    """
    last_err = ""
    for attempt in range(retries):
        try:
            r = _get("/web/mp/articles", {"bookId": bid, "offset": offset}, timeout=40)
            d = r.json()
        except Exception as e:  # noqa: BLE001
            last_err = str(e)[:100]
            time.sleep(0.6 + attempt * 0.5)
            continue

        code = d.get("errCode") or d.get("errcode")
        if code:
            # ★ 分码处理（2026-10-21）：-2041 说明会话是好的，只是这条接口不给。
            if str(code) in {str(x) for x in _ENDPOINT_REFUSED}:
                raise EndpointRefused(
                    "列表接口拒绝 errCode=%s（会话有效，不是登录过期）" % code)
            if str(code) in {str(x) for x in _SESSION_CODES}:
                raise AuthError(
                    "列表接口 errCode=%s %s（续期后仍被拒）\n"
                    "   ⇒ 会话确实不可用，请重跑：node probe/_weread_login.mjs（扫码一次）"
                    % (code, d.get("errMsg") or ""))
            last_err = "errCode=%s" % code
            time.sleep(0.8 + attempt * 0.7)
            continue

        flat = expand_articles(d, bid)
        if flat:
            return flat
        last_err = "空回包(第 %d 次)" % (attempt + 1)
        time.sleep(0.8 + attempt * 0.7)
    say("      ⚠ offset=%d 重试 %d 次仍为空：%s" % (offset, retries, last_err))
    return []


# ============================================================ 最新一篇（cover · 兜底）

# 【2026-10-19 降级为兜底】列表接口 `/web/mp/articles` 已恢复可用（此前 -2041 是密钥过期）。
#   `cover` 只在列表为空时兜底：它只给最新一篇，且不含 time/summary。
_COVER_ERR_HINT = ("-2010", "-2012", "-2041")

_HTML_SP = None


def _norm_title(s: str) -> str:
    """标题归一化：去空白与常见标点、转小写 ⇒ 用于**跨格式**兜底去重。"""
    import re as _re
    return _re.sub(r"[\s\-—–_|｜:：·、,，.。()（）\[\]【】“”\"'']+", "", str(s or "")).lower()


_TITLE_CACHE: set | None = None


def known_titles() -> set:
    """inbox 里已出现过的 (号名, 归一化标题)。

    【为什么需要它】`mp_seen.json` 里的 keys 是**旧格式** originalId
    （旧链路写的，形如 `WR15NwFYq2Rp…`），而新版是 `6sbTBEdZjNgzAAhHNw~-2A`
    这类不透明 id ⇒ 同一篇文章不会命中 seen_keys ⇒ 会在过渡期重复入库。
    故补一层标题去重，把这段窗口挡住。
    """
    global _TITLE_CACHE
    if _TITLE_CACHE is not None:
        return _TITLE_CACHE
    s: set = set()
    try:
        if F_INBOX.exists():
            with io.open(F_INBOX, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except Exception:  # noqa: BLE001
                        continue
                    s.add((str(e.get("account") or ""), _norm_title(e.get("title") or "")))
    except Exception:  # noqa: BLE001
        pass
    _TITLE_CACHE = s
    return s


def _guess_date(title: str, text: str = "") -> str:
    """从标题/正文首部猜发布日期 → 'YYYY-MM-DD'；猜不到返回 ''。

    cover **不返回发布时间**，所以只能猜。证据形态（本项目实测）：
      · 标题尾带 8 位数   恒力期货日报[20261016]
      · 标题带连字符日期  …利润修复[2026-10-12]
      · 正文开头中文日期  [2026年10月16日] / 2026年10月11日
    """
    import re as _re
    for src in (str(title or ""), str(text or "")[:400]):
        m = _re.search(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", src)
        if m:
            y, mo, d = (int(x) for x in m.groups())
        else:
            m2 = _re.search(r"(?<![0-9])(20\d{2})(\d{2})(\d{2})(?![0-9])", src)
            if not m2:
                continue
            y, mo, d = (int(x) for x in m2.groups())
        try:
            if 2000 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
                return "%04d-%02d-%02d" % (y, mo, d)
        except Exception:  # noqa: BLE001
            pass
    return ""


def fetch_latest(bid: str, retries: int = 4) -> list[dict]:
    """取该号**最新一篇**（/web/mp/cover）。返回扁平列表（0 或 1 项）。

    字段与 `expand_articles` **同构** ⇒ 下游（build_entry / collect_account）零改动。
    ⚠ cover 不返回 time/summary/read_num ⇒ 置 0/空（下游不许据此做增量判定）。
    """
    last_err = ""
    for attempt in range(retries):
        try:
            r = _get("/web/mp/cover", {"bookId": bid}, timeout=40)
            d = r.json()
        except Exception as e:  # noqa: BLE001
            last_err = str(e)[:100]
            time.sleep(0.6 + attempt * 0.5)
            continue

        code = d.get("errCode") or d.get("errcode")
        if code:
            if str(code) in _COVER_ERR_HINT:
                # 兜底通道也失败：抛普通异常，让上层记成"本号本次无产出"，
                # 不要因为一个号把整轮（6 个号）打断。
                raise AuthError(
                    "cover 报错 errCode=%s %s" % (code, d.get("errMsg") or ""))
            last_err = "errCode=%s" % code
            time.sleep(0.8 + attempt * 0.7)
            continue

        rid = str(d.get("reviewId") or "")
        if not rid:
            last_err = "回包里没有 reviewId"
            time.sleep(0.8 + attempt * 0.7)
            continue

        # ★ 去重键推导：reviewId = "{bookId}_{originalId}"
        oid = rid.split(bid + "_", 1)[1] if (bid + "_") in rid else rid
        return [{
            "original_id": oid,
            "review_id": rid,
            "title": str(d.get("title") or ""),
            "summary": "",
            "time": 0,
            "read_num": 0,
            "like_num": 0,
            "pic_url": str(d.get("pic") or ""),
            "mp_name": str(d.get("name") or ""),
            "_via": "cover",
        }]

    say(f"      ⚠ cover 重试 {retries} 次仍失败：{last_err}")
    return []


# ============================================================ 正文
_HTML_ENTS = (("&nbsp;", " "), ("&amp;", "&"), ("&quot;", '"'),
              ("&lt;", "<"), ("&gt;", ">"), ("&#39;", "'"))


def _unescape_ents(s: str) -> str:
    for a, b in _HTML_ENTS:
        s = s.replace(a, b)
    return s


def _decode_noencode(raw: str) -> str:
    """解 `content_noencode` 的 JS 转义串（\\x0a 换行、\\x22 双引号 …）并去标签。

    ⚠️ 顺序敏感：`\\x5c`（反斜杠）必须**最后**还原，否则会把后面才处理的转义
       序列又吐出来一层（例：`\\x5cx0a` 应先变 `\\x0a` 再变换行）。
    """
    s = raw
    for esc, rep in ((r"\x0a", "\n"), (r"\x22", '"'), (r"\x27", "'"),
                     (r"\x26", "&"), (r"\x3c", "<"), (r"\x3e", ">")):
        s = s.replace(esc, rep)
    s = s.replace(r"\x5c", "\\")          # 反斜杠放最后
    s = re.sub(r"<[^>]+>", "", s)
    s = _unescape_ents(s)
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def _decode_html(frag: str) -> str:
    """解 `#js_content` 的 HTML 片段成纯文本（保留段落换行）。"""
    s = re.sub(r"<br\s*/?>", "\n", frag)
    s = re.sub(r"</p>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = _unescape_ents(s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def parse_content_html(html: str) -> dict:
    """从正文页 HTML 里抽出 (正文, 原文链接, 标题)。**纯函数，便于单测。**

    提取优先级（**顺序别改**，2026-10 实测）：
      ① content_noencode: '...'  —— 原文以 \\x0a \\x22 转义嵌在 JS 变量里，抗 DOM 改版
      ② id="js_content"          —— HTML 正文，回退方案

    ★ 原文链接必须取 `var msg_link`（该文自己的永久链接，形如
      https://mp.weixin.qq.com/s?__biz=..&mid=..&idx=<篇序>&sn=<hash>）。
      ⚠️ **不要**用页面里那个 `https://mp.weixin.qq.com/s/<token>` —— 实测同一次群发的
         5 篇会**全部命中同一个 token**（那是模板里的固定链接，不是本文的），
         会把 5 篇写成同一个 URL（2026-10-14 踩过，见 tests 里的回归）。
    """
    # ── 原文直链 ────────────────────────────────────────────────────
    url = ""
    m = re.search(r'var\s+msg_link\s*=\s*"([^"]+)"', html)
    if m:
        url = m.group(1).replace("&amp;", "&").strip()
    if not url:
        # 回退：msg_link 缺失时才用短链形式（可能不精确）
        cands = re.findall(r"https?://mp\.weixin\.qq\.com/s/[A-Za-z0-9_\-]{16,}", html)
        url = cands[0] if cands else ""

    # ── 标题 ────────────────────────────────────────────────────────
    title = ""
    m = re.search(r"var\s+msg_title\s*=\s*['\"](.*?)['\"]", html)
    if m:
        title = m.group(1)
    else:
        m = re.search(r'<h1[^>]*class="rich_media_title"[^>]*>(.*?)</h1>', html, re.S)
        if m:
            title = re.sub(r"<[^>]+>", "", m.group(1)).strip()

    # ── 正文 ①：content_noencode ────────────────────────────────────
    # 【判据统一】`ok` 与 `how` 必须**同一个阈值**：正文不足 MIN_BODY_CHARS
    #   一律算未提取（否则短公告会被记成"成功"，把汇总的成功率虚高 ——
    #   这是本函数第一版的 bug，被 tests 里的 test_too_short_body_marked_not_ok 抓到）。
    text, how, raw_chars = "", "", 0

    m = re.search(r"content_noencode:\s*'([^']*)'", html)
    if m:
        cand = _decode_noencode(m.group(1))
        raw_chars = max(raw_chars, len(cand))
        if len(cand) >= MIN_BODY_CHARS:
            text, how = cand, "content_noencode"

    # ── 正文 ②：js_content 回退（仅当前一档没拿到够长正文时）────────
    if not text:
        m = re.search(r'id="js_content"[^>]*>(.*?)</div>\s*(?:<div|<script)', html, re.S)
        if m:
            cand = _decode_html(m.group(1))
            raw_chars = max(raw_chars, len(cand))
            if len(cand) >= MIN_BODY_CHARS:
                text, how = cand, "js_content"

    return {"ok": bool(text), "text": text, "url": url, "title": title,
            "chars": len(text), "raw_chars": raw_chars, "how": how or "未提取到",
            "err": "" if text else f"正文不足({raw_chars} 字 < {MIN_BODY_CHARS})"}


def fetch_content(review_id: str, retries: int = 3) -> dict:
    """取正文（带重试）。回包 3.5MB 左右，故 timeout 给足。"""
    last_err = ""
    for attempt in range(retries):
        try:
            r = _get("/web/mp/content", {"reviewId": review_id}, timeout=60)
            html = r.content.decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last_err = str(e)[:100]
            time.sleep(0.6 + attempt * 0.5)
            continue

        if len(html) < 2000:
            last_err = f"回包过短({len(html)} bytes)"
            time.sleep(0.8 + attempt * 0.7)
            continue

        got = parse_content_html(html)
        if got["ok"]:
            return got
        return got

    return {"ok": False, "text": "", "url": "", "title": "", "chars": 0,
            "how": "", "err": last_err or "失败"}


# ============================================================ 关键：抓一篇 → inbox 条目
def build_entry(acct_name: str, art: dict, content: dict, bid: str) -> dict:
    """组装成 **与合集通道同构** 的 inbox 条目。

    字段契约（对齐 wechat_ingest.py）：
      key/perm_key  稳定去重键（ingest 用它做幂等）
      account       号名 ⇒ ingest 直接用作来源标签（source = "公众号-<号名>"）
      title/date/url/text  ingest 的四个必读字段
    """
    ts = int(art.get("time") or 0)
    date = (_dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "")
    if not date:
        # cover 不返回发布时间 ⇒ 从标题/正文猜；猜不到用**采集日**兜底。
        # ⚠ 兜底值**不写进 create_time**（见下），否则会把水位线推高、污染增量判定。
        date = (_guess_date(art.get("title") or "", content.get("text") or "")
                or _dt.datetime.now().strftime("%Y-%m-%d"))
    key = f"wxmp_{art['original_id']}"
    return {
        "key": key,
        "perm_key": key,
        "account": acct_name,
        "album": "微信读书通道",
        "title": art.get("title") or content.get("title") or "(无标题)",
        "date": date,
        "create_time": ts,
        "url": content.get("url") or "",
        "text": content.get("text") or "",
        "chars": int(content.get("chars") or 0),
        "text_ok": bool(content.get("ok")),
        "summary": art.get("summary") or "",
        "read_num": art.get("read_num") or 0,
        "like_num": art.get("like_num") or 0,
        "pic_url": art.get("pic_url") or "",
        "book_id": bid,
        "review_id": art.get("review_id") or "",
        "how": content.get("how") or "",
    }


# ============================================================ 采集
def collect_account(acct: dict, state: dict, *, mode: str, limit: int,
                    with_content: bool, max_pages: int) -> list[dict]:
    name, bid = acct["name"], acct["book_id"]
    seen = state.setdefault("accounts", {}).setdefault(bid, {"keys": [], "last_time": 0})
    seen_keys = set(seen.get("keys") or [])
    last_time = int(seen.get("last_time") or 0)

    say(f"\n── {name}  ({bid})")
    say(f"   水位线: 已知 {len(seen_keys)} 篇, last_time={last_time or '(无)'}")

    # 【2026-10-19 恢复多篇枚举】`/web/mp/articles` 实测可用（此前 -2041 是密钥过期所致）
    #   ⇒ 主通道翻页取清单；`/web/mp/cover` 仅在"列表完全无数据"时兜底。
    #   停止条件：daily 命中一整页旧文（已到水位线）；backfill 取满 limit 篇。
    picked: list[dict] = []
    known_t = known_titles()
    picked_ids: set[str] = set()
    total_listed = pages = 0
    first_run = not seen_keys
    # ★ daily 必须有界：articles 能列出**全部历史**，不设限就会把上千篇旧文当新文
    #   （2026-10-19 实测：无界时一次要抓 1051 篇）。两道闸：
    #     ① 时间闸 —— 列表按时间降序，早于水位线（last_time）的直接跳过；
    #        整页最旧那条 <= 水位线 ⇒ 后面只会更旧，停止翻页。
    #     ② 数量闸 —— daily 最多 max(limit, 60) 篇；backfill 取满 limit。
    cutoff = 0 if first_run else last_time
    cap = limit if mode == "backfill" else (limit if first_run else max(limit, 60))

    degraded = ""            # "" / articles_refused / auth_failed
    try:
      for page in range(max(1, max_pages)):
        arts = fetch_articles(bid, offset=page * PAGE_SIZE)
        if not arts:
            break
        pages += 1
        total_listed += len(arts)
        fresh = skipped_old = 0
        for a in arts:
            oid = a["original_id"]
            ts = int(a.get("time") or 0)
            # 时间闸：`ts=0` 时不做判断（宁可按 id 去重，也不误杀）
            if cutoff and ts and ts <= cutoff:
                skipped_old += 1
                continue
            if oid in seen_keys or oid in picked_ids:
                continue
            # ★ 跨格式兜底：旧链路写进 seen 的是旧格式 id ⇒ 再按标题挡一道
            if (name, _norm_title(a["title"])) in known_t:
                continue
            picked.append(a)
            picked_ids.add(oid)
            fresh += 1
            if len(picked) >= cap:
                break
        oldest = int(arts[-1].get("time") or 0)
        say("   第%d页 offset=%-4d %3d 篇（新 %3d / 早于水位线 %3d）"
            % (page + 1, page * PAGE_SIZE, len(arts), fresh, skipped_old))
        if len(picked) >= cap or len(arts) < PAGE_SIZE:
            break
        # 整页最旧那条已在水位线之前 ⇒ 后续页只会更旧
        if cutoff and oldest and oldest <= cutoff:
            say("   ← 已翻到水位线，停止翻页")
            break
        if mode == "daily" and fresh == 0:
            break
    except EndpointRefused as exc:
        degraded = "articles_refused"
        say("   ⚠ %s" % exc)
        say("   ⇒ 本号降级：改用 /web/mp/cover 只取最新 1 篇（覆盖率下降，但不是 0）")
    except AuthError as exc:
        degraded = "auth_failed"
        say("   ✘ %s" % exc)
    except Exception as exc:  # noqa: BLE001 - 单号异常不许打断整轮
        degraded = "error"
        say("   ✘ 采集异常：%s: %s" % (type(exc).__name__, str(exc)[:120]))

    # ⚠ 不要在这里落 `_degraded`：兜底失败会把 degraded 再拼一段（+cover_failed），
    #   提前落盘会丢掉后半段（测试 test_collect_account_survives_cover_auth_error 抓到）。

    if total_listed == 0:
        say("   ⚠ 列表通道无数据 ⇒ 回退 /web/mp/cover 取最新一篇")
        # ★ 兜底同样要包 try：第一版只包了翻页循环，结果 cover 一报错
        #   （AuthError / EndpointRefused）照样穿透 collect_account，整轮还是被打断。
        try:
            for a in fetch_latest(bid):
                if a["original_id"] in seen_keys or a["original_id"] in picked_ids:
                    say("   最新一篇已采过（key 命中）: %s" % a["title"][:46])
                    continue
                if (name, _norm_title(a["title"])) in known_t:
                    say("   最新一篇已采过（标题命中·跨格式兜底）: %s" % a["title"][:46])
                    continue
                picked.append(a)
                picked_ids.add(a["original_id"])
        except (AuthError, EndpointRefused) as exc:
            # ★ 用可组合标记：列表被拒 + 兜底也挂 ≠ 只有一种问题。
            #   写成 `degraded or "auth_failed"` 会把前面那个原因盖掉，
            #   排障时就看不到"其实是列表先被拒、兜底又失败"。
            degraded = (degraded + "+cover_failed") if degraded else "cover_failed"
            say("   ✘ 兜底通道也失败：%s" % exc)
        except Exception as exc:  # noqa: BLE001 - 单号异常不许打断整轮
            degraded = (degraded + "+error") if degraded else "error"
            say("   ✘ 兜底通道异常：%s: %s" % (type(exc).__name__, str(exc)[:100]))

    acct["_degraded"] = degraded

    say("   ⇒ 本轮取到 %d 篇新文（翻 %d 页 / 列出 %d 篇）"
        % (len(picked), pages, total_listed))
    if degraded:
        say("   ⚠ 降级模式（%s）：**只覆盖最新 1 篇**，同一天更早发布的文章本轮取不到"
            % degraded)

    if not with_content:
        for a in picked:
            a["_content"] = {"ok": False, "text": "", "url": "", "title": "", "chars": 0,
                             "how": "跳过(no-content)", "err": ""}
        return picked

    for i, a in enumerate(picked, 1):
        c = fetch_content(a["review_id"])
        a["_content"] = c
        # 失败时显示 **raw_chars**（尽力提了多少），否则一律 0 看不出"短"还是"没提到"
        n = c["chars"] if c["ok"] else (c.get("raw_chars") or 0)
        say(f"   [{i}/{len(picked)}] {'★' if c['ok'] else '✗'} {a['title'][:40]:42s} "
            f"{n:5d} 字  [{c['how']}]"
            + ("" if c["ok"] else f"  原因: {c['err']}"))
        time.sleep(random.uniform(1.5, 2.8))   # 礼貌间隔，别把接口打急
    return picked


# ============================================================ main
def main() -> int:
    ap = argparse.ArgumentParser(description="公众号采集 · 微信读书通道")
    ap.add_argument("--discover", action="store_true", help="从书架发现公众号并写入注册表")
    ap.add_argument("--list", action="store_true", help="显示注册表与 cookie 状态")
    ap.add_argument("--daily", action="store_true", help="增量追踪（默认）")
    ap.add_argument("--backfill", type=int, metavar="N",
                    help="每号回填最近 N 篇（按 originalId 幂等去重）")
    ap.add_argument("--account", help="只跑某个号（按号名匹配）")
    ap.add_argument("--no-content", action="store_true", help="只列清单，不抓正文")
    ap.add_argument("--max-pages", type=int, default=10, help="每号最多翻页数(默认10=约200个群发)")
    ap.add_argument("--dry-run", action="store_true", help="不写 inbox，只打印")
    args = ap.parse_args()

    mode = "backfill" if args.backfill else "daily"
    limit = args.backfill or 20
    if args.backfill:
        say("回填模式：每号最多取 %d 篇（列表通道 /web/mp/articles 已恢复翻页）"
            % args.backfill)
        say("")

    say("=" * 78)
    say("公众号采集 · 微信读书通道")
    say("=" * 78)

    # ── --list ────────────────────────────────────────────────────
    if args.list:
        reg = load_registry()
        st = load_state()
        say(f"\n注册表 {F_REGISTRY}")
        accts = reg.get("accounts") or []
        if not accts:
            say("  （空 —— 先跑 --discover）")
        for a in accts:
            s = (st.get("accounts") or {}).get(a["book_id"]) or {}
            say(f"  · {a['name']:22s} {a['book_id']:20s} "
                f"已见 {len(s.get('keys') or []):4d} 篇  水位线 {s.get('last_time') or '-'}")
        say(f"\ncookie: {F_COOKIES}  存在={F_COOKIES.exists()}")
        if F_COOKIES.exists():
            j = json.loads(F_COOKIES.read_text(encoding="utf-8"))
            say(f"  保存于 {j.get('savedAt')}  names={[c['name'] for c in j.get('cookies') or []]}")
        say(f"inbox : {F_INBOX}")
        say("")
        say("取清单通道: /web/mp/articles（翻页·主）  /web/mp/cover（兜底）")
        say("会话密钥  : wr_skey 仅 90 分钟 ⇒ 每轮采集前自动调 /web/login/renewal 续期")
        say("去重键    : wxmp_{originalId}  +  (号名, 标题) 跨格式兜底")
        return 0

    # ── --discover ────────────────────────────────────────────────
    if args.discover:
        found = discover_from_shelf()
        say(f"\n书架里发现 {len(found)} 个公众号：")
        reg = load_registry()
        known = {a["book_id"]: a for a in (reg.get("accounts") or [])}
        for f in found:
            tag = "已注册" if f["book_id"] in known else "★新增"
            say(f"  [{tag}] {f['name']:24s} {f['book_id']}")
            known[f["book_id"]] = f
        reg["accounts"] = list(known.values())
        reg["updated"] = _dt.datetime.now().isoformat(timespec="seconds")
        _save_json(F_REGISTRY, reg)
        say(f"\n✔ 注册表已写 → {F_REGISTRY}")
        return 0

    # ── 采集 ──────────────────────────────────────────────────────
    reg = load_registry()
    accts = reg.get("accounts") or []
    if not accts:
        say("\n✘ 注册表为空。先跑：python wechat_weread_collector.py --discover")
        return 2
    if args.account:
        accts = [a for a in accts if a["name"] == args.account]
        if not accts:
            say(f"\n✘ 注册表里没有号：{args.account}")
            return 2

    # ── 会话续期（★ 必须在任何采集请求之前）────────────────────────
    say("")
    say("检查会话：")
    if not renew_session():
        time.sleep(2)
        if not renew_session():
            say("   ✘ 两次续期都失败 ⇒ 长期凭据（wr_vid/wr_rt）也失效了")
            say("     请扫码：node probe/_weread_login.mjs")
            return 3
    say("   ✔ wr_skey 已刷新（有效期 90 分钟，足够跑完本轮）")

    state = load_state()
    all_new: list[dict] = []
    for acct in accts:
        try:
            got = collect_account(acct, state, mode=mode, limit=limit,
                                  with_content=not args.no_content,
                                  max_pages=args.max_pages)
            all_new.extend((acct, a) for a in got)
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001
            say(f"\n   ✗ {acct['name']} 采集异常：{str(e)[:160]}")

    # ── 组装 + 落盘 ───────────────────────────────────────────────
    entries: list[dict] = []
    for acct, a in all_new:
        try:
            entries.append(build_entry(acct["name"], a, a.get("_content") or {}, acct["book_id"]))
        except Exception as e:  # noqa: BLE001
            # 【2026-10-23】单条坏数据不许拖垮整轮：旧实现裸调 build_entry，
            #   一条字段缺失（降级到 cover 后结构与 articles 不同）就会让整轮
            #   以未捕获异常退出，结果文件不写、已采到的 26 篇全丢。
            say(f"   ✗ 组装失败 [{acct.get('name')}] {str(a.get('title'))[:30]}："
                f"{type(e).__name__}: {str(e)[:80]}")

    degraded_accts = [a.get("name") for a in accts if a.get("_degraded")]

    okc = sum(1 for e in entries if e["text_ok"])
    say("")
    say("=" * 78)
    say(f"【汇总】新增 {len(entries)} 篇（正文成功 {okc} / 失败 {len(entries) - okc}）")
    if degraded_accts:
        say("   ⚠ 降级 %d/%d 个号（列表接口被拒）：%s"
            % (len(degraded_accts), len(accts), "、".join(degraded_accts)))
        say("     降级号本轮只取到最新 1 篇 —— 缺的是覆盖范围，不是正文失败。")
    for e in sorted(entries, key=lambda x: -int(x.get("create_time") or 0)):
        say(f"   {e['date']}  [{e['account']}]  {e['title'][:44]:46s} {e['chars'] or '-':>5}字")

    if entries and not args.dry_run:
        # 更新水位线 + 已知 key（**在写 inbox 之后**才算数，避免写失败却标记已完成）
        for acct, _ in all_new:
            pass
        for e in entries:
            s = state.setdefault("accounts", {}).setdefault(
                e["book_id"], {"keys": [], "last_time": 0})
            if e["key"] not in (s["keys"] or []):
                s["keys"] = (s["keys"] or []) + [e["key"]]
            s["last_time"] = max(int(s.get("last_time") or 0), int(e.get("create_time") or 0))
        state["updated"] = _dt.datetime.now().isoformat(timespec="seconds")
        # 水位线只保留最近 3000 个 key，防文件无限膨胀
        for bid, s in (state.get("accounts") or {}).items():
            if len(s.get("keys") or []) > 3000:
                s["keys"] = s["keys"][-3000:]

        F_INBOX.parent.mkdir(parents=True, exist_ok=True)
        with io.open(F_INBOX, "a", encoding="utf-8", newline="") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        _save_json(F_STATE, state)
        say(f"\n✔ 已追加 {len(entries)} 条 → {F_INBOX}")
        say(f"✔ 水位线已更新 → {F_STATE}")
    elif args.dry_run:
        say("\n（dry-run，未写 inbox）")
    else:
        say("\n（无新文 —— 入口即拦截，没有重复数据进入下游）")

    result = {
        "ts": _dt.datetime.now().isoformat(timespec="seconds"),
        "mode": mode, "accounts": len(accts),
        "new": len(entries), "content_ok": okc, "content_fail": len(entries) - okc,
        # ★ 机器可读的降级标记：调度器/告警据此区分"没新文"与"只看到最新一篇"
        "degraded": {"accounts": degraded_accts,
                     "reasons": sorted({a.get("_degraded") for a in accts if a.get("_degraded")})},
        "dry_run": bool(args.dry_run),
        "items": [{"account": e["account"], "title": e["title"], "date": e["date"],
                   "chars": e["chars"], "url": e["url"]} for e in entries],
    }
    # ★ dry-run 不能覆写**真实**结果文件：调度器 `_wechat_weread_count()` 按 mtime 读
    #   `mp_collect_result.json` 的 `new` 当作本轮产出。实测（2026-10-21）一次
    #   `--dry-run` 就把上一轮"新增 59 篇"的记录冲成 0，告警与入库判断都会跟着错。
    WEREAD_DIR.mkdir(parents=True, exist_ok=True)
    res_json = F_RESULT if not args.dry_run else F_RESULT.with_name("mp_collect_result.dryrun.json")
    res_txt = F_TXT if not args.dry_run else F_TXT.with_name("mp_collect_result.dryrun.txt")
    _save_json(res_json, result)
    io.open(res_txt, "w", encoding="utf-8", newline="").write("\n".join(_log))
    say(f"✔ 结果文件 → {res_json}")
    return 0


def _write_crash_result(exc: BaseException) -> None:
    """进程级崩溃兜底：必须留下**可诊断**的结果文件。

    【为什么需要（2026-10-23 实测）】08:30 那轮以「退出码 1」失败（未捕获异常），
      而 `mp_collect_result.json` 仍停在前一天的旧文件 ⇒ 调度器按 mtime 读到的
      是旧 `new`，告警只能写"退出码 1"，**异常类型与堆栈全丢**，排查只能人工复跑。
      本次复跑 86 秒、26 篇、退出码 0 —— 说明采集器本身没坏，坏的正是"崩溃不留痕"。

    本函数保证：任何未捕获异常都写一份 `crashed: true` 的结果（new=0），
    并把完整堆栈落到 `mp_collect_result.crash.txt`。下次再挂可直接看到原因。
    """
    import traceback

    try:
        tb = traceback.format_exc()
        payload = {
            "ts": _dt.datetime.now().isoformat(timespec="seconds"),
            "mode": "daily", "accounts": 0, "new": 0,
            "content_ok": 0, "content_fail": 0,
            # reasons 用 crashed 与"接口被拒(articles_refused)"区分开
            "degraded": {"accounts": [], "reasons": ["crashed"]},
            "crashed": True,
            "error": f"{type(exc).__name__}: {exc}"[:300],
            "dry_run": False, "items": [],
        }
        WEREAD_DIR.mkdir(parents=True, exist_ok=True)
        _save_json(F_RESULT, payload)
        io.open(F_RESULT.with_name("mp_collect_result.crash.txt"), "w",
                encoding="utf-8", newline="").write(tb)
    except Exception:  # noqa: BLE001 - 兜底自身绝不能再抛
        pass


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001
        _write_crash_result(exc)
        raise
