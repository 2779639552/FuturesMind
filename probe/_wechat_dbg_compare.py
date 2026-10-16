# -*- coding: utf-8 -*-
r"""对照实验：为什么三个目标号解析不出合集？

对照组 = 「多空逻辑」的一篇已采文章（**已知有 2 个合集**，采集器能正常解析）。
实验组 = 用户给的三个号页面。

逐项对比同一批关键词的**上下文形态**，区分两种可能：
  (A) 解析器坏了（对照组也解析不出）  → 修解析
  (B) 这三篇文章确实没被勾进合集（对照组正常、实验组确实没有数据块） → 通道不适用
只读。
"""
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("wac_cmp", ROOT / "wechat_album_collector.py")
wac = importlib.util.module_from_spec(spec)
sys.modules["wac_cmp"] = wac
spec.loader.exec_module(wac)

CTRL = ("对照·多空逻辑(已知2合集)",
        "http://mp.weixin.qq.com/s?__biz=MzIzNTQ3MDExMg==&mid=2247485844&idx=1"
        "&sn=0a1f0f48416990c4925d0c5c38bd3b8c#rd")
PAGES = [
    CTRL,
    ("链接1·混沌天成研究", "https://mp.weixin.qq.com/s/tBTKv-QOuBM5K9lSEsg9uQ"),
    ("链接2·中信建投期货研究", "https://mp.weixin.qq.com/s/Lktb-yomW8HBeS6tBOQb7A"),
    ("链接3·中信期货研究", "https://mp.weixin.qq.com/s/ThbWuBNGj2Kq5CcoGTwpCw"),
]

for label, url in PAGES:
    print("=" * 82)
    print("▸ %s" % label)
    try:
        body, how = wac.get_html(url)
    except Exception as e:
        print("  取页失败: %s" % e)
        continue
    if not body:
        print("  取页为空")
        continue
    print("  how=%s len=%d" % (how, len(body)))

    # ① public_tag_info 的**数据定义形态**（带 tags 的真数据）
    data_def = list(re.finditer(r"public_tag_info\s*:\s*\{", body))
    print("  ① public_tag_info 数据定义形态出现 %d 次" % len(data_def))
    for m in data_def[:2]:
        blk = wac.brace_block(body, m.end() - 1)
        has_tag = "tag_name" in blk
        print("     块长=%d 含tag_name=%s 前120字=%s"
              % (len(blk), has_tag, blk[:120].replace("\n", " ")))
        if has_tag:
            for t in re.finditer(r"tag_name\s*:\s*'([^']*)'", blk):
                seg = blk[t.start():t.start() + 300]
                am = re.search(r"album_id\s*:\s*'(\d+)'", seg)
                print("       ★ tag=%s album_id=%s" % (t.group(1), am.group(1) if am else "?"))

    # ② albums_of_page 的实际返回（采集器自己的解析器）
    print("  ② albums_of_page() = %s" % (wac.albums_of_page(body) or "[]"))

    # ③ album_info_list 上下文（真实数据 vs JS 代码）
    for m in list(re.finditer(r"album_info_list", body))[:2]:
        seg = body[max(0, m.start() - 60):m.start() + 200].replace("\n", " ")
        print("  ③ album_info_list@%d … %s …" % (m.start(), seg))

    # ④ appmsgalbuminfo 是否带真实 album_id
    hits = list(re.finditer(r"appmsgalbuminfo", body))
    real = [m for m in hits
            if re.search(r"album_id\s*[:=]\s*'?\"?\d{8,}", body[m.start():m.start() + 400])]
    print("  ④ appmsgalbuminfo 出现 %d 次，其中附近带真实 album_id 的 %d 处"
          % (len(hits), len(real)))
    for m in real[:2]:
        seg = body[m.start():m.start() + 220].replace("\n", " ")
        print("       … %s" % seg)

    # ⑤ 号名（修正口径：优先 js_name）
    m = re.search(r'id="js_name"[^>]*>\s*([^<]{1,40}?)\s*<', body)
    print("  ⑤ js_name 号名 = %s" % (m.group(1) if m else "—"))
    print()
