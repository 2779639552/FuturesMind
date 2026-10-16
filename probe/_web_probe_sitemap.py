# -*- coding: utf-8 -*-
r"""用 sitemap 确证：三个官网的「研报类栏目」到最近还在不在更新。

混沌天成 sitemap.xml 有 243KB（说明它是个 sitemap index 或收录大量页面）⇒ 解析它，
按 <lastmod> 排序，直接看出**哪些栏目还在更新**、最新一页是什么时候。
中信建投试 sitemap 变体。
只读。
"""
import re
import requests
from collections import defaultdict

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
H = {"User-Agent": UA}


def fetch(url):
    try:
        r = requests.get(url, headers=H, timeout=25, allow_redirects=True)
        return r.status_code, r.content, r.url
    except Exception as e:
        return None, b"", "ERR:%s" % str(e)[:60]


print("=" * 80)
print("▸ 混沌天成 sitemap.xml")
st, body, fin = fetch("https://chaosqh.com/sitemap.xml")
print("  HTTP %s bytes=%d" % (st, len(body)))
t = body.decode("utf-8", errors="replace")
entries = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>\s*(?:<lastmod>\s*([^<\s]+)\s*</lastmod>)?", t, re.S)
print("  条目数 = %d" % len(entries))
if entries:
    # 按路径一级目录聚合，看每组的最后更新时间
    groups = defaultdict(list)
    for loc, lm in entries:
        m = re.match(r"https?://[^/]+/([^/]+)", loc)
        groups[m.group(1) if m else "(root)"].append((lm or "", loc))
    print("  %-18s %-6s %-12s %s" % ("一级目录", "条数", "最新lastmod", "样例"))
    rows = []
    for k, v in groups.items():
        latest = max(x[0] for x in v)
        rows.append((latest, k, len(v), v[0][1]))
    for latest, k, n, sample in sorted(rows, reverse=True)[:18]:
        print("  %-18s %-6d %-12s %s" % (k, n, latest[:10] or "无", sample[:58]))
    # 研究类目录单独看
    print("\n  -- 含「研究/yjbg/hdtcyj」的条目（按 lastmod 倒序前 15） --")
    hits = [(lm or "", loc) for loc, lm in entries
            if any(x in loc for x in ("研究", "yjbg", "hdtcyj", "yjzx"))]
    for lm, loc in sorted(hits, reverse=True)[:15]:
        print("    %-12s %s" % (lm[:10] or "无", loc[:88]))
    if not hits:
        print("    (无)")

print("\n" + "=" * 80)
print("▸ 中信建投 sitemap 变体")
for u in ("https://www.cfc108.com/sitemap.xml",
          "https://www.cfc108.com/sitemap_index.xml",
          "https://www.cfc108.com/sitemap.txt",
          "https://www.cfc108.com/robots.txt"):
    st, body, fin = fetch(u)
    print("  %-46s HTTP %s bytes=%d" % (u, st, len(body)))
    if body and u.endswith("robots.txt"):
        print("     " + body.decode("utf-8", "replace")[:300].replace("\n", "\n     "))
    elif body:
        t2 = body.decode("utf-8", "replace")
        locs = re.findall(r"<loc>\s*([^<\s]+)", t2)[:6]
        if locs:
            print("     样例: %s" % locs)

print("\n" + "=" * 80)
print("▸ 混沌天成「研究」栏目下的子栏目（从 /hdtcyj 页抽取）")
st, body, fin = fetch("https://chaosqh.com/hdtcyj/hdzp")
t3 = body.decode("utf-8", errors="replace")
subs = sorted(set(re.findall(r'href="(/hdtcyj/[^"#]{1,60})"', t3)))
for s in subs[:20]:
    print("    %s" % s)
print("  子栏目数 = %d" % len(subs))
# 该页最新条目日期
dates = sorted(set(re.findall(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", t3)), reverse=True)
print("  该页出现的最新日期 = %s" % (dates[:4] if dates else "无"))
