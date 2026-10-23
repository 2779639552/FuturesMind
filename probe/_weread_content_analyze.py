# -*- coding: utf-8 -*-
"""最后一次探测（**1 个请求**）：把 `/web/mp/content?reviewId=` 的原始回包落盘分析，
确认"正文到底拿不拿得到"。这是新方案能否成立的最后一环。

背景：上一步发现该接口 HTTP 200 / 3.5MB，但**不是 JSON**（`r.json()` 得到 {}）
⇒ 极可能是渲染好的文章 HTML。本脚本落盘后按关键标记分析，分析完即删除样本文件。
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
SAMPLE = Path(__file__).resolve().parent / "_content_sample.html"

print("=" * 74)
print("① GET /web/mp/content?reviewId=…  （1 个请求，落盘分析）")
print("=" * 74)
r = requests.get("https://weread.qq.com/web/mp/content", params={"reviewId": RID},
                 headers={"User-Agent": UA, "Accept": "text/html,application/json,*/*",
                          "Referer": "https://weread.qq.com/", "Cookie": HDR}, timeout=60)
SAMPLE.write_bytes(r.content)
print("  HTTP %s  %d 字节  Content-Type=%s" % (r.status_code, len(r.content),
                                              r.headers.get("Content-Type")))
h = r.text
print("  是否 JSON: %s" % h.lstrip()[:1] in ("{", "["))

print()
print("  关键标记:")
checks = [
    ("<title>", "页面标题"),
    ("js_content", "公众号正文容器 id"),
    ('class="rich_media_content', "rich_media_content 类"),
    ("msg_title", "msg_title 变量"),
    ("window.__INITIAL_STATE__", "INITIAL_STATE"),
    ("var oriCreateTime", "oriCreateTime"),
    ("<article", "<article>"),
    ("参数错误", "「参数错误」"),
    ("环境异常", "风控「环境异常」"),
    ("去 App 查看", "「去 App 查看」"),
]
for pat, label in checks:
    print("    %-24s %s" % (label, "✔ 有" if pat in h else "— 无"))

m = re.search(r"<title>([^<]{0,80})</title>", h)
print("\n  页面标题: %r" % (m.group(1).strip() if m else ""))

# 正文长度估算：把标签剥掉看纯文本
text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", h)
text = re.sub(r"<[^>]+>", " ", text)
text = re.sub(r"\s+", " ", text).strip()
print("  去标签后纯文本: %d 字符" % len(text))
print("  纯文本开头: %s" % text[:200])

print()
print("=" * 74)
print("② 结论")
print("=" * 74)
if len(text) > 500:
    print("  ✔ **正文可达**：/web/mp/content 返回渲染好的文章 HTML（需同一 cookie）")
    print("     ⇒ 新方案闭环成立：cover(1/号) → reviewId → content(1/新文) → 正文")
    print("     需 cookie 的请求量：原来 ~127（整表翻页）→ **6 + 新文数**")
else:
    print("  ✘ 正文仍拿不到（纯文本 %d 字符）⇒ 需要另找路径" % len(text))

SAMPLE.unlink(missing_ok=True)
print("\n  （样本文件已删除：%s）" % SAMPLE.name)
