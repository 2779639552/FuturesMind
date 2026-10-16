"""局域网访问令牌守卫测试(2026-09-24)。

全局 before_request: 所有 /api/*(除 login/status)需登录态; 未登录 401。
默认账号 admin/agentsense2026(DB users 表首次启动自动创建)。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import web_app  # noqa: E402


class _FakeAuthDB:
    def __init__(self):
        self._pw = "agentsense2026"

    def ensure_default_user(self):
        pass

    def verify_user(self, username, password):
        return username == "admin" and password == self._pw

    def update_password(self, username, new_password):
        self._pw = new_password
        return True


@pytest.fixture
def auth_env(monkeypatch):
    """开启守卫(去掉测试默认的 AGENTSENSE_AUTH_DISABLE=1) + fake 登录 DB。

    注意: get_db 必须返回**同一个**实例(单例), 否则改密测试里
    update_password 的状态不会在后续 verify_user 中生效。
    """
    monkeypatch.delenv("AGENTSENSE_AUTH_DISABLE", raising=False)
    fake_db = _FakeAuthDB()
    monkeypatch.setattr(web_app, "get_db", lambda: fake_db)
    return web_app.app.test_client()


def test_guard_401_without_token(auth_env):
    r = auth_env.get("/api/research")
    assert r.status_code == 401


def test_guard_allows_login_and_status(auth_env):
    assert auth_env.get("/api/auth/status").status_code == 200
    r = auth_env.post("/api/auth/login", json={"username": "admin", "password": "agentsense2026"})
    assert r.status_code == 200
    assert "auth_token" in (r.headers.get("Set-Cookie") or "")


def test_guard_401_wrong_password(auth_env):
    r = auth_env.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401


def test_guard_allows_after_login(auth_env):
    auth_env.post("/api/auth/login", json={"username": "admin", "password": "agentsense2026"})
    # test_client 自动携带 auth_token cookie → 受保护路由不再 401
    r = auth_env.post("/api/auth/logout")  # logout 受守卫保护, 带 cookie 应放行
    assert r.status_code == 200


# ── 2026-09-24 改密码接口 ──────────────────────────────────────────────

def test_change_password_401_without_login(auth_env):
    r = auth_env.post("/api/auth/change-password",
                      json={"old_password": "agentsense2026", "new_password": "newpass123"})
    assert r.status_code == 401


def test_change_password_ok(auth_env):
    auth_env.post("/api/auth/login", json={"username": "admin", "password": "agentsense2026"})
    r = auth_env.post("/api/auth/change-password",
                      json={"old_password": "agentsense2026", "new_password": "newpass123"})
    assert r.status_code == 200 and r.get_json().get("ok")
    # 改密后旧密码登录应失败
    r2 = auth_env.post("/api/auth/login", json={"username": "admin", "password": "agentsense2026"})
    assert r2.status_code == 401
    # 新密码登录应成功
    r3 = auth_env.post("/api/auth/login", json={"username": "admin", "password": "newpass123"})
    assert r3.status_code == 200


def test_change_password_wrong_old(auth_env):
    auth_env.post("/api/auth/login", json={"username": "admin", "password": "agentsense2026"})
    r = auth_env.post("/api/auth/change-password",
                      json={"old_password": "wrong", "new_password": "newpass123"})
    assert r.status_code == 403


def test_change_password_too_short(auth_env):
    auth_env.post("/api/auth/login", json={"username": "admin", "password": "agentsense2026"})
    r = auth_env.post("/api/auth/change-password",
                      json={"old_password": "agentsense2026", "new_password": "short"})
    assert r.status_code == 400


def test_change_password_same_as_old(auth_env):
    auth_env.post("/api/auth/login", json={"username": "admin", "password": "agentsense2026"})
    r = auth_env.post("/api/auth/change-password",
                      json={"old_password": "agentsense2026", "new_password": "agentsense2026"})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# 免登录开关(2026-09-29):AGENTSENSE_AUTH_DISABLE=1 → 全站免登录
# ---------------------------------------------------------------------------
@pytest.fixture
def auth_off_env(monkeypatch):
    """关闭守卫(显式开关)+ fake DB,模拟 .env 里 AGENTSENSE_AUTH_DISABLE=1。"""
    monkeypatch.setenv("AGENTSENSE_AUTH_DISABLE", "1")

    class _FakeAuthDB2(_FakeAuthDB):
        # /api/research 会真的读库 → 桩必须补齐该方法,否则 500 会被误当"已放行"
        def list_research_reports(self, *a, **kw):
            return []

    fake_db = _FakeAuthDB2()
    monkeypatch.setattr(web_app, "get_db", lambda: fake_db)
    return web_app.app.test_client()


def test_auth_disabled_allows_api_without_token(auth_off_env):
    """开关打开:不带任何 token 也能访问 /api/*,且**真的成功**(200)。

    【为什么不写 != 401】那样连 500 也算通过 —— 端点内部报错会被当成"已放行"的
    假绿(本用例初版就踩了这个坑:桩缺方法 → 500 → 断言仍然过)。
    """
    assert auth_off_env.get("/api/research").status_code == 200


def test_auth_disabled_status_reports_authenticated(auth_off_env):
    """开关打开时 /api/auth/status 必须回 authenticated=true。

    【为什么要这条】前端 checkAuth() 只在**非 401** 时才 hideLogin();若这里仍回
    {authenticated:false}(HTTP 200),登录遮罩会常驻,用户还得手点一次登录 ——
    "免登录"就没真正做到。这是最容易漏的一环。
    """
    r = auth_off_env.get("/api/auth/status")
    assert r.status_code == 200
    body = r.get_json()
    assert body.get("authenticated") is True
    assert body.get("auth_disabled") is True


def test_auth_disabled_change_password_is_explicit_not_401(auth_off_env):
    """开关打开时改密端点给明确提示而非 401(免登录下无 token 可依,401 会让人以为是 bug)。"""
    r = auth_off_env.post("/api/auth/change-password", json={"old_password": "a" * 8, "new_password": "b" * 8})
    assert r.status_code == 400
    assert "已关闭登录鉴权" in (r.get_json() or {}).get("error", "")


def test_auth_reenabled_restores_guard(monkeypatch):
    """开关关掉(不设/设 0)→ 守卫恢复,未登录仍 401。防"开了就回不去"。"""
    for val in (None, "0"):
        if val is None:
            monkeypatch.delenv("AGENTSENSE_AUTH_DISABLE", raising=False)
        else:
            monkeypatch.setenv("AGENTSENSE_AUTH_DISABLE", val)
        monkeypatch.setattr(web_app, "get_db", lambda: _FakeAuthDB())
        c = web_app.app.test_client()
        assert c.get("/api/research").status_code == 401, f"值={val!r} 时守卫应恢复"
        assert c.get("/api/auth/status").get_json().get("authenticated") is False
