"""
微博平台适配器 (m.weibo.cn 移动端 API)
=====================================
从 validator_weibo.py 平移已验证的代码:
  - build_session (Cookie + Retry)
  - search_weibo (m.weibo.cn 搜索, card_type=9 过滤)
  - parse_created_at (相对/绝对时间解析)

无需 JS 逆向, 无需签名, 只需 Cookie (SUB 字段, 有效期 7-30 天)。

依赖: requests (纯 HTTP, 无浏览器)
"""

import re
import logging
import time
import random
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .base import PlatformAdapter, CredentialError

logger = logging.getLogger("platforms.weibo")

# 凭证路径
CREDENTIALS_DIR = Path(__file__).parent.parent / "credentials"
WEIBO_COOKIE_FILE = CREDENTIALS_DIR / "weibo_cookie.txt"

# 请求配置
REQUEST_TIMEOUT = 15
MAX_RETRIES = 3
BACKOFF_FACTOR = 0.5
MIN_DELAY = 1.0
MAX_DELAY = 4.0


def _parse_fans_count(raw) -> int:
    """解析微博粉丝数字符串为整数。

    微博 API 返回的 followers_count 可能是:
      - 纯数字: "2345" → 2345
      - 带"万": "15.8万" → 158000
      - 带"亿": "1.2亿" → 120000000
      - 整数: 2345 → 2345
    """
    if raw is None:
        return 0
    if isinstance(raw, (int, float)):
        return int(raw)
    s = str(raw).strip()
    if not s:
        return 0
    try:
        if "亿" in s:
            return int(float(s.replace("亿", "")) * 1_0000_0000)
        elif "万" in s:
            return int(float(s.replace("万", "")) * 1_0000)
        else:
            return int(float(s))
    except (ValueError, TypeError):
        return 0

# 微博 API 端点 (与 config.py ENDPOINTS["weibo"] 一致)
WEIBO_SEARCH_URL = "https://m.weibo.cn/api/container/getIndex"
WEIBO_DETAIL_URL = "https://m.weibo.cn/statuses/extend"

# 默认请求头
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://m.weibo.cn/",
}


def _parse_created_at(created_at: str) -> Optional[str]:
    """
    解析微博时间格式 → "YYYY-MM-DD HH:MM:SS"。
    支持: 相对时间 (几分钟前/小时前/昨天) + 绝对时间 (Tue Jul 15 ...) + ISO。
    从 validator_weibo.py:316-351 平移。
    """
    if not created_at:
        return None

    now = datetime.now()

    # 相对时间
    if "分钟前" in created_at:
        try:
            minutes = int(created_at.replace("分钟前", "").strip())
            dt = now - timedelta(minutes=minutes)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    elif "小时前" in created_at:
        try:
            hours = int(created_at.replace("小时前", "").strip())
            dt = now - timedelta(hours=hours)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    elif "昨天" in created_at:
        dt = now - timedelta(days=1)
        return dt.strftime("%Y-%m-%d 00:00:00")
    elif "前天" in created_at:
        dt = now - timedelta(days=2)
        return dt.strftime("%Y-%m-%d 00:00:00")
    elif "秒前" in created_at or "刚刚" in created_at:
        return now.strftime("%Y-%m-%d %H:%M:%S")

    # 绝对时间: "Tue Jul 15 10:30:00 +0800 2026"
    try:
        dt = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        pass

    # 绝对时间: "2026-07-15 10:30:00"
    try:
        dt = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        pass

    return None


def _clean_html(text: str) -> str:
    """去除 HTML 标签 + 常见转义"""
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&')
    text = text.replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&quot;', '"')
    return text.strip()


