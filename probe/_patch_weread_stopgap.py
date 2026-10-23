# -*- coding: utf-8 -*-
"""止血补丁：把 -2041 从「登录过期」里拆出来 + 单号降级 + 续期预算。

【为什么这三件事必须一起改】
  只拆错误码而不降级 ⇒ 仍然整轮退出；
  只降级而不拆码 ⇒ 每个号都会被误续期一次（续期风暴）；
  只限制续期而不拆码 ⇒ -2041 会被当成会话过期，永远救不回来。
"""
import io

P = "wechat_weread_collector.py"
s = io.open(P, encoding="utf-8", newline="").read()
assert "\r\n" not in s, "该文件是 LF"


def once(old, new, tag):
    global s
    n = s.count(old)
    assert n == 1, "[%s] 锚点命中 %d 次" % (tag, n)
    s = s.replace(old, new, 1)


# ── 1) threading（续期预算要锁） ──
once("import sys\nimport time\n", "import sys\nimport threading\nimport time\n", "import")

# ── 2) 错误码分层 + 两个异常 + 续期预算 ──
once('''# 这三个码**都可能**是「会话密钥过期」的表现 ⇒ 先续期再重试一次，而不是直接判死。
_AUTH_CODES = {-2010, -2012, -2041}''',
'''# ── 错误码分层（2026-10-21 止血）─────────────────────────────────────────
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
        return True''', "codes")

# ── 3) _get：只对会话码续期；-2041 明确不动 ──
once('''def _get(path: str, params: dict, timeout: int = 40) -> requests.Response:
    """统一请求入口：撞到鉴权类错误码时**自动续期并重试一次**。"""
    r = _session().get(BASE + path, params=params, headers=_headers(), timeout=timeout)
    code = _auth_code(r)
    if code in _AUTH_CODES and renew_session(quiet=True):
        say("   ↻ 会话密钥已续期（因 errCode=%s）" % code)
        r = _session().get(BASE + path, params=params, headers=_headers(), timeout=timeout)''',
'''def _get(path: str, params: dict, timeout: int = 40) -> requests.Response:
    """统一请求入口：**只有会话过期**才自动续期并重试一次。

    -2041 刻意不在这里处理：它不是会话问题，续期与重试都无效，
      只会把账号往风控里推。交给 `fetch_articles` 抛 `EndpointRefused`，由上层降级。
    """
    r = _session().get(BASE + path, params=params, headers=_headers(), timeout=timeout)
    code = _auth_code(r)
    if code in _SESSION_CODES and _auto_renew():
        say("   ↻ 会话已过期（errCode=%s），续期后重试一次" % code)
        r = _session().get(BASE + path, params=params, headers=_headers(), timeout=timeout)''',
     "_get")

# ── 4) fetch_articles：分码抛不同异常 ──
once('''        code = d.get("errCode") or d.get("errcode")
        if code:
            # `_get` 已经自动续期并重试过一次仍失败 ⇒ 会话是真的不能用了
            if str(code) in {str(x) for x in _AUTH_CODES}:
                raise SystemExit(
                    "✘ 文章接口 errCode=%s %s\\n"
                    "   ⇒ 自动续期后仍被拒：长期凭据（wr_vid/wr_rt）也失效了。\\n"
                    "     请重跑：node probe/_weread_login.mjs（扫码一次）"
                    % (code, d.get("errMsg") or ""))
            last_err = "errCode=%s" % code''',
'''        code = d.get("errCode") or d.get("errcode")
        if code:
            # ★ 分码处理（2026-10-21）：-2041 说明会话是好的，只是这条接口不给。
            if str(code) in {str(x) for x in _ENDPOINT_REFUSED}:
                raise EndpointRefused(
                    "列表接口拒绝 errCode=%s（会话有效，不是登录过期）" % code)
            if str(code) in {str(x) for x in _SESSION_CODES}:
                raise AuthError(
                    "列表接口 errCode=%s %s（续期后仍被拒，需重新扫码）"
                    % (code, d.get("errMsg") or ""))
            last_err = "errCode=%s" % code''', "fetch_articles")

