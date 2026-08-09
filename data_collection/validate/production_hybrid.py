"""
生产级混合采集管道 - 最终版本
================================
两条路互补:

Layer 1 (Playwright Discovery):
  - 搜索关键词, 提取note_id列表
  - 数据: title, author, like_count, publish_time(note_id解码), url
  - 成功率: ~100%

Layer 2 (API Deep Extract - Spider_XHS路径):
  - 对note_id调用小红书API获取完整正文、评论等
  - 需要X-s签名 → 使用Spider_XHS项目
  - 如果Spider_XHS不可用, 则使用浏览器内API签名方案

数据流:
  搜索 → note_ids → 去重 → futures过滤 → API深挖(note_ids) → 完整数据

使用:
  python production_hybrid.py --no-login                          # Phase 1 only
  python production_hybrid.py --no-login --deep spider_xhs        # Phase 1 + Spider_XHS
  python production_hybrid.py --no-login --deep browser_api       # Phase 1 + browser API
"""

import argparse
import json
import logging
import random
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PwTimeout, sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
import contextlib

from xhs_scraper import (
    ANTI_DETECTION_SCRIPT,
    OUTPUT_DIR,
    STORAGE_STATE_FILE,
    decode_objectid_timestamp,
    is_futures_related_xhs,
    parse_like_count,
)

logger = logging.getLogger("prod.hybrid")

FUTURES_KEYWORDS = [
    "螺纹钢期货",
    "铁矿石期货",
    "原油期货分析",
    "黄金期货走势",
    "PTA期货",
    "豆粕期货",
    "股指期货策略",
    "期货日内交易",
    "焦炭期货",
    "棕榈油期货",
]


@dataclass
class DiscoveredNote:
    """发现层数据（搜索页可获取）"""

    note_id: str
    title: str
    author_name: str
    like_count: int
    note_type: str  # normal / video
    publish_time: str  # from note_id decoding
    url: str
    keyword: str
    cover_url: str = ""

    # Scores
    futures_confidence: float = 0.0
    quality_score: float = 0.0  # 综合质量分


@dataclass
class DeepNote:
    """深挖层数据（API获取）"""

    note_id: str
    title: str = ""
    desc: str = ""  # 正文全文
    author_name: str = ""
    author_id: str = ""
    author_fans: int = 0
    like_count: int = 0
    comment_count: int = 0
    collect_count: int = 0
    share_count: int = 0
    tags: list = field(default_factory=list)
    topics: list = field(default_factory=list)
    images: list = field(
        default_factory=list
    )  # [{"url":..., "scene":..., "width":..., "height":...}]
    video_urls: list = field(default_factory=list)  # [{"codec":..., "resolution":..., "url":...}]
    video_duration: int = 0  # milliseconds
    note_type: str = ""
    publish_time: str = ""  # from note_id decoding
    publish_time_api: str = ""  # from API timestamp field
    ip_location: str = ""
    keyword: str = ""
    url: str = ""
    comments: list = field(default_factory=list)


