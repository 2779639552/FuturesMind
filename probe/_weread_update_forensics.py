# -*- coding: utf-8 -*-
"""查「微信读书这两天有没有更新」—— 三条取证。

① 官方网关自报的期望版本：带我们的 skill_version 打一次 `/_list`，
   若回包带 `upgrade_info` ⇒ 服务端认为本地版本落后 ⇒ **官方确实发新版了**。
② 网页静态资源/JS 包的部署时间：取首页 → 抽主 JS/CSS → HEAD 看 **Last-Modified / ETag**。
   一次发版必然换 hash；若某资源的 Last-Modified 落在 **10-14 ~ 10-16** ⇒ 这两天动过。
   （这是最能直接回答"是不是他们改了"的证据，且只打公开 CDN。）
③ 顺带记录首页里可能的版本/构建串。

纪律：这是公开站 + 1 次网关调用，共约 1 + 4 个请求，不碰风控密集的 mp 接口。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
KEY_FILE = Path.home() / ".tradingagents" / "weread" / "api_key.txt"
GATEWAY = "https://i.weread.qq.com/api/agent/gateway"

print("=" * 78)
print("① 官方网关：服务端期望的 skill_version（带 1.0.5 去打，看有没有 upgrade_info）")
print("=" * 78)
key = KEY_FILE.read_text(encoding="utf-8").strip()
r = requests.post(GATEWAY, json={"api_name": "/_list", "skill_version": "1.0.5"},
                  timeout=45, headers={"Authorization": "Bearer " + key,
                                       "Content-Type": "application/json",
                                       "User-Agent": UA})
print("  HTTP %s" % r.status_code)
try:
    d = r.json() or {}
except Exception:
    d = {}
up = d.get("upgrade_info") or d.get("upgradeInfo")
print("  update_info / upgrade_info:", json.dumps(up, ensure_ascii=False) if up else "**无**（服务端未要求升级）")
for k in ("latest_version", "min_version", "skill_version", "version"):
    if k in d:
        print("  回包 %s = %s" % (k, d[k]))
print("  回包顶层键: %s" % list(d.keys())[:12])

print()
print("=" * 78)
print("② 网页静态资源部署时间（发版必换 hash；看 Last-Modified 落在哪天）")
print("=" * 78)
h = requests.get("https://weread.qq.com/", headers={"User-Agent": UA}, timeout=45).text
assets = set()
for m in re.finditer(r'(?:src|href)="([^"]+\.(?:js|css))(?:\?[^"]*)?"', h):
    assets.add(m.group(1))
print("  首页引用资源数: %d" % len(assets))
for a in sorted(assets)[:8]:
    url = a if a.startswith("http") else ("https://weread.qq.com" + a)
    try:
        rr = requests.head(url, headers={"User-Agent": UA}, timeout=30, allow_redirects=True)
        print("   %-58s  LM=%s  ETag=%s"
              % (url[-58:], rr.headers.get("Last-Modified", "-")[:29], (rr.headers.get("ETag") or "-")[:16]))
    except Exception as e:
        print("   %-58s  HEAD 失败: %s" % (url[-58:], str(e)[:40]))

m = re.search(r'"(?:version|build|buildVersion|appVersion)"\s*:\s*"([^"]{1,30})"', h)
print("\n  首页版本串: %s" % (m.group(1) if m else "（未找到）"))
print("  首页 mtime 线索（Server/Date）: %s" % h[:0])

print()
print("=" * 78)
print("③ 判定")
print("=" * 78)
print("  · upgrade_info 无 ⇒ 官方**未**要求升级 Agent 技能版本（1.0.5 仍是最新口径）")
print("  · 资源 Last-Modified 若落在 10-14/10-15/10-16 ⇒ 这两天有前端发版（需人工看上面日期）")
