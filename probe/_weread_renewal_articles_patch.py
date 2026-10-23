# -*- coding: utf-8 -*-
"""给 wechat_weread_collector.py 打补丁：会话自动续期 + 恢复 articles 多篇枚举。

背景（2026-10-19 实测定案）：
  · `wr_skey` Max-Age=5400（90 分钟）；`wr_vid`/`wr_rt` = 360 天。
  · 采集器原来只带静态 cookie、从不调 `/web/login/renewal`
    ⇒ 12 小时一轮的调度**必然**撞 `-2012 登录超时`。
  · 同一份 cookie 在真实浏览器里可用、续期后 Python 侧立刻恢复
    ⇒ 不是"登录过期"，是**少了续期这一步**。
  · `/web/mp/articles` 也没有废弃：有效 skey 下 offset=0 → 101 篇、offset=20 → 109 篇、零重叠。

本补丁只做代码替换，不做任何网络请求。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

SRC = Path("wechat_weread_collector.py")
raw = SRC.read_bytes()
assert raw.count(b"\r\n") == 0, "本文件应为纯 LF（实测 CRLF=%d）" % raw.count(b"\r\n")
t = raw.decode("utf-8")
orig_len = len(t)
shutil.copy2(SRC, SRC.with_suffix(".py.bak_before_renewal"))

steps: list[str] = []


def cut(start: str, end: str, new: str, label: str) -> None:
    """按「起始锚点 → 结束锚点（含）」切片替换，锚点必须唯一。"""
    global t
    assert t.count(start) == 1, "%s：起始锚点命中 %d 次" % (label, t.count(start))
    i = t.index(start)
    assert t.count(end) >= 1, "%s：结束锚点未命中" % label
    j = t.index(end, i) + len(end)
    t = t[:i] + new + t[j:]
    steps.append(label)


# ═══════════════════════════════════════════════ ① 请求层：Session + 续期
NEW_NET = '''# ============================================================ cookie / 会话
# 【2026-10-19 实测定案】`wr_skey` 是**服务端会话密钥，Max-Age=5400（90 分钟）**；
#   `wr_vid` / `wr_rt` 是长期凭据（360 天，且每次续期都被再延到 360 天）。
#   旧实现只带静态 cookie、从不续期 ⇒ 12 小时一轮的调度**必然**在下一轮撞
#   `-2012 登录超时`。这不是"登录过期"——重扫码也只能管 90 分钟。
#   修法：采集前调 `POST /web/login/renewal`，并把服务端下发的新 cookie 写回。
_RENEWAL_PATH = "/web/login/renewal"
_SESSION = None


def _load_cookie_list() -> list[dict]:
    if not F_COOKIES.exists():
        raise SystemExit(
            f"✘ 未找到 cookie：{F_COOKIES}\\n"
            "  先跑一次扫码登录：node probe/_weread_login.mjs")
    j = json.loads(F_COOKIES.read_text(encoding="utf-8"))
    cks = j.get("cookies") or []
    names = {c.get("name") for c in cks}
    # ★ 续期只认长期凭据（实测：把 wr_skey 整个删掉仍能续期成功）
    #   ⇒ 不再把 wr_skey 当必要条件，否则"密钥过期"会被误报成"未登录、去扫码"。
    if not {"wr_vid", "wr_rt"} <= names:
        raise SystemExit(
            "✘ cookie 缺少长期凭据（需要 wr_vid + wr_rt）\\n"
            f"  文件：{F_COOKIES}\\n"
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


# 这三个码**都可能**是「会话密钥过期」的表现 ⇒ 先续期再重试一次，而不是直接判死。
_AUTH_CODES = {-2010, -2012, -2041}


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
    """统一请求入口：撞到鉴权类错误码时**自动续期并重试一次**。"""
    r = _session().get(BASE + path, params=params, headers=_headers(), timeout=timeout)
    code = _auth_code(r)
    if code in _AUTH_CODES and renew_session(quiet=True):
        say("   ↻ 会话密钥已续期（因 errCode=%s）" % code)
        r = _session().get(BASE + path, params=params, headers=_headers(), timeout=timeout)
    return r
'''

cut("# ============================================================ cookie",
    '    return requests.get(BASE + path, params=params, headers=_headers(), timeout=timeout)',
    NEW_NET, "① 请求层（Session + 续期）")


# ═══════════════════════════════════════════════ ② fetch_articles 恢复为主通道
NEW_FETCH = '''def fetch_articles(bid: str, offset: int, retries: int = 4) -> list[dict]:
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
            # `_get` 已经自动续期并重试过一次仍失败 ⇒ 会话是真的不能用了
            if str(code) in {str(x) for x in _AUTH_CODES}:
                raise SystemExit(
                    "✘ 文章接口 errCode=%s %s\\n"
                    "   ⇒ 自动续期后仍被拒：长期凭据（wr_vid/wr_rt）也失效了。\\n"
                    "     请重跑：node probe/_weread_login.mjs（扫码一次）"
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


'''
cut("def fetch_articles(bid: str, offset: int, retries: int = 4) -> list[dict]:",
    "# ============================================================ 最新一篇（cover）",
    NEW_FETCH + "# ============================================================ 最新一篇（cover · 兜底）\n",
    "② fetch_articles 恢复为主通道")


# ═══════════════════════════════════════════════ ③ cover 注释与报错文案
cut("# 【2026-10-16 平台变更】旧接口 /web/mp/articles 已废弃（恒返 -2012 / -2041），",
    "_COVER_ERR_HINT = (\"-2010\", \"-2012\", \"-2041\")",
    '''# 【2026-10-19 降级为兜底】列表接口 `/web/mp/articles` 已恢复可用（此前 -2041 是密钥过期）。
#   `cover` 只在列表为空时兜底：它只给最新一篇，且不含 time/summary。
_COVER_ERR_HINT = ("-2010", "-2012", "-2041")''',
    "③ cover 注释")

_OLD_COVER_ERR = '''                raise SystemExit(
                    "✘ cover 报错 errCode=%s %s\\n"
                    "   ⇒ cookie 的登录态已失效（跑 桌面「重登公众号读书通道.bat」）"
                    % (code, d.get("errMsg") or ""))'''
_NEW_COVER_ERR = '''                raise SystemExit(
                    "✘ cover 报错 errCode=%s %s\\n"
                    "   ⇒ 自动续期后仍被拒：请重跑 node probe/_weread_login.mjs 扫码"
                    % (code, d.get("errMsg") or ""))'''
assert t.count(_OLD_COVER_ERR) == 1, "③ cover 报错文案锚点命中 %d 次" % t.count(_OLD_COVER_ERR)
t = t.replace(_OLD_COVER_ERR, _NEW_COVER_ERR, 1)
steps.append("③ cover 报错文案")


# ═══════════════════════════════════════════════ ④ collect_account：翻页取清单
NEW_LOOP = '''    # 【2026-10-19 恢复多篇枚举】`/web/mp/articles` 实测可用（此前 -2041 是密钥过期所致）
    #   ⇒ 主通道翻页取清单；`/web/mp/cover` 仅在"列表完全无数据"时兜底。
    #   停止条件：daily 命中一整页旧文（已到水位线）；backfill 取满 limit 篇。
    picked: list[dict] = []
    known_t = known_titles()
    picked_ids: set[str] = set()
    total_listed = pages = 0
    first_run = not seen_keys
    cap = limit if (mode == "backfill" or first_run) else 10 ** 9

    for page in range(max(1, max_pages)):
        arts = fetch_articles(bid, offset=page * PAGE_SIZE)
        if not arts:
            break
        pages += 1
        total_listed += len(arts)
        fresh = 0
        for a in arts:
            oid = a["original_id"]
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
        say("   第%d页 offset=%-4d %3d 篇（新 %3d）%s"
            % (page + 1, page * PAGE_SIZE, len(arts), fresh,
               "   ← 已到水位线" if (mode == "daily" and fresh == 0) else ""))
        if len(picked) >= cap or len(arts) < PAGE_SIZE:
            break
        if mode == "daily" and fresh == 0:
            break

    if total_listed == 0:
        say("   ⚠ 列表通道无数据 ⇒ 回退 /web/mp/cover 取最新一篇")
        for a in fetch_latest(bid):
            if a["original_id"] in seen_keys or a["original_id"] in picked_ids:
                say("   最新一篇已采过（key 命中）: %s" % a["title"][:46])
                continue
            if (name, _norm_title(a["title"])) in known_t:
                say("   最新一篇已采过（标题命中·跨格式兜底）: %s" % a["title"][:46])
                continue
            picked.append(a)
            picked_ids.add(a["original_id"])

    say("   ⇒ 本轮取到 %d 篇新文（翻 %d 页 / 列出 %d 篇）"
        % (len(picked), pages, total_listed))'''

cut("    # 【2026-10-16 改造】旧接口 /web/mp/articles 已被平台废弃 ⇒ 改用 /web/mp/cover。",
    '    say(f"   ⇒ 本轮取到 {len(picked)} 篇新文")',
    NEW_LOOP, "④ collect_account 翻页取清单")


# ═══════════════════════════════════════════════ ⑤ main：回填文案 + --list 说明
cut("    if args.backfill and args.backfill > 1:",
    '    say("=" * 78)\n    say("公众号采集 · 微信读书通道")',
    '''    if args.backfill:
        say("回填模式：每号最多取 %d 篇（列表通道 /web/mp/articles 已恢复翻页）"
            % args.backfill)
        say("")

    say("=" * 78)
    say("公众号采集 · 微信读书通道")''',
    "⑤ main 回填文案")

cut('        say("取清单通道: /web/mp/cover（每号 1 篇；旧 /web/mp/articles 已被平台停用）")',
    '        say("去重键    : wxmp_{reviewId 去掉 bookId 前缀}  +  (号名, 标题) 跨格式兜底")',
    '''        say("取清单通道: /web/mp/articles（翻页·主）  /web/mp/cover（兜底）")
        say("会话密钥  : wr_skey 仅 90 分钟 ⇒ 每轮采集前自动调 /web/login/renewal 续期")
        say("去重键    : wxmp_{originalId}  +  (号名, 标题) 跨格式兜底")''',
    "⑤ --list 通道说明")

cut('                    help="每号回填最近 N 篇（⚠ 平台已停用列表接口 ⇒ 实际只能取最新 1 篇）")',
    '                    help="每号回填最近 N 篇（⚠ 平台已停用列表接口 ⇒ 实际只能取最新 1 篇）")',
    '                    help="每号回填最近 N 篇（按 originalId 幂等去重）")',
    "⑤ --backfill 帮助")


# ═══════════════════════════════════════════════ ⑥ main：启动即续期
cut("    state = load_state()\n    all_new: list[dict] = []",
    "    state = load_state()\n    all_new: list[dict] = []",
    '''    # ── 会话续期（★ 必须在任何采集请求之前）────────────────────────
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
    all_new: list[dict] = []''',
    "⑥ main 启动续期")


# ═══════════════════════════════════════════════ ⑦ 顶部说明：cookie 会过期
cut("3. **必须带 cookie**，且**不能用官方 API Key 顶替**（实测伪装成 wr_skey 头也返回",
    "   `{\"errCode\":-2010,\"errMsg\":\"用户不存在\"}`）。cookie 会过期 ⇒ 见 --check。",
    '''3. **必须带 cookie**，且**不能用官方 API Key 顶替**（实测伪装成 wr_skey 头也返回
   `{"errCode":-2010,"errMsg":"用户不存在"}`）。
   ★ `wr_skey` 只是 **90 分钟**的会话密钥（`Max-Age=5400`）；`wr_vid`/`wr_rt` 是 360 天长期凭据。
     **每次采集前必须调 `POST /web/login/renewal` 续期**，否则 12 小时一轮的调度必然
     撞 `-2012 登录超时`（2026-10-19 定案：这不是"登录过期"，重扫码也只能管 90 分钟）。''',
    "⑦ 顶部说明")


SRC.write_bytes(t.encode("utf-8"))
print("已应用 %d 处：" % len(steps))
for s in steps:
    print("   ·", s)
print("字符数 %d → %d" % (orig_len, len(t)))
