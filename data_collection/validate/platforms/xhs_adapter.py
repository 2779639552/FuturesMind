"""
小红书平台适配器 (Spider_XHS API)
================================

【模块角色】
本适配器负责从"小红书"采集笔记，是情绪数据生产链的平台数据源之一。
下游: BatchCollector → 本适配器(XHSAdapter) → 统一 Schema dict
     → NER品种识别 / 情感分析 / 多模态分析。

【采集方式】
不是纯 HTTP、也不是 Playwright，而是调用第三方签名引擎 Spider_XHS:
小红书的 X-s/X-t 请求签名需 JS 逆向，Spider_XHS 封装好了签名算法，
我们只调它的 Python API(search_some_note / get_note_info)，传 Cookie 即可。
签名由 Node.js + static/ 下的 JS 核心文件在运行时动态计算。

【与 base.py 的关系】
继承 PlatformAdapter 抽象基类，必须实现 init / search / normalize。
needs_detail_fetch=True：搜索只返回摘要(note_card 基础信息)，详情字段需逐条深挖。

【代码来源】
从 batch_collect.py 抽离的 XHS 专属逻辑:
  - Spider_XHS API 导入 + Cookie 初始化
  - search_some_note / get_note_info 调用
  - note_card 字段解析 → 统一 Schema

依赖: Spider_XHS/ 目录下的签名引擎 (Node.js + JS 核心文件)
"""

import logging  # 【调用包】日志记录(API 初始化/搜索/详情)
import os  # 【调用包】chdir 切到引擎目录/getcwd 记录原目录
import sys  # 【调用包】把 Spider_XHS 引擎目录临时加入 sys.path
from pathlib import Path  # 【调用包】引擎目录路径构建
from typing import Any  # 【调用包】类型注解(API 实例等)

from .base import CredentialError, PlatformAdapter  # 【调用包】基类接口契约 + 凭证异常

logger = logging.getLogger("platforms.xhs")

# Spider_XHS 路径
SPIDER_XHS_PATH = str(Path(__file__).parent.parent.parent / "Spider_XHS")  # 【变量】Spider_XHS 签名引擎目录(含 .env 与 static/)


def _extract_image_urls(image_list: list) -> list[str]:
    """从 image_list 提取最佳质量 URL。

    【功能】从笔记的 image_list 中提取每张图片的最高清 URL。
    【参数】image_list: 图片信息列表(每个元素含 info_list 或 url)
    【返回】URL 字符串列表
    【关键逻辑】每张图有多个清晰度(info_list)，取最后一项(通常最高清)；
    没有 info_list 时回退用外层 url。
    """
    urls = []
    for img in image_list or []:
        info_list = img.get("info_list", [])
        if info_list:
            urls.append(info_list[-1].get("url", ""))  # 取 info_list 最后一项 (通常最高清)
        elif img.get("url"):
            urls.append(img["url"])
    return urls


