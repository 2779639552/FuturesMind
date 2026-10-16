# -*- coding: utf-8 -*-
r"""验证混沌天成研究栏目的**列表顺序**与**实际更新状态**。

⚠️ 前一步看到 /hdtcyj/hdzp 第 1 页条目停在 2021 年 —— 但若列表是**正序（旧→新）**，
   最新内容在最后一页（_18）。这与微信合集"第一页是最早 20 篇"是同一类陷阱，
   只看第一页会得出"该栏目已停更"的错误结论。
只读。
"""
import re
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
H = {"User-Agent": UA}

PAGES = [
    ("早评 第1页", "https://chaosqh.com/hdtcyj/hdzp"),
    ("早评 第2页", "https://chaosqh.com/hdtcyj/hdzp_2"),
    ("早评 第9页", "https://chaosqh.com/hdtcyj/hdzp_9"),
    ("早评 第18页", "https://chaosqh.com/hdtcyj/hdzp_18"),
    ("周报 第1页", "https://chaosqh.com/hdtcyj/hdzb"),
    ("专题-化工", "https://chaosqh.com/jzjd/jsj/hhyj/ztbg/hgyjbg"),
    ("专题-金属", "https://chaosqh.com/jzjd/jsj/hhyj/ztbg/jsyjbg"),
    ("品种监测", "https://chaosqh.com/hdtcyj/pzjc"),
]


def dates_in(t):
    out = []
    for y, m, d in re.findall(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", t):
        try:
            out.append("%04d-%02d-%02d" % (int(y), int(m), int(d)))
        except Exception:
            pass
    return out


def titles(t):
    """条目标题：通常是 content_xxx 链接的锚文本"""
    out = []
    for h, raw in re.findall(r'<a[^>]+href="([^"]*content_\d+[^"]*)"[^>]*>(.*?)</a>', t, re.S):
        s = re.sub(r"<[^>]+>", "", raw)
        s = re.sub(r"\s+", " ", s).strip()
        if len(s) >= 6:
            out.append(s[:60])
    return out


for label, url in PAGES:
    try:
        r = requests.get(url, headers=H, timeout=20, allow_redirects=True)
        t = r.content.decode("utf-8", errors="replace")
        ds = dates_in(t)
        ts = titles(t)
        print("=" * 78)
        print("▸ %s\n  %s" % (label, url))
        print("  HTTP %s  bytes=%d  条目数=%d" % (r.status_code, len(r.content), len(ts)))
        if ds:
            print("  页面日期范围: %s ~ %s   （最新 4 个: %s）"
                  % (min(ds), max(ds), sorted(set(ds), reverse=True)[:4]))
        else:
            print("  未解析到日期")
        for s in ts[:4]:
            print("     · %s" % s)
    except Exception as e:
        print("=" * 78)
        print("▸ %s\n  ✗ %s" % (label, str(e)[:80]))
