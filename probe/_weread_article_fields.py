# -*- coding: utf-8 -*-
"""微信读书公众号文章列表 —— 字段结构考古 + 分页行为实测。

背景：`GET https://weread.qq.com/web/mp/articles?bookId=MP_WXS_xxx&offset=N`
已实测可用（需 cookie：wr_vid/wr_skey/wr_rt）。但回包**不是**扁平的文章数组：
顶层是 `{reviews, clearAll, synckey}`，每项里嵌 `subReviews[].review.mpInfo`。
本脚本要挖出「标题 / 原文链接 / 发布时间」到底在哪一层，并确认分页口径。

只读。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
COOKIE_FILE = Path.home() / ".tradingagents" / "weread" / "cookies.json"
TARGET = ("混沌天成研究", "MP_WXS_3869147758")


def cookie_header() -> str:
    j = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
    return "; ".join(f"{c['name']}={c['value']}" for c in j["cookies"])


def get_articles(ch: str, book_id: str, offset: int = 0) -> tuple[int, object]:
    r = requests.get(
        "https://weread.qq.com/web/mp/articles",
        params={"bookId": book_id, "offset": offset},
        headers={"User-Agent": UA, "Accept": "application/json, text/plain, */*",
                 "Referer": "https://weread.qq.com/", "Cookie": ch},
        timeout=30,
    )
    try:
        return r.status_code, r.json()
    except Exception:  # noqa: BLE001
        return r.status_code, r.text[:600]


def walk_keys(obj, prefix="", depth=0, out=None):
    """收集所有键路径及示例值类型，便于定位字段。"""
    if out is None:
        out = {}
    if depth > 6:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)):
                out.setdefault(p, f"<{type(v).__name__} len={len(v)}>")
                walk_keys(v, p, depth + 1, out)
            else:
                if p not in out:
                    out[p] = repr(v)[:70]
    elif isinstance(obj, list) and obj:
        walk_keys(obj[0], prefix + "[0]", depth + 1, out)
    return out


def main() -> int:
    ch = cookie_header()
    name, bid = TARGET
    print("=" * 78)
    print(f"字段考古：{name}  ({bid})")
    print("=" * 78)

    st, d = get_articles(ch, bid, 0)
    print(f"\nHTTP {st}")
    if not isinstance(d, dict):
        print("非 JSON:", str(d)[:300])
        return 1
    print("顶层键:", list(d.keys()))
    reviews = d.get("reviews") or []
    print(f"reviews 数 = {len(reviews)}")
    print(f"clearAll = {d.get('clearAll')}   synckey = {d.get('synckey')}")

    if not reviews:
        print("!! reviews 为空")
        return 1

    r0 = reviews[0]
    print(f"\n── reviews[0] 自身键: {list(r0.keys())}")
    print("── reviews[0] 全字段（前 1200 字符）:")
    print(json.dumps(r0, ensure_ascii=False, indent=1)[:1200])

    print("\n" + "─" * 78)
    print("全部键路径（定位标题/链接/时间）")
    print("─" * 78)
    keys = walk_keys(reviews)
    for k, v in sorted(keys.items()):
        print(f"  {k:62s} {v}")

    # 找标题/链接候选
    print("\n" + "─" * 78)
    print("候选字段（名字里含 title/url/link/time/original/cover/summary）")
    print("─" * 78)
    for k, v in sorted(keys.items()):
        low = k.lower()
        if any(t in low for t in ("title", "url", "link", "time", "original", "cover",
                                  "summary", "abstract", "desc", "token", "idx")):
            print(f"  {k:62s} {v}")

    # reviews[0].subReviews 逐条
    print("\n" + "─" * 78)
    print(f"reviews[0].subReviews（{len(r0.get('subReviews') or [])} 条）")
    print("─" * 78)
    for i, sr in enumerate((r0.get("subReviews") or [])):
        rv = sr.get("review") or {}
        mp = rv.get("mpInfo") or {}
        print(f"  [{i}] subReview 键={list(sr.keys())}")
        print(f"      review 键={list(rv.keys())}")
        print(f"      mpInfo 键={list(mp.keys())}")
        print(f"      reviewId   = {str(rv.get('reviewId'))[:70]}…")
        print(f"      mpInfo     = {json.dumps(mp, ensure_ascii=False)[:400]}")
        print(f"      rv 非mpInfo字段: " + json.dumps(
            {k: v for k, v in rv.items() if k not in ("reviewId", "mpInfo")},
            ensure_ascii=False)[:400])

    # 分页行为
    print("\n" + "─" * 78)
    print("分页行为实测（offset 步长）")
    print("─" * 78)
    for off in (0, 20, 50, 100):
        st2, d2 = get_articles(ch, bid, off)
        if isinstance(d2, dict):
            rv2 = d2.get("reviews") or []
            first = ""
            if rv2:
                mp = ((rv2[0].get("subReviews") or [{}])[0].get("review") or {}).get("mpInfo") or {}
                t = rv2[0].get("createTime") or 0
                import datetime
                first = (datetime.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d")
                         if t else "?") + " | " + str(mp.get("title") or mp.get("originalId"))[:30]
            print(f"  offset={off:4d}  HTTP {st2}  reviews={len(rv2):3d}  首条={first}")
        else:
            print(f"  offset={off:4d}  HTTP {st2}  非JSON {str(d2)[:110]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
