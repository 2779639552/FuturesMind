"""
雪球平台适配器 (Playwright + 响应拦截)
======================================
雪球 API 受阿里云 WAF 保护。通过 Playwright 浏览器 + page.on("response")
拦截搜索页加载时的 API 响应，绕过 WAF。

技术路线:
  - init: 启动 Chromium，访问首页建立会话，设置登录 Cookie
  - search: 导航搜索页 + page.on("response") 拦截 /statuses/search.json
  - normalize: 统一 Schema
"""

import re
import json
import logging
import time
from pathlib import Path
from datetime import datetime
from typing import Any, Optional

from .base import PlatformAdapter

logger = logging.getLogger("platforms.xueqiu")

CREDENTIALS_DIR = Path(__file__).parent.parent / "credentials"
COOKIE_FILE = CREDENTIALS_DIR / "xueqiu_cookie.txt"

SEARCH_TIMEOUT = 25


class XueqiuAdapter(PlatformAdapter):
    """雪球数据采集适配器 (Playwright 响应拦截)"""

    name = "xueqiu"
    display_name = "雪球"
    id_prefix = "xq:"

    def __init__(self):
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._intercepted: list[dict] = []
        self._user_cookies: dict = {}

    # ═══════════════════════════════════════════════════════════
    # 生命周期
    # ═══════════════════════════════════════════════════════════

    def init(self) -> None:
        """启动 Playwright，加载登录态"""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise Exception("Playwright not installed. Run: pip install playwright && playwright install chromium")

        self._user_cookies = self._load_cookies()

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )

        self._context = self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
        )
        self._page = self._context.new_page()

        # 反检测
        self._page.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )

        # 访问首页
        logger.info("Navigating to xueqiu.com...")
        try:
            self._page.goto("https://xueqiu.com/", wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)
        except Exception as e:
            logger.warning(f"Homepage: {e}")

        # 设置登录 Cookie
        if self._user_cookies:
            self._context.add_cookies([
                {"name": k, "value": v, "domain": ".xueqiu.com", "path": "/"}
                for k, v in self._user_cookies.items()
            ])
            logger.info(f"Set {len(self._user_cookies)} login cookies")

        # 访问行情页建立完整会话
        try:
            self._page.goto("https://xueqiu.com/hq", wait_until="domcontentloaded", timeout=20000)
            time.sleep(2)
        except Exception:
            pass

        logger.info("Xueqiu browser session ready")

    def close(self) -> None:
        try:
            if self._context: self._context.close()
            if self._browser: self._browser.close()
            if self._playwright: self._playwright.stop()
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════
    # Cookie
    # ═══════════════════════════════════════════════════════════

    def _load_cookies(self) -> dict:
        if not COOKIE_FILE.exists():
            return {}
        raw = COOKIE_FILE.read_text(encoding="utf-8").strip()
        if not raw or raw.startswith("#"):
            return {}
        cookies = {}
        if raw.startswith("{"):
            try:
                data = json.loads(raw)
                cookies = {k: str(v) for k, v in data.items()}
                if cookies: return cookies
            except json.JSONDecodeError:
                pass
        for part in raw.split(";"):
            part = part.strip()
            if "=" in part and "YOUR_" not in part:
                k, _, v = part.partition("=")
                cookies[k.strip()] = v.strip()
        return cookies

    # ═══════════════════════════════════════════════════════════
    # 搜索 (响应拦截)
    # ═══════════════════════════════════════════════════════════

    @property
    def needs_detail_fetch(self) -> bool:
        return False

    def get_detail(self, raw_item: Any) -> Optional[dict]:
        return None

    def search(self, keyword: str, count: int) -> list[Any]:
        """在浏览器内用 fetch() 调搜索 API（绕过 WAF）"""
        import urllib.parse
        all_items = []
        page = 1

        while len(all_items) < count and page <= 5:
            q_enc = urllib.parse.quote(keyword)
            per_page = min(20, count - len(all_items))

            result = self._page.evaluate(f"""
                async () => {{
                    const url = '/query/v1/search/status.json?sortId=1&q={q_enc}&count={per_page}&page={page}';
                    try {{
                        const r = await fetch(url);
                        if (!r.ok) return null;
                        return await r.json();
                    }} catch(e) {{ return null; }}
                }}
            """)

            page += 1

            if not result or not isinstance(result, dict):
                time.sleep(0.5)
                continue

            items = result.get("list", [])
            if not items:
                break

            for item in items:
                sid = str(item.get("id", ""))
                if not sid:
                    continue
                if item.get("retweeted_status") and not item.get("title") and not item.get("description"):
                    continue
                all_items.append(item)

            time.sleep(0.5)

        logger.info(f"  Xueqiu: {len(all_items)} results for '{keyword}'")
        return all_items[:count]

    # ═══════════════════════════════════════════════════════════
    # 归一化
    # ═══════════════════════════════════════════════════════════

    def normalize(self, raw_item: Any, detail: Optional[dict], keyword: str) -> Optional[dict]:
        if not isinstance(raw_item, dict):
            return None

        retweeted = raw_item.get("retweeted_status")
        if retweeted and isinstance(retweeted, dict):
            own = (raw_item.get("title") or "") + " " + (raw_item.get("description") or "")
            content_item = raw_item if own.strip() else retweeted
        else:
            content_item = raw_item

        sid = str(content_item.get("id", ""))
        if not sid:
            return None

        title = (content_item.get("title") or "")[:120]
        desc = (content_item.get("description") or content_item.get("text") or "")[:2000]
        if not title and desc:
            title = desc[:60]

        # 清理 HTML 标签
        title = re.sub(r"<[^>]+>", "", title)
        desc = re.sub(r"<[^>]+>", "", desc)
        title = title.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").strip()
        desc = desc.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").strip()
        full_text = (title + " " + desc).strip()
        if not full_text:
            return None

        user = content_item.get("user", {}) or {}
        author_name = user.get("screen_name", user.get("name", ""))
        author_id = str(user.get("id", ""))

        created_at = content_item.get("created_at", 0)
        if created_at > 10_000_000_000:
            created_at = created_at / 1000
        try:
            publish_time = datetime.fromtimestamp(created_at).strftime("%Y-%m-%d %H:%M:%S") if created_at > 0 else ""
        except Exception:
            publish_time = ""

        tags = []
        for t in (content_item.get("topics", content_item.get("tags", [])) or []):
            if isinstance(t, dict): tags.append(t.get("name", ""))
            elif isinstance(t, str): tags.append(t)

        pics = content_item.get("pics", content_item.get("images", [])) or []
        image_urls = [p.get("url", p.get("src", "")) for p in pics if isinstance(p, dict) and p.get("url")]

        url = content_item.get("target", "") or f"https://xueqiu.com/{sid}"

        return {
            "platform": "xueqiu",
            "note_id": f"{self.id_prefix}{sid}",
            "title": title,
            "desc": full_text,
            "author_name": author_name,
            "author_id": author_id,
            "author_fans": user.get("followers_count", 0) or 0,
            "like_count": int(content_item.get("like_count", 0) or 0),
            "comment_count": int(content_item.get("reply_count", 0) or 0),
            "collect_count": 0,
            "share_count": int(content_item.get("retweet_count", 0) or 0),
            "tags": tags,
            "note_type": "normal",
            "publish_time": publish_time,
            "ip_location": "",
            "keyword": keyword,
            "url": url,
            "desc_length": len(full_text),
            "image_count": len(image_urls),
            "is_video": False,
            "image_urls": image_urls,
        }

    def classify_error(self, exc: Exception) -> str:
        msg = str(exc).lower()
        if "timeout" in msg: return "rate_limit"
        return "other"

    @staticmethod
    def field_mapping() -> dict:
        from .base import FIELD_MAPPING_TABLE
        return FIELD_MAPPING_TABLE.get("xueqiu", {})
