# -*- coding: utf-8 -*-
r"""查三个目标号是否在 wechat2rss 免费收录列表里 + 了解 waytomaster SaaS 的能力。

wechat2rss 免费版只收录 ~485 个号（技术/安全/AI/财经为主）⇒ 期货机构号很可能不在。
抓 all.html 与 opml 做**精确判定**（不靠猜）。
只读。
"""
import re
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
H = {"User-Agent": UA}
TARGETS = ["中信期货研究", "混沌天成研究", "中信建投期货研究", "多空逻辑",
           "中信期货", "混沌天成", "中信建投"]

SOURCES = [
    ("wechat2rss 全部公众号列表", "https://wechat2rss.xlab.app/list/all.html"),
    ("wechat2rss 安全类 opml", "https://wechat2rss.xlab.app/opml/sec.opml"),
    ("wechat2rss 列表页", "https://wechat2rss.xlab.app/list/"),
]

for label, u in SOURCES:
    print("=" * 80)
    print("▸ %s\n  %s" % (label, u))
    try:
        r = requests.get(u, headers=H, timeout=30, allow_redirects=True)
        raw = r.content
        txt = raw.decode("utf-8", errors="replace")
        print("  HTTP %s  bytes=%d" % (r.status_code, len(raw)))
        if not raw:
            print("  (空)")
            continue

        # 统计"号"的数量：opml 用 outline text=，html 用锚文本或 data 属性
        n_opml = len(re.findall(r'<outline[^>]*text="([^"]+)"', txt))
        n_feed = len(re.findall(r'/feed/[0-9a-f]{8,}', txt))
        print("  opml outline 数=%d  /feed/ 链接数=%d" % (n_opml, n_feed))

        hits = {t: txt.count(t) for t in TARGETS if t in txt}
        print("  ★ 目标号命中: %s" % (hits or "无（一个都不在）"))

        # 列出前若干号名，帮助判断这个列表的领域分布
        names = re.findall(r'<outline[^>]*text="([^"]{2,30})"', txt)
        if not names:
            names = re.findall(r'>([^<>]{2,24})</a>', txt)
        names = [re.sub(r"\s+", " ", n).strip() for n in names if n.strip()]
        print("  列表里前 12 个号名: %s" % names[:12])
        print("  列表条目总数(估): %d" % len(names))
    except Exception as e:
        print("  ✗ 异常: %s" % str(e)[:100])

print("\n" + "=" * 80)
print("▸ wechatrss.waytomaster.com（tmwgsicp/wechat-download-api 的 SaaS 版）")
try:
    r = requests.get("https://wechatrss.waytomaster.com/", headers=H, timeout=25)
    t = r.content.decode("utf-8", errors="replace")
    print("  HTTP %s bytes=%d" % (r.status_code, len(r.content)))
    # 找关键词：免费/付费/价格/任意公众号/登录
    for kw in ("免费", "付费", "价格", "元/", "登录", "注册", "任意公众号", "订阅", "试用", "限制"):
        c = t.count(kw)
        if c:
            i = t.find(kw)
            seg = re.sub(r"<[^>]+>", " ", t[max(0, i - 70):i + 90])
            seg = re.sub(r"\s+", " ", seg).strip()
            print("    %-10s x%-3d … %s" % (kw, c, seg[:110]))
except Exception as e:
    print("  ✗ 异常: %s" % str(e)[:100])
