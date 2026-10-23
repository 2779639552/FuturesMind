"""微信读书通道「止血」护栏（2026-10-21）。

【背景】10-19 上线自动续期后，-2012（登录超时）确实消失了，但换成了 **-2041**。
  当时的代码把 -2041 和 -2010/-2012 归为同一类"鉴权失败"，于是：
    ① 误报"长期凭据失效，请扫码"——实测会话是好的（shelf/sync 416 本真实书）；
    ② `raise SystemExit` 穿透单号的 try，**整轮 6 个号全部中断**；
    ③ 每撞一次 -2041 就再续期一次、再重试 4 次 ⇒ 续期风暴，只会把账号推得更深。

【本文件守五条】
  1. -2041 → `EndpointRefused`（普通异常），**不续期、不重试**；
  2. -2010/-2012 → 才允许续期，且每进程**最多 1 次**自动续期；
  3. 单号遇到上述任何一种 → **降级到 cover** 继续，不许打断整轮；
  4. 采集路径里**不许再出现 SystemExit**（AST 检查，防止回潮）；
  5. dry-run 不许覆写真实结果文件（调度器按 mtime 读它当本轮产出）。
"""

from __future__ import annotations

import ast
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wechat_weread_collector as M  # noqa: E402


# ---------------------------------------------------------------------------
# 假响应 / 假 session
# ---------------------------------------------------------------------------
class _Resp:
    def __init__(self, payload: dict, status: int = 200):
        self._p = payload
        self.status_code = status
        self.content = json.dumps(payload).encode("utf-8")
        self.headers: dict = {}

    def json(self):
        return self._p


class _Session:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0

    def get(self, *a, **k):
        self.calls += 1
        return _Resp(self.payload)


@pytest.fixture(autouse=True)
def _reset_renew_budget(monkeypatch):
    """每条用例都从"额度未用"开始，否则测试之间互相污染。"""
    monkeypatch.setattr(M, "_AUTO_RENEWS", 0)
    monkeypatch.setattr(M, "say", lambda *a, **k: None)
    yield


# ---------------------------------------------------------------------------
# ① 错误码分层
# ---------------------------------------------------------------------------
def test_2041_raises_endpoint_refused_not_system_exit(monkeypatch):
    """★ -2041 必须是普通异常 —— SystemExit 会穿透 collect_account 的 try。"""
    monkeypatch.setattr(M, "_get", lambda *a, **k: _Resp({"errCode": -2041, "errMsg": "-2041"}))
    with pytest.raises(M.EndpointRefused):
        M.fetch_articles("MP_WXS_1", offset=0)
    assert not issubclass(M.EndpointRefused, SystemExit)


def test_2012_raises_auth_error(monkeypatch):
    """-2012 是会话真的不可用（续期也没救回来）→ AuthError。"""
    monkeypatch.setattr(M, "_get", lambda *a, **k: _Resp({"errCode": -2012, "errMsg": "登录超时"}))
    with pytest.raises(M.AuthError):
        M.fetch_articles("MP_WXS_1", offset=0)


def test_2041_does_not_trigger_renewal(monkeypatch):
    """★ 接口拒绝不是会话问题：续期只会加重风控标记。"""
    monkeypatch.setattr(M, "_session", lambda: _Session({"errCode": -2041}))
    monkeypatch.setattr(M, "_sync_cookies_if_changed", lambda: None)
    renewed = {"n": 0}

    def _renew(**k):
        renewed["n"] += 1
        return True

    monkeypatch.setattr(M, "renew_session", _renew)
    r = M._get("/web/mp/articles", {"bookId": "x"})
    assert r.json()["errCode"] == -2041
    assert renewed["n"] == 0, "-2041 不应触发续期"


def test_2012_renews_at_most_once(monkeypatch):
    """★ -2012 允许续期，但同一进程只给一次额度（防续期风暴）。"""
    monkeypatch.setattr(M, "_session", lambda: _Session({"errCode": -2012}))
    monkeypatch.setattr(M, "_sync_cookies_if_changed", lambda: None)
    renewed = {"n": 0}

    def _renew(**k):
        renewed["n"] += 1
        return True

    monkeypatch.setattr(M, "renew_session", _renew)
    M._get("/web/mp/articles", {})
    M._get("/web/mp/articles", {})
    M._get("/web/mp/articles", {})
    assert renewed["n"] == 1, "自动续期必须有预算，实测一轮能撞十几次"


def test_auto_renew_budget_is_one():
    assert M._auto_renew() is True
    assert M._auto_renew() is False
    assert M._auto_renew() is False


# ---------------------------------------------------------------------------
# ② 单号降级
# ---------------------------------------------------------------------------
def _acct():
    return {"name": "测试号", "book_id": "MP_WXS_TEST"}


def _state():
    return {"accounts": {}}


