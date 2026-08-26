"""
平台适配器抽象基类 + 统一 Schema 定义
=====================================

【模块角色】
本模块位于"情绪数据生产链"的最底层，定义了两样东西：
1. PlatformAdapter 抽象基类 —— 所有平台适配器(微博/知乎/小红书/雪球)的"接口契约"；
2. UNIFIED_SCHEMA_FIELDS —— 所有平台归一化后的"统一数据格式"。

BatchCollector(批量采集器)只依赖本模块定义的 PlatformAdapter 接口，
不感知具体平台实现细节。新增一个平台时，只需继承 PlatformAdapter 并实现接口。

每个平台适配器继承 PlatformAdapter，实现:
    init()       — 加载凭证 / 建会话 / 启浏览器
    search()     — 关键词搜索 → 返回原生 item 列表
    get_detail() — (可选) 获取单条详情
    normalize()  — 原生 item → 统一 Schema dict

【采集生命周期】(由 BatchCollector 驱动)
    1. adapter.init()                 建立连接(加载Cookie/启动浏览器)
    2. adapter.search(keyword, count) 按关键词搜索，得到"平台原生条目"
    3. 若 needs_detail_fetch=True，逐条调 get_detail() 补全详情
    4. 逐条调 normalize() 转成统一 Schema dict，写入 JSONL
    5. 全部结束后调 adapter.close() 释放资源
"""

from abc import ABC, abstractmethod  # 【调用包】抽象基类与抽象方法(定义适配器接口契约)
from typing import Any  # 【调用包】类型注解(原生 item 作为不透明对象)

# ============================================================
# 统一输出 Schema 字段定义
# 说明: 各平台 normalize() 产出的 dict 都必须包含下列键，顺序不限。
# 下游 NER / 情感分析 / 多模态只读取这些键，这是跨平台数据的"标准格式"。
# 字段注释中标注了该字段的数据类型与缺失时的默认值约定。
# ============================================================

