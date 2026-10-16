"""
抖音评论免登录采集探测(2026-09-07)
====================================
决策门脚本:回答"免登录状态下抖音能采到多少评论数据"。

对每个试点关键词:
  1. 打开抖音搜索页 → 统计可提取的视频卡片(标题/链接/作者/互动数)
  2. 取前 N 个视频 → 打开视频页 → 等待并滚动评论区 → 统计可提取的评论条数
  3. 记录拦截形态(验证码/登录墙/空评区/选择器失效)

产出:终端报告 + output/douyin_probe.json

用法:
    python probe_douyin_comments.py                 # 3 个默认试点关键词
    python probe_douyin_comments.py --keywords 甲醇期货 螺纹钢
    python probe_douyin_comments.py --headless      # 无头(默认),--no-headless 观察浏览器
    python probe_douyin_comments.py --max-videos 2  # 每关键词探测的视频数(默认 2)
"""

import argparse
import json
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

OUTPUT_PATH = Path(__file__).parent / "output" / "douyin_probe.json"

# 反检测脚本:与 validator_douyin.py 同套路
_STEALTH_JS = """
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    delete navigator.__proto__.webdriver;
    window.navigator.chrome = { runtime: {} };
    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
    Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
"""

DEFAULT_KEYWORDS = ["甲醇期货", "螺纹钢期货", "纯碱期货"]


def _rand_sleep(a=1.5, b=3.5):
    time.sleep(random.uniform(a, b))


def _detect_block(page) -> str:
    """探测页面拦截形态,返回描述(无拦截返回空串)。"""
    try:
        title = page.title() or ""
        text = page.inner_text("body")[:2000]
    except Exception:
        return "页面文本读取失败"
    # 标题是抖音拦截页最可靠的信号(验证码中间页 body 可能是 JS 渲染的空壳)
    if "验证码" in title:
        return "验证码/滑块拦截"
    if "验证码" in text or "拖动滑块" in text or "verify" in title.lower():
        return "验证码/滑块拦截"
    if "扫码登录" in text or "登录后即可" in text:
        return "登录墙"
    return ""


def probe_search(page, keyword: str, debug: bool = False) -> dict:
    """搜索页探测:统计视频卡片。"""
    out = {"keyword": keyword, "ok": False, "video_count": 0,
           "videos": [], "block": "", "error": ""}
    url = f"https://www.douyin.com/search/{quote(keyword)}?type=video"
    try:
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        _rand_sleep(3, 5)
        block = _detect_block(page)
        if block:
            out["block"] = block
            return out
        # 等真实视频卡片出现(骨架屏阶段提取必空;登录态下内容靠 XHR 慢慢填充)
        try:
            page.wait_for_selector(
                '[data-e2e="scroll-list-item-item"] a[href*="/video/"]',
                timeout=20000, state="attached")
        except Exception:
            # 兜底:再滚两轮等懒加载
            for _ in range(2):
                page.evaluate("window.scrollBy(0, 900)")
                _rand_sleep(2, 3)
            try:
                page.wait_for_selector(
                    '[data-e2e="scroll-list-item-item"] a[href*="/video/"]',
                    timeout=10000, state="attached")
            except Exception:
                pass
        # 滚动触发懒加载
        for _ in range(3):
            page.evaluate("window.scrollBy(0, 900)")
            _rand_sleep(1, 2)
        if debug:  # 【调试】失败时留页面现场:截图 + 标题 + 正文前 500 字(写 UTF-8 文件防控制台乱码)
            dbg = Path(__file__).parent / "output" / f"probe_debug_{keyword}.png"
            page.screenshot(path=str(dbg), full_page=False)
            body_text = page.inner_text("body")[:500]
            Path(__file__).parent.joinpath("output").mkdir(exist_ok=True)
            with open(Path(__file__).parent / "output" / "probe_debug.txt", "a", encoding="utf-8") as f:
                f.write(f"\n{'='*40} {keyword} @ {datetime.now():%H:%M:%S}\n"
                        f"url={page.url}\ntitle={page.title()}\n{body_text}\n")
        # 多选择器兜底提取视频卡片
        cards = page.query_selector_all('[data-e2e="scroll-list-item-item"], [data-e2e="search-list-item"]')
        if not cards:
            cards = page.query_selector_all('li[class*="search"] a[href*="/video/"]')
        if not cards:
            # 最后兜底:直接从 DOM 里抓所有 /video/ 链接
            hrefs = page.evaluate(
                "Array.from(document.querySelectorAll('a[href*=\"/video/\"]'))"
                ".map(a => ({href: a.href, text: (a.innerText||'').slice(0,120)}))"
            )
            seen = set()
            for h in hrefs:
                m = re.search(r"/video/(\d+)", h["href"])
                if m and m.group(1) not in seen:
                    seen.add(m.group(1))
                    out["videos"].append({"aweme_id": m.group(1), "desc": h["text"], "href": h["href"]})
            out["video_count"] = len(out["videos"])
            out["ok"] = bool(out["videos"])
            return out
        seen = set()
        for c in cards:
            a = c.query_selector('a[href*="/video/"]')
            if not a:
                continue
            href = a.get_attribute("href") or ""
            m = re.search(r"/video/(\d+)", href)
            if not m or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            out["videos"].append({
                "aweme_id": m.group(1),
                "desc": (c.inner_text() or "")[:120],
                "href": a.get_attribute("href") or "",
            })
        out["video_count"] = len(out["videos"])
        out["ok"] = bool(out["videos"])
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:150]}"
    return out


