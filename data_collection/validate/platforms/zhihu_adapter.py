"""
知乎平台适配器 (Playwright/CDP 响应拦截)
=======================================

【模块角色】
本适配器负责从"知乎"采集回答/文章，是情绪数据生产链的平台数据源之一。
下游: BatchCollector → 本适配器(ZhihuAdapter) → 统一 Schema dict
     → NER品种识别 / 情感分析 / 多模态分析。

【采集方式】
基于 Playwright 驱动真实 Chromium 浏览器，而非纯 HTTP 请求。
为什么用浏览器？知乎的搜索/详情 API 需要 x-zse-96 签名(JS 逆向难度高)，
而浏览器自身会正确计算并附带该签名——让"浏览器帮我们算签名"，零逆向工程。
缺点: 每次采集都要起一个浏览器进程，速度慢、占资源。

【与 base.py 的关系】
继承 PlatformAdapter 抽象基类，必须实现 init / search / normalize。
needs_detail_fetch=True：知乎搜索只返回摘要，需逐条深挖全文与评论。

【技术路线】
  - search: 导航搜索页 + page.on("response") 拦截 /api/v4/search_v3
            拦截失败时回退到浏览器内 fetch() 直接调 API。
  - get_detail: page.evaluate() 在浏览器内调 API 拿全文 + 热门评论。
  - needs_detail_fetch: True (搜索只返回摘要)

依赖: playwright (Chromium)
"""

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .base import CredentialError, PlatformAdapter

logger = logging.getLogger("platforms.zhihu")

CREDENTIALS_DIR = Path(__file__).parent.parent / "credentials"
STATE_FILE = CREDENTIALS_DIR / "zhihu_login_state.json"

# 搜索配置
SEARCH_TIMEOUT = 30  # 等待 API 响应的秒数
PAGE_WAIT = 5  # 页面加载后额外等待秒数
MAX_PAGES = 5  # 最大翻页数


