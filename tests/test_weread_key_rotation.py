# -*- coding: utf-8 -*-
"""会话密钥轮换的自动同步（2026-10-19）。

服务端可能在**任意响应**里通过 `Set-Cookie` 换掉 `wr_skey`。
`requests.Session` 会自动收进内存 jar，但**磁盘副本不会自动更新** ——
下次进程启动又拿旧值，这正是"重登后 1.5 小时又失效"的直接成因。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from requests.cookies import RequestsCookieJar


class _Resp:
    def __init__(self, payload):
        self._p = payload
        self.content = json.dumps(payload).encode("utf-8")

    def json(self):
        return self._p


class _FakeSession:
    """最小可用 Session：只提供 cookies jar 与 get。"""

    def __init__(self, skey="OLD0001", rotate_to=None):
        self.cookies = RequestsCookieJar()
        self.cookies.set("wr_vid", "246349406", domain=".weread.qq.com", path="/")
        self.cookies.set("wr_rt", "web%40rt", domain=".weread.qq.com", path="/")
        self.cookies.set("wr_skey", skey, domain=".weread.qq.com", path="/")
        self.rotate_to = rotate_to

    def get(self, url, params=None, headers=None, timeout=None):
        if self.rotate_to:
            self.cookies.set("wr_skey", self.rotate_to, domain=".weread.qq.com", path="/")
        return _Resp({"reviews": []})


def _write_cookies(path: Path, skey: str) -> None:
    path.write_text(json.dumps({
        "savedAt": "2026-10-19T00:00:00",
        "cookies": [
            {"name": "wr_vid", "value": "246349406", "domain": ".weread.qq.com", "path": "/"},
            {"name": "wr_rt", "value": "web%40rt", "domain": ".weread.qq.com", "path": "/"},
            {"name": "wr_skey", "value": skey, "domain": ".weread.qq.com", "path": "/"},
        ],
    }, ensure_ascii=False), encoding="utf-8")


def _disk_skey(path: Path) -> str:
    j = json.loads(path.read_text(encoding="utf-8"))
    return next(c["value"] for c in j["cookies"] if c["name"] == "wr_skey")


@pytest.mark.unit
def test_sync_persists_rotated_key(monkeypatch, tmp_path):
    """★ 内存 jar 的新密钥必须写回磁盘；且第二次调用不再重复写（幂等）。"""
    import wechat_weread_collector as C

    f = tmp_path / "cookies.json"
    _write_cookies(f, "OLD0001")
    monkeypatch.setattr(C, "F_COOKIES", f)
    monkeypatch.setattr(C, "_SESSION", _FakeSession(skey="NEW9999"))
    monkeypatch.setattr(C, "_DISK_FP", None)

    changed = C._sync_cookies_if_changed()
    assert changed is True, "jar 与磁盘不一致时没有触发同步"
    assert _disk_skey(f) == "NEW9999", "轮换后的新密钥没有写回磁盘"

    assert C._sync_cookies_if_changed() is False, "已同步后仍判定为变化（会反复写文件）"
    assert _disk_skey(f) == "NEW9999"


@pytest.mark.unit
def test_no_write_when_unchanged(monkeypatch, tmp_path):
    import wechat_weread_collector as C

    f = tmp_path / "cookies.json"
    _write_cookies(f, "SAME1234")
    monkeypatch.setattr(C, "F_COOKIES", f)
    monkeypatch.setattr(C, "_SESSION", _FakeSession(skey="SAME1234"))
    monkeypatch.setattr(C, "_DISK_FP", None)

    before = f.stat().st_mtime_ns
    assert C._sync_cookies_if_changed() is False
    assert f.stat().st_mtime_ns == before, "没有变化却重写了文件（无谓 IO + 可能撞并发）"


@pytest.mark.unit
def test_every_response_triggers_rotation_check(monkeypatch, tmp_path):
    """★★ 必须在**每次请求后**检查，而不是只在续期时。

    【为什么】服务端会在普通业务响应里顺手轮换密钥（浏览器实测：打开页面即换）。
      只在 renew_session 后同步 ⇒ 普通请求换掉的密钥会被丢掉，磁盘长期落后。
    """
    import wechat_weread_collector as C

    f = tmp_path / "cookies.json"
    _write_cookies(f, "OLD0001")
    monkeypatch.setattr(C, "F_COOKIES", f)
    monkeypatch.setattr(C, "_SESSION", _FakeSession(skey="OLD0001", rotate_to="ROT0001"))
    monkeypatch.setattr(C, "_DISK_FP", None)

    C._get("/web/shelf/sync", {})
    assert _disk_skey(f) == "ROT0001", "普通请求触发的轮换没有被写回磁盘"


@pytest.mark.unit
def test_get_uses_session_not_static_cookie_header():
    """前提：请求必须走 Session（否则 Set-Cookie 根本收不进 jar）。"""
    src = (Path(__file__).resolve().parents[1] / "wechat_weread_collector.py").read_text(encoding="utf-8")
    i = src.index("def _get(")
    body = src[i:i + 900]
    assert "_session().get(" in body, "_get 没走 Session ⇒ 收不到 Set-Cookie"
    assert "_sync_cookies_if_changed()" in body, "_get 没有在响应后检查轮换"
