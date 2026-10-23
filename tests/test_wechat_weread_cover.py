# -*- coding: utf-8 -*-
"""读书通道「清单接口从 /web/mp/articles 换成 /web/mp/cover」的护栏。

【背景】2026-10-16 实测：旧列表接口已被平台停用（恒返 -2012 → -2041），
改用 /web/mp/cover（每号只给最新一篇）。改动落在 wechat_weread_collector.py。

【为什么需要这些断言】
  · 换接口最容易漏的是**去重键的推导**：reviewId = "{bookId}_{originalId}"，
    切错前缀（例如用 `split("_")[-1]`）在 bookId 自身含下划线时会得到错的 key
    ⇒ 每轮都当"新文"入库，造成重复（我在探测阶段就踩过一次）。
  · cover **不返回时间** ⇒ 必须保证"猜不到日期时用采集日兜底，且不污染 create_time"。
  · cover **只给最新一篇** ⇒ 这个覆盖上限必须写进代码注释，否则后人会以为"改完就是全量"。
  · 新旧 originalId 格式不同 ⇒ 必须保留「(号名, 标题)」跨格式兜底，否则过渡期会重复入库。
"""
import importlib
import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("AGENTSENSE_AUTH_DISABLE", "1")

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "wechat_weread_collector.py"


def _src() -> str:
    return SRC.read_text(encoding="utf-8")


def _collector():
    return importlib.import_module("wechat_weread_collector")


@pytest.fixture(scope="module")
def src():
    return _src()


# ============================================================ A. 接口切换
@pytest.mark.unit
def test_collect_account_uses_articles_first_with_cover_fallback(src):
    """★ 取清单必须**先翻 /web/mp/articles**，cover 只作兜底。

    【2026-10-19 更正】上一版这条测试钉的是「必须用 cover、不许调 articles」，
      依据是「articles 已被平台废弃」——那是个**误判**：有效 skey 下 articles
      正常返回（offset=0 → 101 篇、offset=20 → 109 篇、两页零重叠）。
      把错误结论写进护栏的代价是：真因修好后，护栏反而阻止恢复正确实现。
    """
    i = src.index("def collect_account(")
    j = src.index("\ndef ", i + 10)
    body = src[i:j]
    assert "fetch_articles(" in body, "collect_account 没走列表通道（多篇枚举会失效）"
    assert "fetch_latest(bid)" in body, "cover 兜底不见了（列表异常时没有降级路径）"
    assert body.index("fetch_articles(") < body.index("fetch_latest(bid)"), \
        "cover 被放在列表通道之前 ⇒ 每轮只会拿 1 篇"


@pytest.mark.unit
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
    assert "/web/mp/cover" in src[j:j + 900], "fetch_latest 没调 cover 接口"


@pytest.mark.unit
def test_renewal_is_wired_before_collection(src):
    """★★ 采集前必须调 renew_session()，且失败可区分「需扫码」。

    【为什么是硬约束】wr_skey 的 Max-Age=5400（90 分钟）。不续期时，12 小时一轮的调度
      **必然**在下一轮撞 -2012（实测连续 11 轮）。这不是登录过期，重扫码也只管 90 分钟。
    """
    assert "def renew_session(" in src, "缺少 renew_session 实现"
    body = src[src.index("def renew_session("):]
    body = body[:body.index("\ndef ")]
    assert "/web/login/renewal" in src, "没调续期接口"
    assert "succ" in body, "没有校验续期结果（succ==1）"
    assert "_persist_session_cookies()" in body, "续期后没写回 cookies.json（下次又用旧密钥）"
    i_main = src.index("def main(")
    main = src[i_main:]
    assert "renew_session()" in main, "main 没调续期"
    assert main.index("renew_session()") < main.index("collect_account("), \
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
    # ⚠ 只查**可执行行**：函数上方的注释里就写着「不要再拼 Cookie 头」，
    #   整段匹配会恒红（自指陷阱，本轮踩了一次）。
    code = "\n".join(ln for ln in body.splitlines() if not ln.strip().startswith("#"))
    assert "Cookie" not in code, "_headers() 仍拼 Cookie 头 ⇒ 会覆盖续期后的新 wr_skey"
    assert "Session" in src, "没有用 Session（无法持有续期后的 cookie）"


