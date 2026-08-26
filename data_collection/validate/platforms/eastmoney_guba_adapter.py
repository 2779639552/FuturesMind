"""
东方财富股吧平台适配器 (Playwright APIRequestContext + 东财 JSONP 搜索)
====================================================================

【模块角色】
本适配器负责从"东方财富股吧"(guba.eastmoney.com)采集帖子，是情绪数据生产链中的
新增平台数据源。股吧是国内期货讨论量最大的散户聚集地，用于补足冷门品种
(苹果/红枣/花生/玻璃/尿素/短纤等) 的帖子量。

【采集方式】
Playwright 的 APIRequestContext(真实 Chromium 网络栈, 无浏览器进程/无页面/不执行 JS)。
- 为什么不用纯 requests? 东财搜索 API 有 WAF TLS 指纹识别(2026-08-25 实测定位):
    urllib3 的 TLS 指纹被识别为 bot, 无论带什么 Cookie/参数都只返回降级的
    passportWeb(用户账户)结果, 不含帖子;
    Chromium 网络栈的 TLS 指纹正常, 免 Cookie 直接返回 gubaArticleWeb(帖子)。
- APIRequestContext 通过 playwright.request.new_context() 创建, 不起浏览器进程,
  比知乎/雪球的完整浏览器方案更轻量。

【接口约定】 (2026-08-25 实测确认)
- 端点:  GET https://search-api-web.eastmoney.com/search/jsonp (JSONP)
- type:  必须 ["gubaArticleWeb"] (旧文档流传的 gubaWebOld 已失效)
- 子参数: {"gubaArticleWeb": {"pageSize","pageIndex","postTag":"","preTag":"",
                              "sortOrder": 1|2}}   (1=相关度, 2=时间最新)
- 响应:  剥 JSONP 回调壳后 data.result.gubaArticleWeb 为帖子数组
- 字段:  id/title/content/createTime/url/shortName/innerCode
         注意: 无作者昵称、无点赞/评论数 (v1 接受缺省, 文本够情感分析用)

【与 base.py 的关系】
继承 PlatformAdapter 抽象基类，实现 init / search / normalize；
needs_detail_fetch=False(搜索即含标题+正文摘要)。

【与 weibo_adapter.py 的关系】
整体采集循环一致(翻页去重 + 随机延时)，区别: 网络栈改用 Playwright
APIRequestContext(绕 WAF), 端点改为东财 JSONP 搜索, 解析需先剥 JSONP 回调壳。
"""

import logging  # 【调用包】日志记录(采集过程/错误信息输出)
import random  # 【调用包】随机延时(翻页间隔 1~3 秒, 规避限流)
import re  # 【调用包】正则清洗 HTML 标签/实体/JSONP 回调壳
import time  # 【调用包】页间 sleep 延时
from datetime import datetime  # 【调用包】股吧时间字符串解析
from typing import Any  # 【调用包】类型注解(原生 item 为不透明对象)

from .base import CredentialError, PlatformAdapter  # 【调用包】基类接口契约 + 凭证异常

logger = logging.getLogger("platforms.eastmoney_guba")

# 请求配置
REQUEST_TIMEOUT_MS = 20_000  # 【变量】单次请求超时(毫秒, Playwright APIRequestContext 以毫秒计)
MIN_DELAY = 1.0  # 【变量】翻页最小随机延时秒数
MAX_DELAY = 3.0  # 【变量】翻页最大随机延时秒数
PAGE_SIZE = 20  # 【变量】东财搜索每页条数
SORT_ORDER_TIME = 2  # 【变量】排序: 2=按发布时间最新 (情绪采集优先取最新帖)
MAX_PAGE_RETRIES = 2  # 【变量】单页最多重试次数(WAF 限流时返回 200 但空结果, 退避重试)
RETRY_BACKOFF_S = 6.0  # 【变量】空结果重试前的退避秒数

