# -*- coding: utf-8 -*-
r"""深入探测三个官网研报栏目的**列表结构**（决定能否做适配器）。

对每个入口页：报告 状态 / 长度 / 编码，并抽取**条目样例**（标题 + 日期 + 链接），
以及分页线索（页码参数 / 总页数 / next 链接）。
只读。
"""
import re
import requests
from urllib.parse import urljoin

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
H = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}

ENTRIES = [
    ("混沌天成·研究栏目", "https://chaosqh.com/hdtcyj"),
    ("中信建投·研究报告", "https://www.cfc108.com/zxjtqh/yfzx/yjbg/index.shtml"),
    ("中信期货·研报查询(机构平台)", "https://inst.citicsf.com/research-report/researchReportQuery"),
    ("中信期货·研报合集(机构平台)", "https://inst.citicsf.com/research-report/research"),
]


def get(url):
    r = requests.get(url, headers=H, timeout=20, allow_redirects=True)
    raw = r.content
    enc = r.encoding
    if enc in (None, "ISO-8859-1"):
        for e in ("utf-8", "gbk", "gb18030"):
            try:
                return r, raw.decode(e), e
            except Exception:
                continue
        return r, raw.decode("utf-8", errors="replace"), "utf-8(replace)"
    return r, r.text, enc


for label, url in ENTRIES:
    print("=" * 80)
    print("▸ %s\n  %s" % (label, url))
    try:
        r, t, enc = get(url)
    except Exception as e:
        print("  ✗ 异常: %s" % str(e)[:100])
        continue
    print("  HTTP %s  final=%s" % (r.status_code, r.url))
    print("  bytes=%d  decoded_len=%d  encoding=%s" % (len(r.content), len(t), enc))
    if "登录" in t or "login" in r.url.lower():
        print("  ⚠ 页面里出现「登录」/ 跳转到登录页 ⇒ 可能需登录")

    # 抽取所有链接 + 锚文本，挑"像条目"的（锚文本长度 8~80）
    links = re.findall(r'<a[^>]+href="([^"#]{4,200})"[^>]*>(.*?)</a>', t, re.S)
    cands = []
    for h, raw_txt in links:
        txt = re.sub(r"<[^>]+>", "", raw_txt)
        txt = re.sub(r"\s+", " ", txt).strip()
        if 8 <= len(txt) <= 90:
            cands.append((urljoin(r.url, h), txt))
    print("  链接总数=%d  疑似条目=%d" % (len(links), len(cands)))

    seen = set()
    shown = 0
    for h, txt in cands:
        if txt in seen:
            continue
        seen.add(txt)
        # 附近是否有日期
        i = t.find(txt[:20])
        ctx = t[max(0, i - 200):i + 260] if i >= 0 else ""
        d = re.search(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})", ctx)
        flag = "PDF" if h.lower().endswith(".pdf") else ("content" if "content" in h else "")
        print("      %-46s | %s | %s" % (txt[:46], d.group(0) if d else "无日期", flag))
        shown += 1
        if shown >= 8:
            break
    if shown == 0:
        print("      (未找到疑似条目 —— 可能是 JS 渲染或需登录)")

    # 分页线索
    pg = re.findall(r'(?:index|list|page)[_-]?(\d{1,3})\.(?:s?html?|jsp|php)', t)
    pg2 = re.findall(r"共\s*(\d+)\s*页|总页数[:：]?\s*(\d+)", t)
    js = bool(re.search(r"(vue|react|__NEXT_DATA__|window\.__INITIAL)", t, re.I))
    print("  分页线索: 页码文件=%s 共N页=%s  | 前端框架痕迹=%s"
          % (sorted(set(pg))[:6] or "无", pg2 or "无", js))
