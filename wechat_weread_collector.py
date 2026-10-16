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
   `{"errCode":-2010,"errMsg":"用户不存在"}`）。cookie 会过期 ⇒ 见 --check。

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
import io
import json
import os
import random
import re
import sys
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


# ============================================================ cookie
def _cookie_header() -> str:
    if not F_COOKIES.exists():
        raise SystemExit(
            f"✘ 未找到 cookie：{F_COOKIES}\n"
            "  先跑一次扫码登录：node probe/_weread_login.mjs")
    j = json.loads(F_COOKIES.read_text(encoding="utf-8"))
    cks = j.get("cookies") or []
    names = {c["name"] for c in cks}
    if "wr_skey" not in names:
        raise SystemExit(
            "✘ cookie 里没有 wr_skey（登录态已失效）\n"
            f"  文件：{F_COOKIES}\n"
            "  重跑：node probe/_weread_login.mjs")
    return "; ".join(f"{c['name']}={c['value']}" for c in cks)


def _headers() -> dict:
    return {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
            "Referer": BASE + "/", "Cookie": _cookie_header()}


def _get(path: str, params: dict, timeout: int = 40) -> requests.Response:
    return requests.get(BASE + path, params=params, headers=_headers(), timeout=timeout)


# ============================================================ 注册表/水位线
def _load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _save_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    io.open(p, "w", encoding="utf-8", newline="").write(
        json.dumps(obj, ensure_ascii=False, indent=2))


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
    """取一页「群发」并展开成扁平文章列表（带重试）。

    ⚠️ 该接口偶发**静默空回包**（HTTP 200 但 reviews 为空）⇒ 必须退避重试，
       否则会静默漏掉整页（社区实测此坑）。
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

        if d.get("errCode") or d.get("errcode"):
            # -2010 用户不存在 = cookie 失效（重试无意义）
            raise SystemExit(f"✘ 文章接口报错：{d}（cookie 可能已失效，重跑登录脚本）")

        flat = expand_articles(d, bid)
        if flat:
            return flat
        last_err = f"空回包(第 {attempt + 1} 次)"
        time.sleep(0.8 + attempt * 0.7)
    say(f"      ⚠ offset={offset} 重试 {retries} 次仍为空：{last_err}")
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

    # 逐页拉，直到够数 / 碰到水位线 / 页空
    picked: list[dict] = []
    offset = 0
    pages = 0
    while pages < max_pages:
        arts = fetch_articles(bid, offset)
        if not arts:
            break
        pages += 1
        fresh = [a for a in arts if a["original_id"] not in seen_keys]
        if mode == "daily":
            fresh = [a for a in fresh if int(a.get("time") or 0) > last_time] or fresh[:0]
        picked.extend(fresh)
        say(f"   offset={offset:4d} 展开 {len(arts):3d} 篇 其中新 {len(fresh):3d} 篇")

        if mode != "daily" and len(picked) >= limit:
            picked = picked[:limit]
            break
        if mode == "daily" and not fresh and pages >= 2:
            # 连着两页没有新文 ⇒ 到底了（增量场景下的"入口即拦截"）
            break
        offset += PAGE_SIZE
        time.sleep(random.uniform(0.5, 1.1))

    say(f"   ⇒ 本轮取到 {len(picked)} 篇新文")

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
    ap.add_argument("--backfill", type=int, metavar="N", help="每号回填最近 N 篇")
    ap.add_argument("--account", help="只跑某个号（按号名匹配）")
    ap.add_argument("--no-content", action="store_true", help="只列清单，不抓正文")
    ap.add_argument("--max-pages", type=int, default=10, help="每号最多翻页数(默认10=约200个群发)")
    ap.add_argument("--dry-run", action="store_true", help="不写 inbox，只打印")
    args = ap.parse_args()

    mode = "backfill" if args.backfill else "daily"
    limit = args.backfill or 20

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
        entries.append(build_entry(acct["name"], a, a.get("_content") or {}, acct["book_id"]))

    okc = sum(1 for e in entries if e["text_ok"])
    say("")
    say("=" * 78)
    say(f"【汇总】新增 {len(entries)} 篇（正文成功 {okc} / 失败 {len(entries) - okc}）")
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
        "dry_run": bool(args.dry_run),
        "items": [{"account": e["account"], "title": e["title"], "date": e["date"],
                   "chars": e["chars"], "url": e["url"]} for e in entries],
    }
    WEREAD_DIR.mkdir(parents=True, exist_ok=True)
    _save_json(F_RESULT, result)
    io.open(F_TXT, "w", encoding="utf-8", newline="").write("\n".join(_log))
    say(f"✔ 结果文件 → {F_RESULT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
