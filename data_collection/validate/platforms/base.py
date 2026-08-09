"""
平台适配器抽象基类 + 统一 Schema 定义
=====================================

每个平台的适配器继承 PlatformAdapter，实现:
    init()       — 加载凭证 / 建会话 / 启浏览器
    search()     — 关键词搜索 → 返回原生 item 列表
    get_detail() — (可选) 获取单条详情
    normalize()  — 原生 item → 统一 Schema dict

BatchCollector 只依赖 PlatformAdapter 接口，不感知平台实现细节。
"""

from abc import ABC, abstractmethod
from typing import Any

# ============================================================
# 统一输出 Schema 字段定义
# ============================================================

UNIFIED_SCHEMA_FIELDS = [
    # 标识
    "platform",  # str  — "xhs" | "weibo" | "zhihu"
    "note_id",  # str  — 平台内唯一 ID（新平台加前缀: "wb:{mid}", "zh:{type}:{id}"）
    "url",  # str  — 帖子公开链接
    # 内容
    "title",  # str  — 标题（微博无标题时截取正文前60字）
    "desc",  # str  — 正文/摘要
    "note_type",  # str  — "normal" | "video" | "weibo" | "answer" | "article"
    "tags",  # list[str] — 标签/话题
    # 作者
    "author_name",  # str
    "author_id",  # str
    # 互动 (缺省为 0)
    "like_count",  # int
    "comment_count",  # int
    "collect_count",  # int
    "share_count",  # int
    # 时间
    "publish_time",  # str  — "YYYY-MM-DD HH:MM:SS"
    # 地理位置
    "ip_location",  # str
    # 图片
    "image_urls",  # list[str]
    "image_count",  # int
    "is_video",  # bool
    # 元数据
    "keyword",  # str  — 搜索关键词
    "desc_length",  # int
]


# 平台 → 字段映射表（文档用途，供 normalize 实现参考）
# 格式: 统一字段 → 平台原始路径描述
FIELD_MAPPING_TABLE = {
    "xhs": {
        "platform": '"xhs"',
        "note_id": "item.id (24hex ObjectId)",
        "title": "note_card.title / display_title",
        "desc": "note_card.desc",
        "author_name": "user.nickname / nick_name",
        "author_id": "user.user_id",
        "like_count": "interact_info.liked_count",
        "comment_count": "interact_info.comment_count",
        "collect_count": "interact_info.collected_count",
        "share_count": "interact_info.share_count",
        "publish_time": "decode_objectid_timestamp(note_id)",
        "ip_location": "note_card.ip_location",
        "image_urls": "image_list[].url (info_list 可选)",
        "url": "xiaohongshu.com/explore/{id}",
    },
    "weibo": {
        "platform": '"weibo"',
        "note_id": '"wb:" + mblog.mid',
        "title": "text_raw 前60字（微博无标题）",
        "desc": "text_raw (去HTML) / longTextContent",
        "author_name": "user.screen_name",
        "author_id": "user.id",
        "like_count": "attitudes_count",
        "comment_count": "comments_count",
        "collect_count": "0 (无此概念)",
        "share_count": "reposts_count",
        "publish_time": "parse_created_at(created_at)",
        "ip_location": "region_name 去'发布于'前缀",
        "image_urls": "pics[].url",
        "url": "m.weibo.cn/detail/{mid}",
    },
    "zhihu": {
        "platform": '"zhihu"',
        "note_id": '"zh:answer:" + id 或 "zh:article:" + id',
        "title": "question.name / article.title",
        "desc": "excerpt / content (前2000字)",
        "author_name": "author.name",
        "author_id": "author.url_token",
        "like_count": "voteup_count (赞同)",
        "comment_count": "comment_count",
        "collect_count": "0",
        "share_count": "0",
        "publish_time": "created_time (unix ts) → datetime",
        "ip_location": '"" (不支持)',
        "image_urls": "content 内 img 标签提取",
        "url": "zhihu.com/question/{qid}/answer/{aid}",
    },
    "xueqiu": {
        "platform": '"xueqiu"',
        "note_id": '"xq:" + status.id',
        "title": "status.title 或 text 前60字",
        "desc": "status.description / status.text (去HTML)",
        "author_name": "user.screen_name",
        "author_id": "user.id",
        "author_fans": "user.followers_count",
        "like_count": "like_count",
        "comment_count": "reply_count",
        "collect_count": "0 (无此概念)",
        "share_count": "retweet_count",
        "publish_time": "created_at (ms ts) → datetime",
        "ip_location": '"" (不支持)',
        "image_urls": "pics[].url",
        "url": "xueqiu.com/{id} / target",
    },
}


class CredentialError(Exception):
    """凭证缺失/过期异常。消息应包含获取指引。"""

    pass


class PlatformAdapter(ABC):
    """
    平台适配器抽象基类。

    子类必须实现: init(), search(), normalize()
    子类可选覆盖: get_detail(), needs_detail_fetch, classify_error(), close()
    """

    name: str = ""  # 内部标识: "xhs" | "weibo" | "zhihu"
    display_name: str = ""  # 展示名: "小红书" | "微博" | "知乎"
    id_prefix: str = ""  # note_id 前缀（xhs 留空保持兼容，weibo="wb:", zhihu="zh:"）

    # ============================================================
    # 抽象方法（子类必须实现）
    # ============================================================

    @abstractmethod
    def init(self) -> None:
        """
        初始化平台连接。
        - 加载凭证 (Cookie / session / login state)
        - 建立 HTTP 会话 或 启动浏览器
        - 凭证缺失时 raise CredentialError
        """
        ...

    @abstractmethod
    def search(self, keyword: str, count: int) -> list[Any]:
        """
        关键词搜索，返回平台原生 item 列表。
        BatchCollector 将 item 视为不透明对象，随后传给 get_detail / normalize。

        Args:
            keyword: 搜索关键词
            count: 期望返回条数（可能返回略多于 count）

        Returns:
            平台原生 item 列表
        """
        ...

    @abstractmethod
    def normalize(self, raw_item: Any, detail: dict | None, keyword: str) -> dict | None:
        """
        将平台原生 item + 可选详情 → 统一 Schema dict。

        Args:
            raw_item: search() 返回的原始 item
            detail: get_detail() 的返回值（若 needs_detail_fetch=True），否则 None
            keyword: 本次搜索关键词

        Returns:
            统一 Schema dict，字段对齐 UNIFIED_SCHEMA_FIELDS；
            返回 None 表示丢弃此条（如广告、非内容卡片）
        """
        ...

    # ============================================================
    # 可选覆盖
    # ============================================================

    def get_detail(self, raw_item: Any) -> dict | None:
        """
        获取单条详情。默认返回 None（该平台搜索已含全文时不需要）。
        微博: 搜索返回短文全文，长文才调 extend API。
        """
        return None

    @property
    def needs_detail_fetch(self) -> bool:
        """是否需要为每条结果调用 get_detail？
        - True:  小红书（搜索返回摘要，详情有完整字段）
        - False: 微博（搜索直接返回全文+互动）
        """
        return True

    def classify_error(self, exc: Exception) -> str:
        """
        异常分类，供 RateLimiter 自适应退避。
        Returns: 'rate_limit' | 'auth' | 'other'
        """
        return "other"

    def close(self) -> None:  # noqa: B027  intentional no-op default for adapters without resources
        """释放资源（关闭浏览器/Session）。默认无操作，子类按需覆盖。"""
        pass
