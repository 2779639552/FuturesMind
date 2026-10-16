# -*- coding: utf-8 -*-
"""微信读书 Agent Gateway 只读探针 —— 确认「公众号 → 文章清单」能力边界。

要回答的问题（按优先级）：
  Q1 网关到底暴露了哪些接口？（/_list）—— 有没有「列公众号文章」的接口
  Q2 微信读书里「公众号」以什么形态存在？（/shelf/sync 的 books[] / mp）
  Q3 三个目标号在不在微信读书的书库里？（scope=2 搜公众号）
  Q4 能不能搜到文章？（scope=4）—— 说明存在文章级索引
  Q5 公众号的 bookId → /book/info + /book/chapterinfo 能否列出文章清单？

⚠️ 只读：只调用查询类接口，不写任何数据、不改书架。

用法：
    ./venv/Scripts/python.exe probe/_weread_probe_gateway.py
    ./venv/Scripts/python.exe probe/_weread_probe_gateway.py --key wrk-xxxx

Key 来源优先级：--key > 环境变量 WEREAD_API_KEY > ~/.tradingagents/weread/api_key.txt
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests

GATEWAY = "https://i.weread.qq.com/api/agent/gateway"
# ⚠️ 必须与本地技能 SKILL.md 的 `version` 一致：服务端会比对，落后就**每个回包**都塞
#   `upgrade_info`（文档规则 1 要求立即升级）。2026-10-14 实测 1.0.3 → 1.0.5。
SKILL_VERSION = "1.0.5"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")

KEY_FILE = Path.home() / ".tradingagents" / "weread" / "api_key.txt"

TARGETS = ["中信期货研究", "混沌天成研究", "中信建投期货研究",
           "CFC商品策略研究", "CFC金属研究"]

# 搜索 scope 语义（来自 weread-skills/references/search.md）
SCOPE = {0: "全部", 2: "公众号", 4: "公众号文章", 6: "作者", 10: "电子书",
         12: "全文", 13: "书单", 14: "听书", 16: "网文小说"}


def resolve_key(cli_key: str | None) -> str | None:
    if cli_key:
        return cli_key.strip()
    env = (os.environ.get("WEREAD_API_KEY") or "").strip()
    if env:
        return env
    if KEY_FILE.exists():
        t = KEY_FILE.read_text(encoding="utf-8", errors="replace").strip()
        # 允许文件里写 "WEREAD_API_KEY=wrk-xxx" 或纯 key
        if "=" in t:
            t = t.split("=", 1)[1].strip().strip('"').strip("'")
        if t:
            return t
    return None


def call(key: str, api_name: str, **params) -> tuple[int | None, object]:
    body = {"api_name": api_name, "skill_version": SKILL_VERSION}
    body.update(params)
    try:
        r = requests.post(
            GATEWAY,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json",
                     "User-Agent": UA},
            json=body, timeout=30,
        )
    except Exception as e:  # noqa: BLE001
        return None, f"ERR {e}"
    try:
        return r.status_code, r.json()
    except Exception:  # noqa: BLE001
        return r.status_code, r.text[:800]


def brief(obj, n: int = 700) -> str:
    """把回包压成一行便于肉眼读。"""
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    s = s.replace("\n", " ")
    return s[:n] + ("…" if len(s) > n else "")


def unwrap(obj):
    """业务数据通常在某些包装字段下；返回 (data, errcode, errmsg)。"""
    if not isinstance(obj, dict):
        return obj, None, None
    return obj, obj.get("errcode"), obj.get("errmsg")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=None, help="微信读书 API Key（wrk- 开头）")
    args = ap.parse_args()

    key = resolve_key(args.key)
    print("=" * 78)
    print("微信读书 Agent Gateway 只读探针")
    print("=" * 78)
    if not key:
        print("✘ 未找到 API Key。")
        print("  获取方式：https://weread.qq.com/r/weread-skills → 「获取 API Key」→ 微信扫码")
        print(f"  可写入文件：{KEY_FILE}")
        return 2
    print(f"Key: {key[:8]}…{key[-4:]}（长度 {len(key)}）\n")

    # ── Q1 全部可用接口 ────────────────────────────────────────────
    print("─" * 78)
    print("[Q1] /_list —— 网关暴露的全部接口")
    print("─" * 78)
    st, res = call(key, "/_list")
    print(f"HTTP {st}")
    data, ec, em = unwrap(res)
    if isinstance(data, dict):
        # 接口定义可能挂在多个键下，全挖出来
        apis = None
        for k in ("apis", "api_list", "list", "data", "interfaces"):
            v = data.get(k)
            if isinstance(v, list) and v:
                apis = v
                break
            if isinstance(v, dict) and v:
                apis = v
                break
        if apis is None:
            print("回包顶层键：", list(data.keys()))
            print(brief(data, 1500))
        else:
            print(f"接口条目数：{len(apis)}")
            if isinstance(apis, dict):
                items = apis.items()
            else:
                items = [(None, x) for x in apis]
            for k, v in items:
                if isinstance(v, dict):
                    name = v.get("api_name") or v.get("name") or k
                    desc = v.get("desc") or v.get("description") or v.get("title") or ""
                    print(f"  {str(name):32s} {str(desc)[:70]}")
                else:
                    print(f"  {k}")
        gold = json.dumps(data, ensure_ascii=False)
        for kw in ("mp", "article", "album", "公众号", "文章"):
            if kw in gold:
                i = gold.find(kw)
                print(f"  ★ 含关键词「{kw}」: …{gold[max(0,i-90):i+90]}…".replace("\n", " "))
    else:
        print(brief(res, 1200))
    if em:
        print(f"  errcode={ec} errmsg={em}")

    # ── Q2 书架形态 ────────────────────────────────────────────────
    print()
    print("─" * 78)
    print("[Q2] /shelf/sync —— 书架里的「公众号」条目")
    print("─" * 78)
    st, res = call(key, "/shelf/sync")
    d, ec, em = unwrap(res)
    print(f"HTTP {st}  errcode={ec} {em or ''}")
    if isinstance(d, dict):
        books = d.get("books") or []
        albums = d.get("albums") or []
        mp = d.get("mp")
        print(f"books={len(books)}  albums={len(albums)}  mp={'非空' if mp else '空'}")
        # 公众号类条目：微信读书里 title 通常是号名；也有 category/format 线索
        hit = []
        for b in books:
            blob = json.dumps(b, ensure_ascii=False)
            if any(t in blob for t in TARGETS) or "公众号" in blob:
                hit.append(b)
        print(f"★ 疑似公众号条目：{len(hit)}")
        for b in hit[:12]:
            print("   bookId=%-14s title=%-22s author=%-14s category=%s" % (
                b.get("bookId"), str(b.get("title"))[:22],
                str(b.get("author"))[:14], b.get("category")))
        if mp:
            print("   mp 入口：", brief(mp, 400))
        # 书架里搜不到时，把全部标题打出来抽样看形态（最多 30）
        if not hit:
            print("   （书架无目标号，列出前 15 条以观察字段形态）")
            for b in books[:15]:
                print("   bookId=%-14s title=%-26s category=%s" % (
                    b.get("bookId"), str(b.get("title"))[:26], b.get("category")))
    else:
        print(brief(res, 800))

    # ── Q3/Q4 搜索：公众号 + 公众号文章 ────────────────────────────
    for sc in (2, 4):
        print()
        print("─" * 78)
        print(f"[Q{'3' if sc == 2 else '4'}] /store/search scope={sc}（{SCOPE[sc]}）")
        print("─" * 78)
        kw_list = TARGETS if sc == 2 else ["多空逻辑", "中信期货"]
        for kw in kw_list:
            st, res = call(key, "/store/search", keyword=kw, scope=sc)
            d, ec, em = unwrap(res)
            if not isinstance(d, dict):
                print(f"  {kw:18s} HTTP {st}  {brief(res, 220)}")
                continue
            results = d.get("results") or []
            flat = []
            for g in results:
                for b in (g.get("books") or []):
                    info = b.get("bookInfo") or b
                    flat.append(info)
            print(f"  {kw:18s} HTTP {st}  errcode={ec} 分组={len(results)} 条目={len(flat)}"
                  + (f"  errmsg={em}" if em else ""))
            for info in flat[:6]:
                print("      bookId=%-14s title=%-24s author=%-16s" % (
                    info.get("bookId"), str(info.get("title"))[:24],
                    str(info.get("author"))[:16]))
            # 记住第一个命中的 bookId 供 Q5 用
            if sc == 2 and flat:
                globals().setdefault("_found", []).append((kw, flat[0].get("bookId")))

    # ── Q5 公众号 → 文章清单 ───────────────────────────────────────
    print()
    print("─" * 78)
    print("[Q5] 公众号 bookId → /book/info + /book/chapterinfo（文章清单？）")
    print("─" * 78)
    found = globals().get("_found") or []
    if not found:
        print("  搜索未命中任何公众号 bookId，跳过。")
    for name, bid in found:
        if not bid:
            continue
        print(f"\n  ── {name}  bookId={bid}")
        st, res = call(key, "/book/info", bookId=bid)
        d, ec, em = unwrap(res)
        if isinstance(d, dict):
            info = d.get("bookInfo") or d
            for k in ("title", "author", "category", "intro", "publisher",
                      "updateTime", "totalWords", "chapterCount", "payType"):
                if k in info:
                    v = str(info[k])[:90]
                    print(f"     info.{k:14s} = {v}")
            if em:
                print(f"     errcode={ec} {em}")
        else:
            print(f"     /book/info HTTP {st} {brief(res, 200)}")

        st, res = call(key, "/book/chapterinfo", bookId=bid)
        d, ec, em = unwrap(res)
        if isinstance(d, dict):
            chs = d.get("chapters") or d.get("data") or []
            print(f"     chapterinfo: chapters={len(chs) if isinstance(chs, list) else '?'}"
                  + (f"  errcode={ec} {em}" if em else ""))
            if isinstance(chs, list):
                for c in chs[:8]:
                    print("        chapterUid=%-12s %s" % (
                        c.get("chapterUid"), str(c.get("title"))[:52]))
                if len(chs) > 8:
                    print(f"        … 其余 {len(chs) - 8} 章")
                if chs:
                    print("     ★ chapter[0] 全字段：", brief(chs[0], 400))
        else:
            print(f"     /book/chapterinfo HTTP {st} {brief(res, 250)}")

    print()
    print("=" * 78)
    print("探针结束（只读，未修改任何数据）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