@pytest.mark.unit
def test_dedupe_key_strips_bookid_prefix(src):
    """★ `originalId` 必须用 `split(bid + "_", 1)[1]` 推导，**不能用** `split("_")[-1]`。

    【为什么】bookId 自己就含下划线（`MP_WXS_3869147758`）⇒ 用 `[-1]` 会截出错的 token
    （探测阶段实测踩到，白测两轮）。这里同时钉住"正确写法在位"与"错误写法不在位"。
    """
    i = src.index("def fetch_latest(")
    body = src[i:i + 2200]
    assert 'rid.split(bid + "_", 1)[1]' in body, "去重键推导没用 split(bid + '_', 1)（会截错 token）"
    assert 'rid.split("_")[-1]' not in body, "★ 用了 split('_')[-1] —— bookId 含下划线，会截出错的 key"


@pytest.mark.unit
def test_key_shape_matches_legacy(src):
    """键形态 `wxmp_{originalId}` 必须与旧链路**同构**（入库链路才能零改动）。"""
    i = src.index("def build_entry(")
    body = src[i:i + 900]
    assert 'key = f"wxmp_{art[\'original_id\']}"' in body, "去重键形态变了 ⇒ 会与历史数据断裂"


@pytest.mark.unit
def test_cross_format_title_dedupe_wired(src):
    """★ 新旧 originalId 格式不同 ⇒ 必须保留「(号名, 标题)」兜底，且**真的被用**。

    只断言 `known_titles` 函数存在不够 —— 定义了不调用等于没有（本项目同型坑）。
    """
    assert "def known_titles(" in src and "def _norm_title(" in src, "缺跨格式去重所需函数"
    i = src.index("def collect_account(")
    j = src.index("\ndef ", i + 10)
    body = src[i:j]
    assert "known_titles()" in body, "collect_account 没调用 known_titles（兜底未接线）"
    assert "_norm_title(a[\"title\"])" in body or "_norm_title(a['title'])" in body, \
        "兜底判据没落到「(号名, 标题)」上"


# ============================================================ C. 纯函数语义
@pytest.mark.unit
def test_norm_title_removes_noise():
    """标题归一化要能吸收空白/标点/大小写差异（否则兜底形同虚设）。"""
    c = _collector()
    a = c._norm_title("【建投能化|月策合集】原油供应先行回暖，化工板块利润修复2026-10-12")
    b = c._norm_title(" 【建投能化｜月策合集】原油供应先行回暖, 化工板块利润修复2026-10-12 ")
    assert a == b, "同标题的不同写法没归一到一起：%r vs %r" % (a, b)
    assert " " not in a and "|" not in a and "，" not in a
    assert a == a.lower()


@pytest.mark.unit
def test_guess_date_handles_three_forms():
    """★ 日期猜测要覆盖实测到的三种形态（cover 不返回时间，只能猜）。

    形态来源（本轮真实标题/正文）：
      · 标题尾 8 位数     恒力期货日报20261016
      · 标题带连字符日期  …利润修复2026-10-12
      · 正文开头中文日期  宏观&金工早评 | 2026年10月16日
    """
    c = _collector()
    g = c._guess_date
    assert g("恒力期货日报20261016") == "2026-10-16", "8 位数形态没解析对"
    assert g("【建投能化】原油…修复2026-10-12") == "2026-10-12", "连字符形态没解析对"
    assert g("", "宏观&金工早评 | 2026年10月16日 品种：股指") == "2026-10-16", "正文中文日期没解析对"
    # 猜不到要老实返回空串（由 build_entry 用采集日兜底），不能瞎猜
    assert g("没有日期的标题") == "", "猜不到时应返回空串"
    assert g("") == ""
    # 越界值不该被当成日期
    assert g("测试20261345") == "", "非法月日（13 月 45 日）被误认成日期"


