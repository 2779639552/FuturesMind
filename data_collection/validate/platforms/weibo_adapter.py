"""
微博平台适配器 (m.weibo.cn 移动端 API)
=====================================

【模块角色】
本适配器负责从"微博"采集帖子，是情绪数据生产链中的平台数据源之一。
下游: BatchCollector → 本适配器(WeiboAdapter) → 统一 Schema dict
     → NER品种识别 / 情感分析 / 多模态分析。

【采集方式】
纯 HTTP 请求 (requests 库)，直连 m.weibo.cn 移动端 API，无需启动浏览器。
- 优点: 速度快、资源占用低；
- 代价: 依赖 Cookie (SUB 字段，有效期约 7-30 天) 做登录态认证。
无需 JS 逆向、无需签名。

【与 base.py 的关系】
继承 PlatformAdapter 抽象基类，必须实现 init / search / normalize，
并按需覆盖 get_detail / needs_detail_fetch / classify_error / close。
needs_detail_fetch=False：因为微博搜索接口直接返回全文+互动数据，无需逐条深挖。

【代码来源】
从 validator_weibo.py 平移已验证的代码:
  - build_session (Cookie + Retry)
  - search_weibo (m.weibo.cn 搜索, card_type=9 过滤)
  - parse_created_at (相对/绝对时间解析)
"""

import logging  # 【调用包】日志记录(采集过程/错误信息输出)
import random  # 【调用包】随机延时(翻页间隔 1~4 秒, 规避限流)
import re  # 【调用包】正则清洗 HTML 标签/实体
import time  # 【调用包】页间 sleep 延时
from datetime import datetime, timedelta  # 【调用包】相对/绝对时间字符串解析
from pathlib import Path  # 【调用包】凭证文件路径构建
from typing import Any  # 【调用包】类型注解(原生 item 为不透明对象)

import requests  # 【调用包】HTTP 请求(直连 m.weibo.cn 移动端 API)
from requests.adapters import HTTPAdapter  # 【调用包】连接池适配器(挂到 Session 支持重试)
from urllib3.util.retry import Retry  # 【调用包】重试策略(429/5xx 自动重试)

from .base import CredentialError, PlatformAdapter  # 【调用包】基类接口契约 + 凭证异常

logger = logging.getLogger("platforms.weibo")

# 凭证路径
CREDENTIALS_DIR = Path(__file__).parent.parent / "credentials"  # 【变量】凭证根目录(../credentials)
WEIBO_COOKIE_FILE = CREDENTIALS_DIR / "weibo_cookie.txt"  # 【变量】微博 Cookie 文件路径

# 请求配置
REQUEST_TIMEOUT = 15  # 【变量】单次请求超时秒数
MAX_RETRIES = 3  # 【变量】最多自动重试次数
BACKOFF_FACTOR = 0.5  # 【变量】重试间隔指数退避基数(秒)
MIN_DELAY = 1.0  # 【变量】翻页最小随机延时秒数
MAX_DELAY = 4.0  # 【变量】翻页最大随机延时秒数


def _parse_fans_count(raw) -> int:
    """解析微博粉丝数字符串为整数。

    【功能】把微博 API 返回的"粉丝数"统一转成整数。
    【参数】raw: 原始值，可能是字符串或数字
    【返回】解析后的整数；解析失败或为空返回 0
    【关键逻辑】处理三种常见格式（中文计数单位）:
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
WEIBO_SEARCH_URL = "https://m.weibo.cn/api/container/getIndex"  # 【变量】微博综合搜索接口(containerid 传搜索词)
WEIBO_DETAIL_URL = "https://m.weibo.cn/statuses/extend"  # 【变量】微博长文全文接口(id=mid 取 longTextContent)

# 默认请求头
# 伪装成 Android 手机浏览器的请求头，降低被风控识别的概率。
# Referer 与 X-Requested-With 让服务端以为是页面内 AJAX 请求而非爬虫。
REQUEST_HEADERS = {  # 【变量】伪装 Android 手机浏览器的请求头(降低风控识别概率)
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


def _parse_created_at(created_at: str) -> str | None:
    """
    【功能】把微博各种时间字符串统一转成 "YYYY-MM-DD HH:MM:SS"。
    【参数】created_at: 微博返回的时间字符串
    【返回】格式化时间字符串；无法解析时返回 None
    【关键逻辑】微博首页/搜索返回"相对时间"（几分钟前/昨天…），
    详情页/API 则返回"绝对时间"，格式不统一，需要兼容多种写法:
      支持: 相对时间 (几分钟前/小时前/昨天/前天/秒前/刚刚)
            + 绝对时间 (Tue Jul 15 ... +0800 2026) + ISO ("2026-07-15 10:30:00")。
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
    """去除 HTML 标签 + 常见转义

    【功能】把带 HTML 标签/实体转义的文本清洗为纯文本。
    【参数】text: 原始文本（可能含 <a>、&amp; 等 HTML 内容）
    【返回】清洗后的纯文本
    【关键逻辑】两步: 先正则去掉所有 <...> 标签，再把常见 HTML 实体
    (&nbsp; &amp; &lt; &gt; &quot;)还原为对应字符。用于正文/长文清洗。
    """
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"')
    return text.strip()


