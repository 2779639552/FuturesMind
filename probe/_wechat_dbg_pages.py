# -*- coding: utf-8 -*-
r"""取证脚本：三个目标号页面的原始结构 —— 到底是"没建合集"还是"解析器抓不到"。

只读。取 HTML 存到 probe/_dbg_wx_<n>.html 便于反复检查（避免重复请求）。
逐项打印：
  · 号名候选（var nickname / js_name / og:title / data-nickname）—— 定位 parse_author 抓错的原因
  · 合集相关关键词（public_tag_info / album_id / appmsgalbum / album_info_list）出现次数与上下文
"""
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("wac_dbg", ROOT / "wechat_album_collector.py")
wac = importlib.util.module_from_spec(spec)
sys.modules["wac_dbg"] = wac
spec.loader.exec_module(wac)

TARGETS = [
    ("链接1", "https://mp.weixin.qq.com/s/tBTKv-QOuBM5K9lSEsg9uQ"),
    ("链接2", "https://mp.weixin.qq.com/s/Lktb-yomW8HBeS6tBOQb7A"),
    ("链接3", "https://mp.weixin.qq.com/s/ThbWuBNGj2Kq5CcoGTwpCw"),
]
KEYWORDS = ["public_tag_info", "album_id", "appmsgalbum", "album_info_list",
            "albumId", "js_name", "var biz", "og:title"]

for n, (tag, url) in enumerate(TARGETS, 1):
    cache = ROOT / "probe" / ("_dbg_wx_%d.html" % n)
    if cache.exists() and cache.stat().st_size > 100000:
        body = cache.read_text(encoding="utf-8", errors="replace")
        how = "cache"
    else:
        body, how = wac.get_html(url)
        if body:
            cache.write_text(body, encoding="utf-8", errors="replace")
    print("=" * 82)
    print("【%s】%s" % (tag, url))
    print("  how=%s  len=%d" % (how, len(body or "")))
    if not body:
        continue

    print("\n  -- 号名候选 --")
    for label, pat in [
        ("var nickname", r'var\s+nickname\s*=\s*"([^"]{1,50})"'),
        ("var nickname'", r"var\s+nickname\s*=\s*'([^']{1,50})'"),
        ("js_name", r'id="js_name"[^>]*>\s*([^<]{1,50}?)\s*<'),
        ("og:title", r'<meta\s+property="og:title"\s+content="([^"]{1,80})"'),
        ("data-nickname", r'data-nickname=\\?["\']([^"\'\\]{1,50})'),
        ("js_name attr", r'js_name\s*[:=]\s*["\']([^"\']{1,50})["\']'),
    ]:
        m = re.search(pat, body)
        print("    %-16s -> %s" % (label, (m.group(1)[:50] if m else "—")))

    print("\n  -- 合集相关关键词出现次数 --")
    for kw in KEYWORDS:
        c = body.count(kw)
        print("    %-18s %d" % (kw, c))

    print("\n  -- 关键上下文 --")
    for kw in ("public_tag_info", "album_id", "appmsgalbum"):
        idxs = [m.start() for m in re.finditer(re.escape(kw), body)][:3]
        if not idxs:
            print("    %s: 无" % kw)
            continue
        for i in idxs:
            seg = body[max(0, i - 90):i + 130].replace("\n", " ")
            print("    %s@%d … %s …" % (kw, i, seg))
    # 页面里是否出现"合集"中文（前端文案）
    for kw in ("合集", "目录", "话题"):
        print("    中文「%s」出现 %d 次" % (kw, body.count(kw)))
    print()