class ProductionHybrid:
    """生产级混合管道"""

    def __init__(self, headless=False):
        self.headless = headless
        self.pw = None
        self.browser = None
        self.page = None
        self.context = None

    # ============================================
    # 初始化 & 登录
    # ============================================

    def start(self, need_login=True):
        logger.info("Starting browser...")
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ]
            + (["--window-size=1280,900"] if not self.headless else []),
            slow_mo=30,
        )
        ctx_opts = {
            "viewport": {"width": 1280, "height": 900},
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "locale": "zh-CN",
        }
        if STORAGE_STATE_FILE.exists():
            with open(STORAGE_STATE_FILE) as f:
                ctx_opts["storage_state"] = json.load(f)
            logger.info("Loaded saved login state")

        self.context = self.browser.new_context(**ctx_opts)
        self.context.add_init_script(ANTI_DETECTION_SCRIPT)
        self.page = self.context.new_page()

        if need_login:
            self._ensure_login()

    def _ensure_login(self, timeout=180):
        self.page.goto(
            "https://www.xiaohongshu.com/explore", timeout=30000, wait_until="domcontentloaded"
        )
        time.sleep(2)
        has_login = self.page.query_selector(
            'input[placeholder*="search"], input[placeholder*="Search"], #search-input, .avatar'
        )
        if has_login:
            self._save_state()
            return
        print("\n" + "=" * 50)
        print("Please scan QR code in browser to login")
        print(f"   Timeout: {timeout}s")
        print("=" * 50 + "\n")
        start = time.time()
        while time.time() - start < timeout:
            time.sleep(2)
            if self.page.query_selector(
                'input[placeholder*="search"], input[placeholder*="Search"], #search-input, .avatar'
            ):
                logger.info(f"Login OK ({time.time() - start:.0f}s)")
                self._save_state()
                return
        logger.warning("Login timeout")

    def _save_state(self):
        try:
            state = self.context.storage_state()
            STORAGE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(STORAGE_STATE_FILE, "w") as f:
                json.dump(state, f)
        except Exception:
            pass

    def stop(self):
        try:
            if self.browser:
                self.browser.close()
            if self.pw:
                self.pw.stop()
        except Exception:
            pass

    # ============================================
    # Phase 1: Discovery (Playwright Search)
    # ============================================

    def discover(self, keyword: str, max_scroll: int = 4) -> list[DiscoveredNote]:
        """搜索并发现笔记"""
        logger.info(f"Discover: '{keyword}'")
        search_url = (
            f"https://www.xiaohongshu.com/search_result?keyword={keyword}&type=51&sort=time"
        )
        try:
            self.page.goto(search_url, timeout=15000, wait_until="domcontentloaded")
        except PwTimeout:
            logger.warning(f"Search timeout for '{keyword}'")
            return []
        time.sleep(2)

        for _ in range(max_scroll):
            self.page.evaluate(f"window.scrollBy(0, {random.randint(400, 800)})")
            time.sleep(random.uniform(1, 2))

        # 提取数据
        cards = self._extract_search_cards(keyword)
        logger.info(f"  Discovered {len(cards)} notes for '{keyword}'")
        return cards

    def _extract_search_cards(self, keyword: str) -> list[DiscoveredNote]:
        """从搜索页提取笔记数据"""
        notes = []

        # 选择 section.note-item 容器（不是内部链接）
        card_elements = self.page.query_selector_all(
            'section.note-item, [class*="noteItem"], div[class*="note"]'
        )

        # fallback: 如果上面的选择器没命中, 用 JS 提取
        if not card_elements:
            js_data = self.page.evaluate("""
                () => {
                    const sections = document.querySelectorAll('section.note-item, [class*="noteItem"]');
                    if (sections.length === 0) {
                        // try finding note cards via their links
                        const links = document.querySelectorAll('a[href*="/explore/"]');
                        const containers = new Set();
                        links.forEach(a => {
                            let parent = a.closest('section, div[class*="note"], div[class*="card"]');
                            if (parent) containers.add(parent);
                        });
                        return Array.from(containers).slice(0, 30).map(el => ({
                            href: el.querySelector('a[href*="/explore/"]')?.getAttribute('href') || '',
                            title: el.querySelector('.title, [class*="title"], span[class*="title"], h3')?.textContent?.trim() || '',
                            author: el.querySelector('.name, [class*="name"], .author [class*="name"]')?.textContent?.trim() || '',
                            likes: el.querySelector('.count, [class*="count"]')?.textContent?.trim() || '',
                        }));
                    }
                    return Array.from(sections).slice(0, 30).map(el => ({
                        href: el.querySelector('a[href*="/explore/"]')?.getAttribute('href') || '',
                        title: el.querySelector('.title, [class*="title"], span[class*="title"], h3')?.textContent?.trim() || el.textContent?.trim()?.split('\\\\n')[0]?.substring(0, 80) || '',
                        author: el.querySelector('.name, [class*="name"], .author [class*="name"]')?.textContent?.trim() || '',
                        likes: el.querySelector('.count, [class*="count"]')?.textContent?.trim() || '',
                    }));
                }
            """)
            # Parse JS results
            seen_ids = set()
            for item in js_data:
                href = item.get("href", "")
                nid_match = re.search(r"([a-f0-9]{24})", href)
                if not nid_match:
                    continue
                nid = nid_match.group(1)
                if nid in seen_ids:
                    continue
                seen_ids.add(nid)

                note = DiscoveredNote(
                    note_id=nid,
                    title=item.get("title", ""),
                    author_name=item.get("author", ""),
                    like_count=parse_like_count(item.get("likes", "")),
                    note_type="normal",
                    publish_time=decode_objectid_timestamp(nid) or "",
                    url=f"https://www.xiaohongshu.com/explore/{nid}",
                    keyword=keyword,
                )
                notes.append(note)
            return notes

        seen_ids = set()
        for elem in card_elements[:50]:
            try:
                # Try to find link inside section
                link = elem.query_selector('a[href*="/explore/"]')
                href = link.get_attribute("href") if link else elem.get_attribute("href") or ""
                nid_match = re.search(r"([a-f0-9]{24})", href)
                if not nid_match:
                    data_id = elem.get_attribute("data-id") or ""
                    if re.match(r"^[a-f0-9]{24}$", data_id):
                        nid = data_id
                    else:
                        continue
                else:
                    nid = nid_match.group(1)

                if nid in seen_ids:
                    continue
                seen_ids.add(nid)

                # Title: search within the section container
                title = ""
                for s in [
                    ".title",
                    'span[class*="title"]',
                    "h3",
                    '[class*="title"]',
                    'a[class*="title"]',
                    ".note-title",
                ]:
                    el = elem.query_selector(s)
                    if el:
                        title = (el.inner_text() or "").strip()
                        if len(title) > 3:
                            break
                if not title:
                    text = (elem.inner_text() or "").strip()
                    lines = [line for line in text.split("\n") if len(line.strip()) > 5]
                    title = lines[0][:100] if lines else ""

                # Author
                author = ""
                for s in [
                    ".author .name",
                    ".name",
                    '[class*="name"]',
                    ".nickname",
                    ".author span",
                    '[class*="author"] span',
                ]:
                    el = elem.query_selector(s)
                    if el:
                        author = (el.inner_text() or "").strip()
                        if author and len(author) < 50 and not author.startswith(("202", "20")):
                            break

                # Like count
                likes = 0
                for s in [
                    ".count",
                    '[class*="count"]',
                    'span[class*="stat"]',
                    '[class*="like-wrapper"] span',
                ]:
                    el = elem.query_selector(s)
                    if el:
                        likes = parse_like_count(el.inner_text() or "")
                        if likes > 0:
                            break

                # Note type
                note_type = (
                    "video"
                    if elem.query_selector('[class*="video"], [class*="play"], .duration')
                    else "normal"
                )

                # Cover
                cover = ""
                img = elem.query_selector("img")
                if img:
                    cover = img.get_attribute("src") or ""

                pub_time = decode_objectid_timestamp(nid) or ""

                note = DiscoveredNote(
                    note_id=nid,
                    title=title,
                    author_name=author,
                    like_count=likes,
                    note_type=note_type,
                    publish_time=pub_time,
                    url=f"https://www.xiaohongshu.com/explore/{nid}",
                    keyword=keyword,
                    cover_url=cover,
                )
                notes.append(note)

            except Exception:
                continue

        return notes

    # ============================================
    # Phase 2a: Spider_XHS API pipeline (search + detail)
    # ============================================

    def spider_xhs_search(
        self, keyword: str, count: int = 20, spider_xhs_path: str = None
    ) -> list[dict]:
        """
        Use Spider_XHS's own search API. Returns items with:
          id (note_id), xsec_token, note_card{display_title, user, interact_info}
        """
        if spider_xhs_path is None:
            spider_xhs_path = str(Path(__file__).parent.parent / "Spider_XHS")

        # Spider_XHS JS files use relative paths (./static/...).
        # Must run from Spider_XHS directory.
        import os as _os

        _prev_cwd = _os.getcwd()
        _os.chdir(spider_xhs_path)

        try:
            sys.path.insert(0, spider_xhs_path)

            # Lazy init Spider_XHS
            if not hasattr(self, "_xhs_api"):
                from apis.xhs_pc_apis import XHS_Apis
                from xhs_utils.common_util import init

                cookies_str, _ = init()
                if not cookies_str:
                    logger.error("Spider_XHS .env has no COOKIES")
                    return []
                self._xhs_api = XHS_Apis()
                self._xhs_cookies = cookies_str

            success, msg, items = self._xhs_api.search_some_note(keyword, count, self._xhs_cookies)
            if not success or not isinstance(items, list):
                logger.warning(f"Spider_XHS search failed: {msg}")
                return []
            return items
        finally:
            _os.chdir(_prev_cwd)

    def spider_xhs_get_detail(
        self, note_id: str, xsec_token: str, spider_xhs_path: str = None
    ) -> dict | None:
        """
        Get full note detail + comments via Spider_XHS API.
        Returns dict with keys: note_card, comments
        """
        if spider_xhs_path is None:
            spider_xhs_path = str(Path(__file__).parent.parent / "Spider_XHS")

        import os as _os

        _prev_cwd = _os.getcwd()
        _os.chdir(spider_xhs_path)

        try:
            url = f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token={xsec_token}"
            success, msg, detail = self._xhs_api.get_note_info(url, self._xhs_cookies)

            if not success or not isinstance(detail, dict):
                return None

            code = detail.get("code", -1)
            if code != 0:
                return None

            data = detail.get("data", {})
            items = data.get("items", [])
            if not items:
                return None

            note_card = items[0].get("note_card", {})

            # Also fetch comments
            comments = []
            try:
                c_success, c_msg, c_list = self._xhs_api.get_note_all_out_comment(
                    note_id, xsec_token, self._xhs_cookies
                )
                if c_success and c_list:
                    for c in c_list:
                        comment = {
                            "id": c.get("id", ""),
                            "content": c.get("content", ""),
                            "user_name": (c.get("user_info", {}) or {}).get("nickname", ""),
                            "user_id": (c.get("user_info", {}) or {}).get("user_id", ""),
                            "like_count": c.get("like_count", 0),
                            "create_time": c.get("create_time", 0),
                            "sub_comments": [],
                        }
                        # Get sub-comments for each top comment
                        sc_list = c.get("sub_comments", []) or []
                        for sc in sc_list:
                            comment["sub_comments"].append(
                                {
                                    "id": sc.get("id", ""),
                                    "content": sc.get("content", ""),
                                    "user_name": (sc.get("user_info", {}) or {}).get(
                                        "nickname", ""
                                    ),
                                    "like_count": sc.get("like_count", 0),
                                    "target_user": (sc.get("target_comment", {}) or {})
                                    .get("user_info", {})
                                    .get("nickname", ""),
                                }
                            )
                        comments.append(comment)
            except Exception as e:
                logger.debug(f"Comment fetch failed for {note_id[:8]}...: {e}")

            return {"note_card": note_card, "comments": comments}
        finally:
            _os.chdir(_prev_cwd)

    def _parse_spider_xhs_note_card(
        self, nc: dict, note_id: str, keyword: str, comments: list = None
    ) -> DeepNote:
        """Parse note_card from Spider_XHS API into DeepNote with full dimensions"""
        user = nc.get("user", {}) or {}
        interact = nc.get("interact_info", {}) or {}

        # --- Tags ---
        tags = [t.get("name", "") for t in (nc.get("tag_list", []) or []) if t.get("name")]

        # --- Topics ---
        topics = []
        for t in nc.get("topic_list", []) or []:
            name = t.get("name", "") or t.get("topic_name", "")
            if name:
                topics.append(name)

        # --- Images (ALL formats: original, thumbnail, webp) ---
        images = []
        for img in nc.get("image_list", []) or []:
            if not isinstance(img, dict):
                continue
            # info_list contains multiple resolutions
            info_list = img.get("info_list", [])
            if info_list:
                for entry in info_list:
                    url = entry.get("url", "") if isinstance(entry, dict) else ""
                    if url and url.startswith("http"):
                        scene = entry.get("image_scene", "")
                        images.append(
                            {
                                "url": url,
                                "scene": scene,
                                "width": img.get("width", 0),
                                "height": img.get("height", 0),
                            }
                        )
            # Also check direct URL fields
            for key in ["url_default", "url", "original"]:
                val = img.get(key, {})
                if isinstance(val, dict):
                    url = val.get("url", "")
                    if url and url.startswith("http"):
                        images.append(
                            {
                                "url": url,
                                "scene": key,
                                "width": img.get("width", 0),
                                "height": img.get("height", 0),
                            }
                        )

        # --- Video URL ---
        video_urls = []
        video = nc.get("video", {})
        if video:
            media = video.get("media", {})
            stream = media.get("stream", {})
            for codec in ["h264", "h265"]:
                codec_stream = stream.get(codec, {})
                for res in ["1080p", "720p", "480p", "360p"]:
                    res_stream = codec_stream.get(res, {})
                    master_url = res_stream.get("master_url", "")
                    if master_url:
                        video_urls.append(
                            {
                                "codec": codec,
                                "resolution": res,
                                "url": master_url,
                            }
                        )
            # Also check video duration
            video_duration = video.get("duration", 0)  # in milliseconds
        else:
            video_duration = 0

        # --- Tags within desc (topic tags like #铁矿石[话题]) ---
        # These are embedded in the desc text, preserved as-is in desc field

        # API timestamp (may differ from note_id decoding)
        api_time = nc.get("time", 0) or nc.get("create_time", 0)
        api_time_str = ""
        if api_time and api_time > 0:
            with contextlib.suppress(Exception):
                api_time_str = datetime.fromtimestamp(api_time / 1000).strftime("%Y-%m-%d %H:%M:%S")

        return DeepNote(
            note_id=note_id,
            title=nc.get("title", "") or nc.get("display_title", ""),
            desc=nc.get("desc", ""),  # preserves all emoji as UTF-8
            author_name=user.get("nickname", user.get("nick_name", "")),
            author_id=str(user.get("user_id", user.get("id", ""))),
            author_fans=int(user.get("follower_count", 0) or 0),
            like_count=int(interact.get("liked_count", 0) or 0),
            comment_count=int(interact.get("comment_count", 0) or 0),
            collect_count=int(interact.get("collected_count", 0) or 0),
            share_count=int(interact.get("share_count", 0) or 0),
            tags=tags,
            topics=topics,
            images=images,
            video_urls=video_urls,
            video_duration=video_duration,
            note_type=nc.get("type", "normal"),
            publish_time=decode_objectid_timestamp(note_id) or "",
            publish_time_api=api_time_str,
            ip_location=nc.get("ip_location", ""),
            keyword=keyword,
            url=f"https://www.xiaohongshu.com/explore/{note_id}",
            comments=comments or [],
        )

    def deep_extract_spider_xhs(
        self, keywords: list[str], max_depth: int = 20, spider_xhs_path: str = None
    ) -> list[DeepNote]:
        """
        Full Spider_XHS pipeline: search + detail extraction.
        Each keyword: search → get item.xsec_token → get_note_info → parse.
        """
        if spider_xhs_path is None:
            spider_xhs_path = str(Path(__file__).parent.parent / "Spider_XHS")

        results = []
        seen_ids = set()
        per_kw = max(1, max_depth // len(keywords)) if keywords else max_depth

        logger.info(f"Spider_XHS pipeline: {len(keywords)} keywords, ~{per_kw} each")
        print(f"\nSpider_XHS deep pipeline: {len(keywords)} keywords")

        for kw in keywords:
            print(f"\n  Keyword: '{kw}'")
            try:
                items = self.spider_xhs_search(kw, count=per_kw + 5)
            except Exception as e:
                logger.error(f"Search failed for '{kw}': {e}")
                continue

            fetched = 0
            for item in items:
                if fetched >= per_kw:
                    break

                nid = item.get("id", "")
                xsec = item.get("xsec_token", "")  # NOTE-LEVEL token, not user.xsec_token!
                nc = item.get("note_card", {})

                if not nid or nid in seen_ids:
                    continue
                seen_ids.add(nid)

                # First: basic data from search
                basic_title = nc.get("display_title", "")
                basic_author = (nc.get("user", {}) or {}).get("nickname", "")

                # Try get full detail + comments
                full_nc = None
                full_comments = []
                if xsec:
                    try:
                        full_result = self.spider_xhs_get_detail(nid, xsec)
                        if full_result:
                            full_nc = full_result.get("note_card")
                            full_comments = full_result.get("comments", [])
                    except Exception as e:
                        logger.debug(f"Detail failed for {nid[:8]}...: {e}")
                    time.sleep(random.uniform(0.3, 0.8))

                # Use full detail if available, otherwise search basic
                source_nc = full_nc if full_nc else nc
                deep = self._parse_spider_xhs_note_card(source_nc, nid, kw, comments=full_comments)

                if not deep.title and basic_title:
                    deep.title = basic_title
                if not deep.author_name and basic_author:
                    deep.author_name = basic_author

                results.append(deep)
                fetched += 1

                has_full = "FULL" if full_nc else "basic"
                desc_len = len(deep.desc) if deep.desc else 0
                print(
                    f"    [{fetched}] {nid[:8]}... {has_full} | "
                    f"L{deep.like_count} C{deep.comment_count} | "
                    f"desc={desc_len}c tags={len(deep.tags)} | "
                    f"{deep.title[:50]}"
                )

            print(f"    -> {fetched} notes extracted")

            # Avoid rate limiting
            if keywords.index(kw) < len(keywords) - 1:
                time.sleep(random.uniform(1, 3))

        logger.info(f"Spider_XHS pipeline done: {len(results)} notes")
        return results

    # ============================================
    # Phase 2b: Browser API Deep Extract (fallback)
    # ============================================

    def deep_extract_browser_api(self, note_ids: list[str], max_depth: int = 10) -> list[DeepNote]:
        """
        浏览器内API方案: 使用XHS页面内已加载的签名机制。
        通过XHS自己的XHR拦截器自动添加X-s签名。
        """
        # This requires Spider_XHS signature knowledge.
        # For now, return empty - will be populated when Spider_XHS is available.
        logger.warning("Browser API extract requires Spider_XHS signature engine")
        return []

    # ============================================
    # Full Pipeline
    # ============================================

    def run_discovery_only(
        self, keywords: list[str], filter_futures: bool = True
    ) -> list[DiscoveredNote]:
        """只运行发现层"""
        self.start(need_login=not STORAGE_STATE_FILE.exists())

        all_notes = []
        for kw in keywords:
            notes = self.discover(kw)
            all_notes.extend(notes)
            print(f"  '{kw}': {len(notes)} notes")

        # 去重
        seen = set()
        unique = []
        for n in all_notes:
            if n.note_id not in seen:
                seen.add(n.note_id)
                unique.append(n)
        all_notes = unique

        # 期货过滤
        if filter_futures:
            before = len(all_notes)
            filtered = []
            for n in all_notes:
                is_rel, conf = is_futures_related_xhs(n.title, "", [])
                if is_rel:
                    filtered.append(n)
            print(f"Futures filter: {before} -> {len(filtered)} notes")
            # Debug: print first few titles that passed
            if not filtered and all_notes:
                print("  DEBUG: First 5 titles before filter:")
                for n in all_notes[:5]:
                    print(f"    title='{n.title[:60]}'")
            all_notes = filtered

        # 计算质量分数
        for n in all_notes:
            is_rel, conf = is_futures_related_xhs(n.title, "", [])
            n.futures_confidence = conf
            n.quality_score = min(1.0, (n.like_count + 1) / 50 + conf * 0.5)

        return all_notes

    def run_full(self, keywords, filter_futures=True, deep_method="none", max_depth=10) -> dict:
        """运行完整管道"""
        # Phase 1
        print("\n" + "=" * 60)
        print("PHASE 1: Discovery")
        print("=" * 60)

        discovered = self.run_discovery_only(keywords, filter_futures=filter_futures)
        print(f"\nDiscovered: {len(discovered)} notes")

        # Phase 2 (optional)
        deep_notes = []
        if deep_method != "none" and discovered:
            print("\n" + "=" * 60)
            print(f"PHASE 2: Deep Extract ({deep_method})")
            print("=" * 60)

            note_ids = [n.note_id for n in discovered[:max_depth]]

            if deep_method == "spider_xhs":
                deep_notes = self.deep_extract_spider_xhs(keywords, max_depth=max_depth)
            elif deep_method == "browser_api":
                deep_notes = self.deep_extract_browser_api(note_ids, max_depth)

            print(f"\nDeep extracted: {len(deep_notes)} notes")

        self.stop()
        return {"discovered": discovered, "deep_notes": deep_notes}

    def save_results(self, results: dict, filename: str = None) -> Path:
        """保存结果"""
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        if filename is None:
            filename = f"prod_hybrid_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        discovered = results.get("discovered", [])
        deep = results.get("deep_notes", [])

        output = {
            "metadata": {
                "timestamp": datetime.now().isoformat(),
                "method": "production_hybrid",
                "discovered_count": len(discovered),
                "deep_count": len(deep),
            },
            "discovered": [
                {
                    "note_id": n.note_id,
                    "title": n.title,
                    "author_name": n.author_name,
                    "like_count": n.like_count,
                    "note_type": n.note_type,
                    "publish_time": n.publish_time,
                    "url": n.url,
                    "keyword": n.keyword,
                    "futures_confidence": round(n.futures_confidence, 3),
                    "quality_score": round(n.quality_score, 3),
                    "cover_url": n.cover_url,
                }
                for n in discovered
            ],
            "deep_notes": [
                {
                    "note_id": n.note_id,
                    "title": n.title,
                    "desc": (n.desc or "")[:3000],  # full body text with emoji
                    "author_name": n.author_name,
                    "author_id": n.author_id,
                    "author_fans": n.author_fans,
                    "like_count": n.like_count,
                    "comment_count": n.comment_count,
                    "collect_count": n.collect_count,
                    "share_count": n.share_count,
                    "tags": n.tags,
                    "topics": n.topics,
                    "images": n.images,  # ALL image URLs with metadata
                    "video_urls": n.video_urls,  # video stream URLs
                    "video_duration_ms": n.video_duration,
                    "note_type": n.note_type,
                    "publish_time": n.publish_time,
                    "publish_time_api": n.publish_time_api,
                    "ip_location": n.ip_location,
                    "keyword": n.keyword,
                    "url": n.url,
                    "comments": n.comments[:50],  # top-level + sub_comments
                    "comments_count_actual": len(n.comments),
                }
                for n in deep
            ],
        }

        filepath = OUTPUT_DIR / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        return filepath


def print_summary(results: dict):
    """打印汇总"""
    discovered = results.get("discovered", [])
    deep = results.get("deep_notes", [])

    print("\n" + "=" * 60)
    print("PIPELINE RESULTS")
    print("=" * 60)
    print(f"  Discovered: {len(discovered)} notes")
    print(f"  Deep extracted: {len(deep)} notes")

    if discovered:
        # Top notes
        sorted_notes = sorted(discovered, key=lambda n: n.quality_score, reverse=True)
        print("\n  Top discovered notes:")
        for i, n in enumerate(sorted_notes[:5], 1):
            title = (n.title or "")[:70].replace("\n", " ")
            print(f"  {i}. [{n.note_type[0].upper()}] @{n.author_name} | {title}")
            print(f"     L{n.like_count} | {n.publish_time} | score={n.quality_score:.2f}")

        # Keyword distribution
        from collections import Counter

        kw_dist = Counter(n.keyword for n in discovered)
        print("\n  Keyword distribution:")
        for kw, count in kw_dist.most_common():
            print(f"    {kw}: {count}")


def main():
    parser = argparse.ArgumentParser(description="Production Hybrid Pipeline")
    parser.add_argument("--keywords", nargs="+", default=FUTURES_KEYWORDS[:4])
    parser.add_argument("--no-login", action="store_true")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument(
        "--deep",
        choices=["none", "spider_xhs", "browser_api"],
        default="none",
        help="Deep extract method",
    )
    parser.add_argument("--max-depth", type=int, default=10)
    parser.add_argument("--no-filter", action="store_true")
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--discovery-only", action="store_true", help="Phase 1 only, skip deep extraction"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    pipeline = ProductionHybrid(headless=args.headless)

    try:
        deep_method = "none" if args.discovery_only else args.deep

        if deep_method == "none":
            # Phase 1 only
            discovered = pipeline.run_discovery_only(
                keywords=args.keywords,
                filter_futures=not args.no_filter,
            )
            results = {"discovered": discovered, "deep_notes": []}
        else:
            results = pipeline.run_full(
                keywords=args.keywords,
                filter_futures=not args.no_filter,
                deep_method=deep_method,
                max_depth=args.max_depth,
            )

        print_summary(results)
        path = pipeline.save_results(results, args.output)
        print(f"\nSaved to: {path}")

    except KeyboardInterrupt:
        print("\nInterrupted")
    except Exception as e:
        logger.error(f"Pipeline error: {e}", exc_info=args.verbose)
    finally:
        pipeline.stop()


if __name__ == "__main__":
    sys.exit(main())