class WeiboAdapter(PlatformAdapter):
    """微博数据采集适配器 (m.weibo.cn)

    【采集方式】纯 HTTP (requests)，依赖 Cookie 登录态。
    【接口实现】init / search / normalize 必实现；needs_detail_fetch=False。
    """

    name = "weibo"
    display_name = "微博"
    id_prefix = "wb:"  # note_id 加 "wb:" 前缀，避免与其他平台 ID 冲突

    def __init__(self):
        """初始化内部状态: 会话与 Cookie 初始为空，由 init() 填充。"""
        self._session: requests.Session | None = None  # 【变量】HTTP 会话(init 时构建, 含重试+常驻 Cookie)
        self._cookie: str = ""  # 【变量】微博登录 Cookie 字符串(须含 SUB 字段)

    # ============================================================
    # 生命周期
    # ============================================================

    def init(self) -> None:
        """加载 Cookie, 构建 requests.Session (含重试策略)。

        【功能】初始化微博连接：读取 Cookie 并建立带重试的 HTTP 会话。
        【参数】无 【返回】None
        【关键逻辑】
        凭证优先级: 环境变量 WEIBO_COOKIE > credentials/weibo_cookie.txt 文件。
        Cookie 必须含 SUB 字段（微博登录态核心），否则判定凭证无效。
        """
        import os  # 【调用包】读环境变量取 Cookie(凭证优先级最高)

        # 读取 Cookie: 优先环境变量 WEIBO_COOKIE，其次从 credentials/weibo_cookie.txt 读取
        cookie = os.environ.get("WEIBO_COOKIE", "")  # 【调用函数】从环境变量取 Cookie(第一优先来源)
        if not cookie and WEIBO_COOKIE_FILE.exists():
            cookie = WEIBO_COOKIE_FILE.read_text(encoding="utf-8").strip()  # 【调用函数】无环境变量时回退读 Cookie 文件

        # 校验: 微博 Cookie 必须含 "SUB" 字段(登录态标志)，缺失即视为无效凭证
        if not cookie or "SUB" not in cookie:
            # 抛出带获取指引的异常，方便使用者自助解决
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
        self._session = self._build_session(cookie)  # 【调用函数】构建带重试/常驻 Cookie 的 HTTP 会话
        logger.info(f"Weibo session created. Cookie length: {len(cookie)}")

    def close(self) -> None:
        """释放 HTTP 会话资源。

        【功能】关闭 requests.Session 连接池。
        【参数】无 【返回】None
        【关键逻辑】不复用则清空引用，避免后续误用已关闭的会话。
        """
        if self._session:
            self._session.close()  # 【调用函数】关闭 requests 连接池
            self._session = None

    def _build_session(self, cookie: str) -> requests.Session:
        """构建带重试机制的 HTTP 会话。

        【功能】创建带"自动重试"能力的 requests.Session。
        【参数】cookie: 微博登录 Cookie 字符串
        【返回】配置好重试策略与请求头的 Session
        【关键逻辑】
        - 用 urllib3 的 Retry 实现指数退避重试: 对 429(限流) 及 5xx(服务器错)
          自动重试 MAX_RETRIES 次，backoff_factor 控制重试间隔递增。
        - 仅对 GET 方法重试(搜索/详情都是 GET)，避免 POST 重复提交副作用。
        - 把默认请求头与 Cookie 常驻在 Session 上，之后每次请求自动携带。
        从 validator_weibo.py:194-220 平移。
        """
        session = requests.Session()  # 【调用函数】新建 requests 会话(连接池)
        retry_strategy = Retry(  # 【变量】自动重试策略(指数退避, 仅 GET)
            total=MAX_RETRIES,
            backoff_factor=BACKOFF_FACTOR,
            status_forcelist=[429, 500, 502, 503, 504],  # 这些状态码会自动重试
            allowed_methods=["GET"],  # 只对 GET 重试
        )
        adapter = HTTPAdapter(  # 【变量】HTTP 连接池适配器(最多 5 个并发连接)
            max_retries=retry_strategy,
            pool_connections=5,  # 连接池大小: 同时最多 5 个连接
            pool_maxsize=5,
        )
        session.mount("https://", adapter)  # 【调用函数】为 https 挂载重试适配器
        session.mount("http://", adapter)
        session.headers.update(REQUEST_HEADERS)  # 【调用函数】默认请求头写入会话(每次请求自动带)
        session.headers["Cookie"] = cookie  # 常驻 Cookie，之后请求自动带上
        return session

    # ============================================================
    # 搜索
    # ============================================================

    def search(self, keyword: str, count: int) -> list[Any]:
        """
        【功能】微博关键词搜索，分页获取帖子。
        【参数】
            keyword: 搜索关键词
            count:   期望返回条数
        【返回】清洗后的 mblog dict 列表 (已清除 HTML)。
        【关键逻辑】
        - 调用 m.weibo.cn 搜索接口，每页约 10 条，循环翻页直到凑够 count。
        - 用 seen_mids 集合去重（翻页时同一帖可能重复出现）。
        - 只保留 card_type=9 的卡片（微博帖子），过滤广告等其他卡片。
        - 页与页之间 sleep 随机延时，配合微博 API 约 15 req/min 的限流。
        从 validator_weibo.py:227-288 平移。
        """
        items = []
        seen_mids: set = set()  # 已见帖子的 mid 集合，用于去重
        page = 1
        max_pages = (count // 10) + 3  # 每页约 10 条，多翻几页兜底

        while len(items) < count and page <= max_pages:
            # containerid=100103type=1&q=关键词 是微博"综合搜索"接口约定
            params = {
                "containerid": f"100103type=1&q={keyword}",
                "page": page,
            }

            # 发起请求; 网络异常直接中断翻页(已拿到的结果仍返回)
            try:
                response = self._session.get(  # 【调用函数】GET 微博搜索接口(带 params/超时)
                    WEIBO_SEARCH_URL,
                    params=params,
                    timeout=REQUEST_TIMEOUT,
                )
                response.raise_for_status()  # 【调用函数】非 2xx 抛异常(配合重试)
            except requests.exceptions.RequestException:
                break

            try:
                data = response.json()  # 【调用函数】解析响应 JSON(ok=1 表示业务成功)
            except ValueError:
                break  # 返回非 JSON(如被重定向到验证页)时放弃本页

            # 微博 API 用 ok==1 表示业务成功；否则打印失败原因后停止
            if data.get("ok") != 1:
                logger.warning(
                    f"Weibo search '{keyword}' page={page} ok!=1: {str(data.get('msg', ''))[:80]}"
                )
                break

            cards = data.get("data", {}).get("cards", [])
            if not cards:
                break  # 没有卡片说明已翻到底/无结果

            page_added = 0
            for card in cards:
                if card.get("card_type") != 9:  # card_type=9 = 微博帖子; 其余是广告/推荐等
                    continue

                mblog = card.get("mblog", {})
                if not mblog:
                    continue

                mid = mblog.get("mid", "")  # mid 是微博帖子的唯一 ID
                if mid in seen_mids:
                    continue  # 翻页/交叉时已见过的帖子直接跳过(去重)
                seen_mids.add(mid)

                # 清洗文本: 优先取无 HTML 的 text_raw，否则回退到 text 再去标签
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
                break  # 本页一条新帖都没加，说明后续页大概率也无新内容

            page += 1
            # 页间延时 (微博 API 约 15 req/min): 随机 1~4 秒，避免触发限流
            time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

        logger.info(f"  Weibo: {len(items)} posts for '{keyword}' ({page} pages)")
        return items

    # ============================================================
    # 详情 (仅长文需要)
    # ============================================================

    @property
    def needs_detail_fetch(self) -> bool:
        """是否需要逐条补详情？—— 不需要。

        【关键逻辑】微博搜索接口直接返回全文+互动数据，
        因此覆盖为 False 可大幅节省请求量(省掉每条一次的详情请求)。
        """
        return False  # 微博搜索直接返回全文+互动数据

    def get_detail(self, raw_item: Any) -> dict | None:
        """
        【功能】获取微博长文全文 (仅 isLongText=True 时会被调用)。
        【参数】raw_item: search() 返回的帖子 item
        【返回】含 "long_text" 键的 dict；非长文或失败时返回 None
        【关键逻辑】
        只有"长文"(isLongText=True)才需要额外请求:
        m.weibo.cn/statuses/extend?id={mid} 拿完整正文 longTextContent。
        """
        mid = raw_item.get("mid", "")
        if not raw_item.get("isLongText"):
            return None

        params = {"id": mid}  # 【变量】详情接口查询参数(传帖子 mid)
        try:
            response = self._session.get(  # 【调用函数】GET 微博长文接口(id=mid 取全文)
                WEIBO_DETAIL_URL,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()  # 【调用函数】解析 JSON(ok=1 时含 longTextContent)
        except Exception:
            return None

        if data.get("ok") == 1:
            return {"long_text": _clean_html(data.get("data", {}).get("longTextContent", ""))}
        return None

    # ============================================================
    # 归一化
    # ============================================================

    def normalize(self, raw_item: Any, detail: dict | None, keyword: str) -> dict | None:
        """微博 mblog → 统一 Schema dict

        【功能】把微博原生 item 转成下游统一格式的 dict。
        【参数】
            raw_item: search() 返回的微博 item
            detail:   get_detail() 的长文详情 (可能为 None)
            keyword:  本次搜索关键词
        【返回】对齐 UNIFIED_SCHEMA_FIELDS 的 dict；无 mid 时返回 None
        【关键逻辑】
        - 无 mid 视为无效条目，返回 None 丢弃。
        - 正文优先级: 长文详情 > 搜索返回的 text。
        - 微博无标题，用正文前 60 字充当标题。
        - 时间用 _parse_created_at 兼容相对/绝对格式；IP 属地去掉"发布于"前缀。
        """
        mid = raw_item.get("mid", "")
        if not mid:
            return None  # 缺 mid 的帖子不可用，丢弃

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
        author_fans = _parse_fans_count(
            user.get("followers_count", 0)
        )  # 粉丝数(解析"15.8万"→158000)

        # IP 属地: 去掉"发布于"前缀
        ip_location = (raw_item.get("region_name", "") or "").replace("发布于 ", "").strip()

        note_dict = {  # 【变量】统一 Schema 输出(键对齐 UNIFIED_SCHEMA_FIELDS)
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
        """微博异常分类，供限流退避使用。

        【功能】根据异常消息判断错误类型。
        【参数】exc: 采集抛出的异常
        【返回】'rate_limit' | 'auth' | 'other'
        【关键逻辑】
        - 418/403/rate limit: 微博风控拒绝 → 限流退避。
        - 401/unauthorized: 登录态失效 → 需要重新登录。
        """
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
        """返回微博的"统一字段 ← 原始字段"映射表（文档用）。

        【功能】供外部查看/文档渲染微博字段映射关系。
        【参数】无 【返回】dict（来自 base.FIELD_MAPPING_TABLE["weibo"]）
        """
        from .base import FIELD_MAPPING_TABLE  # 【调用包】取微博字段映射表(文档用)

        return FIELD_MAPPING_TABLE["weibo"]  # 【调用函数】返回"统一字段←微博原始字段"映射
