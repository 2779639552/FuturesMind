# -*- coding: utf-8 -*-
"""探针: 生意社大宗商品(非期货)字典 —— 是否有 原油/红枣 可用于第3方兜底
只读取证。
"""
import re
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
S = requests.Session()
S.headers.update({"User-Agent": UA, "Referer": "https://www.100ppi.com/"})

# 先解锁 cookie
r0 = S.get("https://www.100ppi.com/sf/792.html", timeout=20)
m = re.search(r'var\s+_0x2\s*=\s*"([0-9a-fA-F]+)"', r0.text)
if m:
    S.cookies.set("HW_CHECK", m.group(1), domain="www.100ppi.com", path="/")
    print("[cookie] HW_CHECK 已写入")

CANDS = [
    ("大宗榜首页", "https://www.100ppi.com/mprice/"),
    ("能源榜",     "https://www.100ppi.com/mprice/plist-1-0-0-1.html"),
    ("搜索原油",   "https://www.100ppi.com/search/?k=%E5%8E%9F%E6%B2%B9"),
    ("原油详情猜测", "https://www.100ppi.com/sf/832.html"),
    ("化工榜",     "https://www.100ppi.com/mprice/plist-2-0-0-1.html"),
]
for label, url in CANDS:
    print()
    print("=" * 74)
    print("%s  %s" % (label, url))
    print("=" * 74)
    try:
        r = S.get(url, timeout=25, allow_redirects=True)
        body = r.text
        print("  status=%s  len=%s  final=%s" % (r.status_code, len(r.content), r.url[:80]))
        if len(r.content) < 1200:
            print("  疑似挑战/空页, body[:150]=%s" % body[:150].replace("\n", " "))
            continue
        for kw in ["原油", "红枣", "低硫", "燃料油"]:
            c = body.count(kw)
            if c:
                print("  含 '%s' × %d" % (kw, c))
        # 抽取可能的品种链接
        links = re.findall(r'href="(/sf/(\d+)\.html)"[^>]*>([^<]{1,20})<', body)
        if links:
            print("  /sf/ 链接 %d 个, 样例:" % len(links), links[:6])
        # 抽取商品页链接
        cl = re.findall(r'href="(/mprice/[^"]{1,60})"', body)
        if cl:
            print("  /mprice/ 链接 %d 个, 样例:" % len(cl), list(dict.fromkeys(cl))[:6])
    except Exception as e:
        print("  ERR:", type(e).__name__, str(e)[:160])

print()
print("完成")
