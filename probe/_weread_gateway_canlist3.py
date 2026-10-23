# -*- coding: utf-8 -*-
"""方案 C 评估 · 收尾探：书架里那个「文章收藏」(`mpbook`) 能不能列出文章。

上一探已确认：`MP_WXS_*` 的 chapterinfo 恒为 []（普通书正常，43 章，非接口故障）。
还剩一个可能：`/shelf/sync` 回包里有个 `mp` 字段（`bookId="mpbook"`，标题「文章收藏」）
—— 那是"用户收藏过的文章"入口。若它能列文章，虽不等于"某号全部文章"，
但也许能当**增量**来源（用户收藏 = 一种筛选）。值得一测。

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
print("① /shelf/sync 的 mp 字段（文章收藏入口）")
print("=" * 78)
st, d = call("/shelf/sync")
mp = (d or {}).get("mp") or {}
print("  mp = %s" % json.dumps(mp, ensure_ascii=False)[:260])

print()
print("=" * 78)
print("② 对 mpbook 问三种接口（看能不能列文章）")
print("=" * 78)
for api in ("/book/info", "/book/chapterinfo", "/book/bookmarklist"):
    st2, r2 = call(api, bookId="mpbook")
    ch = r2.get("chapters") or []
    bms = r2.get("updated") or r2.get("bookmarks") or []
    print("  %-22s HTTP %-4s errcode=%-7s 章节=%-4d 书签=%d"
          % (api, st2, r2.get("errcode"), len(ch), len(bms)))
    if ch:
        for c in ch[:6]:
            print("       · %s" % json.dumps(c, ensure_ascii=False)[:200])
    elif api == "/book/chapterinfo":
        print("       回包: %s" % json.dumps(r2, ensure_ascii=False)[:200])

print()
print("=" * 78)
print("③ /user/notebooks（有笔记的书籍列表 —— 会不会包含公众号/文章？）")
print("=" * 78)
st3, r3 = call("/user/notebooks")
books = (r3.get("books") or []) if isinstance(r3, dict) else []
print("  HTTP %s errcode=%s 条目=%d" % (st3, r3.get("errcode"), len(books)))
mpn = [b for b in books if str((b.get("book") or b).get("bookId") or "").startswith("MP_WXS_")]
print("  其中公众号类: %d" % len(mpn))
for b in mpn[:8]:
    bb = b.get("book") or b
    print("    %-20s %s" % (bb.get("bookId"), bb.get("title")))

print()
print("=" * 78)
print("④ 方案 C 最终判定")
print("=" * 78)
print("  · /shelf/sync            ✅ 能列 6 个公众号**条目**（但条目≠文章）")
print("  · /book/chapterinfo      ❌ MP_WXS 恒 chapters:[]（普通书 43 章，非接口故障）")
print("  · /store/search scope=2/4 ❌ 恒 -2041（公众号 / 公众号文章 两个 tab 被拒）")
print("  · /user/notebooks        ❓ 见上（即便含公众号，也只是「有笔记的」子集）")
print("  ⇒ **方案 C 无法列出/追踪某号的新文章** ⇒ 不能替代读书通道。")
