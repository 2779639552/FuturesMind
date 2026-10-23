# -*- coding: utf-8 -*-
"""第二组对照：会话到底是不是"已登录态"，以及 cover 能拿到什么。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wechat_weread_collector import BASE, _get, _session  # noqa: E402

print("[A] /web/shelf/sync 结构")
r = _session().get(BASE + "/web/shelf/sync", timeout=30)
d = r.json()
print("    top keys:", sorted(d.keys())[:12])
books = d.get("books") or []
print("    errCode=%s 书架条目=%d synckey=%s" % (
    d.get("errCode"), len(books), "有" if d.get("synckey") else "无"))
if books:
    b = books[0]
    print("    首本字段:", sorted(b.keys())[:10])

print("\n[B] /web/mp/cover 结构")
r = _get("/web/mp/cover", {"bookId": "MP_WXS_3869147758"}, timeout=40)
d = r.json()
print("    top keys:", sorted(d.keys()))
print("    errCode=%s reviewId=%s" % (d.get("errCode"), str(d.get("reviewId"))[:40]))
for k in ("title", "mp_name", "author", "time", "updateTime"):
    if k in d:
        print("    %-10s %s" % (k, str(d.get(k))[:50]))

print("\n[C] /web/mp/articles 换 3 组参数（判断是参数问题还是接口级拒绝）")
for params in (
    {"bookId": "MP_WXS_3869147758", "offset": 0, "limit": 20},
    {"bookId": "MP_WXS_3869147758", "offset": 20},
    {"bookId": "MP_WXS_3591344548", "offset": 0},
):
    r = _get("/web/mp/articles", params, timeout=40)
    try:
        j = r.json()
    except Exception:
        j = {}
    print("    %-52s → errCode=%s" % (str(params), j.get("errCode")))
