"""
小红书平台适配器 (Spider_XHS API)
================================
从 batch_collect.py 抽离的 XHS 专属逻辑:
  - Spider_XHS API 导入 + Cookie 初始化
  - search_some_note / get_note_info 调用
  - note_card 字段解析 → 统一 Schema

依赖: Spider_XHS/ 目录下的签名引擎 (Node.js + JS 核心文件)
"""

import os
import sys
import logging
from pathlib import Path
from typing import Any, Optional

from .base import PlatformAdapter, CredentialError

logger = logging.getLogger("platforms.xhs")

# Spider_XHS 路径
SPIDER_XHS_PATH = str(Path(__file__).parent.parent.parent / "Spider_XHS")


def _extract_image_urls(image_list: list) -> list[str]:
    """从 image_list 提取最佳质量 URL。取 info_list 最后一项 (通常最高清)。"""
    urls = []
    for img in (image_list or []):
        info_list = img.get("info_list", [])
        if info_list:
            urls.append(info_list[-1].get("url", ""))
        elif img.get("url"):
            urls.append(img["url"])
    return urls


class XHSAdapter(PlatformAdapter):
    """小红书数据采集适配器"""

    name = "xhs"
    display_name = "小红书"
    id_prefix = ""  # 保持裸 ObjectId，向后兼容旧批次数据

    def __init__(self):
        self._api: Any = None               # XHS_Apis 实例
        self._cookies_str: str = ""
        self._original_cwd: str = ""

    # ============================================================
    # 生命周期
    # ============================================================

    def init(self) -> None:
        """初始化 Spider_XHS API：切目录 → 读 Cookie → 建 API 实例"""
        self._original_cwd = os.getcwd()

        # Spider_XHS 签名 JS 文件用相对路径 ./static/，必须在目录下运行
        os.chdir(SPIDER_XHS_PATH)

        # 临时注入 Spider_XHS 到 sys.path（持续到 close）
        if SPIDER_XHS_PATH not in sys.path:
            sys.path.insert(0, SPIDER_XHS_PATH)

        try:
            from xhs_utils.common_util import init as xhs_init
            from apis.xhs_pc_apis import XHS_Apis

            cookies_str, _ = xhs_init()
            if not cookies_str:
                raise CredentialError(
                    "Spider_XHS .env has no COOKIES.\n"
                    "Run: python xhs_scraper.py"
                )

            self._api = XHS_Apis()
            self._cookies_str = cookies_str
            logger.info(f"XHS API initialized. Cookies: {len(cookies_str)} chars")

        except CredentialError:
            raise
        except Exception as e:
            raise CredentialError(
                f"Failed to init Spider_XHS: {e}\n"
                "Ensure Spider_XHS/.env has valid COOKIES."
            ) from e

    def close(self) -> None:
        """恢复原始工作目录"""
        try:
            if self._original_cwd:
                os.chdir(self._original_cwd)
        except OSError:
            pass

    # ============================================================
    # 搜索
    # ============================================================

    def search(self, keyword: str, count: int) -> list[Any]:
        """
        小红书关键词搜索。
        返回: Spider_XHS items 列表（含 note_card 基础信息）。
        """
        success, msg, items = self._api.search_some_note(
            keyword, count + 5, self._cookies_str
        )

        if not success or not isinstance(items, list):
            logger.warning(f"XHS search '{keyword}' returned no results: {msg}")
            return []

        logger.info(f"  Found {len(items)} items for '{keyword}'")
        return items

    # ============================================================
    # 详情 (needs_detail_fetch = True)
    # ============================================================

    @property
    def needs_detail_fetch(self) -> bool:
        return True  # 小红书搜索仅返回摘要，需逐条深挖

    def get_detail(self, raw_item: Any) -> Optional[dict]:
        """
        获取小红书笔记详情。
        raw_item: search() 返回的 item（需含 id + xsec_token）。
        返回: note_card 完整 dict 或 None。
        """
        nid = raw_item.get("id", "")
        xsec = raw_item.get("xsec_token", "")

        if not nid:
            return None

        url = f"https://www.xiaohongshu.com/explore/{nid}?xsec_token={xsec}"

        try:
            success, msg, detail_raw = self._api.get_note_info(url, self._cookies_str)
        except Exception:
            return None

        if not success or not isinstance(detail_raw, dict):
            return None

        code = detail_raw.get("code", -1)
        if code != 0:
            if code == 300031:
                logger.debug(f"  Note {nid[:8]}... not accessible (300031)")
            else:
                logger.debug(
                    f"  Note {nid[:8]}... code={code} "
                    f"msg={detail_raw.get('msg', '')[:50]}"
                )
            return None

        data = detail_raw.get("data", {})
        inner_items = data.get("items", [])
        if not inner_items:
            return None

        return inner_items[0].get("note_card", {})

    # ============================================================
    # 归一化
    # ============================================================

    def normalize(
        self, raw_item: Any, detail: Optional[dict], keyword: str
    ) -> Optional[dict]:
        """
        小红书 item → 统一 Schema dict。
        detail 为 get_detail() 返回的完整 note_card（优先使用），
        raw_item 中的 note_card 作为 fallback。
        """
        nid = raw_item.get("id", "")
        if not nid:
            return None

        # 优先用详情，其次用搜索返回的基础信息
        nc_basic = raw_item.get("note_card", {}) or {}
        nc = detail if detail else nc_basic

        user = nc.get("user", {}) or {}
        author_fans = user.get("follower_count", user.get("fans", 0)) or 0
        interact = nc.get("interact_info", {}) or {}
        tags = [
            t.get("name", "")
            for t in (nc.get("tag_list", []) or [])
            if t.get("name")
        ]

        title = nc.get("title", "") or nc.get("display_title", "")
        desc = nc.get("desc", "")

        # 时间: ObjectId → datetime string
        try:
            from production_hybrid import decode_objectid_timestamp
            publish_time = decode_objectid_timestamp(nid) or ""
        except ImportError:
            publish_time = ""

        note_dict = {
            "platform": "xhs",
            "note_id": nid,
            "title": title,
            "desc": desc,
            "author_name": user.get("nickname", user.get("nick_name", "")),
            "author_id": str(user.get("user_id", "")),
            "author_fans": author_fans,
            "like_count": int(interact.get("liked_count", 0) or 0),
            "comment_count": int(interact.get("comment_count", 0) or 0),
            "collect_count": int(interact.get("collected_count", 0) or 0),
            "share_count": int(interact.get("share_count", 0) or 0),
            "tags": tags,
            "note_type": nc.get("type", "normal"),
            "publish_time": publish_time,
            "ip_location": nc.get("ip_location", ""),
            "keyword": keyword,
            "url": f"https://www.xiaohongshu.com/explore/{nid}",
            "desc_length": len(desc) if desc else 0,
            "image_count": len(nc.get("image_list", []) or []),
            "is_video": nc.get("type", "") == "video",
            "image_urls": _extract_image_urls(nc.get("image_list", []) or []),
        }

        return note_dict

    # ============================================================
    # 错误分类
    # ============================================================

    def classify_error(self, exc: Exception) -> str:
        msg = str(exc).lower()
        if "rate limit" in msg or "too many requests" in msg or "429" in msg:
            return "rate_limit"
        if "unauthorized" in msg or "login" in msg or "cookie" in msg:
            return "auth"
        return "other"

    # ============================================================
    # 字段映射 (文档)
    # ============================================================

    @staticmethod
    def field_mapping() -> dict:
        from .base import FIELD_MAPPING_TABLE
        return FIELD_MAPPING_TABLE["xhs"]
