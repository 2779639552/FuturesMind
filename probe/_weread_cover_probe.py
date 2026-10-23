# -*- coding: utf-8 -*-
"""探测 `/web/mp/cover`（疑似替代已被废弃的 `/web/mp/articles` 的轻量增量接口）。

═══ 背景（2026-10-16 大转弯）═══
同一份 cookie：
  · `GET /web/shelf/sync`        → **HTTP 200 / 411 本书 / 无 errCode** ⇒ **登录态有效**
  · `GET /web/mp/articles?...`   → `-2012`（15:41）→ `-2041`（16:15）⇒ **接口层**问题
⇒ 之前的诊断方向（"会话失效 → 必须天天扫码"）**被推翻**：会话没问题，是那个接口不可用了。

外部证据一致：有活跃开源项目（we-mp-rss）记载"新版微信读书**已废弃 /web/mp/articles**
（实测恒返 -2041），前端 JS 里也不再有列表接口"，替代方案是
  · `GET /web/mp/cover?bookId=MP_WXS_xxx`  → 该号**最新一篇**（reviewId + 标题 + 封面）
  · `GET /web/mp/content?reviewId=xxx`     → 正文（#js_content）
且 `reviewId` 的 token 段**就是** `mp.weixin.qq.com/s/{token}` 原文直链。

═══ 纪律 ═══
今天已多次打 mp 接口（其中一层是风控）⇒ 本探针**只打 1 个号、最多 2 个请求**
（cover + 可选 content），宁少勿多。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
CK = json.loads((Path.home() / ".tradingagents/weread/cookies.json").read_text(encoding="utf-8"))
HDR = "; ".join("%s=%s" % (c["name"], c["value"]) for c in CK["cookies"])
H = {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
     "Referer": "https://weread.qq.com/", "Cookie": HDR}

MEAN = {"-2010": "未登录/用户不存在", "-2012": "登录超时",
        "-2041": "认证/风控错误（或该接口已废弃）", "-2042": "未收录"}

BOOK = ("混沌天成研究", "MP_WXS_3869147758")

print("=" * 74)
print("① GET /web/mp/cover?bookId=%s   （**1 个请求**）" % BOOK[1])
print("=" * 74)
r = requests.get("https://weread.qq.com/web/mp/cover",
                 params={"bookId": BOOK[1]}, headers=H, timeout=40)
print("  HTTP %s   %d 字节" % (r.status_code, len(r.content)))
try:
    d = r.json() or {}
except Exception:
    d = {}
code = d.get("errCode", d.get("errcode"))
if code is None:
    print("  ★ **可用**！返回字段: %s" % ", ".join(list(d.keys())[:14]))
    print("  回包: %s" % json.dumps(d, ensure_ascii=False)[:500])
    rid = str(d.get("reviewId") or (d.get("review") or {}).get("reviewId") or "")
    if rid:
        tok = rid.split("_")[-1]
        print()
        print("  reviewId = %s" % rid[:60])
        if len(tok) > 10:
            print("  ★ 原文直链 https://mp.weixin.qq.com/s/%s" % tok)
else:
    print("  ✘ errCode=%s  %s" % (code, MEAN.get(str(code), "")))
    print("  回包: %s" % json.dumps(d, ensure_ascii=False)[:300])

print()
print("=" * 74)
print("② 结论")
print("=" * 74)
if code is None:
    print("  ✔ /web/mp/cover 可用 ⇒ **不必整表翻页**。")
    print("    用「每号 1 个请求」的 cover 增量（拿最新一篇 + reviewId 去重）即可追踪新文，")
    print("    风控暴露面比原来（6 号 × 翻页 ≈ 127 请求）小一个数量级。")
    print("    正文可走 /web/mp/content?reviewId=（同 cookie），也可走原文直链（免登录）。")
else:
    print("  ✘ cover 也不可用 ⇒ mp 模块整体受限，需换路线。")
sys.exit(0 if code is None else 1)
