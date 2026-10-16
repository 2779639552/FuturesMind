# -*- coding: utf-8 -*-
"""微信读书「公众号」条目深探 —— 目标：拿到该号的文章清单。

上一轮发现：
  · /shelf/sync 里公众号条目的 bookId 形如 `MP_WXS_<biz_uin>`，author=公众号
  · /book/chapterinfo 的章节字段含 `isMPChapter` ⇒ 支持"文章即章节"的模型
  · 但 /store/search 的 scope=2/4 疑似未生效（返回的是电子书）

本轮只回答一件事：**能不能从 MP_WXS_ 条目列出文章清单**（成败关键）。
另附带验证 scope 是否真的被网关转发。

⚠️ 只读。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _weread_probe_gateway import KEY_FILE, call, brief, resolve_key, unwrap  # noqa: E402


def dump(title: str, obj, n: int = 900) -> None:
    print(f"\n── {title}")
    print("   " + brief(obj, n))


def main() -> int:
    key = resolve_key(None)
    if not key:
        print("✘ 无 Key")
        return 2
    print("=" * 78)
    print("微信读书「公众号条目」深探（只读）")
    print("=" * 78)

    # ── [A] 书架里全部 MP_WXS_ 公众号条目 ──────────────────────────
    print("\n[A] /shelf/sync → 所有 MP_ 前缀条目")
    st, res = call(key, "/shelf/sync")
    d, _, _ = unwrap(res)
    mp_books = []
    if isinstance(d, dict):
        for b in (d.get("books") or []):
            bid = str(b.get("bookId") or "")
            if bid.startswith("MP_") or b.get("author") == "公众号":
                mp_books.append(b)
        print(f"   公众号条目数 = {len(mp_books)}（书架 books 共 {len(d.get('books') or [])}）")
        for b in mp_books:
            print("   " + json.dumps(b, ensure_ascii=False)[:280])
    else:
        print("   ", brief(res, 400))
        return 1

    if not mp_books:
        print("   ✘ 书架无公众号条目")
        return 1

    target = mp_books[0]
    bid = target["bookId"]
    print(f"\n   选中探测对象：{target.get('title')} → {bid}")

    # ── [B] /book/info ─────────────────────────────────────────────
    print("\n[B] /book/info")
    st, res = call(key, "/book/info", bookId=bid)
    dump(f"HTTP {st}", res, 1100)

    # ── [C] /book/chapterinfo —— 成败关键 ──────────────────────────
    print("\n[C] /book/chapterinfo  ← 能不能列出文章清单？")
    st, res = call(key, "/book/chapterinfo", bookId=bid)
    d2, ec, em = unwrap(res)
    print(f"   HTTP {st}  errcode={ec} {em or ''}")
    if isinstance(d2, dict):
        print("   顶层键：", list(d2.keys()))
        chs = d2.get("chapters")
        if chs is None:
            for k in ("data", "chapterList", "list"):
                if isinstance(d2.get(k), list):
                    chs = d2[k]
                    print(f"   （章节挂在 `{k}` 下）")
                    break
        if isinstance(chs, list):
            print(f"   ★ 章节数 = {len(chs)}")
            for c in chs[:10]:
                flags = []
                if c.get("isMPChapter"):
                    flags.append("isMPChapter=1")
                for fk in ("updateTime", "createTime", "wordCount", "paid", "price"):
                    if c.get(fk):
                        flags.append(f"{fk}={c[fk]}")
                print("      uid=%-10s %-46s %s" % (
                    c.get("chapterUid"), str(c.get("title"))[:46], " ".join(flags)))
            if len(chs) > 10:
                print(f"      … 其余 {len(chs) - 10} 章")
            if chs:
                print("\n   ★ chapters[0] 全字段：")
                print("   " + json.dumps(chs[0], ensure_ascii=False, indent=2).replace("\n", "\n   "))
                print("\n   ★ chapters[-1] 全字段：")
                print("   " + json.dumps(chs[-1], ensure_ascii=False, indent=2).replace("\n", "\n   "))
        else:
            print("   章节字段类型异常：", type(chs))
            print("   ", brief(d2, 900))
    else:
        print("   ", brief(res, 700))

    # ── [D] 其他可能的文章清单接口 ─────────────────────────────────
    print("\n[D] 其他接口对 MP_WXS_ 条目的反应")
    for api, extra in [("/book/bookmarklist", {}),
                       ("/book/getprogress", {}),
                       ("/book/bestbookmarks", {}),
                       ("/review/list", {"type": 0})]:
        st, res = call(key, api, bookId=bid, **extra)
        dd, ec, em = unwrap(res)
        keys = list(dd.keys()) if isinstance(dd, dict) else type(dd).__name__
        print(f"   {api:26s} HTTP {st} errcode={ec} keys={keys}"
              + (f" errmsg={em}" if em else ""))
        if isinstance(dd, dict) and em is None:
            print("        " + brief(dd, 320))

    # ── [E] scope 到底有没有被转发 ─────────────────────────────────
    print("\n[E] /store/search scope 转发验证（同词不同 scope，看结果是否变化）")
    kw = "混沌天成研究"
    for sc in (0, 2, 4):
        st, res = call(key, "/store/search", keyword=kw, scope=sc)
        dd, ec, em = unwrap(res)
        if isinstance(dd, dict):
            groups = [(g.get("title"), g.get("scope"), len(g.get("books") or []))
                      for g in (dd.get("results") or [])]
            print(f"   scope={sc}  HTTP {st}  分组={groups}")
        else:
            print(f"   scope={sc}  HTTP {st}  {brief(res, 160)}")

    st, res = call(key, "/store/search", keyword=kw, scope=2, v=2)
    dd, _, _ = unwrap(res)
    if isinstance(dd, dict):
        print(f"   额外参数 v=2  分组={[(g.get('title'), g.get('scope')) for g in (dd.get('results') or [])]}")

    print("\n" + "=" * 78)
    print("深探结束（只读）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
