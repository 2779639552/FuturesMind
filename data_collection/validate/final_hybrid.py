"""
最终混合管道: Playwright发现 + 浏览器内API调用深挖
=====================================================

核心策略:
  Layer 1: Playwright搜索 → 收集note_id列表
  Layer 2: 在浏览器内直接用 fetch() 调用小红书API → 拿完整JSON

为什么这个方案更好:
  - 不需要拦截网络请求（不可靠）
  - 不需要点击卡片（卡片不可见）
  - 直接利用浏览器的认证和context发起fetch
  - API返回结构化JSON，比DOM解析干净10倍

使用:
  python final_hybrid.py --no-login --keywords "铁矿石" "螺纹钢" --max-depth 5
"""

import argparse
import json
import logging
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from xhs_scraper import (
    ANTI_DETECTION_SCRIPT,
    OUTPUT_DIR,
    STORAGE_STATE_FILE,
    decode_objectid_timestamp,
    is_futures_related_xhs,
)

logger = logging.getLogger("final.hybrid")

FUTURES_KEYWORDS = ["螺纹钢期货", "铁矿石期货", "原油期货", "黄金期货", "PTA期货", "豆粕期货"]


@dataclass
class RichNote:
    """从API获取的完整笔记数据"""

    note_id: str
    title: str
    desc: str
    author_name: str
    author_id: str
    author_fans: int
    like_count: int
    comment_count: int
    collect_count: int
    share_count: int
    tags: list
    topics: list
    images: list
    note_type: str
    publish_time: str
    ip_location: str
    keyword: str
    url: str


