"""
雪球平台适配器 (Playwright + 响应拦截)
======================================

【模块角色】
本适配器负责从"雪球"(投资社区)采集帖子，是情绪数据生产链的平台数据源之一。
下游: BatchCollector → 本适配器(XueqiuAdapter) → 统一 Schema dict
     → NER品种识别 / 情感分析 / 多模态分析。

【采集方式】
基于 Playwright 驱动 Chromium，在浏览器页面内用 fetch() 直接调雪球搜索 API。
为什么用浏览器？雪球 API 受阿里云 WAF 保护，直接 requests 打接口容易被拦截；
浏览器会携带完整的浏览器环境特征(JS指纹/Cookie)，能绕过 WAF 风控。

【与 base.py 的关系】
继承 PlatformAdapter 抽象基类，必须实现 init / search / normalize。
needs_detail_fetch=False：雪球搜索接口已返回全文+互动数据，无需逐条深挖。

【技术路线】
  - init: 启动 Chromium，访问首页建立会话，注入登录 Cookie(反爬关键)
  - search: page.evaluate() 在浏览器内 fetch /query/v1/search/status.json
  - normalize: 统一 Schema；纯转发(转帖)且无正文的条目会被丢弃
"""

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .base import PlatformAdapter

logger = logging.getLogger("platforms.xueqiu")

CREDENTIALS_DIR = Path(__file__).parent.parent / "credentials"
COOKIE_FILE = CREDENTIALS_DIR / "xueqiu_cookie.txt"

SEARCH_TIMEOUT = 25


