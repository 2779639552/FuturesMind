# -*- coding: utf-8 -*-
"""★ 决定性测试: 那 6 个号**有没有合集**?

【为什么这是用户那个问题的答案】
用户问: "既然正文可以走公开, 为什么还要扫码?"
答: 正文免登录 != **清单**免登录。清单(哪篇/何时发)必须有来源。
但来源**不必**是 cookie —— 微信的「合集」接口 (`appmsgalbum?action=getalbum&f=json`)
只需要 `__biz + album_id`, 而且 **完全免登录**。

而 `__biz` 我们**早就有了** (库里的 source_url 里就带着), album_id 可以从**任意一篇
公开文章页**反查出来 (公开页, 免登录)。

⇒ 若这些号有合集: 扫码/ cookie 可以**彻底去掉**。
⇒ 若没有合集: 才需要退回读书通道的 cover (需 cookie)。

【成本】每号 1 个公开请求 (文章页) + 命中合集时 1 个 getalbum 请求。公开页面, 无登录态。

用法:
  python probe/_weread_album_route_probe.py            # 只测 1 个号(默认混沌天成)
  python probe/_weread_album_route_probe.py --all      # 测全部 6 个号(慢, 5s 间隔)
"""
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
H = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}

DB = Path.home() / ".tradingagents/agentsense.db"


def rows_for(acc_kw, limit=1):
    """从库里取某号的一条带完整 __biz URL 的记录。"""
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    out = []
    for r in db.execute(
        "select source, source_url, title from research_reports "
        "where filename like 'wxmp_%' and source_url like '%__biz=%' order by id desc"
    ):
        if acc_kw in str(r["source"]):
            out.append(dict(r))
        if len(out) >= limit:
            break
    return out


def accounts():
    """全库里出现过的公众号 -> 一条样例 URL。"""
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    seen = {}
    for r in db.execute(
        "select source, source_url from research_reports "
        "where filename like 'wxmp_%' and source_url like '%__biz=%' order by id desc"
    ):
        name = str(r["source"]).replace("公众号-", "")
        seen.setdefault(name, str(r["source_url"]))
    return seen


def parse_album(html: str):
    """从文章页解析合集归属。**两路合并**(与合集通道同款判据)。"""
    hits = {}
    # 路 1: public_tag_info.tags -> [{album_id, title}]
    blk = re.search(r"public_tag_info\s*[:=]\s*\{(.{0,4000}?)\}\s*,\s*\n", html, re.S)
    if blk:
        for m in re.finditer(r"album_id\s*:\s*'(\d+)'", blk.group(1)):
            hits.setdefault(m.group(1), None)
        for m in re.finditer(r"title\s*:\s*'([^']{1,40})'", blk.group(1)):
            if hits:
                k = list(hits)[-1]
                if hits[k] is None:
                    hits[k] = m.group(1)
    # 路 2: album_info_list 数组
    blk2 = re.search(r"album_info_list\s*[:=]\s*\[(.{0,4000}?)\]", html, re.S)
    if blk2:
        for m in re.finditer(r"albumId\s*:\s*'(\d+)'", blk2.group(1)):
            hits.setdefault(m.group(1), None)
    # 兜底: 裸 album_id 扫描(仅作提示, 不当作确认)
    loose = sorted(set(re.findall(r'album_id["\']?\s*[:=]\s*["\']?(\d{8,})', html)))
    return hits, loose


def probe_one(name: str, url: str):
    print("=" * 78)
    print("  %s" % name)
    print("=" * 78)
    m = re.search(r"__biz=([A-Za-z0-9+/=]+)", url)
    biz = m.group(1) if m else "?"
    print("  __biz   = %s" % biz)
    print("  样例文章 = %s" % url[:96])

    r = requests.get(url, headers=H, timeout=45)
    html = r.text
    print("  文章页: HTTP %s  %d 字节  正文容器=%s  参数错误=%s"
          % (r.status_code, len(html), 'id="js_content"' in html, "参数错误" in html))

    hits, loose = parse_album(html)
    if hits:
        print("  ★ 反查到合集: %s" % json.dumps(hits, ensure_ascii=False))
    else:
        print("  ✘ 页面里没有合集归属 (public_tag_info.tags / album_info_list 均为空)")
    if loose:
        print("    (页面另有裸 album_id: %s —— 未见于标准字段, 只作提示)" % loose[:4])

    # 命中则真的调一次 getalbum, 确认免登录能列
    for aid, title in (hits or {}).items():
        u = ("https://mp.weixin.qq.com/mp/appmsgalbum?__biz=%s&action=getalbum"
             "&album_id=%s&count=20&is_from_sn=1&f=json" % (biz, aid))
        rr = requests.get(u, headers=H, timeout=45)
        ok = False
        n = 0
        try:
            j = rr.json()
            g = j.get("getalbum_resp") or {}
            arts = g.get("article_list") or []
            n = int(g.get("article_count") or len(arts))
            ok = bool(arts)
        except Exception:
            j = {}
        print("       → getalbum(免登录): HTTP %s  解析=%s  文章数=%s  %s"
              % (rr.status_code, ok, n, "✔ 免登录可列!" if ok else "✘"))
        if ok:
            j2 = rr.json()["getalbum_resp"]
            for a in (j2.get("article_list") or [])[:3]:
                print("          %s  %s" % (str(a.get("create_time") or "")[:10],
                                            str(a.get("title") or "")[:52]))
    print()
    return bool(hits)


def main():
    all_flag = "--all" in sys.argv
    accs = accounts()
    if all_flag:
        targets = list(accs.items())
    else:
        # 默认只测一个号(省请求)
        first = "混沌天成研究"
        targets = [(first, accs[first])] if first in accs else list(accs.items())[:1]
    print("★ 测试目标: %d 个号%s" % (len(targets), "（全量）" if all_flag else "（默认单号, 加 --all 测全部）"))
    print()
    hit = 0
    for i, (name, url) in enumerate(targets):
        if i:
            time.sleep(5)   # 公开页面, 但仍守节制
        try:
            if probe_one(name, url):
                hit += 1
        except Exception as e:
            print("  ★ 异常: %s" % str(e)[:120])
            print()
    print("=" * 78)
    print("  汇总: %d/%d 个号反查到合集" % (hit, len(targets)))
    print("  ⇒ 若全部命中: 清单可走免登录合集接口 ⇒ **扫码彻底不需要**")
    print("     (前提: 每号的 album_id 登记一次, 之后永不需要 cookie)")
    print("  ⇒ 若未命中: 该号没建合集 ⇒ 清单只能走读书通道 cover (需 cookie)")
    print("=" * 78)


if __name__ == "__main__":
    main()
