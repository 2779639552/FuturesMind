"""
期货社交媒体 — 混合采集管道 (Hybrid Pipeline)
================================================

两层架构:
  Layer 1 (Playwright发现层): 浏览器搜索 → 收集note_id + 基础元数据
  Layer 2 (API拦截深挖层): 拦截XHR响应 → 获取结构化JSON正文 + 评论

核心思路:
  打开小红书搜索结果 → 搜索关键词 → 获取笔记列表 (标题/作者/时间/点赞)
  → 点击笔记卡片打开浮层 → 拦截 /api/sns/web/v1/feed 的API响应
  → 直接从JSON中提取完整正文、标签、评论、互动数据

优势:
  - 不需要逆向X-s签名 (浏览器自动完成)
  - 数据结构完整 (API返回的JSON比DOM提取更可靠)
  - 同一个浏览器会话 (复用登录态)
  - 正文、评论、标签 一次性获取

使用方式:
    python hybrid_pipeline.py                          # 首次运行(需要扫码)
    python hybrid_pipeline.py --no-login               # 使用已保存登录态
    python hybrid_pipeline.py --keywords "螺纹钢" "铁矿"  # 自定义关键词
    python hybrid_pipeline.py --max-depth 10            # 每个关键词深挖10篇
    python hybrid_pipeline.py --filter-futures          # 期货相关性过滤
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

# --- 复用已有的工具函数 ---
sys.path.insert(0, str(Path(__file__).parent))
from xhs_scraper import (
    ANTI_DETECTION_SCRIPT,
    OUTPUT_DIR,
    STORAGE_STATE_FILE,
    XHSNote,
    decode_objectid_timestamp,
    filter_futures_notes,
    is_futures_related_xhs,
    parse_like_count,
)

logger = logging.getLogger("hybrid.pipeline")

# 小红书 API 端点 (用于拦截)
XHS_API_PATTERNS = {
    "note_detail": "**/api/sns/web/v1/feed**",  # 笔记详情
    "note_comments": "**/api/sns/web/v2/comment/**",  # 评论
    "search_notes": "**/api/sns/web/v1/search/notes",  # 搜索
    "sub_notes": "**/api/sns/web/v1/note/sub**",  # 子笔记
}


@dataclass
class DeepNote:
    """深度采集的笔记 (API级别数据)"""

    note_id: str = ""
    title: str = ""
    desc: str = ""  # 正文全文
    author_name: str = ""
    author_id: str = ""
    author_followers: int = 0  # 作者粉丝数
    like_count: int = 0
    comment_count: int = 0
    collect_count: int = 0
    share_count: int = 0
    tags: list = field(default_factory=list)
    topics: list = field(default_factory=list)  # 话题
    images: list = field(default_factory=list)  # 图片URL列表
    note_type: str = ""  # normal / video
    publish_time: str = ""
    ip_location: str = ""  # IP属地
    keyword: str = ""  # 搜索关键词
    comments: list = field(default_factory=list)  # 评论列表 (前20条)
    raw_api_response: dict = field(default_factory=dict)


class HybridPipeline:
    """
    混合采集管道
    - Phase 1: Playwright搜索 → 收集note_id列表
    - Phase 2: API拦截 → 对每个note_id获取完整数据
    """

    def __init__(self, headless: bool = False):
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.page = None
        self.context = None

        # API响应拦截缓存
        self.api_cache = {}
        self.intercept_enabled = False

    # ================================================================
    # Phase 0: 启动和登录
    # ================================================================

    def start(self, need_login: bool = True):
        """启动浏览器并登录"""
        logger.info("Starting browser...")
        self.playwright = sync_playwright().start()

        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ]
        if not self.headless:
            launch_args.append("--window-size=1280,900")

        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            args=launch_args,
            slow_mo=50,
        )

        context_options = {
            "viewport": {"width": 1280, "height": 900},
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
        }

        # 加载已保存的登录态
        if STORAGE_STATE_FILE.exists():
            try:
                with open(STORAGE_STATE_FILE) as f:
                    context_options["storage_state"] = json.load(f)
                logger.info("Loaded saved login state")
            except Exception:
                pass

        self.context = self.browser.new_context(**context_options)
        self.context.add_init_script(ANTI_DETECTION_SCRIPT)
        self.page = self.context.new_page()

        # 登录
        if need_login:
            self._login()

    def _login(self, timeout: int = 180):
        """打开首页并等待登录"""
        logger.info("Opening homepage...")
        self.page.goto(
            "https://www.xiaohongshu.com/explore", timeout=30000, wait_until="domcontentloaded"
        )
        time.sleep(2)

        # 检查是否已登录
        search_input = self.page.query_selector(
            'input[placeholder*="search"], input[placeholder*="Search"], '
            '#search-input, [class*="search-input"]'
        )
        if search_input:
            logger.info("Already logged in")
            self._save_state()
            return

        print("\n" + "=" * 50)
        print("Please scan QR code in browser to login")
        print(f"   Timeout: {timeout}s")
        print("=" * 50 + "\n")

        wait_start = time.time()
        while time.time() - wait_start < timeout:
            time.sleep(2)
            search_input = self.page.query_selector(
                'input[placeholder*="search"], input[placeholder*="Search"], '
                '#search-input, [class*="search-input"]'
            )
            # 也检查用户头像
            avatar = self.page.query_selector(
                '.avatar, [class*="avatar"], img[class*="avatar"], '
                '.side-bar-user img, [class*="user-avatar"]'
            )
            if search_input or avatar:
                elapsed = time.time() - wait_start
                logger.info(f"Login success! ({elapsed:.0f}s)")
                self._save_state()
                return

        logger.warning(f"Login timeout ({timeout}s)")

    def _save_state(self):
        """保存登录态"""
        try:
            state = self.context.storage_state()
            STORAGE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(STORAGE_STATE_FILE, "w") as f:
                json.dump(state, f)
            logger.info(f"State saved to {STORAGE_STATE_FILE}")
        except Exception as e:
            logger.warning(f"Failed to save state: {e}")

    # ================================================================
    # Phase 1: 搜索发现层 (Playwright)
    # ================================================================

    def discover(self, keyword: str, max_scroll: int = 5) -> list[XHSNote]:
        """
        搜索关键词并收集笔记基础数据。
        返回 note_id, title, author_name, like_count, publish_time, url 等。
        """
        notes = []
        logger.info(f"Discovering: '{keyword}'")

        search_url = (
            f"https://www.xiaohongshu.com/search_result?keyword={keyword}&type=51&sort=time"
        )
        self.page.goto(search_url, timeout=20000, wait_until="domcontentloaded")
        time.sleep(random.uniform(2, 4))

        # 滚动加载
        for _ in range(max_scroll):
            self._human_scroll()
            time.sleep(random.uniform(1, 2.5))

        # 提取笔记元素
        note_elements = self.page.query_selector_all(
            '.note-item, [class*="noteItem"], section.note-item, '
            'a[href*="/explore/"][class*="note"], '
            'div[class*="feeds-page"] section, '
            'div[class*="search"] section'
        )

        logger.debug(f"  Found {len(note_elements)} potential note elements")

        seen_ids = set()
        for elem in note_elements[:40]:
            try:
                note = self._parse_search_card(elem, keyword)
                if note and note.note_id and note.note_id not in seen_ids:
                    seen_ids.add(note.note_id)
                    notes.append(note)
            except Exception:
                pass

        logger.info(f"  Discovered {len(notes)} notes for '{keyword}'")
        return notes

    def _parse_search_card(self, elem, keyword: str) -> XHSNote | None:
        """从搜索卡片中提取基础数据"""
        note = XHSNote(keyword=keyword)

        # URL和note_id
        link = elem.query_selector('a[href*="/explore/"], a[href*="/discovery/"]')
        if not link:
            link = elem
        href = link.get_attribute("href") or ""
        note_id_match = re.search(r"/(?:explore|discovery/item)/([a-f0-9]{24})", href)
        if note_id_match:
            note.note_id = note_id_match.group(1)
            note.url = f"https://www.xiaohongshu.com/explore/{note.note_id}"
        else:
            # 尝试 data-id 属性
            data_id = elem.get_attribute("data-id") or ""
            if re.match(r"^[a-f0-9]{24}$", data_id):
                note.note_id = data_id
                note.url = f"https://www.xiaohongshu.com/explore/{note.note_id}"
            else:
                return None

        # 时间戳
        note.publish_time = decode_objectid_timestamp(note.note_id) or ""

        # 标题
        for sel in [".title", '[class*="title"]', 'span[class*="title"]', "h3"]:
            el = elem.query_selector(sel)
            if el:
                note.title = (el.inner_text() or "").strip()
                if len(note.title) > 3:
                    break

        # 作者
        for sel in [
            ".author .name",
            ".name",
            '[class*="author"] [class*="name"]',
            ".nickname",
            '[class*="nickname"]',
        ]:
            el = elem.query_selector(sel)
            if el:
                note.author_name = (el.inner_text() or "").strip()
                if note.author_name:
                    break

        # 互动数据
        count_els = elem.query_selector_all(
            '.count, [class*="count"], [class*="like"] [class*="count"], span[class*="stat"]'
        )
        if len(count_els) >= 1:
            note.like_count = (count_els[0].inner_text() or "").strip()
            note.like_count_int = parse_like_count(note.like_count)

        # 封面
        img = elem.query_selector("img")
        if img:
            note.cover_url = img.get_attribute("src") or ""

        # 笔记类型
        if elem.query_selector('[class*="video"], [class*="play"], .duration'):
            note.note_type = "video"
        else:
            note.note_type = "normal"

        # 如果没有标题，尝试获取全部可见文本
        if not note.title:
            text = (elem.inner_text() or "").strip()
            lines = [line for line in text.split("\n") if len(line) > 5]
            note.title = lines[0][:100] if lines else ""

        return note

    def _human_scroll(self):
        """模拟人类滚动"""
        dist = random.randint(400, 900)
        steps = random.randint(3, 6)
        for _ in range(steps):
            self.page.evaluate(f"window.scrollBy(0, {dist // steps})")
            time.sleep(random.uniform(0.1, 0.3))

    # ================================================================
    # Phase 2: API拦截深挖层
    # ================================================================

    def setup_api_interceptor(self):
        """设置API响应拦截器"""
        if self.intercept_enabled:
            return

        def on_response(response):
            """拦截XHS API响应"""
            url = response.url
            try:
                # 只拦截API调用
                if not any(
                    domain in url for domain in ["edith.xiaohongshu.com", "www.xiaohongshu.com/api"]
                ):
                    return

                # 匹配笔记详情API
                if "/api/sns/web/v1/feed" in url or "/api/sns/web/v1/note/" in url:
                    body = response.json()
                    self.api_cache["note_detail"] = body
                    logger.debug("Intercepted note detail API")

                # 匹配评论API
                elif "/api/sns/web/v2/comment" in url:
                    body = response.json()
                    cache_key = f"comment_{url}"
                    self.api_cache[cache_key] = body
                    logger.debug("Intercepted comment API")

                # 匹配搜索API (可以获取更丰富的搜索结果)
                elif "/api/sns/web/v1/search/notes" in url:
                    body = response.json()
                    self.api_cache["search_results"] = body
                    logger.debug("Intercepted search API")

            except Exception:
                pass  # 非JSON响应忽略

        self.page.on("response", on_response)
        self.intercept_enabled = True
        logger.info("API interceptor enabled")

    def deep_extract_one(self, note_id: str) -> DeepNote | None:
        """
        打开笔记详情页，拦截API响应，提取完整数据。
        返回 DeepNote 或 None。
        """
        # 清空之前的缓存
        self.api_cache.pop("note_detail", None)

        # 打开详情页
        detail_url = f"https://www.xiaohongshu.com/explore/{note_id}"
        try:
            self.page.goto(detail_url, timeout=20000, wait_until="domcontentloaded")
        except PwTimeout:
            logger.warning(f"Detail page timeout: {note_id[:8]}...")
            return None

        # 等待API响应 (笔记详情通过XHR加载)
        wait_start = time.time()
        timeout = 10  # 最多等10秒
        while "note_detail" not in self.api_cache:
            time.sleep(0.5)
            if time.time() - wait_start > timeout:
                break

        # 如果API没被拦截到，尝试从DOM提取 (降级)
        if "note_detail" not in self.api_cache:
            logger.debug(f"API not intercepted for {note_id[:8]}..., trying DOM")
            return self._extract_from_dom_fallback(note_id)

        # 从API响应中解析
        api_data = self.api_cache["note_detail"]

        try:
            deep = self._parse_api_response(api_data, note_id)
            return deep
        except Exception as e:
            logger.warning(f"Failed to parse API response: {e}")
            return None

    def _parse_api_response(self, data: dict, note_id: str) -> DeepNote:
        """解析 /api/sns/web/v1/feed 响应"""
        deep = DeepNote(note_id=note_id)
        deep.raw_api_response = data

        # 小红书API响应结构: {"data": {"items": [{"note_card": {...}}, ...]}}
        items = data.get("data", {}).get("items", [])
        if not items:
            logger.debug("No items in API response")
            return deep

        note_card = items[0].get("note_card", {})
        if not note_card:
            return deep

        # --- 基础字段 ---
        deep.title = note_card.get("title", "") or note_card.get("display_title", "")
        deep.desc = note_card.get("desc", "")
        deep.note_type = note_card.get("type", "normal")  # "normal" or "video"
        deep.publish_time = decode_objectid_timestamp(note_id) or ""

        # IP属地
        deep.ip_location = note_card.get("ip_location", "")

        # --- 作者信息 ---
        user = note_card.get("user", {})
        deep.author_name = user.get("nickname", user.get("nick_name", ""))
        deep.author_id = user.get("user_id", user.get("id", ""))
        deep.author_followers = int(user.get("follower_count", 0) or 0)

        # --- 互动数据 ---
        interact = note_card.get("interact_info", {})
        deep.like_count = int(interact.get("liked_count", 0) or 0)
        deep.collect_count = int(interact.get("collected_count", 0) or 0)
        deep.comment_count = int(interact.get("comment_count", 0) or 0)
        deep.share_count = int(interact.get("share_count", 0) or 0)

        # --- 标签 ---
        tag_list = note_card.get("tag_list", [])
        for tag in tag_list:
            tag_name = tag.get("name", "")
            if tag_name:
                deep.tags.append(tag_name)

        # --- 话题 ---
        topic_list = note_card.get("topic_list", []) or note_card.get("topics", [])
        for topic in topic_list:
            topic_name = topic.get("name", "") or topic.get("topic_name", "")
            if topic_name:
                deep.topics.append(topic_name)

        # --- 图片 ---
        image_list = note_card.get("image_list", [])
        for img in image_list:
            img_url = ""
            # 尝试多种URL字段
            for key in ["url_default", "url", "trace_id", "original"]:
                img_info = img.get(key, {}) if isinstance(img, dict) else {}
                if isinstance(img_info, dict):
                    img_url = img_info.get("url", "")
                elif isinstance(img_info, str) and img_info.startswith("http"):
                    img_url = img_info
                if img_url:
                    break
            if not img_url and isinstance(img, dict):
                # 直接尝试URL
                for key in img:
                    val = img[key]
                    if isinstance(val, str) and val.startswith("http"):
                        img_url = val
                        break
            if img_url:
                deep.images.append(img_url)

        # --- 视频 ---
        if deep.note_type == "video":
            video = note_card.get("video", {})
            video_info = video.get("media", {}).get("stream", {})
            # 取最高画质
            for quality in ["h264", "h265"]:
                for res in ["1080p", "720p", "480p"]:
                    stream = video_info.get(quality, {}).get(res, {})
                    master_url = stream.get("master_url", "")
                    if master_url:
                        deep.images.append(master_url)  # 复用images字段存视频URL
                        break
                if deep.images:
                    break

        logger.debug(
            f"Deep: {note_id[:8]}... "
            f"desc={len(deep.desc)}chars "
            f"tags={len(deep.tags)} "
            f"likes={deep.like_count} "
            f"author={deep.author_name}"
        )

        return deep

    def _extract_from_dom_fallback(self, note_id: str) -> DeepNote | None:
        """API拦截失败时的DOM降级方案"""
        deep = DeepNote(note_id=note_id)
        deep.publish_time = decode_objectid_timestamp(note_id) or ""

        # 等待页面渲染
        time.sleep(2)

        # 正文
        for sel in ["#detail-desc", ".note-text", '[class*="noteText"]', ".desc", ".note-content"]:
            el = self.page.query_selector(sel)
            if el:
                text = (el.inner_text() or "").strip()
                if len(text) > 20:
                    deep.desc = text[:3000]
                    break

        # 互动数据
        for sel in [".interact-item", '[class*="interact"] [class*="item"]']:
            els = self.page.query_selector_all(sel)
            if len(els) >= 1:
                deep.like_count = parse_like_count(els[0].inner_text() or "")
            if len(els) >= 2:
                deep.collect_count = parse_like_count(els[1].inner_text() or "")
            if len(els) >= 3:
                deep.comment_count = parse_like_count(els[2].inner_text() or "")
            break

        # 作者
        link = self.page.query_selector('a[href*="/user/profile/"]')
        if link:
            href = link.get_attribute("href") or ""
            uid = re.search(r"/user/profile/([a-f0-9]{24})", href)
            if uid:
                deep.author_id = uid.group(1)
            author_el = link.query_selector('[class*="name"], [class*="nickname"]')
            if author_el:
                deep.author_name = (author_el.inner_text() or "").strip()

        if deep.desc or deep.author_name:
            logger.debug(f"DOM fallback: {note_id[:8]}... desc={len(deep.desc)}chars")
            return deep
        return None

    def deep_extract_batch(self, note_ids: list[str], max_depth: int = 10) -> list[DeepNote]:
        """
        批量深挖笔记。
        对每个note_id调用API拦截提取。
        """
        self.setup_api_interceptor()
        results = []

        ids_to_fetch = note_ids[:max_depth]
        logger.info(f"Deep extracting {len(ids_to_fetch)} notes...")

        for i, nid in enumerate(ids_to_fetch):
            logger.info(f"  [{i + 1}/{len(ids_to_fetch)}] {nid[:8]}...")
            try:
                deep = self.deep_extract_one(nid)
                if deep:
                    results.append(deep)
                    status = "OK" if deep.desc else "no_content"
                    print(
                        f"  [{i + 1}/{len(ids_to_fetch)}] {nid[:8]}... {status} "
                        f"(desc={len(deep.desc)}c, tags={len(deep.tags)}, likes={deep.like_count})"
                    )
                else:
                    print(f"  [{i + 1}/{len(ids_to_fetch)}] {nid[:8]}... FAILED")
            except Exception as e:
                logger.warning(f"Deep extract failed for {nid[:8]}...: {e}")
                print(f"  [{i + 1}/{len(ids_to_fetch)}] {nid[:8]}... ERROR: {e}")

            # 避免触发反爬
            time.sleep(random.uniform(2, 4))

        logger.info(f"Deep extraction done: {len(results)}/{len(ids_to_fetch)} successful")
        return results

    # ================================================================
    # Phase 3: 整合运行
    # ================================================================

    def run(
        self,
        keywords: list[str],
        max_depth: int = 10,
        filter_futures: bool = True,
        need_login: bool = True,
    ) -> dict:
        """
        完整运行混合管道:
        1. 搜索发现 → note_id列表
        2. API拦截深挖 → 完整笔记数据
        3. 期货过滤 → 高相关子集
        """
        self.start(need_login=need_login)

        all_discovered = []  # Phase 1 结果
        all_deep = []  # Phase 2 结果

        try:
            # Phase 1: 发现
            print("\n" + "=" * 60)
            print("PHASE 1: Discovery (Playwright Search)")
            print("=" * 60)

            for kw in keywords:
                notes = self.discover(kw, max_scroll=5)
                all_discovered.extend(notes)
                print(f"  '{kw}': {len(notes)} notes")

            print(f"\nPhase 1 total: {len(all_discovered)} notes discovered")

            # 期货过滤（在发现阶段先做一次初筛，减少深挖量）
            if filter_futures:
                before = len(all_discovered)
                all_discovered = filter_futures_notes(all_discovered)
                print(f"Futures filter (Phase 1): {before} -> {len(all_discovered)} notes")

            # 去重 + 按点赞数排序（优先深挖高互动的笔记）
            seen = set()
            unique_notes = []
            for n in all_discovered:
                if n.note_id not in seen:
                    seen.add(n.note_id)
                    unique_notes.append(n)
            unique_notes.sort(key=lambda n: n.like_count_int, reverse=True)

            # Phase 2: 深挖
            print("\n" + "=" * 60)
            print("PHASE 2: Deep Extraction (API Intercept)")
            print("=" * 60)

            note_ids = [n.note_id for n in unique_notes]
            all_deep = self.deep_extract_batch(note_ids, max_depth=max_depth)

            # Phase 3: 最终过滤和排序
            if filter_futures:
                # 在深度数据上做更精准的期货相关性判断
                deep_filtered = []
                for deep in all_deep:
                    is_rel, conf = is_futures_related_xhs(
                        deep.title or "",
                        deep.desc or "",
                        deep.tags + deep.topics,
                    )
                    if is_rel:
                        deep_filtered.append(deep)
                all_deep = deep_filtered
                print(f"\nFutures filter (Phase 3): {len(all_deep)} deep notes retained")

            return {
                "discovered": all_discovered,
                "deep_notes": all_deep,
                "stats": {
                    "keywords_count": len(keywords),
                    "discovered_count": len(all_discovered),
                    "deep_extracted_count": len(all_deep),
                    "deep_success_rate": len(all_deep) / max(len(note_ids), 1),
                },
            }

        finally:
            pass  # 不关闭浏览器，保留给后续使用

    def stop(self):
        """关闭浏览器"""
        try:
            if self.browser:
                self.browser.close()
        except Exception:
            pass
        try:
            if self.playwright:
                self.playwright.stop()
        except Exception:
            pass

    def save_results(self, results: dict, filename: str = None):
        """保存结果到JSON"""
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        if filename is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"hybrid_results_{ts}.json"

        output = {
            "metadata": {
                "timestamp": datetime.now().isoformat(),
                "method": "hybrid_playwright_api_intercept",
                "stats": results.get("stats", {}),
            },
            "discovered": [
                {
                    "note_id": n.note_id,
                    "title": n.title,
                    "author_name": n.author_name,
                    "like_count_int": n.like_count_int,
                    "publish_time": n.publish_time,
                    "url": n.url,
                    "keyword": n.keyword,
                    "note_type": n.note_type,
                }
                for n in results.get("discovered", [])
            ],
            "deep_notes": [
                {
                    "note_id": d.note_id,
                    "title": d.title,
                    "desc": d.desc[:1000] if d.desc else "",
                    "author_name": d.author_name,
                    "author_id": d.author_id,
                    "author_followers": d.author_followers,
                    "like_count": d.like_count,
                    "comment_count": d.comment_count,
                    "collect_count": d.collect_count,
                    "share_count": d.share_count,
                    "tags": d.tags,
                    "topics": d.topics,
                    "images": d.images[:5],
                    "note_type": d.note_type,
                    "publish_time": d.publish_time,
                    "ip_location": d.ip_location,
                    "keyword": d.keyword,
                }
                for d in results.get("deep_notes", [])
            ],
        }

        filepath = OUTPUT_DIR / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        logger.info(f"Results saved to: {filepath}")
        return filepath


def print_summary(results: dict):
    """打印结果摘要"""
    stats = results.get("stats", {})
    results.get("discovered", [])
    deep = results.get("deep_notes", [])

    print("\n" + "=" * 60)
    print("HYBRID PIPELINE RESULTS")
    print("=" * 60)
    print(f"  Keywords:      {stats.get('keywords_count', 0)}")
    print(f"  Discovered:    {stats.get('discovered_count', 0)} notes")
    print(f"  Deep extracted:{stats.get('deep_extracted_count', 0)} notes")
    print(f"  Success rate:  {stats.get('deep_success_rate', 0) * 100:.0f}%")

    if deep:
        print("\n  Top deep notes:")
        for i, d in enumerate(deep[:5], 1):
            title = (d.title or d.desc or "")[:80].replace("\n", " ")
            desc_len = len(d.desc) if d.desc else 0
            print(f"  {i}. @{d.author_name} | {title}")
            print(
                f"     L{d.like_count} C{d.comment_count} | {desc_len} chars | {len(d.tags)} tags"
            )
            if d.tags:
                print(f"     Tags: {', '.join(d.tags[:8])}")


def main():
    parser = argparse.ArgumentParser(
        description="Hybrid Pipeline: Playwright discovery + API intercept deep extraction"
    )
    parser.add_argument(
        "--keywords",
        type=str,
        nargs="+",
        default=["螺纹钢期货", "铁矿石期货", "原油期货", "黄金期货"],
        help="Search keywords",
    )
    parser.add_argument("--no-login", action="store_true", help="Skip login (use saved state)")
    parser.add_argument("--headless", action="store_true", help="Headless mode")
    parser.add_argument(
        "--max-depth", type=int, default=10, help="Max notes to deep-extract per keyword"
    )
    parser.add_argument(
        "--filter-futures", action="store_true", default=True, help="Apply futures relevance filter"
    )
    parser.add_argument("--no-filter", action="store_true", help="Disable futures filter")
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    filter_futures = not args.no_filter

    pipeline = HybridPipeline(headless=args.headless)

    try:
        results = pipeline.run(
            keywords=args.keywords,
            max_depth=args.max_depth,
            filter_futures=filter_futures,
            need_login=not args.no_login,
        )

        print_summary(results)
        output_path = pipeline.save_results(results, args.output)
        print(f"\nDone! Results: {output_path}")

    except KeyboardInterrupt:
        print("\nInterrupted")
    except Exception as e:
        logger.error(f"Pipeline error: {e}", exc_info=args.verbose)
    finally:
        pipeline.stop()


if __name__ == "__main__":
    sys.exit(main())
