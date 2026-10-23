# -*- coding: utf-8 -*-
"""验证新方案的关键一腿：**正文走原文直链（免登录）**。

═══ 为什么要验 ═══
`/web/mp/cover` 已实测可用，返回 `reviewId`，其 token 段就是
`https://mp.weixin.qq.com/s/{token}` —— **永久原文直链**。
若这条链接**免登录**就能取到正文，那么新方案只需：
   每号 1 个 cover 请求（要 cookie）→ 拿到 reviewId/标题/直链 → 正文走**公开**页面（不要 cookie）
⇒ 需要 cookie 的环节从"整表翻页 ≈127 请求"降到"**6 个请求**"，
   风控暴露面小一个数量级；而且与已经稳定跑了近一个月的「合集通道」同一取正文方式。

纪律：mp.weixin.qq.com 是**公开站**（合集通道每天都在打它）⇒ 本次只打 **1 个** 直链。
"""
from __future__ import annotations

import re
import sys

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
URL = "https://mp.weixin.qq.com/s/6sbTBEdZjNgzAAhHNw~-2A"

print("=" * 74)
print("① GET %s   （**1 个请求，不带任何 cookie**）" % URL)
print("=" * 74)
r = requests.get(URL, headers={"User-Agent": UA}, timeout=40)
html = r.text
print("  HTTP %s   %d 字节" % (r.status_code, len(html)))
for pat, label in (("<title>", "标题"), ('id="js_content"', "#js_content 正文容器"),
                   ("var msg_title", "msg_title 变量"), ("正文", "含「正文」字样"),
                   ("环境异常", "风控提示「环境异常」"), ("去 App 查看", "「去 App 查看」限制")):
    print("   %-24s %s" % (label, "有" if pat in html else "无"))

m = re.search(r"<title>([^<]{0,80})</title>", html)
if m:
    print("\n  页面标题: %s" % m.group(1).strip()[:70])

print()
print("=" * 74)
print("② 结论")
print("=" * 74)
ok = r.status_code == 200 and ('id="js_content"' in html or "var msg_title" in html)
if ok:
    print("  ✔ 原文直链**免登录可取正文** ⇒ 新方案成立：")
    print("     每号 1 个 cover 请求（需 cookie）→ reviewId + 标题 + 直链")
    print("     正文走公开页面（**不需 cookie**）→ 与「合集通道」同一取法")
    print("     ⇒ 需 cookie 的请求量 127 → 6")
else:
    print("  ✘ 直链取不到正文（可能需要 cookie 或有登录墙）⇒ 正文得另想办法")
sys.exit(0 if ok else 1)