class WeiboAdapter(PlatformAdapter):
    """微博数据采集适配器 (m.weibo.cn)"""

    name = "weibo"
    display_name = "微博"
    id_prefix = "wb:"

    def __init__(self):
        self._session: Optional[requests.Session] = None
        self._cookie: str = ""

    # ============================================================
    # 生命周期
    # ============================================================

    def init(self) -> None:
        """加载 Cookie, 构建 requests.Session (含重试策略)。
        优先级: 环境变量 WEIBO_COOKIE > credentials/weibo_cookie.txt
        """
        import os

        cookie = os.environ.get("WEIBO_COOKIE", "")
        if not cookie and WEIBO_COOKIE_FILE.exists():
            cookie = WEIBO_COOKIE_FILE.read_text(encoding="utf-8").strip()

        if not cookie or "SUB" not in cookie:
            raise CredentialError(
                "微博 Cookie 缺失或无效 (需含 SUB 字段)。\n\n"
                "获取方法:\n"
                "1. 浏览器访问 https://m.weibo.cn 并登录\n"
                "2. F12 → Application → Cookies → m.weibo.cn\n"
                "3. 复制完整 Cookie 字符串\n"
                "4. 保存到 credentials/weibo_cookie.txt\n\n"
                "或运行: python weibo_login.py  (Playwright 扫码登录)"
            )

        self._cookie = cookie
        self._session = self._build_session(cookie)
        logger.info(f"Weibo session created. Cookie length: {len(cookie)}")

    def close(self) -> None:
        if self._session:
            self._session.close()
            self._session = None

    def _build_session(self, cookie: str) -> requests.Session:
        """构建带重试机制的 HTTP 会话。从 validator_weibo.py:194-220 平移。"""
        session = requests.Session()
        retry_strategy = Retry(
            total=MAX_RETRIES,
            backoff_factor=BACKOFF_FACTOR,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=5,
            pool_maxsize=5,
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update(REQUEST_HEADERS)
        session.headers["Cookie"] = cookie
        return session

    # ============================================================
    # 搜索
    # ============================================================

    def search(self, keyword: str, count: int) -> list[Any]:
        """
        微博关键词搜索 (m.weibo.cn)。
        分页获取直到达到 count 或没有更多结果。
        返回: mblog dict 列表 (已清除 HTML)。
        从 validator_weibo.py:227-288 平移。
        """
        items = []
        seen_mids: set = set()
        page = 1
        max_pages = (count // 10) + 3  # 每页约 10 条

        while len(items) < count and page <= max_pages:
            params = {
                "containerid": f"100103type=1&q={keyword}",
                "page": page,
            }

            try:
                response = self._session.get(
                    WEIBO_SEARCH_URL,
                    params=params,
                    timeout=REQUEST_TIMEOUT,
                )
                response.raise_for_status()
            except requests.exceptions.RequestException:
                break

            try:
                data = response.json()
            except ValueError:
                break

            if data.get("ok") != 1:
                logger.warning(
                    f"Weibo search '{keyword}' page={page} ok!=1: "
                    f"{str(data.get('msg', ''))[:80]}"
                )
                break

            cards = data.get("data", {}).get("cards", [])
            if not cards:
                break

            page_added = 0
            for card in cards:
                if card.get("card_type") != 9:  # card_type=9 = 微博帖子
                    continue

                mblog = card.get("mblog", {})
                if not mblog:
                    continue

                mid = mblog.get("mid", "")
                if mid in seen_mids:
                    continue
                seen_mids.add(mid)

                # 清洗文本
                text = mblog.get("text_raw", "")
                if not text:
                    text = mblog.get("text", "")
                text = _clean_html(text)

                # 构建标准化 item (保留原始 mblog 所有字段供 normalize 使用)
                item = {
                    "mid": mid,
                    "text": text,
                    "text_raw": mblog.get("text_raw", ""),
                    "created_at": mblog.get("created_at"),
                    "user": mblog.get("user", {}),
                    "reposts_count": mblog.get("reposts_count", 0),
                    "comments_count": mblog.get("comments_count", 0),
                    "attitudes_count": mblog.get("attitudes_count", 0),
                    "source": mblog.get("source", ""),
                    "pics": [p.get("url", "") for p in mblog.get("pics", [])],
                    "isLongText": mblog.get("isLongText", False),
                    "region_name": mblog.get("region_name", ""),
                    "_raw": mblog,
                }
                items.append(item)
                page_added += 1

                if len(items) >= count:
                    break

            if page_added == 0:
                break

            page += 1
            # 页间延时 (微博 API 约 15 req/min)
            time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

        logger.info(f"  Weibo: {len(items)} posts for '{keyword}' ({page} pages)")
        return items

    # ============================================================
    # 详情 (仅长文需要)
    # ============================================================

    @property
    def needs_detail_fetch(self) -> bool:
        return False  # 微博搜索直接返回全文+互动数据

    def get_detail(self, raw_item: Any) -> Optional[dict]:
        """
        获取微博长文详情 (isLongText=True 时调用)。
        m.weibo.cn/statuses/extend?id={mid}
        """
        mid = raw_item.get("mid", "")
        if not raw_item.get("isLongText"):
            return None

        params = {"id": mid}
        try:
            response = self._session.get(
                WEIBO_DETAIL_URL,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
        except Exception:
            return None

        if data.get("ok") == 1:
            return {"long_text": _clean_html(data.get("data", {}).get("longTextContent", ""))}
        return None

    # ============================================================
    # 归一化
    # ============================================================

    def normalize(
        self, raw_item: Any, detail: Optional[dict], keyword: str
    ) -> Optional[dict]:
        """微博 mblog → 统一 Schema dict"""
        mid = raw_item.get("mid", "")
        if not mid:
            return None

        # 正文: 长文详情优先, 其次 text
        desc = raw_item.get("text", "")
        if detail and detail.get("long_text"):
            desc = detail["long_text"]

        # 标题: 微博无标题, 截取正文前60字
        title = desc[:60] if desc else ""

        # 时间
        publish_time = _parse_created_at(raw_item.get("created_at", "")) or ""

        # 作者
        user = raw_item.get("user", {}) or {}
        author_name = user.get("screen_name", "unknown")
        author_id = str(user.get("id", ""))
        author_fans = _parse_fans_count(user.get("followers_count", 0))  # 粉丝数(解析"15.8万"→158000)

        # IP 属地: 去掉"发布于"前缀
        ip_location = (raw_item.get("region_name", "") or "").replace("发布于 ", "").strip()

        note_dict = {
            "platform": "weibo",
            "note_id": f"{self.id_prefix}{mid}",
            "title": title,
            "desc": desc,
            "author_name": author_name,
            "author_id": author_id,
            "author_fans": author_fans,
            "like_count": raw_item.get("attitudes_count", 0) or 0,
            "comment_count": raw_item.get("comments_count", 0) or 0,
            "collect_count": 0,  # 微博无收藏
            "share_count": raw_item.get("reposts_count", 0) or 0,
            "tags": [],
            "note_type": "weibo",
            "publish_time": publish_time,
            "ip_location": ip_location,
            "keyword": keyword,
            "url": f"https://m.weibo.cn/detail/{mid}",
            "desc_length": len(desc) if desc else 0,
            "image_count": len(raw_item.get("pics", []) or []),
            "is_video": False,
            "image_urls": raw_item.get("pics", []) or [],
        }

        return note_dict

    # ============================================================
    # 错误分类
    # ============================================================

    def classify_error(self, exc: Exception) -> str:
        msg = str(exc).lower()
        if "418" in msg or "403" in msg or "rate limit" in msg:
            return "rate_limit"
        if "401" in msg or "unauthorized" in msg:
            return "auth"
        return "other"

    # ============================================================
    # 字段映射 (文档)
    # ============================================================

    @staticmethod
    def field_mapping() -> dict:
        from .base import FIELD_MAPPING_TABLE
        return FIELD_MAPPING_TABLE["weibo"]