def probe_comments(page, video: dict, max_scroll: int = 8) -> dict:
    """视频页评论探测:统计可提取的评论条数。"""
    out = {"aweme_id": video["aweme_id"], "ok": False, "comment_count": 0,
           "samples": [], "block": "", "error": ""}
    url = video.get("href") or f"https://www.douyin.com/video/{video['aweme_id']}"
    if url.startswith("/"):
        url = f"https://www.douyin.com{url}"
    try:
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        _rand_sleep(3, 5)
        block = _detect_block(page)
        if block:
            out["block"] = block
            return out
        # 等评论面板出现
        got_panel = False
        for sel in ('[data-e2e="comment-list"]', '[class*="comment"]'):
            try:
                page.wait_for_selector(sel, timeout=8000)
                got_panel = True
                break
            except Exception:
                continue
        # 滚动评论区加载更多(评论面板在右侧,滚动它;失败就滚整页)
        for i in range(max_scroll):
            page.evaluate("""
                const el = document.querySelector('[data-e2e="comment-list"]')
                    || document.querySelector('[class*="commentList"]') || document.scrollingElement;
                if (el) el.scrollBy(0, 1200); else window.scrollBy(0, 1200);
            """)
            _rand_sleep(1, 2)
        # 提取评论条目
        items = page.query_selector_all('[data-e2e="comment-item"]')
        if not items:
            items = page.query_selector_all('[class*="CommentItem"], [class*="comment-item"]')
        seen = set()
        for it in items:
            try:
                txt = (it.inner_text() or "").strip()
                if not txt or txt[:40] in seen:
                    continue
                seen.add(txt[:40])
                if len(out["samples"]) < 10:
                    out["samples"].append(txt[:150].replace("\n", " / "))
            except Exception:
                continue
        out["comment_count"] = len(seen)
        out["ok"] = bool(seen)
        if not got_panel and not seen:
            out["block"] = out["block"] or "评论面板未出现(可能需登录)"
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:150]}"
    return out


def main():
    ap = argparse.ArgumentParser(description="抖音评论免登录采集探测")
    ap.add_argument("--keywords", nargs="+", default=DEFAULT_KEYWORDS)
    ap.add_argument("--max-videos", type=int, default=2)
    ap.add_argument("--no-headless", action="store_true", help="显示浏览器窗口观察")
    ap.add_argument("--use-login-state", action="store_true",
                    help="加载 credentials/douyin_login_state.json(存在时)")
    ap.add_argument("--debug", action="store_true", help="每次搜索保存截图+正文转储到 output/")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    state_file = Path(__file__).parent / "credentials" / "douyin_login_state.json"
    report = {
        "probed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "logged_in": args.use_login_state and state_file.exists(),
        "keywords": [],
    }
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not args.no_headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
                  "--disable-dev-shm-usage"],
        )
        ctx_kw = {
            "viewport": {"width": 1440, "height": 900},
            "user_agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/126.0.0.0 Safari/537.36"),
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
        }
        if args.use_login_state and state_file.exists():
            ctx_kw["storage_state"] = str(state_file)
        context = browser.new_context(**ctx_kw)
        context.add_init_script(_STEALTH_JS)
        page = context.new_page()

        for kw in args.keywords:
            print(f"\n=== 关键词: {kw} ===")
            sr = probe_search(page, kw, debug=args.debug)
            print(f"  搜索: ok={sr['ok']} 视频数={sr['video_count']} "
                  f"拦截={sr['block'] or '无'} 错误={sr['error'] or '无'}")
            kw_entry = {"keyword": kw, **sr, "comment_probes": []}
            for v in sr["videos"][:args.max_videos]:
                cr = probe_comments(page, v)
                print(f"    视频 {v['aweme_id']}: 评论数={cr['comment_count']} "
                      f"拦截={cr['block'] or '无'} 错误={cr['error'] or '无'}")
                if cr["samples"]:
                    for s in cr["samples"][:3]:
                        print(f"      样本: {s[:60]}")
                kw_entry["comment_probes"].append(cr)
                _rand_sleep(2, 4)
            report["keywords"].append(kw_entry)
            _rand_sleep(2, 5)
        browser.close()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 汇总判定
    total_videos = sum(k["video_count"] for k in report["keywords"])
    total_comments = sum(cp["comment_count"] for k in report["keywords"]
                         for cp in k["comment_probes"])
    blocks = {cp["block"] for k in report["keywords"] for cp in k["comment_probes"] if cp["block"]}
    print("\n" + "=" * 60)
    print(f"探测汇总: 视频 {total_videos} 条 | 评论 {total_comments} 条 | 拦截形态: {blocks or '无'}")
    verdict = ("免登录可采" if total_comments >= 10 else
               "免登录基本不可采(建议扫码登录后重试: python douyin_login.py)")
    print(f"判定: {verdict}")
    print(f"详情已写: {OUTPUT_PATH}")
    return 0 if total_comments >= 10 else 1


if __name__ == "__main__":
    sys.exit(main())
