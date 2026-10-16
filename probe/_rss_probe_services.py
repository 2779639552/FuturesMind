# -*- coding: utf-8 -*-
r"""验证第三方公众号 RSS 服务的可用性（针对三个期货机构号）。

要回答三个问题：
  1. wechat2rss 公开实例是否活着？它的公众号**列表/搜索**在哪？
  2. 三个目标号是否在它的免费收录列表里？（免费版只收录 ~485 个技术/安全/AI/财经号）
  3. 另一个新服务 wechatrss.waytomaster.com 是否可用（开源项目 tmwgsicp/wechat-download-api 的 SaaS 版）？
只读。
"""
import json
import re
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
H = {"User-Agent": UA, "Accept": "text/html,application/json,*/*"}

TARGETS = ["中信期货研究", "混沌天成研究", "中信建投期货研究", "多空逻辑"]

CANDIDATES = [
    "https://wechat2rss.xlab.app/",
    "https://wechat2rss.xlab.app/list",
    "https://wechat2rss.xlab.app/feed/list",
    "https://wechat2rss.xlab.app/api/list",
    "https://wechatrss.waytomaster.com/",
]

for u in CANDIDATES:
    print("=" * 80)
    print("▸ %s" % u)
    try:
        r = requests.get(u, headers=H, timeout=20, allow_redirects=True)
        raw = r.content
        ct = r.headers.get("content-type", "")
        print("  HTTP %s  final=%s" % (r.status_code, r.url))
        print("  bytes=%d  content-type=%s" % (len(raw), ct[:60]))
        if not raw:
            print("  (空响应)")
            continue
        txt = raw.decode("utf-8", errors="replace")
        # 若是 JSON，看看结构
        if "json" in ct.lower() or txt.lstrip()[:1] in ("[", "{"):
            try:
                j = json.loads(txt)
                if isinstance(j, dict):
                    print("  JSON keys: %s" % list(j.keys())[:12])
                    for k, v in list(j.items())[:2]:
                        print("     %s: %s" % (k, str(v)[:160]))
                elif isinstance(j, list):
                    print("  JSON 数组 len=%d  样例=%s" % (len(j), str(j[:2])[:200]))
                # 在 JSON 里找目标号名
                for t in TARGETS:
                    if t in txt:
                        print("     ★ 命中目标号: %s" % t)
            except Exception as e:
                print("  (JSON 解析失败: %s)" % str(e)[:50])
        else:
            # HTML：找导航/列表入口 + 目标号名
            navs = re.findall(r'href="([^"]{1,80})"[^>]*>([^<]{1,30})', txt)
            interesting = [(h, t.strip()) for h, t in navs
                           if any(k in (h + t) for k in ("列表", "公众号", "订阅", "list", "feed", "搜索"))][:8]
            print("  导航样例: %s" % (interesting or "无"))
            for t in TARGETS:
                if t in txt:
                    print("     ★ 命中目标号: %s" % t)
            # 页面里出现多少 "feed/" 形式的订阅链接
            feeds = re.findall(r'(?:feed|rss)/[A-Za-z0-9_\-]{6,}', txt)
            print("  页面内 feed 链接样例: %s" % (feeds[:5] or "无"))
    except Exception as e:
        print("  ✗ 异常: %s" % str(e)[:100])
