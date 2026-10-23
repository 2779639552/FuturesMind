# -*- coding: utf-8 -*-
"""重写 tests/test_wechat_weread_cover.py 里 5 条结论已过期的护栏 + 补新护栏。"""
from pathlib import Path

P = Path("tests/test_wechat_weread_cover.py")
S = P.read_text(encoding="utf-8")
assert S.count("\r\n") == 0

BODIES = {}

BODIES["test_collect_account_uses_cover_not_articles"] = '''@pytest.mark.unit
def test_collect_account_uses_articles_first_with_cover_fallback(src):
    """★ 取清单必须**先翻 /web/mp/articles**，cover 只作兜底。

    【2026-10-19 更正】上一版这条测试钉的是「必须用 cover、不许调 articles」，
      依据是「articles 已被平台废弃」——那是个**误判**：有效 skey 下 articles
      正常返回（offset=0 → 101 篇、offset=20 → 109 篇、两页零重叠）。
      把错误结论写进护栏的代价是：真因修好后，护栏反而阻止恢复正确实现。
    """
    i = src.index("def collect_account(")
    j = src.index("\\ndef ", i + 10)
    body = src[i:j]
    assert "fetch_articles(" in body, "collect_account 没走列表通道（多篇枚举会失效）"
    assert "fetch_latest(bid)" in body, "cover 兜底不见了（列表异常时没有降级路径）"
    assert body.index("fetch_articles(") < body.index("fetch_latest(bid)"), \\
        "cover 被放在列表通道之前 ⇒ 每轮只会拿 1 篇"'''

BODIES["test_old_articles_api_is_marked_deprecated"] = '''@pytest.mark.unit
def test_articles_api_restored_as_primary(src):
    """fetch_articles 必须标注「已恢复为主通道」，并写明当初的误判。

    【为什么留这段历史】否则下一个维护者看到旧注释「已废弃」会把它改回 cover，
      重新失去多篇枚举与回填能力（本次修复的核心收益）。
    """
    i = src.index("def fetch_articles(")
    body = src[i:i + 1600]
    assert "/web/mp/articles" in body, "说明里没点名接口"
    assert "2026-10-19" in body, "没写恢复日期"
    assert "误判" in body, "没记录「曾误判为接口废弃」——后人会重犯"
    assert "已废弃" not in body[:600], "仍在标注已废弃（与实现矛盾）"


@pytest.mark.unit
def test_cover_is_fallback_only(src):
    """cover 必须明确是兜底，且写明它只给最新一篇。"""
    i = src.index("最新一篇（cover · 兜底）")
    body = src[i:i + 900]
    assert "兜底" in body, "cover 没标注为兜底通道"
    assert ("只给最新一篇" in body) or ("没有往期列表" in body), "缺 cover 的能力上限说明"
    j = src.index("def fetch_latest(")
    assert "/web/mp/cover" in src[j:j + 900], "fetch_latest 没调 cover 接口"'''

BODIES["test_cover_endpoint_and_meaning_of_latest_only"] = '''@pytest.mark.unit
def test_renewal_is_wired_before_collection(src):
    """★★ 采集前必须调 renew_session()，且失败可区分「需扫码」。

    【为什么是硬约束】wr_skey 的 Max-Age=5400（90 分钟）。不续期时，12 小时一轮的调度
      **必然**在下一轮撞 -2012（实测连续 11 轮）。这不是登录过期，重扫码也只管 90 分钟。
    """
    assert "def renew_session(" in src, "缺少 renew_session 实现"
    body = src[src.index("def renew_session("):]
    body = body[:body.index("\\ndef ")]
    assert "/web/login/renewal" in src, "没调续期接口"
    assert "succ" in body, "没有校验续期结果（succ==1）"
    assert "_persist_session_cookies()" in body, "续期后没写回 cookies.json（下次又用旧密钥）"
    i_main = src.index("def main(")
    main = src[i_main:]
    assert "renew_session()" in main, "main 没调续期"
    assert main.index("renew_session()") < main.index("collect_account("), \\
        "续期在采集之后才发生（第一轮就已经 -2012）"


@pytest.mark.unit
def test_get_auto_renews_and_retries_once(monkeypatch):
    """★★ 行为判据：_get 撞到 -2012 时必须续期并重试一次。

    【为什么不只查源码】「调了 renew_session」和「在错误码分支里真的调到」是两件事。
      这里用假 Session 走真实分支：第一次返回 -2012，续期后第二次应正常。
    """
    import json as _json
    import wechat_weread_collector as C

    class _Resp:
        def __init__(self, payload):
            self._p = payload
            self.content = _json.dumps(payload).encode("utf-8")

        def json(self):
            return self._p

    class _FakeSession:
        def __init__(self):
            self.gets = 0
            self.posts = 0
            self._first = True
            self.cookies = []

        def get(self, url, params=None, headers=None, timeout=None):
            self.gets += 1
            if self._first:
                self._first = False
                return _Resp({"errCode": -2012, "errMsg": "登录超时"})
            return _Resp({"reviews": []})

        def post(self, url, headers=None, timeout=None):
            self.posts += 1
            return _Resp({"succ": 1})

    fake = _FakeSession()
    tmp = Path(__file__).with_name("_tmp_cookies_probe.json")
    monkeypatch.setattr(C, "_SESSION", fake)
    monkeypatch.setattr(C, "F_COOKIES", tmp)
    try:
        C._get("/web/shelf/sync", {})
        assert fake.gets == 2, "撞到 -2012 后没有重试（只发了 %d 次）" % fake.gets
        assert fake.posts == 1, "没有调用续期接口（posts=%d）" % fake.posts
    finally:
        tmp.unlink(missing_ok=True)


@pytest.mark.unit
def test_cookie_header_is_not_hardcoded(src):
    """★ _headers() 不能再拼 Cookie 头。

    【为什么】续期把新 skey 写进 Session 的 cookie jar；若 _headers() 仍返回静态
      Cookie 头，它会**覆盖**新密钥 ⇒ 续期看似成功、下一次请求照样 -2012。
    """
    i = src.index("def _headers()")
    body = src[i:i + 420]
    assert "Cookie" not in body, "_headers() 仍拼 Cookie 头 ⇒ 会覆盖续期后的新 wr_skey"
    assert "Session" in src, "没有用 Session（无法持有续期后的 cookie）"'''

