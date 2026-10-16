# -*- coding: utf-8 -*-
"""验证线上 5000 端口是否已加载「外文研报」新路由。"""
import json
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕过系统代理

out = []
for path in ("/api/foreign/jobs", "/api/foreign/job/doesnotexist", "/api/warrant/overview"):
    try:
        r = opener.open("http://127.0.0.1:5000" + path, timeout=20)
        body = r.read(400).decode("utf-8", "replace")
        out.append("GET %s -> %s  %s" % (path, r.status, body[:200].replace("\n", " ")))
    except urllib.error.HTTPError as e:
        out.append("GET %s -> HTTP %s  %s" % (path, e.code, e.read(200).decode("utf-8", "replace").replace("\n", " ")))
    except Exception as e:
        out.append("GET %s -> %s: %s" % (path, type(e).__name__, str(e)[:200]))

# 页面本身应包含新 tab
try:
    r = opener.open("http://127.0.0.1:5000/", timeout=20)
    html = r.read().decode("utf-8", "replace")
    out.append("GET / -> %s len=%d" % (r.status, len(html)))
    for needle in ('data-tab="foreign"', 'id="tab-foreign"', "startForeign", "loadForeignJobs"):
        out.append("   contains %r : %s" % (needle, needle in html))
except Exception as e:
    out.append("GET / -> %s: %s" % (type(e).__name__, str(e)[:200]))

out_path = __file__.replace("foreign_online_check.py", "_foreign_online_check.txt")
open(out_path, "w", encoding="utf-8").write("\n".join(out))
print("\n".join(out))
