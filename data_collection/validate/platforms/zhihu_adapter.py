"""
知乎平台适配器 (Playwright/CDP 响应拦截)
=======================================
通过 Playwright 浏览器自动计算 x-zse-96 签名，零逆向工程。

技术路线:
  - search: 导航搜索页 + page.on("response") 拦截 /api/v4/search_v3
  - get_detail: 导航答案页 + js-initialData SSR 解析
  - needs_detail_fetch: True (搜索只返回摘要)

依赖: playwright (Chromium)
"""

import json, re, time, logging
from pathlib import Path
from datetime import datetime
from typing import Any, Optional

from .base import PlatformAdapter, CredentialError

logger = logging.getLogger("platforms.zhihu")

CREDENTIALS_DIR = Path(__file__).parent.parent / "credentials"
STATE_FILE = CREDENTIALS_DIR / "zhihu_login_state.json"

# 搜索配置
SEARCH_TIMEOUT = 30  # 等待 API 响应的秒数
PAGE_WAIT = 5        # 页面加载后额外等待秒数
MAX_PAGES = 5        # 最大翻页数


class ZhihuAdapter(PlatformAdapter):
    """知乎数据采集适配器 (Playwright)"""

    name = "zhihu"
    display_name = "知乎"
    id_prefix = "zh:"

    def __init__(self):
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._intercepted: list[dict] = []  # 收集拦截到的 API 响应

    # ============================================================
    # 生命周期
    # ============================================================

    def init(self) -> None:
        """启动 Playwright，加载登录态"""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise CredentialError(
                "Playwright not installed.\n"
                "Run: pip install playwright && playwright install chromium"
            )

        if not STATE_FILE.exists():
            raise CredentialError(
                "Zhihu login state not found.\n"
                "Run: python zhihu_login.py"
            )

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)

        # 加载已保存的登录态
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            storage_state = json.load(f)

        self._context = self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            storage_state=storage_state,
        )
        self._page = self._context.new_page()
        logger.info("Zhihu browser started (headless)")

    def close(self) -> None:
        """关闭浏览器"""
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass

    # ============================================================
    # 搜索 (Playwright 拦截器)
    # ============================================================

    def search(self, keyword: str, count: int) -> list[Any]:
        """
        知乎关键词搜索。
        导航到搜索页，拦截 /api/v4/search_v3 的 AJAX 响应。
        返回: 标准化 item dict 列表。
        """
        self._intercepted = []

        def _on_response(response):
            """拦截搜索 API 响应"""
            if "/api/v4/search_v3" in response.url:
                try:
                    data = response.json()
                    self._intercepted.append(data)
                except Exception:
                    pass

        self._page.on("response", _on_response)

        try:
            # 导航到搜索页（PC 端）
            encoded_q = keyword.replace(" ", "+")
            search_url = (
                f"https://www.zhihu.com/search"
                f"?type=content&q={encoded_q}"
            )
            self._page.goto(search_url, wait_until="domcontentloaded", timeout=30000)

            # 等待 API 响应到达
            waited = 0
            while not self._intercepted and waited < SEARCH_TIMEOUT:
                time.sleep(1)
                waited += 1

            # 如果首页没截到，尝试滚动触发更多
            if not self._intercepted:
                self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2)

        except Exception as e:
            logger.warning(f"Zhihu search navigation failed: {e}")
        finally:
            self._page.remove_listener("response", _on_response)

        # 解析拦截到的响应
        items = []
        seen_ids: set = set()

        for data in self._intercepted:
            results = data.get("data", [])
            if isinstance(results, list):
                for r in results:
                    obj = r.get("object", r)
                    oid = str(obj.get("id", ""))
                    if oid and oid not in seen_ids:
                        seen_ids.add(oid)
                        items.append(self._parse_search_item(r, obj))

            # 如果已有足够结果，可以尝试翻页
            if len(items) >= count:
                break

        # Fallback: 如果页面拦截没拿到数据，直接用浏览器内 fetch() 调搜索 API
        if not items:
            try:
                items = self._search_via_fetch(keyword, count, seen_ids)
            except Exception as e:
                logger.warning(f"  Zhihu fetch fallback also failed: {e}")

        # 如果不够，翻页
        if 0 < len(items) < count:
            try:
                more = self._search_via_fetch(keyword, count, seen_ids, offset=len(items))
                items.extend(more)
            except Exception:
                pass

        logger.info(f"  Zhihu: {len(items)} results for '{keyword}'")
        return items[:count]

    def _search_via_fetch(
        self, keyword: str, count: int, seen_ids: set, offset: int = 0
    ) -> list[dict]:
        """
        Fallback: 用 page.evaluate() + fetch() 直接调搜索 API。
        浏览器自动算 x-zse-96 签名，比拦截页面请求更可靠。
        """
        import urllib.parse
        q = urllib.parse.quote(keyword)
        api_url = (
            f"/api/v4/search_v3"
            f"?t=general&q={q}&correction=1&offset={offset}&limit=20"
            f"&lc_idx=0&show_all_topics=0&search_source=Normal"
        )
        js_code = f"""
            async () => {{
                const r = await fetch('{api_url}');
                if (!r.ok) return null;
                return await r.json();
            }}
        """
        try:
            data = self._page.evaluate(js_code)
        except Exception as e:
            logger.warning(f"  Zhihu fetch search failed: {e}")
            return []

        if not data or not isinstance(data, dict):
            return []

        items = []
        results = data.get("data", [])
        if isinstance(results, list):
            for r in results:
                obj = r.get("object", r)
                oid = str(obj.get("id", ""))
                if oid and oid not in seen_ids:
                    seen_ids.add(oid)
                    items.append(self._parse_search_item(r, obj))
                    if len(items) >= count:
                        break

        return items

    def _parse_search_item(self, raw: dict, obj: dict) -> dict:
        """解析搜索 API 返回的单个条目为标准化 dict"""
        obj_type = raw.get("type", obj.get("type", "answer"))
        oid = str(obj.get("id", ""))

        # 提取作者
        author = obj.get("author", {}) or {}
        if not author:
            # 有些结果 author 在顶层
            author = raw.get("author", {}) or {}
        author_fans = author.get("follower_count", 0) or 0  # 知乎粉丝数

        # 提取问题
        question = obj.get("question", {}) or {}

        return {
            "id": oid,
            "type": obj_type,
            "title": question.get("title", ""),
            "excerpt": obj.get("excerpt", ""),
            "content": obj.get("content", ""),
            "author_name": author.get("name", ""),
            "author_id": str(author.get("url_token", author.get("id", ""))),
            "author_fans": author_fans,
            "voteup_count": obj.get("voteup_count", 0) or 0,
            "comment_count": obj.get("comment_count", 0) or 0,
            "created_time": obj.get("created_time", 0),
            "question_id": str(question.get("id", "")),
            "url": obj.get("url", ""),
        }

    # ============================================================
    # 详情 (SSR js-initialData 解析)
    # ============================================================

    @property
    def needs_detail_fetch(self) -> bool:
        return True  # 浏览器内 fetch() 拿全文+评论

    def get_detail(self, raw_item: Any) -> Optional[dict]:
        """
        获取知乎回答全文 + 热门评论。
        使用 page.evaluate() 在浏览器内调 API (签名由浏览器自动算)。
        """
        oid = raw_item.get("id", "")
        obj_type = raw_item.get("type", "search_result")
        if not oid:
            return None

        result = {"full_content": "", "content_length": 0, "comments": []}

        # ---- 1. 获取回答全文 ----
        # 如果搜索已返回完整 content，跳过 API 调用
        if raw_item.get("content") and len(raw_item.get("content", "")) > 200:
            result["full_content"] = raw_item["content"]
            result["content_length"] = len(raw_item["content"])
        else:
            try:
                answer_url = f"https://www.zhihu.com/question/{raw_item.get('question_id', '')}/answer/{oid}"
                api_path = f"/api/v4/answers/{oid}?include=content,excerpt,voteup_count,comment_count,created_time,author"
                resp = self._page.evaluate(f"""
                    async () => {{
                        const r = await fetch('{api_path}');
                        if (!r.ok) return null;
                        return await r.json();
                    }}
                """)
                if resp and isinstance(resp, dict):
                    result["full_content"] = resp.get("content", resp.get("excerpt", ""))
                    result["content_length"] = len(result["full_content"])
                    result["voteup_count"] = resp.get("voteup_count", 0)
                    result["comment_count"] = resp.get("comment_count", 0)
                    result["created_time"] = resp.get("created_time", 0)
            except Exception as e:
                logger.debug(f"  Zhihu detail API failed for {oid[:8]}: {e}")

        # ---- 2. 获取热门评论 ----
        try:
            comments_path = f"/api/v4/answers/{oid}/comments?order_by=score&limit=20"
            resp = self._page.evaluate(f"""
                async () => {{
                    const r = await fetch('{comments_path}');
                    if (!r.ok) return null;
                    return await r.json();
                }}
            """)
            if resp and isinstance(resp, dict):
                comment_list = resp.get("data", [])
                for c in (comment_list or [])[:10]:  # 取前10条高赞评论
                    content = c.get("content", "")
                    content = re.sub(r'<[^>]+>', '', content)  # 去HTML
                    author = (c.get("author", {}) or {}).get("name", "")
                    vote = c.get("vote_count", 0)
                    result["comments"].append({
                        "author": author,
                        "content": content[:300],
                        "vote": vote,
                    })
        except Exception as e:
            logger.debug(f"  Zhihu comments fetch failed for {oid[:8]}: {e}")

        return result if (result["full_content"] or result["comments"]) else None

    # ============================================================
    # 归一化
    # ============================================================

    def normalize(
        self, raw_item: Any, detail: Optional[dict], keyword: str
    ) -> Optional[dict]:
        """知乎 item → 统一 Schema"""
        oid = raw_item.get("id", "")
        obj_type = raw_item.get("type", "answer")

        if not oid:
            return None

        # 正文: 详情全文 > 搜索返回的 content > excerpt
        desc = ""
        if detail and detail.get("full_content"):
            desc = detail["full_content"]
        elif raw_item.get("content"):
            desc = raw_item["content"]
        elif raw_item.get("excerpt"):
            desc = raw_item["excerpt"]

        # 拼入热门评论文本 (用于 NER/情感分析)
        if detail and detail.get("comments"):
            comment_texts = []
            for c in detail["comments"]:
                ct = c.get("content", "")
                if ct:
                    comment_texts.append(ct)
            if comment_texts:
                desc = desc + "\n\n--- 评论 ---\n" + "\n".join(comment_texts)

        # 截断过长文本
        desc = desc[:3000] if desc else ""

        # 去除 HTML 标签
        desc = re.sub(r'<[^>]+>', '', desc)
        desc = desc.replace('&nbsp;', ' ').replace('&amp;', '&')

        # 标题
        title = raw_item.get("title", "")
        if not title:
            title = desc[:60] if desc else ""

        # 时间
        created_ts = detail.get("created_time", raw_item.get("created_time", 0)) if detail else raw_item.get("created_time", 0)
        if created_ts and created_ts > 0:
            try:
                publish_time = datetime.fromtimestamp(created_ts).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                publish_time = ""
        else:
            publish_time = ""

        # 互动
        like_count = 0
        comment_count = 0
        if detail:
            like_count = detail.get("voteup_count", raw_item.get("voteup_count", 0)) or 0
            comment_count = detail.get("comment_count", raw_item.get("comment_count", 0)) or 0
        else:
            like_count = raw_item.get("voteup_count", 0) or 0
            comment_count = raw_item.get("comment_count", 0) or 0

        note_dict = {
            "platform": "zhihu",
            "note_id": f"{self.id_prefix}{obj_type}:{oid}",
            "title": title,
            "desc": desc,
            "author_name": raw_item.get("author_name", ""),
            "author_id": raw_item.get("author_id", ""),
            "author_fans": raw_item.get("author_fans", 0),
            "like_count": int(like_count),
            "comment_count": int(comment_count),
            "collect_count": 0,
            "share_count": 0,
            "tags": [],
            "note_type": obj_type,
            "publish_time": publish_time,
            "ip_location": "",
            "keyword": keyword,
            "url": raw_item.get("url", f"https://www.zhihu.com/question/{raw_item.get('question_id','')}/answer/{oid}"),
            "desc_length": len(desc) if desc else 0,
            "image_count": 0,
            "is_video": False,
            "image_urls": [],
        }

        return note_dict

    # ============================================================
    # 错误分类
    # ============================================================

    def classify_error(self, exc: Exception) -> str:
        msg = str(exc).lower()
        if "timeout" in msg:
            return "rate_limit"
        if "403" in msg or "unhuman" in msg:
            return "rate_limit"
        if "login" in msg or "auth" in msg:
            return "auth"
        return "other"

    @staticmethod
    def field_mapping() -> dict:
        from .base import FIELD_MAPPING_TABLE
        return FIELD_MAPPING_TABLE["zhihu"]