class ZhihuAdapter(PlatformAdapter):
    """知乎数据采集适配器 (Playwright)

    【采集方式】Playwright 驱动 Chromium，浏览器自动计算 x-zse-96 签名。
    【接口实现】init / search / normalize 必实现；needs_detail_fetch=True。
    """

    name = "zhihu"
    display_name = "知乎"
    id_prefix = "zh:"  # note_id 前缀，格式 "zh:{类型}:{id}"

    def __init__(self):
        """初始化浏览器相关状态，均在 init() 中真正创建。"""
        self._playwright: Any = None  # playwright 控制器
        self._browser: Any = None  # Chromium 浏览器实例
        self._context: Any = None  # 浏览器上下文(含登录态/UA/视口)
        self._page: Any = None  # 当前页面
        self._intercepted: list[dict] = []  # 收集拦截到的 API 响应

    # ============================================================
    # 生命周期
    # ============================================================

    def init(self) -> None:
        """启动 Playwright，加载登录态。

        【功能】拉起无头 Chromium 并注入已保存的知乎登录态。
        【参数】无 【返回】None
        【关键逻辑】
        - 登录态从 zhihu_login_state.json 加载(storage_state，含 Cookie/localStorage)，
          使浏览器处于已登录状态，避免被知乎要求验证码。
        - 依赖 playwright 包，未安装或登录态文件缺失时抛 CredentialError 并给出指引。
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise CredentialError(
                "Playwright not installed.\n"
                "Run: pip install playwright && playwright install chromium"
            ) from None

        # 登录态文件是采集的前提: 没有它就进不了已登录会话
        if not STATE_FILE.exists():
            raise CredentialError("Zhihu login state not found.\nRun: python zhihu_login.py")

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)

        # 加载已保存的登录态 (含 Cookie 等)
        with open(STATE_FILE, encoding="utf-8") as f:
            storage_state = json.load(f)

        self._context = self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            storage_state=storage_state,  # 注入登录态
        )
        self._page = self._context.new_page()
        logger.info("Zhihu browser started (headless)")

    def close(self) -> None:
        """关闭浏览器。

        【功能】释放 Chromium 资源(上下文→浏览器→playwright 依次关闭)。
        【参数】无 【返回】None
        【关键逻辑】必须逐级关闭并 stop()，否则 Chromium 进程残留导致脚本挂住。
        用 try/except 吞异常，保证即使已关过也不会报错。
        """
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
        【功能】知乎关键词搜索，返回标准化 item 列表。
        【参数】
            keyword: 搜索关键词
            count:   期望返回条数
        【返回】标准化 item dict 列表 (最多 count 条)
        【关键逻辑】
        主方案: 导航到搜索页，用 page.on("response") 拦截 /api/v4/search_v3 的 AJAX 响应。
        兜底方案: 拦截失败(如页面懒加载未触发)时，改用浏览器内 fetch() 直接调 API。
        用 seen_ids 去重；结果不足时带 offset 翻页补足。
        """
        self._intercepted = []

        def _on_response(response):
            """拦截搜索 API 响应: 命中 search_v3 就存下其 JSON。"""
            if "/api/v4/search_v3" in response.url:
                try:
                    data = response.json()
                    self._intercepted.append(data)
                except Exception:
                    pass  # 非 JSON 响应(如 HTML)直接忽略

        # 注册响应监听器(仅在导航期间生效，finally 里会移除)
        self._page.on("response", _on_response)

        try:
            # 导航到搜索页（PC 端）; 关键词里的空格要转成 "+" 拼接 URL
            encoded_q = keyword.replace(" ", "+")
            search_url = f"https://www.zhihu.com/search?type=content&q={encoded_q}"
            self._page.goto(search_url, wait_until="domcontentloaded", timeout=30000)

            # 等待 API 响应到达: 轮询直到拦截到数据或超时(SEARCH_TIMEOUT 秒)
            waited = 0
            while not self._intercepted and waited < SEARCH_TIMEOUT:
                time.sleep(1)
                waited += 1

            # 如果首页没截到，尝试滚动到底部触发懒加载，从而发出更多请求
            if not self._intercepted:
                self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2)

        except Exception as e:
            logger.warning(f"Zhihu search navigation failed: {e}")
        finally:
            self._page.remove_listener("response", _on_response)

        # 解析拦截到的响应
        items = []
        seen_ids: set = set()  # 已见过的内容 id，去重

        for data in self._intercepted:
            results = data.get("data", [])
            if isinstance(results, list):
                for r in results:
                    obj = r.get("object", r)  # 搜索结果通常包一层 "object"
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

        # 如果不够，翻页: 用已收集数量作 offset 取下一页
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
        【功能】兜底搜索: 在浏览器页面内用 fetch() 直接调知乎搜索 API。
        【参数】
            keyword: 搜索关键词
            count:   期望条数
            seen_ids: 已见 id 集合(跨方案去重)
            offset:   翻页偏移量，0 表示第一页
        【返回】标准化 item dict 列表
        【关键逻辑】
        Fallback: 用 page.evaluate() + fetch() 直接调搜索 API。
        浏览器自动算 x-zse-96 签名，比拦截页面请求更可靠。
        请求走浏览器同源页面，Cookie/签名都由浏览器自动带上。
        """
        import urllib.parse

        q = urllib.parse.quote(keyword)
        # 知乎搜索 API 的查询参数约定; limit=20 为每页条数
        api_url = (
            f"/api/v4/search_v3"
            f"?t=general&q={q}&correction=1&offset={offset}&limit=20"
            f"&lc_idx=0&show_all_topics=0&search_source=Normal"
        )
        # 注入到浏览器执行的 JS: 调 fetch 取 JSON，失败返回 null
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
        """解析搜索 API 返回的单个条目为标准化 dict

        【功能】把知乎搜索 API 的单个原始结果整理成内部统一的 item dict。
        【参数】
            raw: 搜索结果原始条目
            obj: 从条目中取出的内容对象(可能是答案/文章/话题)
        【返回】含 id/type/title/作者/互动等字段的 dict
        【关键逻辑】
        - 知乎搜索结果类型多样(答案 answer / 文章 article / 话题 topic)，
          用 type 字段区分，note_id 会带上类型前缀。
        - author 有时嵌在 obj 内、有时在顶层(raw)，两处都尝试取。
        """
        obj_type = raw.get("type", obj.get("type", "answer"))
        oid = str(obj.get("id", ""))

        # 提取作者
        author = obj.get("author", {}) or {}
        if not author:
            # 有些结果 author 在顶层
            author = raw.get("author", {}) or {}
        author_fans = author.get("follower_count", 0) or 0  # 知乎粉丝数

        # 提取问题 (答案是挂在某个问题下的)
        question = obj.get("question", {}) or {}

        return {
            "id": oid,
            "type": obj_type,
            "title": question.get("title", ""),  # 答案没有独立标题，用所属问题标题
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
        """是否需要逐条补详情？—— 需要。

        【关键逻辑】知乎搜索只返回摘要，全文和评论必须再调一次详情接口，
        因此覆盖为 True，让采集器逐条调用 get_detail()。
        """
        return True  # 浏览器内 fetch() 拿全文+评论

    def get_detail(self, raw_item: Any) -> dict | None:
        """
        【功能】获取知乎回答全文 + 热门评论。
        【参数】raw_item: search() 返回的知乎 item
        【返回】含 full_content / content_length / comments / 互动数的 dict；
               全文和评论都为空时返回 None
        【关键逻辑】
        使用 page.evaluate() 在浏览器内调 API (x-zse-96 签名由浏览器自动算)。
        1) 全文: 若搜索已带回较完整 content(>200字)则直接复用，省一次请求；
           否则调 /api/v4/answers/{id} 拿完整正文。
        2) 评论: 调 /api/v4/answers/{id}/comments 取前 10 条高赞评论。
        """
        oid = raw_item.get("id", "")
        raw_item.get("type", "search_result")
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
                # 【待确认】下一行仅为未使用的表达式(无实际作用)，疑似历史遗留代码，
                # 保留未动；真正请求走下面的 api_path fetch。
                (f"https://www.zhihu.com/question/{raw_item.get('question_id', '')}/answer/{oid}")
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
            # 按得分(order_by=score)取 20 条候选，再取前 10 条高赞评论
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
                    content = re.sub(r"<[^>]+>", "", content)  # 去HTML
                    author = (c.get("author", {}) or {}).get("name", "")
                    vote = c.get("vote_count", 0)
                    result["comments"].append(
                        {
                            "author": author,
                            "content": content[:300],  # 单条评论截断到 300 字
                            "vote": vote,
                        }
                    )
        except Exception as e:
            logger.debug(f"  Zhihu comments fetch failed for {oid[:8]}: {e}")

        # 全文和评论都拿不到才返回 None(说明该条详情彻底失败)
        return result if (result["full_content"] or result["comments"]) else None

    # ============================================================
    # 归一化
    # ============================================================

    def normalize(self, raw_item: Any, detail: dict | None, keyword: str) -> dict | None:
        """知乎 item → 统一 Schema

        【功能】把知乎 item 转成下游统一格式的 dict。
        【参数】
            raw_item: search() 返回的知乎 item
            detail:   get_detail() 的详情(全文+评论)，可能为 None
            keyword:  本次搜索关键词
        【返回】对齐 UNIFIED_SCHEMA_FIELDS 的 dict；无 id 时返回 None
        【关键逻辑】
        - 正文优先级: 详情全文 > 搜索返回的 content > excerpt。
        - 把热门评论拼接进正文，便于 NER/情感分析一并读到"读者反应"。
        - 全文截断到 3000 字；note_id 带类型前缀(如 zh:answer:123)。
        """
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
        desc = re.sub(r"<[^>]+>", "", desc)
        desc = desc.replace("&nbsp;", " ").replace("&amp;", "&")

        # 标题
        title = raw_item.get("title", "")
        if not title:
            title = desc[:60] if desc else ""

        # 时间
        created_ts = (
            detail.get("created_time", raw_item.get("created_time", 0))
            if detail
            else raw_item.get("created_time", 0)
        )
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
            "url": raw_item.get(
                "url",
                f"https://www.zhihu.com/question/{raw_item.get('question_id', '')}/answer/{oid}",
            ),
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
        """知乎异常分类，供限流退避使用。

        【功能】根据异常消息判断错误类型。
        【参数】exc: 采集抛出的异常
        【返回】'rate_limit' | 'auth' | 'other'
        【关键逻辑】
        - timeout / 403 / "unhuman"(知乎风控关键词): 视为限流，退避重试。
        - login / auth 相关: 登录态失效，需重新登录。
        """
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
        """返回知乎的"统一字段 ← 原始字段"映射表（文档用）。

        【功能】供外部查看/文档渲染知乎字段映射关系。
        【参数】无 【返回】dict（来自 base.FIELD_MAPPING_TABLE["zhihu"]）
        """
        from .base import FIELD_MAPPING_TABLE

        return FIELD_MAPPING_TABLE["zhihu"]