class FinalHybrid:
    def __init__(self):
        self.pw = None
        self.browser = None
        self.page = None

    def start(self, need_login=True):
        """启动浏览器"""
        logger.info("Starting browser...")
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--window-size=1280,900"],
            slow_mo=30,
        )
        ctx_opts = {
            "viewport": {"width": 1280, "height": 900},
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
            "locale": "zh-CN",
        }
        if STORAGE_STATE_FILE.exists():
            with open(STORAGE_STATE_FILE) as f:
                ctx_opts["storage_state"] = json.load(f)
            logger.info("Loaded login state")

        context = self.browser.new_context(**ctx_opts)
        context.add_init_script(ANTI_DETECTION_SCRIPT)
        self.page = context.new_page()

        if need_login:
            self._ensure_login()

    def _ensure_login(self, timeout=180):
        """确保已登录"""
        self.page.goto(
            "https://www.xiaohongshu.com/explore", timeout=30000, wait_until="domcontentloaded"
        )
        time.sleep(2)
        search = self.page.query_selector(
            'input[placeholder*="search"], input[placeholder*="Search"], #search-input'
        )
        if search:
            logger.info("Already logged in")
            self._save_state()
            return
        print("\n" + "=" * 50)
        print("Please scan QR code to login")
        print(f"   Timeout: {timeout}s")
        print("=" * 50)
        start = time.time()
        while time.time() - start < timeout:
            time.sleep(2)
            search = self.page.query_selector(
                'input[placeholder*="search"], input[placeholder*="Search"], #search-input, .avatar'
            )
            if search:
                logger.info(f"Login OK ({time.time() - start:.0f}s)")
                self._save_state()
                return
        logger.warning("Login timeout")

    def _save_state(self):
        try:
            state = self.page.context.storage_state()
            STORAGE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(STORAGE_STATE_FILE, "w") as f:
                json.dump(state, f)
        except Exception:
            pass

    # ============= Layer 1: Discovery =============

    def discover(self, keyword: str, max_scroll=3) -> list[dict]:
        """搜索并收集note_id列表"""
        logger.info(f"Discover: '{keyword}'")
        self.page.goto(
            f"https://www.xiaohongshu.com/search_result?keyword={keyword}&type=51&sort=time",
            timeout=15000,
            wait_until="domcontentloaded",
        )
        time.sleep(2)

        for _ in range(max_scroll):
            self.page.evaluate(f"window.scrollBy(0, {random.randint(400, 800)})")
            time.sleep(random.uniform(1, 2))

        # 从DOM提取note_id — 这是最可靠的方式
        note_ids = self.page.evaluate("""
            () => {
                const ids = [];
                const links = document.querySelectorAll('a[href*="/explore/"], a[href*="/discovery/item/"]');
                links.forEach(a => {
                    const href = a.getAttribute('href');
                    const match = href && href.match(/([a-f0-9]{24})/);
                    if (match) ids.push(match[1]);
                });
                return [...new Set(ids)];  // dedup
            }
        """)

        logger.info(f"  Found {len(note_ids)} unique note_ids for '{keyword}'")
        return [{"note_id": nid, "keyword": keyword} for nid in note_ids[:30]]

    # ============= Layer 2: API Deep Extract =============

    def deep_extract_one(self, note_id: str) -> dict | None:
        """
        在浏览器内用 fetch() 直接调用小红书API。
        浏览器自动处理X-s签名和Cookie，无需逆向。
        """
        js_code = f"""
            async () => {{
                try {{
                    // API 1: note detail
                    const url = '/api/sns/web/v1/feed?source_note_id={note_id}&image_formats=jpg,webp,avif&extra=need_webp_image';
                    const resp = await fetch(url, {{ credentials: 'include' }});
                    if (!resp.ok) return {{ error: 'HTTP ' + resp.status }};
                    const data = await resp.json();
                    return {{ success: true, data: data }};
                }} catch(e) {{
                    return {{ error: e.message }};
                }}
            }}
        """

        result = self.page.evaluate(js_code)
        return result

    def deep_extract_batch(self, notes: list[dict], max_depth=10) -> list[RichNote]:
        """批量深挖"""
        results = []
        ids_to_fetch = notes[:max_depth]

        logger.info(f"Deep extracting {len(ids_to_fetch)} notes via in-browser API...")
        print(f"\nDeep extracting {len(ids_to_fetch)} notes...")

        for i, meta in enumerate(ids_to_fetch):
            nid = meta["note_id"]
            time.sleep(random.uniform(1.5, 3))

            try:
                # 先导航到任意搜索页 — 确保浏览器context正确
                if i == 0:
                    # 第一个需要在一个搜索页面上
                    self.page.goto(
                        "https://www.xiaohongshu.com/search_result?keyword=futures&type=51",
                        timeout=10000,
                        wait_until="domcontentloaded",
                    )
                    time.sleep(1)

                raw = self.deep_extract_one(nid)

                if raw and raw.get("success"):
                    rich = self._parse_note_api(raw["data"], nid, meta.get("keyword", ""))
                    results.append(rich)
                    print(
                        f"  [{i + 1}/{len(ids_to_fetch)}] {nid[:8]}... OK "
                        f"| {rich.title[:50] if rich.title else '(no title)'} "
                        f"| L{rich.like_count} C{rich.comment_count}"
                    )
                else:
                    err = raw.get("error", "no_data") if raw else "null_response"
                    print(f"  [{i + 1}/{len(ids_to_fetch)}] {nid[:8]}... FAIL ({err})")

            except Exception as e:
                print(f"  [{i + 1}/{len(ids_to_fetch)}] {nid[:8]}... ERROR: {str(e)[:60]}")

        logger.info(f"Deep extraction: {len(results)}/{len(ids_to_fetch)} success")
        return results

    def _parse_note_api(self, data: dict, note_id: str, keyword: str) -> RichNote:
        """解析API响应"""
        items = data.get("data", {}).get("items", [])
        if not items:
            return RichNote(
                note_id=note_id,
                title="",
                desc="",
                author_name="",
                author_id="",
                author_fans=0,
                like_count=0,
                comment_count=0,
                collect_count=0,
                share_count=0,
                tags=[],
                topics=[],
                images=[],
                note_type="",
                publish_time="",
                ip_location="",
                keyword=keyword,
                url=f"https://www.xiaohongshu.com/explore/{note_id}",
            )

        nc = items[0].get("note_card", {})
        user = nc.get("user", {})
        interact = nc.get("interact_info", {})

        tags = [t.get("name", "") for t in (nc.get("tag_list", []) or [])]
        topics = [
            t.get("name", "") or t.get("topic_name", "")
            for t in (nc.get("topic_list", []) or nc.get("topics", []) or [])
        ]

        images = []
        for img in nc.get("image_list", []) or []:
            for key in ["url_default", "url", "original"]:
                info = img.get(key, img if isinstance(img, str) else {})
                url = (
                    info.get("url", "")
                    if isinstance(info, dict)
                    else (info if isinstance(info, str) and info.startswith("http") else "")
                )
                if url:
                    images.append(url)
                    break

        return RichNote(
            note_id=note_id,
            title=nc.get("title", "") or nc.get("display_title", ""),
            desc=nc.get("desc", ""),
            author_name=user.get("nickname", user.get("nick_name", "")),
            author_id=user.get("user_id", user.get("id", "")),
            author_fans=int(user.get("follower_count", 0) or 0),
            like_count=int(interact.get("liked_count", 0) or 0),
            comment_count=int(interact.get("comment_count", 0) or 0),
            collect_count=int(interact.get("collected_count", 0) or 0),
            share_count=int(interact.get("share_count", 0) or 0),
            tags=tags,
            topics=topics,
            images=images,
            note_type=nc.get("type", "normal"),
            publish_time=decode_objectid_timestamp(note_id) or "",
            ip_location=nc.get("ip_location", ""),
            keyword=keyword,
            url=f"https://www.xiaohongshu.com/explore/{note_id}",
        )

    # ============= Main =============

    def run(self, keywords, max_depth=10, filter_futures=True, need_login=True):
        self.start(need_login=need_login)

        all_ids = []
        print("\n" + "=" * 60)
        print("PHASE 1: Discovery")
        print("=" * 60)
        for kw in keywords:
            notes = self.discover(kw)
            all_ids.extend(notes)
            print(f"  '{kw}': {len(notes)} note_ids")

        # 去重
        seen = set()
        unique = []
        for n in all_ids:
            if n["note_id"] not in seen:
                seen.add(n["note_id"])
                unique.append(n)
        all_ids = unique
        print(f"\nTotal unique note_ids: {len(all_ids)}")

        print("\n" + "=" * 60)
        print("PHASE 2: Deep Extract (in-browser API fetch)")
        print("=" * 60)
        deep_notes = self.deep_extract_batch(all_ids, max_depth=max_depth)

        if filter_futures and deep_notes:
            filtered = []
            for n in deep_notes:
                is_rel, conf = is_futures_related_xhs(n.title, n.desc, n.tags + n.topics)
                if is_rel:
                    filtered.append(n)
            print(f"\nFutures filter: {len(deep_notes)} -> {len(filtered)} notes")
            deep_notes = filtered

        return {"discovered_count": len(all_ids), "deep_notes": deep_notes}

    def stop(self):
        try:
            if self.browser:
                self.browser.close()
            if self.pw:
                self.pw.stop()
        except Exception:
            pass

    def save(self, results, filename=None):
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        if not filename:
            filename = f"final_hybrid_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        output = {
            "metadata": {
                "timestamp": datetime.now().isoformat(),
                "method": "hybrid_in_browser_api",
            },
            "discovered_count": results["discovered_count"],
            "deep_notes": [
                {
                    "note_id": n.note_id,
                    "title": n.title,
                    "desc": n.desc[:1500] if n.desc else "",
                    "author_name": n.author_name,
                    "author_id": n.author_id,
                    "author_fans": n.author_fans,
                    "like_count": n.like_count,
                    "comment_count": n.comment_count,
                    "collect_count": n.collect_count,
                    "share_count": n.share_count,
                    "tags": n.tags,
                    "topics": n.topics,
                    "images": n.images[:5],
                    "note_type": n.note_type,
                    "publish_time": n.publish_time,
                    "ip_location": n.ip_location,
                    "keyword": n.keyword,
                    "url": n.url,
                }
                for n in results["deep_notes"]
            ],
        }
        path = OUTPUT_DIR / filename
        with open(path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        return path


def main():
    parser = argparse.ArgumentParser(description="Final Hybrid Pipeline")
    parser.add_argument("--keywords", nargs="+", default=FUTURES_KEYWORDS[:4])
    parser.add_argument("--no-login", action="store_true")
    parser.add_argument("--max-depth", type=int, default=10)
    parser.add_argument("--no-filter", action="store_true")
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    pipeline = FinalHybrid()
    try:
        results = pipeline.run(
            keywords=args.keywords,
            max_depth=args.max_depth,
            filter_futures=not args.no_filter,
            need_login=not args.no_login,
        )

        deep = results["deep_notes"]
        print(f"\n{'=' * 60}")
        print(f"RESULTS: {len(deep)} deep notes")
        print(f"{'=' * 60}")
        for i, n in enumerate(deep[:5], 1):
            title = (n.title or n.desc or "")[:80].replace("\n", " ")
            print(f"  {i}. [{n.note_type[0].upper()}] @{n.author_name} | {title}")
            print(
                f"     L{n.like_count} C{n.comment_count} S{n.share_count} | {len(n.desc)} chars | {len(n.tags)} tags | fans={n.author_fans}"
            )

        path = pipeline.save(results, args.output)
        print(f"\nSaved: {path}")
    finally:
        pipeline.stop()


if __name__ == "__main__":
    sys.exit(main())
