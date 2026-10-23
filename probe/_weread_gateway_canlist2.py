# -*- coding: utf-8 -*-
"""方案 C 评估 · 第二探：把两条线索挖到底。

线索一：`/book/chapterinfo` 对公众号返 `chapters: []`
        ⇒ 先做**sanity check**（拿一本普通电子书问，确认接口本身没坏），
          再判"公众号在网关里是否本来就没有章节"。

线索二：`/store/search` 里 **scope=0 成功（4 条）、scope=2/4 返 -2041**
        ⇒ 系统性扫一遍 scope，看哪些可用；并把 scope=0 的回包**原文 dump**，
          确认那 4 条是什么（公众号？文章？有没有日期/URL ⇒ 能否做"追踪新文"）。

⚠ 只读。
"""
from __future__ import annotations

import json
from pathlib import Path

import requests

GATEWAY = "https://i.weread.qq.com/api/agent/gateway"
SKILL_VERSION = "1.0.5"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
KEY_FILE = Path.home() / ".tradingagents" / "weread" / "api_key.txt"

SCOPES = {0: "全部", 2: "公众号", 4: "公众号文章", 6: "作者", 10: "电子书",
          12: "全文", 13: "书单", 14: "听书", 16: "网文小说"}


def call(api_name: str, **params) -> tuple[int, dict]:
    key = KEY_FILE.read_text(encoding="utf-8").strip()
    body = {"api_name": api_name, "skill_version": SKILL_VERSION, **params}
    r = requests.post(GATEWAY, json=body, timeout=45, headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json",
        "User-Agent": UA})
    try:
        return r.status_code, (r.json() or {})
    except Exception:
        return r.status_code, {"_raw": r.text[:400]}


print("=" * 78)
print("① sanity：普通电子书的 chapterinfo（确认接口本身没坏）")
print("=" * 78)
st, d = call("/shelf/sync")
books = [b for b in (d.get("books") or []) if not str(b.get("bookId") or "").startswith("MP_WXS_")]
probe_book = next((b for b in books if b.get("bookId")), None)
if probe_book:
    st2, ch = call("/book/chapterinfo", bookId=probe_book["bookId"])
    n = len(ch.get("chapters") or [])
    print("  普通书《%s》 chapterinfo: HTTP %s 章节数=%d %s"
          % (str(probe_book.get("title"))[:22], st2, n, "✔ 接口正常" if n else "★ 接口也没数据"))
else:
    print("  书架里没有普通电子书可作对照")

print()
print("=" * 78)
print("② 系统性扫 scope（哪些可用；-2041 是不是 scope 级限制）")
print("=" * 78)
print("  %-4s %-10s %-6s %-9s %s" % ("scope", "语义", "HTTP", "errcode", "条目数/分组"))
for sc, name in SCOPES.items():
    st3, r3 = call("/store/search", keyword="期货", scope=sc, maxIdx=0, count=10)
    cnt, groups = 0, 0
    if isinstance(r3, dict):
        for g in (r3.get("results") or []):
            groups += 1
            cnt += len(g.get("books") or [])
    print("  %-4s %-10s %-6s %-9s %d/%d" % (sc, name, st3, r3.get("errcode"), cnt, groups))

print()
print("=" * 78)
print("③ scope=0 回包原文 dump（那 4 条到底是什么）")
print("=" * 78)
st4, r4 = call("/store/search", keyword="混沌天成研究", scope=0, maxIdx=0, count=20)
print("  HTTP %s  errcode=%s  顶层键=%s" % (st4, r4.get("errcode"), list(r4.keys())[:10]))
for gi, g in enumerate(r4.get("results") or []):
    print("  ── 分组 %d: %s" % (gi, json.dumps({k: v for k, v in g.items() if k != "books"},
                                             ensure_ascii=False)[:150]))
    for it in (g.get("books") or []):
        print("       %s" % json.dumps(it, ensure_ascii=False)[:260])

print()
print("=" * 78)
print("④ 关键判据：能不能「按号搜到它的文章」（方案 C 的可行性核心）")
print("=" * 78)
for kw in ("混沌天成研究", "恒力期货研究院", "双焦", "甲醇"):
    st5, r5 = call("/store/search", keyword=kw, scope=4, maxIdx=0, count=10)
    st6, r6 = call("/store/search", keyword=kw, scope=0, maxIdx=0, count=10)
    def cnt(x):
        return sum(len(g.get("books") or []) for g in ((x or {}).get("results") or []))
    print("  %-14s scope=4 → errcode=%-7s (%d 条) | scope=0 → errcode=%-7s (%d 条)"
          % (kw, r5.get("errcode"), cnt(r5), r6.get("errcode"), cnt(r6)))
print()
print("  ⇒ 若 scope=4 恒 -2041，则「搜文章」不可用；方案 C 的清单能力仅剩 scope=0 的混合结果。")