# 东财搜索 JSONP 端点 (2026-08-25 实测有效; 类型必须为 gubaArticleWeb)
SEARCH_URL = "https://search-api-web.eastmoney.com/search/jsonp"  # 【变量】东财搜索 JSONP 端点

# 默认请求头: 伪装桌面 Chrome + Referer 指向搜索页, 降低风控识别概率
REQUEST_HEADERS = {  # 【变量】伪装桌面 Chrome 请求头(Referer 指向 so.eastmoney.com)
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://so.eastmoney.com/",
}


def _strip_jsonp(text: str) -> dict | None:
    """剥掉 JSONP 回调壳, 返回内部 JSON 对象。

    【功能】JSONP 响应形如 `jsonpCallback({...})`, 去掉回调名括号后 json.loads。
    【参数】text: 原始 JSONP 文本
    【返回】解析后的 dict; 非 JSONP/解析失败返回 None
    """
    m = re.match(r"^\s*(?:[\w$]+)\s*\((.*)\)\s*;?\s*$", text, re.S)
    if not m:
        return None
    try:
        import json  # 【调用包】解析剥壳后的 JSON

        return json.loads(m.group(1))
    except (ValueError, TypeError):
        return None


def _clean_html(text: str) -> str:
    """去除 HTML 标签 + 常见转义 + $合约标签$ 包裹符号。

    【功能】把带 HTML 标签/实体转义/合约标记的文本清洗为纯文本。
    【参数】text: 原始文本
    【返回】清洗后的纯文本
    【关键逻辑】
    - 股吧标题/正文常带 <em> 高亮标签, 直接剥掉;
    - 正文常见 `$螺纹钢2610(SHFE|rb2610)$` 合约引用格式, 剥掉两侧 $ 保留内文。
    """
    text = re.sub(r"<[^>]+>", "", text)  # 剥 HTML 标签(含 <em> 高亮)
    text = re.sub(r"\$([^$]*)\$", r"\1", text)  # 剥 $合约标签$ 的包裹符号, 保留内文
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"')
    return text.strip()


def _parse_guba_time(raw: Any) -> str:
    """解析股吧时间字符串为 "YYYY-MM-DD HH:MM:SS"。

    【功能】兼容常见写法:
        "2026-08-25 10:00:00" (完整, 东财 createTime 主格式)
        "08-25 10:00"        (无年份 → 补当前年份)
        unix 秒/毫秒时间戳
    【参数】raw: 股吧返回的时间
    【返回】格式化时间字符串; 无法解析返回 ""
    """
    if raw is None:
        return ""
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 1e12:  # 毫秒 → 秒
            ts /= 1000
        try:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OSError):
            return ""
    s = str(raw).strip()
    if not s:
        return ""
    # "2026-08-25 10:00:00" 或 "2026-08-25 10:00" 或 "2026-08-25"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    # "08-25 10:00" (无年份 → 补当前年份)
    m = re.match(r"^(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?$", s)
    if m:
        month, day, hour, minute = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        second = int(m.group(5)) if m.group(5) else 0
        now = datetime.now()
        try:
            dt = datetime(now.year, month, day, hour, minute, second)
            if dt > now:  # 未来时间 → 视为去年
                dt = dt.replace(year=now.year - 1)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return ""
    return ""