class XueqiuAdapter(PlatformAdapter):
    """雪球数据采集适配器 (Playwright 响应拦截)

    【采集方式】Playwright 驱动 Chromium，浏览器内 fetch() 调 API 绕过 WAF。
    【接口实现】init / search / normalize 必实现；needs_detail_fetch=False。
    """

    name = "xueqiu"
    display_name = "雪球"
    id_prefix = "xq:"  # note_id 前缀，避免与其他平台 ID 冲突

    def __init__(self):
        """初始化浏览器相关状态，均在 init() 中真正创建。"""
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._intercepted: list[dict] = []
        self._user_cookies: dict = {}  # 从 Cookie 文件解析出的用户 Cookie

    # ═══════════════════════════════════════════════════════════
    # 生命周期
    # ═══════════════════════════════════════════════════════════

    def init(self) -> None:
        """启动 Playwright，加载登录态。

        【功能】拉起无头 Chromium，访问雪球首页建立会话并注入登录 Cookie。
        【参数】无 【返回】None
        【关键逻辑】(顺序很重要)
        1. 用 "--disable-blink-features=AutomationControlled" 隐藏自动化痕迹，
           配合 add_init_script 抹掉 navigator.webdriver 标记 → 过 WAF 反爬。
        2. 先访问首页(让雪球种下会话 Cookie)，再 add_cookies 注入登录 Cookie，
           最后访问行情页 /hq 把整个会话"跑热"，之后 fetch 搜索接口才稳。
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise Exception(
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            ) from None

        self._user_cookies = self._load_cookies()

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=True,
            # 关键反爬参数: 禁用"自动化受控"特性 + 无沙箱(容器/服务器环境需要)
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

        # 反检测: 覆盖 navigator.webdriver 为 undefined，伪装成真人浏览器
        self._page.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )

        # 访问首页
        logger.info("Navigating to xueqiu.com...")
        try:
            self._page.goto("https://xueqiu.com/", wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)  # 等首页加载完、会话 Cookie 落地
        except Exception as e:
            logger.warning(f"Homepage: {e}")

        # 设置登录 Cookie
        if self._user_cookies:
            self._context.add_cookies(
                [
                    {"name": k, "value": v, "domain": ".xueqiu.com", "path": "/"}
                    for k, v in self._user_cookies.items()
                ]
            )
            logger.info(f"Set {len(self._user_cookies)} login cookies")

        # 访问行情页建立完整会话
        try:
            self._page.goto("https://xueqiu.com/hq", wait_until="domcontentloaded", timeout=20000)
            time.sleep(2)
        except Exception:
            pass

        logger.info("Xueqiu browser session ready")

    def close(self) -> None:
        """释放 Chromium 资源。

        【功能】关闭浏览器上下文/浏览器/playwright 控制器。
        【参数】无 【返回】None
        【关键逻辑】逐级关闭并 stop()，防止 Chromium 进程残留；吞掉关闭异常。
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

    # ═══════════════════════════════════════════════════════════
    # Cookie
    # ═══════════════════════════════════════════════════════════

    def _load_cookies(self) -> dict:
        """从 Cookie 文件解析出 Cookie 字典。

        【功能】读取 credentials/xueqiu_cookie.txt 并解析为 {key: value}。
        【参数】无 【返回】Cookie 字典；文件缺失/为空时返回空 dict
        【关键逻辑】兼容两种格式:
        - JSON 格式(以 { 开头): json.loads 解析。
        - 浏览器标准格式(以 ; 分隔的 key=value): 逐个拆分；
          跳过含 "YOUR_" 的占位值(说明用户没填真实 Cookie)。
        """
        if not COOKIE_FILE.exists():
            return {}  # 没有 Cookie 文件 → 匿名会话(可能被 WAF 拦截)
        raw = COOKIE_FILE.read_text(encoding="utf-8").strip()
        if not raw or raw.startswith("#"):
            return {}  # 空文件或以 # 开头的注释 → 视为无 Cookie
        cookies = {}
        # 优先按 JSON 格式解析
        if raw.startswith("{"):
            try:
                data = json.loads(raw)
                cookies = {k: str(v) for k, v in data.items()}
                if cookies:
                    return cookies
            except json.JSONDecodeError:
                pass  # JSON 解析失败则回退到键值对格式
        # 回退: 浏览器复制出来的 "k1=v1; k2=v2; ..." 格式
        for part in raw.split(";"):
            part = part.strip()
            if "=" in part and "YOUR_" not in part:  # 跳过未填的占位模板
                k, _, v = part.partition("=")
                cookies[k.strip()] = v.strip()
        return cookies

    # ═══════════════════════════════════════════════════════════
    # 搜索 (响应拦截)
    # ═══════════════════════════════════════════════════════════

    @property
    def needs_detail_fetch(self) -> bool:
        """是否需要逐条补详情？—— 不需要。

        【关键逻辑】雪球搜索接口已返回全文+互动数据，
        因此覆盖为 False，可省掉逐条详情请求。
        """
        return False

    def get_detail(self, raw_item: Any) -> dict | None:
        """雪球无需单独详情(搜索已含全文)，保持基类默认行为返回 None。

        【功能】占位实现。 【参数】raw_item: 雪球 item 【返回】恒为 None
        """
        return None

    def search(self, keyword: str, count: int) -> list[Any]:
        """在浏览器内用 fetch() 调搜索 API（绕过 WAF）

        【功能】雪球关键词搜索，分页获取帖子。
        【参数】
            keyword: 搜索关键词
            count:   期望返回条数
        【返回】雪球原始 status item 列表 (最多 count 条)
        【关键逻辑】
        - 用 page.evaluate() 在浏览器里 fetch 搜索接口，浏览器自动带 Cookie/签名，
          从而绕过阿里云 WAF 风控。
        - 每页最多 20 条(per_page)，最多翻 5 页(page<=5)。
        - 过滤"纯转发且无自写正文"的条目(无内容价值)。
        """
        import urllib.parse

        all_items = []
        page = 1

        while len(all_items) < count and page <= 5:
            q_enc = urllib.parse.quote(keyword)
            per_page = min(20, count - len(all_items))  # 本页取多少条

            # 在浏览器内执行 JS: fetch 雪球搜索接口，失败返回 null
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
                continue  # 本次请求失败，短暂等待后翻下一页

            items = result.get("list", [])
            if not items:
                break  # 没有更多结果

            for item in items:
                sid = str(item.get("id", ""))
                if not sid:
                    continue
                # 过滤: 纯转发(retweeted_status)且无自己写的标题/正文 → 无内容价值，丢弃
                if (
                    item.get("retweeted_status")
                    and not item.get("title")
                    and not item.get("description")
                ):
                    continue
                all_items.append(item)

            time.sleep(0.5)  # 页间延时，避免请求过快触发风控

        logger.info(f"  Xueqiu: {len(all_items)} results for '{keyword}'")
        return all_items[:count]

    # ═══════════════════════════════════════════════════════════
    # 归一化
    # ═══════════════════════════════════════════════════════════

    def normalize(self, raw_item: Any, detail: dict | None, keyword: str) -> dict | None:
        """雪球 item → 统一 Schema

        【功能】把雪球原生 status 转成下游统一格式的 dict。
        【参数】
            raw_item: search() 返回的雪球 item
            detail:   雪球不需要详情，恒为 None
            keyword:  本次搜索关键词
        【返回】对齐 UNIFIED_SCHEMA_FIELDS 的 dict；无效条目返回 None
        【关键逻辑】
        - 转发处理: 若本条是转发(retweeted_status)，且自己没有标题/正文，
          则用"被转发的原帖"作为内容来源。
        - 时间: created_at 单位是毫秒(>10^10)则先除以 1000 转秒。
        - 图片 URL 从 pics 或 images 字段提取。
        """
        if not isinstance(raw_item, dict):
            return None  # 非 dict 说明数据格式异常，丢弃

        # 转发处理: 纯转发时退回到原帖，保证正文有内容
        retweeted = raw_item.get("retweeted_status")
        if retweeted and isinstance(retweeted, dict):
            own = (raw_item.get("title") or "") + " " + (raw_item.get("description") or "")
            content_item = raw_item if own.strip() else retweeted
        else:
            content_item = raw_item

        sid = str(content_item.get("id", ""))
        if not sid:
            return None  # 缺 id 无法标识，丢弃

        title = (content_item.get("title") or "")[:120]
        desc = (content_item.get("description") or content_item.get("text") or "")[:2000]
        if not title and desc:
            title = desc[:60]  # 无标题时截取正文前 60 字

        # 清理 HTML 标签
        title = re.sub(r"<[^>]+>", "", title)
        desc = re.sub(r"<[^>]+>", "", desc)
        # 还原 HTML 实体 (&nbsp; &amp; 等)
        title = (
            title.replace("&nbsp;", " ")
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .strip()
        )
        desc = (
            desc.replace("&nbsp;", " ")
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .strip()
        )
        full_text = (title + " " + desc).strip()
        if not full_text:
            return None  # 标题正文都为空，丢弃

        user = content_item.get("user", {}) or {}
        author_name = user.get("screen_name", user.get("name", ""))
        author_id = str(user.get("id", ""))

        # 雪球时间戳是毫秒(13 位，>10^10)，需先转成秒再格式化
        created_at = content_item.get("created_at", 0)
        if created_at > 10_000_000_000:
            created_at = created_at / 1000
        try:
            publish_time = (
                datetime.fromtimestamp(created_at).strftime("%Y-%m-%d %H:%M:%S")
                if created_at > 0
                else ""
            )
        except Exception:
            publish_time = ""  # 时间戳非法时留空，不中断整条

        # 话题/标签: 兼容 dict 和 str 两种形态
        tags = []
        for t in content_item.get("topics", content_item.get("tags", [])) or []:
            if isinstance(t, dict):
                tags.append(t.get("name", ""))
            elif isinstance(t, str):
                tags.append(t)

        # 图片: 从 pics 或 images 字段提取 URL
        pics = content_item.get("pics", content_item.get("images", [])) or []
        image_urls = [
            p.get("url", p.get("src", "")) for p in pics if isinstance(p, dict) and p.get("url")
        ]

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
        """雪球异常分类，供限流退避使用。

        【功能】根据异常消息判断错误类型。
        【参数】exc: 采集抛出的异常
        【返回】'rate_limit' | 'other'
        【关键逻辑】超时(timeout)多半是 WAF/限流拖慢响应，判为限流退避重试。
        """
        msg = str(exc).lower()
        if "timeout" in msg:
            return "rate_limit"
        return "other"

    @staticmethod
    def field_mapping() -> dict:
        """返回雪球的"统一字段 ← 原始字段"映射表（文档用）。

        【功能】供外部查看/文档渲染雪球字段映射关系。
        【参数】无 【返回】dict（来自 base.FIELD_MAPPING_TABLE，取不到返回空 dict）
        """
        from .base import FIELD_MAPPING_TABLE

        return FIELD_MAPPING_TABLE.get("xueqiu", {})