BODIES["test_backfill_limitation_is_surfaced"] = '''@pytest.mark.unit
def test_backfill_can_paginate_and_is_bounded(src):
    """★ 回填要能翻页取 N 篇；daily 必须有界（否则把上千篇历史当新文）。"""
    body = src[src.index("def collect_account("):]
    body = body[:body.index("\\ndef ")]
    assert "offset=page * PAGE_SIZE" in body, "没按页偏移翻页 ⇒ 回填失效"
    assert "cutoff" in body and "last_time" in body, \\
        "daily 没有时间闸 ⇒ articles 会把全部历史当新文（实测会抓 1051 篇）"
    assert "skipped_old" in body, "没有「早于水位线」的统计/跳过逻辑"
    assert "max(limit, 60)" in body, "daily 没有数量硬上限"
    assert 'mode == "backfill"' in body, "backfill 与 daily 的停止条件没区分"


@pytest.mark.unit
def test_backfill_help_text_is_accurate(src):
    """文案不能再说「只能取最新 1 篇」（已能翻页）。"""
    assert "只能取最新 1 篇" not in src, "旧的能力限制文案仍在（与实现矛盾）"
    assert "已恢复翻页" in src, "缺翻页能力说明"'''

BODIES["test_cover_error_is_actionable"] = '''@pytest.mark.unit
def test_auth_errors_are_actionable_after_renewal(src):
    """鉴权类报错：先自动续期；续期仍失败才提示扫码。"""
    for code in ("-2010", "-2012", "-2041"):
        assert code in src, "缺少错误码 %s 的分类" % code
    i = src.index("def fetch_articles(")
    body = src[i:i + 2200]
    assert "自动续期后仍被拒" in body, "articles 报错没说明「已尝试自动续期」"
    assert "_weread_login.mjs" in body, "报错没给可执行的下一步（扫码脚本）"'''

for name, body in BODIES.items():
    start = "@pytest.mark.unit\ndef %s(" % name
    assert S.count(start) == 1, "%s 起锚命中 %d" % (name, S.count(start))
    i = S.index(start)
    try:
        j = S.index("@pytest.mark.unit\n", i + len(start))
    except ValueError:          # 最后一个函数：后面没有下一个装饰器
        j = len(S)
    S = S[:i] + body.rstrip() + "\n\n\n" + S[j:]

EXTRA = '''


@pytest.mark.unit
def test_long_lived_credentials_required_not_wr_skey(monkeypatch, tmp_path):
    """★ 只要求 wr_vid + wr_rt；不再要求 wr_skey。

    【为什么】wr_skey 只是 90 分钟的会话密钥。旧实现缺它就 SystemExit 并提示
      「登录失效」，把「密钥过期」误报成「未登录」（本次误诊的源头之一）。
    """
    import json as _json
    import wechat_weread_collector as C

    f = tmp_path / "cookies.json"
    f.write_text(_json.dumps({"cookies": [{"name": "wr_vid", "value": "1"},
                                          {"name": "wr_rt", "value": "2"}]}), encoding="utf-8")
    monkeypatch.setattr(C, "F_COOKIES", f)
    assert len(C._load_cookie_list()) == 2, "缺 wr_skey 时不该拒绝（续期只需长期凭据）"

    f.write_text(_json.dumps({"cookies": [{"name": "wr_skey", "value": "x"}]}), encoding="utf-8")
    try:
        C._load_cookie_list()
    except SystemExit:
        pass
    else:
        raise AssertionError("只有 wr_skey、缺长期凭据时必须拒绝（续不了期）")


@pytest.mark.unit
def test_ttl_facts_are_documented(src):
    """★ 90 分钟 / 360 天这两个数字必须写进注释（整套修复的前提）。"""
    assert ("5400" in src) or ("90 分钟" in src), "没记录 wr_skey 的 90 分钟 TTL"
    assert ("360 天" in src) or ("31104000" in src), "没记录长期凭据的 360 天寿命"
'''

P.write_text(S.rstrip() + EXTRA, encoding="utf-8")
print("  已重写 6 条 + 追加 2 条")
