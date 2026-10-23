# -*- coding: utf-8 -*-
"""方案 C 评估探针：官方 Agent 网关能不能**列出某号的文章清单**。

═══ 为什么必须测这一条 ═══
上一轮基准确认：网关有 17 个接口，其中
  · `/shelf/sync`   → ✅ 能列出书架里的**公众号条目**（本项目 6 个号全在）
  · `/store/search` → ❌ `-2041`（本轮再核一次是不是参数问题）
但"能列条目"≠"能列该号下的文章"。上一轮探针的 Q5 因"搜索没命中 bookId"而跳过，
**没测到**。本轮改成：**直接拿 `/shelf/sync` 给的 bookId** 去问
  · `/book/info`        —— 这个号的元信息
  · `/book/chapterinfo` —— ★ 章节目录：文档里有 `chapters[].isMPChapter`
                            （是否公众号章节）⇒ 若成立，文章就是"章节"
从而回答：**方案 C 能不能替代读书通道**（列清单），以及清单里有没有**原文链接**
（有的话正文可以走免登录 HTTP，与合集通道同路）。

⚠ 只读：只调查询类接口，不写数据、不改书架。
⚠ 版本号必须与本地技能一致（服务端比对，落后就每个回包塞 upgrade_info）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

GATEWAY = "https://i.weread.qq.com/api/agent/gateway"
SKILL_VERSION = "1.0.5"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
KEY_FILE = Path.home() / ".tradingagents" / "weread" / "api_key.txt"

# 目标：读书通道当初注册的 6 个号（+ 合集通道的多空逻辑做对照，它不在书架里）
WANT = ["混沌天成研究", "紫金天风期货研究所", "中信期货研究",
        "物产中大期货研究", "恒力期货研究院", "中信建投期货研究"]


def call(api_name: str, **params) -> tuple[int, dict]:
    key = KEY_FILE.read_text(encoding="utf-8").strip()
    body = {"api_name": api_name, "skill_version": SKILL_VERSION, **params}
    r = requests.post(GATEWAY, json=body, timeout=45, headers={
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "User-Agent": UA,
    })
    try:
        return r.status_code, (r.json() or {})
    except Exception:
        return r.status_code, {"_raw": r.text[:300]}


def main() -> int:
    print("=" * 78)
    print("① /shelf/sync —— 取出公众号条目（bookId + 名称）")
    print("=" * 78)
    st, d = call("/shelf/sync")
    books = (d.get("books") or []) if isinstance(d, dict) else []
    mps = [b for b in books if str(b.get("bookId") or "").startswith("MP_WXS_")]
    print("  HTTP %s   books=%d   其中公众号条目 %d" % (st, len(books), len(mps)))
    for b in mps:
        print("    %-14s %s" % (b.get("bookId"), b.get("title")))
    if not mps:
        print("  ★ 没拿到公众号条目 ⇒ 后续无从谈起")
        return 1

    print()
    print("=" * 78)
    print("② ★ 关键：/book/chapterinfo —— 能不能把文章当「章节」列出来")
    print("=" * 78)
    ok_any = False
    sample_ids: list[str] = []
    for b in mps[:3]:
        bid, name = b.get("bookId"), b.get("title")
        st1, info = call("/book/info", bookId=bid)
        st2, chs = call("/book/chapterinfo", bookId=bid)
        ch = chs.get("chapters") or [] if isinstance(chs, dict) else []
        print()
        print("  ── %s (%s)" % (name, bid))
        print("     /book/info        HTTP %s  errcode=%s  title=%s"
              % (st1, info.get("errcode"), (info.get("title") or "")[:34]))
        print("     /book/chapterinfo HTTP %s  errcode=%s  章节数=%d"
              % (st2, chs.get("errcode"), len(ch)))
        if ch:
            ok_any = True
            sample_ids.append(bid)
            print("       字段样例: %s" % json.dumps(ch[0], ensure_ascii=False)[:220])
            mp_flag = [c.get("isMPChapter") for c in ch[:8]]
            print("       前 8 章的 isMPChapter: %s" % mp_flag)
            for c in ch[:5]:
                t = c.get("title") or c.get("name") or ""
                print("        · %s   %s" % (str(c.get("chapterUid") or c.get("idx") or "")[:10], t[:52]))
        else:
            print("       回包: %s" % json.dumps(chs, ensure_ascii=False)[:200])

    print()
    print("=" * 78)
    print("③ /store/search 复核（-2041 是「接口封了」还是「参数不对」）")
    print("=" * 78)
    for scope, kw in ((2, "混沌天成研究"), (0, "混沌天成研究"), (4, "混沌天成")):
        st3, s3 = call("/store/search", keyword=kw, scope=scope, maxIdx=0, count=10)
        n = 0
        if isinstance(s3, dict):
            for g in (s3.get("results") or []):
                for it in (g.get("books") or []):
                    n += 1
        print("  scope=%-2s %-14s HTTP %-4s errcode=%-7s 条目=%d"
              % (scope, kw, st3, s3.get("errcode"), n))

    print()
    print("=" * 78)
    print("④ 结论")
    print("=" * 78)
    if ok_any:
        print("  ✔ 网关**能**列出公众号的文章清单（见 ②），样本号: %s" % ", ".join(sample_ids))
        print("  ⇒ 方案 C 的「列清单」这一半成立；剩下要确认的是**正文怎么取**")
        print("     （清单里有没有原文 URL → 有则正文可走免登录 HTTP，与合集通道同路）")
    else:
        print("  ✘ 网关拿不到文章清单 ⇒ 方案 C 只能做到「列条目」，做不了「追踪新文」")
    return 0


if __name__ == "__main__":
    sys.exit(main())
