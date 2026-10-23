# -*- coding: utf-8 -*-
"""★ 端到端验证: 新链路到底能不能"采到"文章?

链路: cover(1 请求/号) → reviewId → 推出 originalId & 去重键 → content(1 请求/新文) → 正文

【为什么要单独验这一步】
前面几轮分别验过 cover(能拿最新一篇) 与 content(能拿正文), 但**没验过接起来之后
能不能真的"采下来一篇"** —— 中间还差: 原样产出与库里既有数据是否冲突 / 去重键能否推出。

【关键推导】
`reviewId` = `{bookId}_{originalId}`   (采集器源码: review_id = f"{bid}_{oid}")
⇒ `originalId` = reviewId 去掉 `{bookId}_` 前缀
⇒ 去重键 `key = "wxmp_" + originalId`  —— **入库链路完全不用改**

用法:
  python probe/_weread_pipeline_e2e.py                 # 单号(混沌天成), 2 个请求
  python probe/_weread_pipeline_e2e.py --all           # 全部 6 号, 5s 间隔
  python probe/_weread_pipeline_e2e.py --no-content    # 只验到 reviewId/去重键(1 请求/号)
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
BASE = {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
        "Referer": "https://weread.qq.com/"}
WEREAD = Path.home() / ".tradingagents/weread"
DB = Path.home() / ".tradingagents/agentsense.db"


def cookie_header():
    j = json.loads((WEREAD / "cookies.json").read_text(encoding="utf-8"))
    return "; ".join("%s=%s" % (c["name"], c["value"]) for c in (j.get("cookies") or []))


def accounts():
    j = json.loads((WEREAD / "mp_accounts.json").read_text(encoding="utf-8"))
    return [(a["name"], a["book_id"]) for a in j["accounts"]]


def cover(bid, ck):
    r = requests.get("https://weread.qq.com/web/mp/cover", params={"bookId": bid},
                     headers={**BASE, "Cookie": ck}, timeout=45)
    try:
        return r.json()
    except Exception:
        return {"_raw": r.text[:200]}


def strip_html(html):
    t = re.sub(r"<script[\s\S]*?</script>", " ", html)
    t = re.sub(r"<style[\s\S]*?</style>", " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    t = t.replace("&quot;", '"').replace("&#39;", "'")
    return re.sub(r"\s+", " ", t).strip()


def extract_content(html):
    """从 /web/mp/content 的回包里取正文。先 js_content 容器, 再 fellback 全页剥标签。"""
    m = re.search(r'id="js_content"[^>]*>([\s\S]*?)</div>\s*(?:<div|<script)', html)
    if m and len(strip_html(m.group(1))) > 80:
        return strip_html(m.group(1)), "js_content"
    m2 = re.search(r'class="rich_media_content[^"]*"[^>]*>([\s\S]*?)</div>\s*<script', html)
    if m2 and len(strip_html(m2.group(1))) > 80:
        return strip_html(m2.group(1)), "rich_media_content"
    return strip_html(html), "全页(回退)"


def in_db(key, title):
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    a = db.execute("select id, status from research_reports where filename like ?",
                   (key + "%",)).fetchone()
    b = None
    if title:
        b = db.execute("select id, status, source from research_reports "
                       "where title = ? limit 1", (title,)).fetchone()
    return (dict(a) if a else None), (dict(b) if b else None)


def one(name, bid, ck, do_content=True):
    print("=" * 80)
    print("  %s   %s" % (name, bid))
    print("=" * 80)
    j = cover(bid, ck)
    if j.get("errCode"):
        print("  ✘ cover: errCode=%s %s" % (j.get("errCode"), j.get("errMsg") or ""))
        return None
    title = j.get("title") or ""
    rid = j.get("reviewId") or ""
    print("  ① cover  ✔")
    print("     title     = %s" % title)
    print("     reviewId  = %s" % rid)

    # ★ 推导 originalId 与去重键
    oid = rid.split(bid + "_", 1)[1] if (bid + "_") in rid else ""
    key = "wxmp_" + oid if oid else "(推不出)"
    print("     originalId= %s   ← 由 reviewId 去前缀推出" % oid)
    print("     ★ 去重键  = %s" % key)

    hit_key, hit_title = in_db(key, title)
    print("     库里按此键: %s" % ("已存在 id=%s(%s)" % (hit_key["id"], hit_key["status"]) if hit_key else "★ 无 ⇒ 是新文"))
    if hit_title:
        print("     ⚠ 库里同标题已存在: id=%s(%s, %s) —— 新旧 originalId 格式不同 ⇒ 可能重复入库"
              % (hit_title["id"], hit_title["status"], hit_title["source"]))
    elif title:
        print("     库里同标题: 无")

    if not do_content:
        print()
        return {"name": name, "title": title, "key": key, "new": not hit_key}

    # ② 正文
    r = requests.get("https://weread.qq.com/web/mp/content", params={"reviewId": rid},
                     headers={**BASE, "Cookie": ck}, timeout=60)
    txt, how = extract_content(r.text)
    print("  ② content  HTTP %s  %d B  正文 %d 字  取法=%s" % (r.status_code, len(r.content), len(txt), how))
    print("     正文开头: %s" % txt[:110])
    ok = len(txt) >= 200
    print("     %s" % ("✔ 正文可达" if ok else "✘ 正文过短, 视为未取到"))
    print()
    return {"name": name, "title": title, "key": key, "new": not hit_key,
            "chars": len(txt), "ok": ok}


def main():
    ck = cookie_header()
    accs = accounts()
    if "--all" in sys.argv:
        targets = accs
    else:
        targets = [a for a in accs if a[0] == "混沌天成研究"] or accs[:1]
    do_content = "--no-content" not in sys.argv
    print("★ 目标 %d 个号（%s）  正文=%s" % (len(targets), "全量" if "--all" in sys.argv else "默认单号",
                                          "取" if do_content else "跳过"))
    print()
    out = []
    for i, (name, bid) in enumerate(targets):
        if i:
            time.sleep(5)
        try:
            r = one(name, bid, ck, do_content)
            if r:
                out.append(r)
        except Exception as e:
            print("  ★ 异常: %s" % str(e)[:130])
            print()
    print("=" * 80)
    print("  汇总")
    print("=" * 80)
    for r in out:
        print("  %-18s %-10s %s" % (r["name"], "新文" if r["new"] else "已采过",
                                    r.get("chars", "-")))
    newc = [r for r in out if r["new"]]
    okc = [r for r in out if r.get("ok")]
    print()
    print("  新文 %d 篇；正文可达 %d/%d" % (len(newc), len(okc), len([r for r in out if 'ok' in r])))
    print("  ⇒ %s" % ("★ 新链路可采集" if okc else "需再排查"))


if __name__ == "__main__":
    main()