UNIFIED_SCHEMA_FIELDS = [  # 【变量】统一输出 Schema 字段清单(下游 NER/情感只认这些键)
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
# 用途: 帮助零基础读者对照"统一字段 ← 平台原始字段"的映射关系，
#       也是新增平台时编写 normalize() 的参照模板。
# 注: 这只是文档，真正取值逻辑在各自 normalize() 里实现，勿在此表直接取数。
FIELD_MAPPING_TABLE = {  # 【变量】"平台 → 统一字段←平台原始字段"映射文档表
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
    "eastmoney_guba": {
        "platform": '"eastmoney_guba"',
        "note_id": '"emg:" + id (搜索接口 gubaArticleWeb 主键)',
        "title": "title (去<em>高亮)",
        "desc": "content (去HTML + 剥 $合约标签$ 包裹符号)",
        "author_name": '"unknown" (搜索响应无作者字段, v1 缺省)',
        "author_id": '"" (同上)',
        "like_count": "0 (搜索响应无互动字段, v1 缺省)",
        "comment_count": "0 (同上)",
        "collect_count": "0 (无此概念)",
        "share_count": "0",
        "publish_time": "createTime (已是 YYYY-MM-DD HH:MM:SS)",
        "ip_location": '"" (不支持)',
        "image_urls": "[] (无此字段)",
        "url": "url (http→https 升级)",
    },
}


class CredentialError(Exception):
    """凭证缺失/过期异常。消息应包含获取指引。"""

    pass


class PlatformAdapter(ABC):
    """
    平台适配器抽象基类 (接口契约)。

    【接口约定】
    子类必须实现 (abstractmethod，不实现会报错):
        init()      — 初始化平台连接 (加载凭证/建会话/启浏览器)
        search()    — 关键词搜索 → 返回平台原生 item 列表
        normalize() — 原生 item → 统一 Schema dict

    子类可选覆盖 (基类提供默认实现):
        get_detail()        — 获取单条详情 (默认返回 None，搜索已含全文时无需覆盖)
        needs_detail_fetch  — 是否需要逐条调 get_detail (默认 True)
        classify_error()    — 异常分类，供限流退避决策 (默认 'other')
        close()             — 释放浏览器/会话等资源 (默认空操作)

    【类属性约定】(子类必须给赋值)
        name         内部标识: "xhs" | "weibo" | "zhihu" | "xueqiu"
        display_name 展示名:  "小红书" | "微博" | "知乎" | "雪球"
        id_prefix    note_id 前缀, 保证跨平台 ID 不冲突

    【生命周期】由 BatchCollector 驱动:
        init → (search → get_detail → normalize) × N 个关键词 → close
    """

    name: str = ""  # 内部标识: "xhs" | "weibo" | "zhihu" | "xueqiu"
    display_name: str = ""  # 展示名: "小红书" | "微博" | "知乎" | "雪球"
    id_prefix: str = ""  # note_id 前缀（xhs 留空保持兼容，weibo="wb:", zhihu="zh:", xueqiu="xq:"）

    # ============================================================
    # 抽象方法（子类必须实现）
    # ============================================================

    @abstractmethod
    def init(self) -> None:
        """
        【功能】初始化平台连接，为后续 search / get_detail 做好准备。
        【参数】无
        【返回】None
        【关键逻辑】
        - 加载凭证 (Cookie / session / login state)
        - 建立 HTTP 会话 或 启动浏览器
        - 凭证缺失或过期时 raise CredentialError，错误消息应包含获取指引
        """
        ...

    @abstractmethod
    def search(self, keyword: str, count: int) -> list[Any]:
        """
        【功能】按关键词搜索，返回平台原生 item 列表。
        【参数】
            keyword: 搜索关键词
            count: 期望返回条数（可能返回略多于 count，由实现自行翻页补齐）
        【返回】平台原生 item 列表
        【关键逻辑】
        BatchCollector 将 item 视为"不透明对象"，不解析其内部结构，
        只会原样传给 get_detail / normalize。因此各平台 item 结构可以完全不同。
        """
        ...

    @abstractmethod
    def normalize(self, raw_item: Any, detail: dict | None, keyword: str) -> dict | None:
        """
        【功能】将"平台原生 item + 可选详情"转换为统一 Schema dict。
        【参数】
            raw_item: search() 返回的原始 item
            detail:   get_detail() 的返回值（若 needs_detail_fetch=True），否则 None
            keyword:  本次搜索关键词
        【返回】
            统一 Schema dict，字段须对齐 UNIFIED_SCHEMA_FIELDS；
            返回 None 表示丢弃此条（如广告、非内容卡片）
        【关键逻辑】字段名必须与 UNIFIED_SCHEMA_FIELDS 一致，
        下游 NER/情感分析只认这套字段，不感知任何平台差异。
        """
        ...

    # ============================================================
    # 可选覆盖
    # ============================================================

    def get_detail(self, raw_item: Any) -> dict | None:
        """
        【功能】获取单条详情，补充搜索摘要里缺失的完整字段。
        【参数】raw_item: search() 返回的平台原生 item
        【返回】详情 dict，结构由各平台自定义；无详情时返回 None
        【关键逻辑】
        默认返回 None（该平台搜索已含全文时不需要覆盖此方法）。
        微博: 搜索返回短文全文，仅长文(isLongText)才调 extend API。
        """
        return None

    @property
    def needs_detail_fetch(self) -> bool:
        """是否需要为每条结果调用 get_detail？
        - True:  小红书（搜索返回摘要，详情有完整字段）
        - False: 微博（搜索直接返回全文+互动）

        【关键逻辑】该开关决定 BatchCollector 是否对每条结果逐条调 get_detail()。
        True 时每条结果多一次网络/浏览器请求，采集耗时显著变长，
        因此"搜索已含全文"的平台应覆盖为 False 以省时省流量。
        """
        return True

    def classify_error(self, exc: Exception) -> str:
        """
        【功能】对采集过程中的异常做分类，供 RateLimiter 自适应退避。
        【参数】exc: 采集过程中抛出的异常对象
        【返回】字符串分类: 'rate_limit' | 'auth' | 'other'
        【关键逻辑】各平台错误码/消息不同，子类需覆盖本方法做针对性识别。
        - 'rate_limit': 触发限流退避（降低请求频率）
        - 'auth':       凭证失效，可能需要重新登录
        - 'other':      其他错误，按普通错误处理
        """
        return "other"

    def close(self) -> None:  # noqa: B027  intentional no-op default for adapters without resources
        """释放资源（关闭浏览器/Session）。默认无操作，子类按需覆盖。

        【功能】释放浏览器 / HTTP Session 等占用的系统资源。
        【参数】无 【返回】None
        【关键逻辑】Playwright 类适配器必须在此关闭浏览器并 stop()，
        否则 Chromium 进程会残留，导致脚本无法正常退出。
        # noqa: B027 表示"空方法默认实现"是有意为之，忽略静态检查告警。
        """
        pass
