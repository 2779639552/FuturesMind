# -*- coding: utf-8 -*-
"""
抖音采集探测(持久 profile + API 拦截版, 2026-09-15)
====================================================
配套 douyin_login_profile.py:用同一个持久 profile 打开搜索页与视频页,
不依赖 DOM 渲染(登录态下搜索页 DOM 可能渲染失败),直接拦截页面自己发的
接口响应解析:

  搜索: /aweme/v1/web/search/item/       → aweme_list[].aweme_id
  评论: /aweme/v1/web/comment/list/      → comments[]

用法:
    python probe_douyin_profile.py                       # 3 个默认关键词
    python probe_douyin_profile.py --keywords 甲醇期货
    python probe_douyin_profile.py --max-videos 3 --max-comment-pages 2
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent))
from douyin_login_profile import open_profile_context, is_logged_in  # noqa: E402

OUTPUT_PATH = Path(__file__).parent / "output" / "douyin_profile_probe.json"
DEFAULT_KEYWORDS = ["甲醇期货", "螺纹钢期货", "纯碱期货"]


def _wait_search_response(page, timeout=25):
    """等下一次 search/item 响应并解析(登录态页面自己签名发请求,直接收结果)。"""
    holder = []

    def handler(r):
        if "/aweme/v1/web/search/item/" in r.url and len(holder) == 0:
            try:
                holder.append(r.json())
            except Exception:
                pass

    page.on("response", handler)
    try:
        deadline = time.time() + timeout
        while time.time() < deadline and not holder:
            time.sleep(0.5)
    finally:
        page.remove_listener("response", handler)
    return holder[0] if holder else None


def probe_search(page, keyword: str) -> dict:
    out = {"keyword": keyword, "ok": False, "video_count": 0, "videos": [],
           "block": "", "error": ""}
    try:
        # 综合通道(general/search/single)可用;?type=video 的 search/item 通道被风控软拦(200空)
        d_holder = []

        def search_handler(r):
            if "/aweme/v1/web/general/search/single/" in r.url and len(d_holder) < 5:
                try:
                    d_holder.append(r.json())
                except Exception:
                    pass

        page.on("response", search_handler)
        try:
            page.goto(f"https://www.douyin.com/search/{quote(keyword)}",
                      timeout=30000, wait_until="domcontentloaded")
        except Exception:
            pass  # 页面继续由下面的等待逻辑接管
        # 等响应(最多 25s)
        deadline = time.time() + 25
        while time.time() < deadline and not d_holder:
            time.sleep(0.5)
        time.sleep(2)
        # 登录墙/验证码检测
        title = page.title() or ""
        if "验证码" in title:
            out["block"] = "验证码/滑块拦截"
            return out
        body = page.inner_text("body")[:1500] if page.query_selector("body") else ""
        if "扫码登录" in body or "登录后即可" in body:
            out["block"] = "登录墙"
            return out
        if not d_holder:
            out["error"] = "未捕获 general/search/single 响应"
            return out
        # data[] 每项可能是 {type, aweme_info} 或直接 aweme 对象
        seen = set()
        raw = []
        for d in d_holder:
            for item in d.get("data") or []:
                aw = item.get("aweme_info") or (item if item.get("aweme_id") else None)
                if aw and aw.get("aweme_id"):
                    raw.append(aw)
        out["status_code"] = d_holder[0].get("status_code")
        seen = set()
        for item in raw:
            aw = item.get("aweme_info") or item
            vid = aw.get("aweme_id")
            if not vid or vid in seen:
                continue
            seen.add(vid)
            st = aw.get("statistics") or {}
            out["videos"].append({
                "aweme_id": vid,
                "desc": (aw.get("desc") or "")[:100],
                "author": ((aw.get("author") or {}).get("nickname") or ""),
                "like_count": st.get("digg_count"),
                "comment_count": st.get("comment_count"),
                "create_time": aw.get("create_time"),
                "href": f"https://www.douyin.com/video/{vid}",
            })
        out["video_count"] = len(out["videos"])
        out["ok"] = bool(out["videos"])
        if not out["videos"] and d.get("status_code") == 0:
            out["block"] = "接口 200 但结果为空(疑似风控软拦)"
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:150]}"
    return out


def probe_comments(page, video: dict, max_pages: int = 1) -> dict:
    """打开视频页,拦截 comment/list 响应(可翻页触发更多)。"""
    out = {"aweme_id": video["aweme_id"], "ok": False, "comment_count": 0,
           "total": None, "samples": [], "block": "", "error": ""}
    comments, seen = [], set()

    def handler(r):
        if "/aweme/v1/web/comment/list/" in r.url:
            try:
                d = r.json()
                for c in d.get("comments") or []:
                    cid = c.get("cid")
                    if cid and cid not in seen:
                        seen.add(cid)
                        comments.append({
                            "cid": cid,
                            "text": (c.get("text") or "")[:200],
                            "user": ((c.get("user") or {}).get("nickname") or ""),
                            "like_count": c.get("digg_count"),
                            "create_time": c.get("create_time"),
                            "ip_label": c.get("ip_label"),
                        })
                if d.get("total") is not None:
                    out["total"] = d.get("total")
            except Exception:
                pass

    try:
        page.on("response", handler)
        page.goto(video.get("href") or
                  f"https://www.douyin.com/video/{video['aweme_id']}",
                  timeout=30000, wait_until="domcontentloaded")
        time.sleep(4)
        title = page.title() or ""
        if "验证码" in title:
            out["block"] = "验证码/滑块拦截"
            return out
        # 滚动评论区触发翻页加载
        for i in range(max_pages):
            page.evaluate("""
                const el = document.querySelector('[data-e2e="comment-list"]')
                    || document.scrollingElement;
                if (el) el.scrollBy(0, 1500);
            """)
            time.sleep(2.5)
        out["comment_count"] = len(comments)
        out["ok"] = bool(comments)
        out["samples"] = comments[:10]
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:150]}"
    finally:
        page.remove_listener("response", handler)
    return out


def main():
    ap = argparse.ArgumentParser(description="抖音持久profile采集探测")
    ap.add_argument("--keywords", nargs="+", default=DEFAULT_KEYWORDS)
    ap.add_argument("--max-videos", type=int, default=2)
    ap.add_argument("--max-comment-pages", type=int, default=1)
    ap.add_argument("--no-headless", action="store_true")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    report = {"probed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              "keywords": []}
    with sync_playwright() as p:
        ctx = open_profile_context(p, headless=not args.no_headless)
        if not is_logged_in(ctx):
            print("!! profile 无登录态,请先运行: python douyin_login_profile.py")
            ctx.close()
            return 2
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        for kw in args.keywords:
            print(f"\n=== 关键词: {kw} ===")
            sr = probe_search(page, kw)
            print(f"  搜索: ok={sr['ok']} 视频数={sr['video_count']} "
                  f"拦截={sr['block'] or '无'} 错误={sr['error'] or '无'}")
            kw_entry = {"keyword": kw, **sr, "comment_probes": []}
            for v in sr["videos"][:args.max_videos]:
                cr = probe_comments(page, v, max_pages=args.max_comment_pages)
                print(f"    视频 {v['aweme_id']} ({v['desc'][:20]}): "
                      f"评论={cr['comment_count']}/总数{cr['total']} "
                      f"拦截={cr['block'] or '无'} 错误={cr['error'] or '无'}")
                for s in cr["samples"][:3]:
                    print(f"      样本: {s['text'][:50]}")
                kw_entry["comment_probes"].append(cr)
                time.sleep(2)
            report["keywords"].append(kw_entry)
            time.sleep(3)
        ctx.close()

    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    total_v = sum(k["video_count"] for k in report["keywords"])
    total_c = sum(cp["comment_count"] for k in report["keywords"]
                  for cp in k["comment_probes"])
    print("\n" + "=" * 60)
    print(f"探测汇总: 视频 {total_v} | 评论 {total_c} 条")
    print(f"详情: {OUTPUT_PATH}")
    return 0 if total_c >= 10 else 1


if __name__ == "__main__":
    sys.exit(main())