# ── 5) fetch_latest：兜底通道也不能 SystemExit ──
once('''            if str(code) in _COVER_ERR_HINT:
                raise SystemExit(
                    "✘ cover 报错 errCode=%s %s\\n"
                    "   ⇒ 自动续期后仍被拒：请重跑 node probe/_weread_login.mjs 扫码"
                    % (code, d.get("errMsg") or ""))''',
'''            if str(code) in _COVER_ERR_HINT:
                # 兜底通道也失败：抛普通异常，让上层记成"本号本次无产出"，
                # 不要因为一个号把整轮（6 个号）打断。
                raise AuthError(
                    "cover 报错 errCode=%s %s" % (code, d.get("errMsg") or ""))''',
     "fetch_latest")

# ── 6) collect_account：循环包 try，单号降级 ──
once('''    for page in range(max(1, max_pages)):
        arts = fetch_articles(bid, offset=page * PAGE_SIZE)
        if not arts:
            break''',
'''    degraded = ""            # "" / articles_refused / auth_failed
    try:
      for page in range(max(1, max_pages)):
        arts = fetch_articles(bid, offset=page * PAGE_SIZE)
        if not arts:
            break''', "loop open")

once('''        if mode == "daily" and fresh == 0:
            break

    if total_listed == 0:
        say("   ⚠ 列表通道无数据 ⇒ 回退 /web/mp/cover 取最新一篇")''',
'''        if mode == "daily" and fresh == 0:
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

    acct["_degraded"] = degraded

    if total_listed == 0:
        say("   ⚠ 列表通道无数据 ⇒ 回退 /web/mp/cover 取最新一篇")''', "loop close")

# ── 7) 降级时把覆盖率写清楚，别让人以为"只发了 1 篇" ──
once('''    say("   ⇒ 本轮取到 %d 篇新文（翻 %d 页 / 列出 %d 篇）"
        % (len(picked), pages, total_listed))''',
'''    say("   ⇒ 本轮取到 %d 篇新文（翻 %d 页 / 列出 %d 篇）"
        % (len(picked), pages, total_listed))
    if degraded:
        say("   ⚠ 降级模式（%s）：**只覆盖最新 1 篇**，同一天更早发布的文章本轮取不到"
            % degraded)''', "degraded note")

# ── 8) main：汇总里如实报降级 ──
once('''    for acct, a in all_new:
        entries.append(build_entry(acct["name"], a, a.get("_content") or {}, acct["book_id"]))''',
'''    for acct, a in all_new:
        entries.append(build_entry(acct["name"], a, a.get("_content") or {}, acct["book_id"]))

    degraded_accts = [a.get("name") for a in accts if a.get("_degraded")]''', "main agg")

once('''    say(f"【汇总】新增 {len(entries)} 篇（正文成功 {okc} / 失败 {len(entries) - okc}）")''',
'''    say(f"【汇总】新增 {len(entries)} 篇（正文成功 {okc} / 失败 {len(entries) - okc}）")
    if degraded_accts:
        say("   ⚠ 降级 %d/%d 个号（列表接口被拒）：%s"
            % (len(degraded_accts), len(accts), "、".join(degraded_accts)))
        say("     降级号本轮只取到最新 1 篇 —— 缺的是覆盖范围，不是正文失败。")''', "main say")

once('''        "new": len(entries), "content_ok": okc, "content_fail": len(entries) - okc,''',
'''        "new": len(entries), "content_ok": okc, "content_fail": len(entries) - okc,
        # ★ 机器可读的降级标记：调度器/告警据此区分"没新文"与"只看到最新一篇"
        "degraded": {"accounts": degraded_accts,
                     "reasons": sorted({a.get("_degraded") for a in accts if a.get("_degraded")})},''',
     "main result")

io.open(P, "w", encoding="utf-8", newline="").write(s)
print("collector patched; LF intact =", "\r\n" not in s)