def _one_article():
    return {"original_id": "oid1", "review_id": "MP_WXS_TEST_oid1", "title": "最新一篇",
            "summary": "", "time": 0, "push_time": 0, "mp_rank": 0, "read_num": 0}


def test_collect_account_degrades_to_cover(monkeypatch):
    """★ 列表接口被拒 → 用 cover 取最新 1 篇继续，不许抛异常。"""
    monkeypatch.setattr(M, "known_titles", lambda: set())
    monkeypatch.setattr(M, "fetch_articles",
                        lambda *a, **k: (_ for _ in ()).throw(M.EndpointRefused("拒绝")))
    monkeypatch.setattr(M, "fetch_latest", lambda *a, **k: [_one_article()])
    acct = _acct()
    got = M.collect_account(acct, _state(), mode="daily", limit=20,
                            with_content=False, max_pages=1)
    assert len(got) == 1 and got[0]["title"] == "最新一篇"
    assert acct["_degraded"] == "articles_refused"


def test_collect_account_survives_cover_auth_error(monkeypatch):
    """列表被拒后 cover 也失败：本号 0 产出，但不许打断整轮。"""
    monkeypatch.setattr(M, "known_titles", lambda: set())
    monkeypatch.setattr(M, "fetch_articles",
                        lambda *a, **k: (_ for _ in ()).throw(M.EndpointRefused("拒绝")))
    monkeypatch.setattr(M, "fetch_latest",
                        lambda *a, **k: (_ for _ in ()).throw(M.AuthError("cover 也失败")))
    acct = _acct()
    got = M.collect_account(acct, _state(), mode="daily", limit=20,
                            with_content=False, max_pages=1)
    assert got == []
    # 列表先被拒、兜底又失败 ⇒ 两个原因都要留下
    assert acct["_degraded"] == "articles_refused+cover_failed"


def test_one_bad_account_does_not_stop_the_rest(monkeypatch):
    """★ 这是本次事故最直接的损失：第一个号失败后，其余 5 个号连试都没试。"""
    monkeypatch.setattr(M, "known_titles", lambda: set())
    calls = {"n": 0}

    def _fa(*a, **k):
        calls["n"] += 1
        # 第一个号只会调 1 次 fetch_articles（异常直接跳出翻页循环），
        # 所以阈值是 <=1，不是 <=2（第一版写错，导致"好号"也被拒）。
        if calls["n"] <= 1:
            raise M.EndpointRefused("拒绝")
        return [_one_article()]

    monkeypatch.setattr(M, "fetch_articles", _fa)
    monkeypatch.setattr(M, "fetch_latest", lambda *a, **k: [])   # 第一个号 cover 无产出
    acct1 = {"name": "坏号", "book_id": "MP_WXS_BAD"}
    got1 = M.collect_account(acct1, _state(), mode="daily", limit=20,
                             with_content=False, max_pages=1)
    assert got1 == []

    acct2 = {"name": "好号", "book_id": "MP_WXS_OK"}
    got2 = M.collect_account(acct2, {"accounts": {}}, mode="daily", limit=20,
                             with_content=False, max_pages=1)
    assert len(got2) == 1, "前一个号失败后，后续号必须照常采"


# ---------------------------------------------------------------------------
# ③ 结构：采集路径不许再有 SystemExit / 单结果文件不许被 dry-run 覆写
# ---------------------------------------------------------------------------
def test_no_systemexit_in_fetch_paths():
    """★ AST 级检查：`fetch_articles` / `fetch_latest` / `collect_account` 里
    不许出现 `raise SystemExit` —— 它会穿透单号的 try，让整轮失败。"""
    src = io.open(M.__file__, encoding="utf-8").read()
    tree = ast.parse(src)
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name not in ("fetch_articles", "fetch_latest", "collect_account"):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Raise) and isinstance(sub.exc, ast.Call):
                fn = sub.exc.func
                if isinstance(fn, ast.Name) and fn.id == "SystemExit":
                    bad.append("%s:%d" % (node.name, sub.lineno))
    assert not bad, "采集路径又出现了 SystemExit：%s" % bad


def test_dryrun_writes_separate_result_file():
    """★ dry-run 不许覆写真实结果文件（调度器按 mtime 读它当本轮产出）。

    实测：一次 `--dry-run` 把上一轮"新增 59 篇"冲成 0，告警与入库判断都会跟着错。
    """
    src = io.open(M.__file__, encoding="utf-8").read()
    assert "mp_collect_result.dryrun.json" in src
    assert "res_json = F_RESULT if not args.dry_run else" in src


def test_degradation_is_machine_readable():
    """结果 JSON 必须带 degraded 字段，别让下游把"只看到最新一篇"读成"没有新文"。"""
    src = io.open(M.__file__, encoding="utf-8").read()
    assert '"degraded": {"accounts": degraded_accts' in src
