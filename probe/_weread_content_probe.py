# -*- coding: utf-8 -*-
"""① 直链为何取不到正文（看它实际返回了什么）；② 试正确的正文接口 /web/mp/content。

【外部资料线索】weread-mp README（2026-10 更新）记载：
  · `originalId` 已从 22 字符短链 token 变成 **75 字符不透明 id**
     旧 ggsDTrCt3N1elArnpLf9uw → mp.weixin.qq.com/s/{id}  ✅
     新 WR1EkJ9AcEvy2XsC4QGyH1gj9u83Gg4NL62g… → mp.weixin.qq.com/s/{id}  ❌ 参数错误
  · ⇒ "read 不再自己拼 URL，**改走 `/web/mp/content` 换真实链接**"
⇒ 本轮验证：直链为什么不行 + `/web/mp/content?reviewId=` 能否拿到正文/真实链接。

纪律：共 **2 个请求**（1 个公开站、1 个 weread 接口）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
CK = json.loads((Path.home() / ".tradingagents/weread/cookies.json").read_text(encoding="utf-8"))
HDR = "; ".join("%s=%s" % (c["name"], c["value"]) for c in CK["cookies"])
RID = "MP_WXS_3869147758_6sbTBEdZjNgzAAhHNw~-2A"
TOKEN = RID.split("_", 2)[-1]
URL = "https://mp.weixin.qq.com/s/" + TOKEN

print("=" * 74)
print("① 直链返回的到底是什么（公开站，1 个请求）")
print("=" * 74)
r = requests.get(URL, headers={"User-Agent": UA}, timeout=40)
h = r.text
print("  HTTP %s  %d 字节" % (r.status_code, len(h)))
for pat in ("参数错误", "环境异常", "去 App 查看", "js_content", "msg_title",
            "该内容已被发布者删除", "此内容因违规无法查看", "验证"):
    if pat in h:
        i = h.index(pat)
        print("   命中 %-14s → …%s…" % (pat, h[max(0, i - 40):i + 60].replace("\n", " ")[:110]))
m = re.search(r'<meta property="og:title" content="([^"]{0,60})', h) or re.search(r"<title>([^<]{0,60})", h)
print("  页面标题: %r" % (m.group(1).strip() if m else ""))
print("  片段: %s" % h[:220].replace("\n", " "))

print()
print("=" * 74)
print("② /web/mp/content?reviewId=…（weread 接口，1 个请求）")
print("=" * 74)
r2 = requests.get("https://weread.qq.com/web/mp/content", params={"reviewId": RID},
                  headers={"User-Agent": UA, "Accept": "application/json, text/plain, */*",
                           "Referer": "https://weread.qq.com/", "Cookie": HDR}, timeout=45)
print("  HTTP %s  %d 字节" % (r2.status_code, len(r2.content)))
d = {}
try:
    d = r2.json() or {}
except Exception:
    pass
code = d.get("errCode", d.get("errcode"))
print("  errCode=%s  顶层键=%s" % (code if code is not None else "(无)", list(d.keys())[:14]))
blob = json.dumps(d, ensure_ascii=False)
print("  回包片段: %s" % blob[:600])
for pat in ("mp.weixin.qq.com/s/", "js_content", "content", "html"):
    if pat in blob:
        i = blob.index(pat)
        print("   含 %-20s → …%s…" % (pat, blob[max(0, i - 50):i + 90]))
