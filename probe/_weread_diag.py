# -*- coding: utf-8 -*-
"""微信读书通道诊断：分开测「账号会话」与「内容接口」。

【为什么必须分开】-2041 有两种完全不同的成因：
  ① 长期凭据（wr_vid/wr_rt）真的失效 → 所有需要登录的接口都会拒；
  ② 会话有效、只是内容接口不可用（接口改版/风控/权限）→ shelf 能过、mp 不过。
不看这个区别就会把"接口问题"误判成"要扫码"，或反过来。
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wechat_weread_collector import (  # noqa: E402
    BASE, _get, _session, renew_session, _load_cookie_list, F_COOKIES,
)

def code_of(r):
    try:
        d = r.json()
    except Exception:
        return "非JSON(html %d 字节)" % len(r.content)
    if not isinstance(d, dict):
        return "非dict"
    return "errCode=%s succ=%s errMsg=%s" % (
        d.get("errCode"), d.get("succ"), str(d.get("errMsg") or "")[:60])

print("cookie 条目：")
for c in _load_cookie_list():
    print("  %-8s len=%d" % (c.get("name"), len(str(c.get("value")))))

print("\n[0] POST /web/login/renewal")
ok = renew_session(quiet=True)
print("    renewal 返回 succ=1 ?", ok)

print("\n[1] GET /web/shelf/sync   （账号级：验证会话本身）")
r = _session().get(BASE + "/web/shelf/sync", timeout=30)
print("    http=%d %s" % (r.status_code, code_of(r)))

print("\n[2] GET /web/mp/articles  （内容级：主通道）")
r = _get("/web/mp/articles", {"bookId": "MP_WXS_3869147758", "offset": 0}, timeout=40)
print("    http=%d %s" % (r.status_code, code_of(r)))
if r.status_code == 200 and len(r.content) < 4096:
    print("    body:", r.text[:200])

print("\n[3] GET /web/mp/cover     （内容级：兜底通道）")
r = _get("/web/mp/cover", {"bookId": "MP_WXS_3869147758"}, timeout=40)
print("    http=%d %s" % (r.status_code, code_of(r)))

print("\n[4] GET /web/user          （账号级：另一条独立读数）")
r = _session().get(BASE + "/web/user", timeout=30)
print("    http=%d %s" % (r.status_code, code_of(r)))