class EastmoneyGubaAdapter(PlatformAdapter):
    """东方财富股吧数据采集适配器 (Playwright APIRequestContext)

    【采集方式】Playwright APIRequestContext(真实 Chromium TLS 指纹), 免 Cookie/浏览器进程。
    【接口实现】init / search / normalize 必实现；needs_detail_fetch=False。
    【关键背景】东财搜索 WAF 对 urllib3 TLS 指纹降级(只回 passportWeb)，
    必须走 Chromium 网络栈。详情见模块 docstring。
    """

    name = "eastmoney_guba"
    display_name = "东方财富股吧"
    id_prefix = "emg:"  # note_id 加 "emg:" 前缀, 避免与其他平台 ID 冲突

    def __init__(self):
        """初始化内部状态: playwright 控制器与 APIRequestContext 为空, 由 init() 构建。"""
        self._pw: Any = None  # playwright 控制器(sync_playwright().start())
        self._api: Any = None  # APIRequestContext(真实 Chromium 网络栈, 无浏览器进程)

    # ============================================================
    # 生命周期
    # ============================================================

    def init(self) -> None:
        """构建 Playwright APIRequestContext(公开接口, 无需 Cookie)。

        【功能】初始化东财股吧连接: 创建 APIRequestContext, 不校验凭证。
        【参数】无 【返回】None
        【关键逻辑】
        - 用 playwright.request.new_context() 建 APIRequestContext: 真实 Chromium
          网络栈(TLS 指纹正常), 但不起浏览器进程/不执行 JS, 比完整浏览器方案轻量。
        - 公开 JSONP 接口匿名可用, 不抛 CredentialError; 未装 playwright 时抛
          CredentialError 并给出安装指引(与知乎适配器同款)。
        """
        try:
            from playwright.sync_api import sync_playwright  # 【调用包】Playwright 同步 API(驱动 Chromium 网络栈)
        except ImportError:
            raise CredentialError(
                "Playwright not installed.\n"
                "Run: pip install playwright && playwright install chromium"
            ) from None

        self._pw = sync_playwright().start()  # 【调用函数】启动 Playwright 控制器
        # 建 APIRequestContext: 真实 Chromium TLS 指纹 + 桌面 UA 伪装
        self._api = self._pw.request.new_context(
            user_agent=REQUEST_HEADERS["User-Agent"],  # 伪装桌面 Chrome(与请求头一致)
            extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9"},
        )
        logger.info("Eastmoney Guba APIRequestContext created (anonymous).")

    def close(self) -> None:
        """释放 APIRequestContext 与 Playwright 控制器资源。

        【功能】依次 dispose APIRequestContext → stop playwright 控制器。
        【参数】无 【返回】None
        【关键逻辑】必须 stop(), 否则 playwright 控制器残留导致脚本无法退出。
        用 try/except 吞异常, 保证即使已关过也不会报错。
        """
        try:
            if self._api:
                self._api.dispose()  # 【调用函数】释放 APIRequestContext 连接池
            if self._pw:
                self._pw.stop()  # 【调用函数】停止 Playwright 控制器(防进程残留)
        except Exception:
            pass
        self._api = None
        self._pw = None

    # ============================================================
    # 搜索
    # ============================================================

    def search(self, keyword: str, count: int) -> list[Any]:
        """东财股吧关键词搜索, 分页获取帖子。

        【功能】调用东财 JSONP 搜索接口(gubaArticleWeb 类型), 每页约 20 条, 循环翻页。
        【参数】
            keyword: 搜索关键词
            count:   期望返回条数
        【返回】清洗后的股吧 item dict 列表 (最多 count 条)。
        【关键逻辑】
        - type=["gubaArticleWeb"] + sortOrder=2(按发布时间最新), 情绪采集优先拿新帖;
        - 请求走 APIRequestContext(真实 Chromium TLS, 绕 WAF), 用 _strip_jsonp 剥回调壳;
        - 用 seen_ids 去重(翻页可能重复);
        - 页与页之间随机 1~3 秒延时, 规避限流;
        - 请求异常/非 JSONP/结果为空 → 直接 break(已拿到的结果仍返回)。
        """
        import json as _json  # 【调用包】序列化 param 参数(东财 JSONP 约定)
        import urllib.parse  # 【调用包】URL 编码 param JSON(中文/特殊字符安全拼进 query)

        if self._api is None:
            return []  # init() 未调用/失败时安全返回空

        items = []
        seen_ids: set = set()  # 已见帖子 ID 集合, 用于去重
        page = 1
        max_pages = (count // PAGE_SIZE) + 3  # 每页约 20 条, 多翻几页兜底

        while len(items) < count and page <= max_pages:
            # 东财搜索请求体: type 必须 gubaArticleWeb, 子参数见模块 docstring(实测确认)
            param_json = {
                "uid": "",
                "keyword": keyword,
                "type": ["gubaArticleWeb"],
                "client": "web",
                "clientType": "web",
                "clientVersion": "curr",
                "param": {
                    "gubaArticleWeb": {
                        "pageSize": PAGE_SIZE,
                        "pageIndex": page,
                        "postTag": "",
                        "preTag": "",
                        "sortOrder": SORT_ORDER_TIME,  # 2=时间最新
                    }
                },
            }
            url = (
                SEARCH_URL
                + "?cb=jsonpCallback&param="
                + urllib.parse.quote(_json.dumps(param_json, ensure_ascii=False))
                + f"&_={int(time.time() * 1000)}"
            )

            # 发起请求; 网络异常/非200/空结果 → 按 MAX_PAGE_RETRIES 退避重试,
            # 仍失败才中断翻页(已拿到的结果仍返回)。
            # 背景: 东财 WAF 限流时返回 HTTP 200 但结果降级为空/仅 passportWeb,
            # 直接放弃会整批漏采, 因此 200+空结果同样走退避重试。
            data = None
            for _attempt in range(1, MAX_PAGE_RETRIES + 1):
                try:
                    resp = self._api.get(  # 【调用函数】GET 东财搜索 JSONP 接口(真实 Chromium 网络栈)
                        url, headers={"Referer": "https://so.eastmoney.com/"}, timeout=REQUEST_TIMEOUT_MS
                    )
                    data = _strip_jsonp(resp.text()) if resp.status == 200 else None  # 【调用函数】剥 JSONP 回调壳 → dict
                except Exception as e:
                    logger.warning(
                        f"  EastmoneyGuba request error for '{keyword}' page {page} (attempt {_attempt}/{MAX_PAGE_RETRIES}): {e}"
                    )
                    data = None
                if data is None:
                    time.sleep(RETRY_BACKOFF_S)  # 请求异常/非 JSONP → 退避后重试
                    continue
                # 结果列表位置: data.result.gubaArticleWeb (2026-08-25 实测)
                post_list = (data.get("result", {}) or {}).get("gubaArticleWeb") or []
                if post_list:
                    break  # 拿到帖子即成功
                # 200 但空结果: 疑似限流降级 → 退避后重试
                logger.warning(
                    f"  EastmoneyGuba empty page {page} for '{keyword}' (attempt {_attempt}/{MAX_PAGE_RETRIES}), backoff {RETRY_BACKOFF_S}s"
                )
                time.sleep(RETRY_BACKOFF_S)
            if data is None:
                break  # 非 JSONP / 被重定向到验证页时放弃本页

            # 结果列表位置: data.result.gubaArticleWeb (2026-08-25 实测)
            result = data.get("result", {}) or {}
            post_list = result.get("gubaArticleWeb") or []
            if not post_list:
                break  # 重试后仍无结果/已翻到底

            page_added = 0
            for raw in post_list:
                post_id = raw.get("id") or raw.get("post_id") or ""
                if not post_id:
                    continue
                if post_id in seen_ids:
                    continue  # 翻页/交叉时已见过的帖子直接跳过(去重)
                seen_ids.add(post_id)

                item = {
                    "post_id": str(post_id),
                    "id": str(post_id),  # 【关键】batch_collect 逐条循环用 item["id"] 取唯一ID(batch_collect.py:420), 缺了会整批被跳过
                    "title": raw.get("title") or "",
                    "content": raw.get("content") or "",
                    "publish_time": raw.get("createTime") or raw.get("post_publish_time") or "",
                    "url": raw.get("url") or "",
                    "short_name": raw.get("shortName") or "",
                    "inner_code": raw.get("innerCode") or "",
                    "_raw": raw,
                }
                items.append(item)
                page_added += 1

                if len(items) >= count:
                    break

            if page_added == 0:
                break  # 本页一条新帖都没加, 说明后续页大概率也无新内容

            page += 1
            # 页间延时(随机 1~3 秒), 避免触发限流
            time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

        logger.info(f"  EastmoneyGuba: {len(items)} posts for '{keyword}' ({page} pages)")
        return items

    # ============================================================
    # 详情 (搜索已含全文, 不需要)
    # ============================================================

    @property
    def needs_detail_fetch(self) -> bool:
        """是否需要逐条补详情？—— 不需要(搜索即含标题+正文摘要)。"""
        return False  # 东财搜索返回标题+正文; 作者/互动在搜索响应中无字段, v1 接受缺省

    # ============================================================
    # 归一化
    # ============================================================

    def normalize(self, raw_item: Any, detail: dict | None, keyword: str) -> dict | None:
        """股吧 item → 统一 Schema dict

        【功能】把东财股吧原生 item 转成下游统一格式的 dict。
        【参数】
            raw_item: search() 返回的股吧 item
            detail:   get_detail() 的返回值(本平台为 None)
            keyword:  本次搜索关键词
        【返回】对齐 UNIFIED_SCHEMA_FIELDS 的 dict; 无 post_id 时返回 None
        【关键逻辑】
        - 正文/标题去 <em> 高亮与 $合约标签$ 包裹符号;
        - 搜索响应无作者/点赞/评论字段 → author_name 缺省 "unknown", 互动计 0;
        - 时间 createTime 已是 "YYYY-MM-DD HH:MM:SS", 直接透传(兼容其他格式)。
        """
        post_id = raw_item.get("post_id", "")
        if not post_id:
            return None  # 缺 post_id 的帖子不可用, 丢弃

        title = _clean_html(raw_item.get("title", ""))
        desc = _clean_html(raw_item.get("content", ""))
        # 标题为空时截取正文前 60 字兜底; 再空则用品种名(short_name)兜底
        if not title and desc:
            title = desc[:60]
        if not title and raw_item.get("short_name"):
            title = raw_item.get("short_name")

        publish_time = _parse_guba_time(raw_item.get("publish_time", ""))
        post_url = raw_item.get("url") or ""
        if post_url.startswith("http://"):  # 统一升级为 https
            post_url = "https://" + post_url[len("http://"):]

        note_dict = {  # 【变量】统一 Schema 输出(键对齐 UNIFIED_SCHEMA_FIELDS)
            "platform": "eastmoney_guba",
            "note_id": f"{self.id_prefix}{post_id}",
            "title": title,
            "desc": desc,
            "author_name": "unknown",  # 搜索响应无作者字段, v1 缺省
            "author_id": "",
            "like_count": 0,  # 搜索响应无互动字段, v1 计 0
            "comment_count": 0,
            "collect_count": 0,  # 股吧无收藏概念
            "share_count": 0,
            "tags": [],
            "note_type": "post",
            "publish_time": publish_time,
            "ip_location": "",
            "keyword": keyword,
            "url": post_url or "https://guba.eastmoney.com/",
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
        """东财股吧异常分类, 供限流退避使用。

        【功能】根据异常消息判断错误类型。
        【参数】exc: 采集抛出的异常
        【返回】'rate_limit' | 'auth' | 'other'
        【关键逻辑】403/超时: 东财风控 → 限流退避; playwright 未装 → 凭证类错误。
        """
        msg = str(exc).lower()
        if "playwright" in msg and "installed" in msg:
            return "auth"
        if "403" in msg or "rate limit" in msg or "timed out" in msg or "timeout" in msg:
            return "rate_limit"
        return "other"

    # ============================================================
    # 字段映射 (文档)
    # ============================================================

    @staticmethod
    def field_mapping() -> dict:
        """返回股吧的"统一字段 ← 原始字段"映射表(文档用)。"""
        from .base import FIELD_MAPPING_TABLE  # 【调用包】取字段映射表(文档用)

        return FIELD_MAPPING_TABLE["eastmoney_guba"]  # 【调用函数】返回映射