@pytest.mark.unit
def test_build_entry_date_fallback_does_not_pollute_watermark(src):
    """★ cover 无时间 ⇒ date 用采集日兜底，但 `create_time` **不能被兜底值污染**。

    【为什么要紧】`create_time` 参与水位线推进（`last_time = max(last_time, create_time)`）。
    若把"采集日"当作 create_time 写进去，水位线会被推到未来 ⇒ 之后真正的新文被判为"旧文"
    ⇒ **静默再也不采**（这类"把辅助值写进判定量"的错，症状是无声的）。
    """
    i = src.index("def build_entry(")
    j = src.index("\ndef ", i + 10)
    body = src[i:j]
    assert "_guess_date(" in body, "build_entry 没做日期猜测"
    assert "不写进 create_time" in body, "缺「兜底值不写进 create_time」的说明"
    # create_time 仍只来自 art["time"]
    assert '"create_time": ts,' in body, "create_time 应仍只取 art['time']"


# ============================================================ D. 能力边界要如实告知
@pytest.mark.unit
def test_backfill_can_paginate_and_is_bounded(src):
    """★ 回填要能翻页取 N 篇；daily 必须有界（否则把上千篇历史当新文）。"""
    body = src[src.index("def collect_account("):]
    body = body[:body.index("\ndef ")]
    assert "offset=page * PAGE_SIZE" in body, "没按页偏移翻页 ⇒ 回填失效"
    # ⚠ 必须断言**完整表达式**：只查 "cutoff" in body 时，把逻辑改成
    #   `cutoff = 0`（等于没有时间闸）仍会通过（变异自检抓到的洞）。
    assert "cutoff = 0 if first_run else last_time" in body, \
        "daily 没有时间闸 ⇒ articles 会把全部历史当新文（实测会抓 1051 篇）"
    assert "skipped_old" in body, "没有「早于水位线」的统计/跳过逻辑"
    assert "max(limit, 60)" in body, "daily 没有数量硬上限"
    assert 'mode == "backfill"' in body, "backfill 与 daily 的停止条件没区分"


@pytest.mark.unit
def test_backfill_help_text_is_accurate(src):
    """文案不能再说「只能取最新 1 篇」（已能翻页）。"""
    assert "只能取最新 1 篇" not in src, "旧的能力限制文案仍在（与实现矛盾）"
    assert "已恢复翻页" in src, "缺翻页能力说明"


@pytest.mark.unit
def test_auth_errors_are_actionable_after_renewal(src):
    """鉴权类报错要**分码**给出可执行的下一步。

    【2026-10-21 改契约】旧断言要求 -2041 也提示"扫码"，但实测会话是好的
      （shelf/sync 416 本真实书），扫码修不了接口拒绝。新契约：
        -2010/-2012（会话不可用）→ 提示重新扫码；
        -2041（接口拒绝）      → 明确写"会话有效、不是登录过期"，**不许**叫用户扫码。
    """
    assert "class EndpointRefused" in src, "缺少 EndpointRefused（-2041 不是会话问题）"
    assert "class AuthError" in src, "缺少 AuthError（会话真的不可用）"
    i = src.index("def fetch_articles(")
    body = src[i:i + 2600]
    assert "_ENDPOINT_REFUSED" in body and "_SESSION_CODES" in body, \
        "articles 报错没按错误码分层"
    # 扫码提示只能出现在 AuthError 分支里
    j = body.find("raise AuthError")
    assert j > 0, "缺 AuthError 分支"
    assert "_weread_login.mjs" in body[j:], "会话真的失效时仍应给出扫码脚本"
    k = body.find("raise EndpointRefused")
    assert 0 < k < j
    assert "_weread_login.mjs" not in body[k:j], \
        "-2041 分支里不要叫用户扫码（实测会话有效，扫码无效）"


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
