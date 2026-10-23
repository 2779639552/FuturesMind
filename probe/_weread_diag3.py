# -*- coding: utf-8 -*-
"""第三组对照：接口级拒绝 vs 会话失效。

① 匿名（不带任何 cookie）请求 mp/articles —— 若同样是 -2041，说明是**接口级**拒绝；
② cover 是否支持翻页 —— 决定降级后能捞回几篇。
"""
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wechat_weread_collector import BASE, UA, _get, _session  # noqa: E402

H = {"User-Agent": UA, "Accept": "application/json, text/plain, */*", "Referer": BASE + "/"}

print("[A] 匿名（无 cookie）请求 /web/mp/articles")
s = requests.Session()
r = s.get(BASE + "/web/mp/articles",
          params={"bookId": "MP_WXS_3869147758", "offset": 0}, headers=H, timeout=30)
try:
    d = r.json()
except Exception:
    d = {}
print("    http=%d errCode=%s errMsg=%s" % (r.status_code, d.get("errCode"), str(d.get("errMsg"))[:40]))

print("\n[B] 匿名请求 /web/mp/cover")
r = s.get(BASE + "/web/mp/cover", params={"bookId": "MP_WXS_3869147758"}, headers=H, timeout=30)
try:
    d = r.json()
except Exception:
    d = {}
print("    http=%d errCode=%s reviewId=%s" % (r.status_code, d.get("errCode"), str(d.get("reviewId"))[:30]))

print("\n[C] cover 是否支持翻页（带 offset / 其它参数）")
for params in ({"bookId": "MP_WXS_3869147758", "offset": 1},
               {"bookId": "MP_WXS_3869147758", "offset": 5}):
    r = _get("/web/mp/cover", params, timeout=30)
    try:
        d = r.json()
    except Exception:
        d = {}
    print("    %-50s → reviewId=%s" % (str(params), str(d.get("reviewId"))[:34]))

print("\n[D] 带登录态请求 mp/articles 时看响应头（是否被风控标记）")
r = _session().get(BASE + "/web/mp/articles",
                   params={"bookId": "MP_WXS_3869147758", "offset": 0}, timeout=30)
print("    status=%d server=%s x-cache=%s set-cookie=%s" % (
    r.status_code, r.headers.get("server"), r.headers.get("x-cache"),
    ",".join(c.split("=")[0] for c in r.headers.get("set-cookie", "").split(", "))[:80]))
