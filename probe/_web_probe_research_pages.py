# -*- coding: utf-8 -*-
r"""探测三个机构官网的研报栏目入口（零登录可行性）。

策略：批量试"首页 + 常见栏目路径 + sitemap"，对每个 URL 报告
  状态码 / 重定向后 URL / 内容长度 / 编码 / 是否含「研究报告|研报|策略报告」/ 含"研究"的链接样例。
只读。
"""
import re
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
H = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}

URLS = [
    # sitemap（最省事：能直接看出栏目结构）
    "https://chaosqh.com/sitemap.xml",
    "https://www.cfc108.com/sitemap.xml",
    "https://www.citicsf.com/sitemap.xml",
    # 混沌天成：upload 路径里出现过 hhyj（疑似研报目录），试同级栏目
    "https://chaosqh.com/",
    "https://chaosqh.com/hhyj/",
    "https://chaosqh.com/yjbg/",
    "https://chaosqh.com/gywm/",
    # 中信建投期货
    "https://www.cfc108.com/",
    "https://www.cfc108.com/yjbg/",
    "https://www.cfc108.com/research/",
    # 中信期货
    "https://www.citicsf.com/e-futures/",
    "https://inst.citicsf.com/information/researchInformation/varietyInfo",
]

for u in URLS:
    try:
        r = requests.get(u, headers=H, timeout=18, allow_redirects=True)
        raw = r.content
        # 中文站常见 GBK/GB2312，requests 猜错时手动兜底
        if r.encoding in (None, "ISO-8859-1"):
            for enc in ("utf-8", "gbk", "gb18030"):
                try:
                    t = raw.decode(enc)
                    break
                except Exception:
                    continue
            else:
                t = raw.decode("utf-8", errors="replace")
        else:
            t = r.text
        kws = {k: t.count(k) for k in ("研究报告", "研报", "策略报告", "研究") if t.count(k)}
        print("=" * 78)
        print("▸ %s" % u)
        print("  HTTP %s  final=%s" % (r.status_code, r.url if r.url != u else "(同)"))
        print("  bytes=%d  encoding=%s  keywords=%s" % (len(raw), r.encoding, kws or "无"))
        # 含"研究"的链接样例
        links = re.findall(r'href="([^"]{4,120})"[^>]*>([^<]{0,30})', t)
        hits = [(h, txt.strip()) for h, txt in links
                if ("研究" in txt or "研究" in h or "研报" in txt)][:6]
        for h, txt in hits:
            print("      link: %-60s  %s" % (h[:60], txt[:24]))
        if not hits:
            print("      (无含「研究」的链接)")
    except Exception as e:
        print("=" * 78)
        print("▸ %s" % u)
        print("  ✗ 异常: %s" % str(e)[:90])