class XHSAdapter(PlatformAdapter):
    """小红书数据采集适配器

    【采集方式】调用第三方签名引擎 Spider_XHS 的 Python API。
    【接口实现】init / search / normalize 必实现；needs_detail_fetch=True。
    """

    name = "xhs"
    display_name = "小红书"
    id_prefix = ""  # 保持裸 ObjectId(24位十六进制)，向后兼容旧批次数据

    def __init__(self):
        """初始化内部状态，均在 init() 中真正创建。"""
        self._api: Any = None  # XHS_Apis 实例
        self._cookies_str: str = ""  # 【变量】登录 Cookie 字符串(来自 Spider_XHS/.env)
        self._original_cwd: str = ""  # 记录原始工作目录，close 时恢复

    # ============================================================
    # 生命周期
    # ============================================================

    def init(self) -> None:
        """初始化 Spider_XHS API：切目录 → 读 Cookie → 建 API 实例。

        【功能】把签名引擎加载进进程，供后续搜索/详情调用。
        【参数】无 【返回】None
        【关键逻辑】
        - 签名引擎的 JS 文件用相对路径 ./static/ 加载，必须先 chdir 到引擎目录，
          否则签名生成会失败。这是本适配器最特殊的一步。
        - 把引擎目录临时加进 sys.path 以便 import；close() 时恢复原工作目录。
        - Cookie 从 Spider_XHS/.env 的 COOKIES 字段读取，缺失则抛 CredentialError。
        """
        self._original_cwd = os.getcwd()  # 记住原目录，close 时恢复

        # Spider_XHS 签名 JS 文件用相对路径 ./static/，必须在目录下运行
        os.chdir(SPIDER_XHS_PATH)  # 【调用函数】切到引擎目录(签名 JS 用相对路径加载)

        # 临时注入 Spider_XHS 到 sys.path（持续到 close）
        if SPIDER_XHS_PATH not in sys.path:
            sys.path.insert(0, SPIDER_XHS_PATH)  # 【调用函数】临时注入引擎目录到 import 搜索路径

        try:
            # 延迟导入: 只有真正初始化时才加载签名引擎
            from apis.xhs_pc_apis import XHS_Apis  # 【调用包】小红书 API 类(签名由引擎内部计算)
            from xhs_utils.common_util import init as xhs_init  # 【调用包】读取 .env COOKIES 的初始化函数

            cookies_str, _ = xhs_init()  # 【调用函数】读取 Spider_XHS/.env 的登录 Cookie
            if not cookies_str:
                raise CredentialError("Spider_XHS .env has no COOKIES.\nRun: python xhs_scraper.py")

            self._api = XHS_Apis()  # 【调用函数】实例化小红书 API 引擎
            self._cookies_str = cookies_str
            logger.info(f"XHS API initialized. Cookies: {len(cookies_str)} chars")

        except CredentialError:
            raise  # 凭证类错误原样抛出，不包裹
        except Exception as e:
            # 其他初始化失败统一转成 CredentialError，并提示检查 .env
            raise CredentialError(
                f"Failed to init Spider_XHS: {e}\nEnsure Spider_XHS/.env has valid COOKIES."
            ) from e

    def close(self) -> None:
        """恢复原始工作目录。

        【功能】把工作目录切回 init() 之前的位置。
        【参数】无 【返回】None
        【关键逻辑】init() 里 chdir 到了引擎目录，必须还原，
        否则调用方后续相对路径操作会出错；吞掉 OSError(目录可能已被删)。
        """
        try:
            if self._original_cwd:
                os.chdir(self._original_cwd)  # 【调用函数】还原工作目录(防止调用方相对路径出错)
        except OSError:
            pass

    # ============================================================
    # 搜索
    # ============================================================

    def search(self, keyword: str, count: int) -> list[Any]:
        """
        【功能】小红书关键词搜索。
        【参数】
            keyword: 搜索关键词
            count:   期望返回条数
        【返回】Spider_XHS items 列表（含 note_card 基础信息）；失败返回空列表
        【关键逻辑】委托给签名引擎 search_some_note；多取 count+5 条，
        因为部分条目可能没有详情(id/xsec_token 不全)，留出余量供后续过滤。
        """
        success, msg, items = self._api.search_some_note(keyword, count + 5, self._cookies_str)  # 【调用函数】委托签名引擎搜索(多取5条留过滤余量)

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
        """是否需要逐条补详情？—— 需要。

        【关键逻辑】小红书搜索仅返回摘要，完整字段(全文/互动/图片)要靠详情接口，
        因此覆盖为 True，让采集器逐条调用 get_detail()。
        """
        return True  # 小红书搜索仅返回摘要，需逐条深挖

    def get_detail(self, raw_item: Any) -> dict | None:
        """
        【功能】获取小红书单条笔记的完整详情。
        【参数】raw_item: search() 返回的 item（需含 id + xsec_token）
        【返回】note_card 完整 dict；无 id/请求失败/权限受限时返回 None
        【关键逻辑】
        - 需要 id + xsec_token 拼出详情页 URL；xsec_token 是反爬令牌。
        - 委托签名引擎 get_note_info；业务码 code!=0 视为失败。
        - code==300031 表示该笔记无访问权限，常见(作者设限/删除)，仅 debug 记录。
        """
        nid = raw_item.get("id", "")
        xsec = raw_item.get("xsec_token", "")  # 反爬令牌，缺了拿不到详情

        if not nid:
            return None  # 没有 id 无法定位笔记

        url = f"https://www.xiaohongshu.com/explore/{nid}?xsec_token={xsec}"  # 【变量】详情页 URL(带反爬令牌 xsec_token)

        try:
            success, msg, detail_raw = self._api.get_note_info(url, self._cookies_str)  # 【调用函数】委托签名引擎拿完整详情
        except Exception:
            return None  # 网络/签名异常按"拿不到详情"处理，不中断整体

        if not success or not isinstance(detail_raw, dict):
            return None

        code = detail_raw.get("code", -1)
        if code != 0:
            if code == 300031:
                logger.debug(f"  Note {nid[:8]}... not accessible (300031)")
            else:
                logger.debug(
                    f"  Note {nid[:8]}... code={code} msg={detail_raw.get('msg', '')[:50]}"
                )
            return None  # 业务失败，跳过该条

        data = detail_raw.get("data", {})
        inner_items = data.get("items", [])
        if not inner_items:
            return None  # 返回结构异常

        return inner_items[0].get("note_card", {})  # 取第一项的 note_card 作为详情

    # ============================================================
    # 归一化
    # ============================================================

    def normalize(self, raw_item: Any, detail: dict | None, keyword: str) -> dict | None:
        """
        【功能】小红书 item → 统一 Schema dict。
        【参数】
            raw_item: search() 返回的小红书 item
            detail:   get_detail() 返回的完整 note_card（优先使用）
            keyword:  本次搜索关键词
        【返回】对齐 UNIFIED_SCHEMA_FIELDS 的 dict；无 id 时返回 None
        【关键逻辑】
        - 数据源优先级: 完整详情 note_card > 搜索摘要里的 note_card。
        - 发布时间从笔记 id(ObjectId 内含时间戳)反解，而非依赖返回字段。
        - 图片 URL 用 _extract_image_urls 取最高清版本。
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
        tags = [t.get("name", "") for t in (nc.get("tag_list", []) or []) if t.get("name")]

        title = nc.get("title", "") or nc.get("display_title", "")
        desc = nc.get("desc", "")

        # 时间: ObjectId → datetime string
        # 小红书 note_id 是 Mongo ObjectId，前 4 字节即 Unix 时间戳
        try:
            from production_hybrid import decode_objectid_timestamp  # 【调用包】从笔记 ObjectId 反解发布时间(引擎内模块)

            publish_time = decode_objectid_timestamp(nid) or ""  # 【调用函数】ObjectId→发布时间字符串(前4字节=Unix时间戳)
        except ImportError:
            publish_time = ""  # 引擎模块不在路径时留空，不中断

        note_dict = {  # 【变量】统一 Schema 输出(键对齐 UNIFIED_SCHEMA_FIELDS)
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
        """小红书异常分类，供限流退避使用。

        【功能】根据异常消息判断错误类型。
        【参数】exc: 采集抛出的异常
        【返回】'rate_limit' | 'auth' | 'other'
        【关键逻辑】
        - rate limit / too many requests / 429: 签名接口限流 → 退避。
        - unauthorized / login / cookie: 凭证失效 → 需重新抓 Cookie。
        """
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
        """返回小红书的"统一字段 ← 原始字段"映射表（文档用）。

        【功能】供外部查看/文档渲染小红书字段映射关系。
        【参数】无 【返回】dict（来自 base.FIELD_MAPPING_TABLE["xhs"]）
        """
        from .base import FIELD_MAPPING_TABLE  # 【调用包】取小红书字段映射表(文档用)

        return FIELD_MAPPING_TABLE["xhs"]  # 【调用函数】返回"统一字段←小红书原始字段"映射
