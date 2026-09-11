"""research_collector_htfc.py — 华泰期货官方天玑研报自动接入(能化 21 品种 日报+周报)

【模块角色】
  每天开盘前从华泰期货官方"天玑"平台(ent.htfc.com)拉取 21 个目标品种
  (能化板块 19 码 + 碳酸锂 LC + 液化石油气 PG)的当日研报日报与近一周周报,
  写入本机研报库并复用 web_app._process_research_report 的 LLM 提取链路
  (结构化 + 观点 → research_reports 表 + 按品种聚合 JSON)。这是 fxbaogao 上
  "华泰期货"刮取源被下架后,改走的官方数据源(正文全文、无需 PDF 下载,
  质量与完整性远高于刮取页)。

  由两条路径触发:
    1. scheduler.py 每日定时子进程(与 fxbaogao 采集同一 research_times 时刻,
       额外注册 research_htfc_{time} job;timeout 90 分钟)。
    2. 手动 CLI(见"用法")。

【采集方式】
  天玑接口(skill htfc-research-report-skill 的 API,见 htfc_api.py):
    - 栏目 日报 = /bus/report/specificList?item_value=10074
      · 实测 pageSize 有效、curPage 恒返第 1 页(后端不分页)⇒ 用大 pageSize
        一次拉取(默认 100,覆盖当日全部 ~36 篇),取 publishDateTime 当日项。
    - 栏目 周报 = /bus/report/specificList?item_value=10075(2026-09-07 实测)
      · 周报集中在周日/周一发布 ⇒ 当日过滤必漏(周一跑漏掉周末的),
        故用 WEEKLY_WINDOW_DAYS=10 回看窗口;跨次重跑由 seen 状态去重兜底。
      · 实测 totalRows 19211,reportType 字段值='周报'。
    - 列表项字段(两频道一致):id(RE…)、itemValue(10070)、reportType
      (日报/周报)、publishDateTime、subclassCodeName(中文品种,逗号分隔,可多品种)。
    - 详情 = /bus/report/reportInfo?articleId&itemValue=10070
      · content 为完整 HTML 正文(可见文本 ~0.8k-2.4k 字符/日报)。

【品种选择(21 码)】
  TARGET_VARIETIES = 能化 19(TA MA FG BU SA EB V PP L EG PX UR PF SC LU FU
  RU NR SH)+ LC + PG。subclassCodeName 按 token 精确匹配(拆 [，,、;；/] 及空白),
  标题作兜底(子串;拉丁别名词边界,挡 "FU" 命中 English "Futures")。
  日报(当日)与周报(近 10 天)**独立**逐品种挑最新一篇 —— 同一品种可同时进
  日报与周报两篇(分别喂饱每日总结与周报总结两种口径),多品种报告被首个
  品种认领后进 union,按 articleId 去重。

【正文过短剔除】
  去标签后正文 <200 字符(简讯/仅标题类)跳过不入库——不产生 error 行。

【增量去重】
  状态文件 ~/.tradingagents/htfc_collector_state.json:{seen:[articleId…], last_run}。
  seen 记最近 1000 篇(成功/失败/过短都在内);同日重跑命中全在 seen ⇒ no-op。
  跨日由"日期==目标日期"(日报)/"10 天窗口"(周报)天然隔离,seen 兜底防
  周报窗口内重复接(不用水位,因为天玑不分页拉不全历史)。

  用法:
    python research_collector_htfc.py                    # 接今天 21 码(日报+周报)
    python research_collector_htfc.py --date 2026-09-02  # 指定日期(回补)
    python research_collector_htfc.py --dry-run          # 只打印命中不写库
    python research_collector_htfc.py --variety SC BU    # 只接指定品种(测试用)
    python research_collector_htfc.py --channel 周报     # 只接周报频道(缺省=日报+周报)
    python research_collector_htfc.py --reset-state      # 清 seen(强制重跑)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from concurrent.futures import (
    ThreadPoolExecutor,  # 【调用包】线程池(多篇并行处理,LLM 同步调用是大头)
)
from datetime import datetime
from html import unescape  # 【调用函数】HTML 实体还原(&amp;/&nbsp;/&lt;)
from pathlib import Path

# ── 常量 ────────────────────────────────────────────────────────────────

SOURCE_ORG = "华泰期货"  # 【变量】机构名(研报库目录 + source 归位)
SOURCE_LABEL = "华泰期货-天玑"  # 【变量】入库 source(区别于 fxbaogao 时代的"发现报告-华泰期货")
FEED_CHANNEL_DAILY = "10074"  # 【变量】天玑 栏目 日报 的 item_value(实测)
FEED_CHANNEL_WEEKLY = "10075"  # 【变量】天玑 栏目 周报 的 item_value(ptypes_v2 articleTypeList,2026-09-07 实测)
FEED_ITEM_VALUE = FEED_CHANNEL_DAILY  # 【变量】兼容别名(历史外部引用=日报频道)
WEEKLY_WINDOW_DAYS = 10  # 【变量】周报回看窗口(天):周报集中在周日/周一发布,"上周日→本周一"最大跨 8 天,窗口 7 会漏 → 10 天留裕量;seen 去重 + 每品种只取最新,放大无副作用
DETAIL_ITEM_DEFAULT = "10070"  # 【变量】详情默认 itemValue(实测列表项即为此值,两频道一致)
PAGE_SIZE = 100  # 【变量】列表单次拉取条数(实测 curPage 不分页,大 pageSize 才覆盖当日)
MIN_BODY_CHARS = 200  # 【变量】正文最小可见字符数(去标签后),低于则跳过不入库
MAX_SEEN = 1000  # 【变量】状态文件 seen 列表上限(滚动丢弃最旧)
PARALLEL_WORKERS = 4  # 【变量】并行处理线程数(LLM 账户并发上限 5,每篇同时只挂 1 个调用,4 并发安全;21 篇 ~15min)
_STATE_LOCK = threading.Lock()  # 【变量】状态文件落盘互斥锁(并行线程逐篇 persist)

# 【变量】目标品种代码:2026-09-09 起统一引用 ACTIVE_VARIETIES(20 品种池,与
# GTJA/全项目同一口径),不再各自手抄清单;华泰不发池内部分品种时自然无数据。
from tradingagents.dataflows.commodity_futures import (  # noqa: E402  # 【调用包】活跃品种池
    ACTIVE_VARIETIES,
)

TARGET_VARIETIES = tuple(sorted(ACTIVE_VARIETIES))

# 【变量】代码 → 命中别名(匹配用;拉丁别名词边界,中文子串)。
# subclassCodeName 拆 token 后精确命中优先,标题兜底用同一张表。
CODE_ALIASES = {
    "TA": ("PTA", "TA", "对苯二甲酸"),
    "MA": ("甲醇", "MA"),
    "FG": ("玻璃", "FG"),
    "BU": ("沥青", "BU", "石油沥青"),
    "SA": ("纯碱", "SA", "重碱"),
    "EB": ("苯乙烯", "EB"),
    "BZ": ("纯苯", "石油苯", "加氢苯", "BZ"),
    "BR": ("丁二烯橡胶", "顺丁橡胶", "BR"),
    "V": ("PVC", "V", "聚氯乙烯"),
    "PP": ("聚丙烯", "PP"),
    "L": ("塑料", "聚乙烯", "L"),
    "EG": ("乙二醇", "EG"),
    "PX": ("对二甲苯", "PX"),
    "UR": ("尿素", "UR"),
    "PF": ("短纤", "PF", "瓶片"),
    "SC": ("原油", "SC", "SC原油"),
    "LU": ("低硫燃料油", "低硫燃油", "LU", "低硫"),
    "FU": ("燃料油", "高硫燃料油", "高硫燃油", "燃油", "FU"),
    "RU": ("橡胶", "天然橡胶", "RU"),
    "NR": ("20号胶", "20号", "NR"),
    "SH": ("烧碱", "SH"),
    "LC": ("碳酸锂", "LC"),
    "PS": ("多晶硅", "硅料", "PS"),
    "SI": ("工业硅", "SI"),
    "M": ("豆粕", "M"),
    "CF": ("棉花", "CF", "郑棉"),
    "CJ": ("红枣", "CJ"),
    "LH": ("生猪", "LH", "猪价"),
    "PG": ("LPG", "液化石油气", "PG"),
}

# 【变量】长别名遮蔽串(标题兜底用):子串重叠会误命中——'合成橡胶'是泛称(BR 走
# 专用别名'丁二烯橡胶/顺丁橡胶',泛称仅屏蔽防误中 '橡胶'(RU)),'低硫燃料油' 含
# '燃料油'(FU)。标题匹配前先把这些长 token 认出归位(归其所属码/非目标则仅屏蔽),
# 再从标题里抹掉,挡短别名的子串 false positive。
_OVERLAP_MASKS = ("合成橡胶", "低硫燃料油", "高硫燃料油", "低硫燃油", "高硫燃油")


def _overlap_code(long_alias: str) -> str | None:
    """长别名归属的代码;非目标长别名(如合成橡胶=BR)返回 None(仅屏蔽)。"""
    for code, aliases in CODE_ALIASES.items():
        if long_alias in aliases:
            return code
    return None

_STATE_DIR = Path.home() / ".tradingagents"  # 【变量】状态/数据目录(与 RESEARCH_UPLOAD_DIR 同根)
STATE_FILE = _STATE_DIR / "htfc_collector_state.json"  # 【变量】状态文件:{seen: [...], last_run}


# ── 状态读写 ────────────────────────────────────────────────────────────

def _load_state() -> dict:
    """读状态文件;无文件/损坏返回空 dict(视为首次运行)。"""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_state(state: dict):
    """写状态文件(目录不存在则创建;seen 超上限滚动丢弃最旧)。"""
    seen = state.get("seen") or []
    if len(seen) > MAX_SEEN:
        state["seen"] = seen[-MAX_SEEN:]
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 天玑 API 封装(轻包装 htfc_api,统一 RuntimeError) ──────────────────

def _fetch_channel_items(item_value: str, date_pred) -> list[dict]:
    """拉单频道列表并按日期谓词过滤(日报/周报共用的底层)。

    【参数】item_value: 栏目 id(10074 日报 / 10075 周报);date_pred: pub(YYYY-MM-DD)→bool。
    【返回】[{id, itemValue, reportType, publishDateTime, subclassCodeName, title}] 命中项。
    【关键逻辑】curPage 恒返第 1 页(后端不分页),故用 PAGE_SIZE=100 单次拉取。
    """
    import htfc_api  # 【调用包】vendored 天玑 API 客户端(懒导入)

    resp = htfc_api.search_reports(item_value, cur_page=1, page_size=PAGE_SIZE)
    data = htfc_api.data_of(resp) or {}
    result = data.get("resultList") or []
    items = []
    for it in result:
        if not isinstance(it, dict):
            continue
        pub = str(it.get("publishDateTime") or "")[:10]
        if date_pred(pub) and it.get("id") and it.get("title"):
            items.append({
                "id": str(it["id"]),
                "itemValue": str(it.get("itemValue") or DETAIL_ITEM_DEFAULT),
                "reportType": it.get("reportType") or "",
                "publishDateTime": it.get("publishDateTime") or "",
                "subclassCodeName": it.get("subclassCodeName") or "",
                "title": str(it.get("title") or "").strip(),
            })
    return items


def fetch_today_items(target_date: str) -> list[dict]:
    """拉目标日期当天的日报列表(日报频道,当日精确过滤)。

    【参数】target_date: "YYYY-MM-DD"。
    【返回】当日项列表(同 _fetch_channel_items 字段)。
    """
    return _fetch_channel_items(FEED_CHANNEL_DAILY, lambda pub: pub == target_date)


def fetch_weekly_items(target_date: str, window_days: int = WEEKLY_WINDOW_DAYS) -> list[dict]:
    """拉周报频道最近 window_days 天(含目标日期当日)的列表。

    【参数】target_date: "YYYY-MM-DD";window_days: 回看窗口天数(默认 10)。
    【返回】窗口内项列表(同 _fetch_channel_items 字段,列表 API 最新在前)。
    【关键逻辑】周报集中在周日/周一发布,若只过滤"当日"则周一跑必漏掉周末的
              周报 ⇒ 窗口 [target_date-(window_days-1), target_date] 闭区间;
              跨次重跑的重复接由 seen 状态(articleId)去重兜底,窗口放大无副作用。
    """
    end = datetime.strptime(target_date, "%Y-%m-%d").date()
    start = end.fromordinal(end.toordinal() - (window_days - 1))
    start_s, end_s = start.isoformat(), end.isoformat()
    return _fetch_channel_items(
        FEED_CHANNEL_WEEKLY, lambda pub: start_s <= pub <= end_s
    )


# ── HTML → 文本 ─────────────────────────────────────────────────────────

# 【变量】块级标签:这些标签前后的内容确实分行,替换为换行。
_BLOCK_TAGS = {
    "div", "p", "br", "hr", "tr", "table", "thead", "tbody", "tfoot",
    "ul", "ol", "dl", "dt", "dd", "blockquote", "section", "article",
    "header", "footer", "nav", "aside", "main", "figure", "figcaption",
    "h1", "h2", "h3", "h4", "h5", "h6", "pre", "li",
}
# 【变量】内联标签:只修饰文字、不构成分行(span/font/b/em/strong/a…),直接丢弃。
#  天玑正文里每个数字都被 <span data-type="num"> 单独包一层,若把 </span> 当换行,
#  会把 "动力煤775元/吨(+20)" 拆成 4 行,LLM 读到的数字全是碎片。
_INLINE_TAGS = {
    "span", "font", "b", "i", "em", "strong", "u", "s", "strike", "del",
    "ins", "a", "sub", "sup", "code", "small", "mark", "label", "abbr",
    "cite", "q", "time", "var", "samp", "kbd", "bdi", "bdo", "ruby",
    "rt", "rp", "img", "input", "button", "select", "textarea",
}

# 【变量】标签正则:捕获 <…> 整体,交给 _replace_tag 按标签名决定替换成什么。
_TAG_RE = re.compile(r"<[^>]+>")


def _replace_tag(match: re.Match[str]) -> str:
    """单个标签 → 换行/空串/空格,由标签名决定(块级换行,内联丢弃,未知补空格)。

    【参数】match:<…> 正则匹配对象。
    【返回】替换字符串。
    """
    inner = match.group(0)[1:-1].strip()
    if inner.startswith("!"):  # 【分支】注释 <!----> / <!DOCTYPE> 直接丢弃
        return ""
    name = re.match(r"/?\s*([a-zA-Z][a-zA-Z0-9]*)", inner)
    if not name:
        return ""
    tag = name.group(1).lower()
    if tag in _BLOCK_TAGS:
        return "\n"
    if tag in _INLINE_TAGS:
        return ""  # 【分支】内联标签绝不插换行,否则数字被拆散
    return " "  # 【分支】未知标签(含 td/th 等)按空格处理,保守不拆行


def html_to_text(html: str) -> str:
    """HTML → 可见文本(剥 script/style/标签,块级标签换行,内联标签保留同行)。

    【关键】天玑正文用 <span data-type="num"> 把每个数字单独包一层,若所有标签
    一律替换为换行,"动力煤775元/吨(+20)" 会被拆成 4 行。故按标签名区分处理。
    """
    if not html:
        return ""
    html = re.sub(r"(?is)<script.*?</script>", " ", html)
    html = re.sub(r"(?is)<style.*?</style>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    text = _TAG_RE.sub(_replace_tag, html)
    text = unescape(text)  # 【调用函数】实体还原(&amp;/&nbsp;/&lt; 等)
    # 【清洗】同行内合并多余空白(内联标签丢弃后可能留下连续空格),但保留换行
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines)


# ── 品种匹配 ────────────────────────────────────────────────────────────

def _match_tokens(subclass_code_name: str) -> set[str]:
    """从 subclassCodeName 拆 token 精确匹配代码(主路径)。

    【关键逻辑】天玑 subclassCodeName 用 [，,、/;；] 分隔多品种中文名
    (如 '燃油,低硫燃油' / '橡胶,合成橡胶,20号胶');token 与别名精确
    相等才命中,避免 '低硫燃油' 被 '燃油' 误拉成 FU。
    """
    hits: set[str] = set()
    if not subclass_code_name:
        return hits
    for token in re.split(r"[，,、;；/\s]+", subclass_code_name):
        token = token.strip()
        if not token:
            continue
        for code, aliases in CODE_ALIASES.items():
            if token in aliases:
                hits.add(code)
    return hits


def _match_title(title: str) -> set[str]:
    """标题兜底匹配(别名子串;拉丁别名词边界,防 'FU' 命中 'Futures')。

    【关键逻辑】只处理 subclassCodeName 为空/未命中时。中文别名子串即可;
    拉丁别名(SC/TA/FU 等 2-3 字母)需词边界,避免出现在英文单词内。
    """
    hits: set[str] = set()
    if not title:
        return hits
    masked = title
    for long_alias in sorted(_OVERLAP_MASKS, key=len, reverse=True):
        # 【关键逻辑】长别名优先:认出即归位(所属码),再从标题抹掉,防短别名
        # (RU 的 '橡胶' / FU 的 '燃料油')子串命中到长别名里造成误标。
        if long_alias in title:
            code = _overlap_code(long_alias)
            if code:
                hits.add(code)
            masked = masked.replace(long_alias, "_")
    for code, aliases in CODE_ALIASES.items():
        for alias in aliases:
            if not alias:
                continue
            if alias.isascii() and alias.isalnum():
                # 拉丁别名(SC/TA/FU 等 2-3 字母)词边界,防 'FU' 命中 English 'Futures'
                if re.search(rf"\b{re.escape(alias)}\b", masked, re.I):
                    hits.add(code)
            elif alias in masked:
                hits.add(code)
    return hits


def match_codes(item: dict) -> set[str]:
    """判定一条列表项命中哪些目标品种代码。

    【返回】命中代码集合;subclassCodeName 精确匹配为主,标题兜底为辅。
    """
    hits = _match_tokens(item.get("subclassCodeName") or "")
    if not hits:
        hits = _match_title(item.get("title") or "")
    return {c for c in hits if c in TARGET_VARIETIES}


def select_today_items(items: list[dict], requested: set[str] | None = None) -> list[dict]:
    """当日日报逐品种挑最新一篇,多品种报告首次命中即入 union,按 id 去重。

    【参数】items: fetch_today_items 当日项(最新在前);requested: 只处理这些代码
            (None=全部 21)。
    【返回】[item] 覆盖目标品种的最少报告集合(≤21),item 附 codes 命中标注。
    【关键逻辑】items 已按发布时间最新在前;遍历维护 covered 集合,首个覆盖某
              品种的报告即最新一篇;同一篇覆盖多个品种(如玻碱合金)一并记账。
    """
    want = set(TARGET_VARIETIES if requested is None else requested)
    covered: set[str] = set()
    chosen: list[dict] = []
    for it in items:
        codes = match_codes(it)
        new = codes & want - covered
        if not new:
            continue
        chosen.append({**it, "codes": sorted(codes)})
        covered |= codes
        if want <= covered:
            break
    return chosen


# ── 入库(写文件 + 落库 + 触发 LLM 处理,镜像 research_collector._ingest_one) ─

def _sanitize_filename(name: str) -> str:
    """文件名安全化:非法字符替换为下划线,截断到 60 字符。"""
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_")
    return safe[:60] or "report"


def _ingest_one(item: dict, body: str, dry_run: bool = False) -> bool:
    """把一篇日报写入研报库并触发 LLM 处理。

    【参数】item: 列表项(+codes);body: 去标签后的正文文本。
    【返回】True=成功/已入库;False=正文过短/入库失败(print 原因)。
    """
    body = (body or "").strip()
    if len(body) < MIN_BODY_CHARS:
        print(f"    ! {item['id']} {item['title'][:40]} 正文过短({len(body)}字符),跳过")
        return False

    from database import get_db  # 【调用包】数据库实例(落 research_reports 表)
    from web_app import (  # 【调用包】研报存储目录 + 后台处理函数
        RESEARCH_UPLOAD_DIR,
        _process_research_report,
    )

    upload_dir = RESEARCH_UPLOAD_DIR / SOURCE_ORG  # 【变量】机构子目录
    upload_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{item['id']}_{_sanitize_filename(item['title'])}.md"  # 【变量】本地文件名
    file_path = upload_dir / fname
    db = get_db()
    existing = db.get_research_report_by_filename(file_path.name)
    md = (
        f"# {item['title']}\n"
        f"- 来源: {SOURCE_LABEL}\n"
        f"- 日期: {item.get('publishDateTime', '')}\n\n"
        f"{body}\n"
    )
    file_path.write_text(md, encoding="utf-8")  # 覆盖写:孤儿/上次半成品一律补齐正文
    if existing and existing.get("status") != "processing":
        # 【幂等】以 DB 行为准(文件名前缀即 articleId):只有真插入过才算已入库;
        # 崩溃若只留下孤儿文件(未 insert)会落入下方 insert 分支,不丢当日该篇。
        print(f"    = {item['id']} {item['title'][:50]} 已在研报库(status={existing['status']}),跳过(幂等)")
        return False
    if existing:
        # 【自愈】status=='processing' 说明上次进程在 LLM 处理中途崩溃残留:
        # 复用该行重跑处理(不重复 insert,避免行/聚合重复),否则观点永久缺失。
        print(f"    ~ {item['id']} {item['title'][:50]} 上次卡在 processing,复用该行重跑处理")
        report_id = existing["id"]
    else:
        report_id = db.insert_research_report(
            variety="",
            title=item["title"],
            source=SOURCE_LABEL,
            filename=file_path.name,
            file_path=str(file_path),
            ingest_source="auto",  # 【来源】华泰天玑自动采集入库(数据仓库"研报库"徽标=自动)
            publish_date=str(item.get("publishDateTime") or "")[:10],  # 【发布日期】接口自带,与目标日期一致
            report_type=str(item.get("reportType") or "").strip(),  # 【类型】接口 reportType 字段现成(日报/周报,直接接前端总结口径)
        )
    _process_research_report(report_id)  # 【调用函数】复用 web_app 后台处理(LLM 提取 → 落库 → 写聚合)
    print(f"    + {item['id']} {item['title'][:50]} -> report_id={report_id}")
    return True


def ingest_today(target_date: str, requested: set[str] | None = None, dry_run: bool = False,
                 reset_state: bool = False, channels: set[str] | None = None) -> dict:
    """接入目标日期的目标品种研报(日报当日 + 周报近 10 天,两频道独立选品)。

    【参数】channels: 只接这些栏目({"日报"}/{"周报"});None=日报+周报都接。
    【返回】{"collected", "processed", "skipped", "errors", "items"}。
    【关键逻辑】1) 两频道各自拉列表、各自逐品种挑最新一篇(同品种日报/周报
              两篇都会进——分别喂每日总结与周报总结口径);2) seen 过滤(已处理/
              已跳过不再碰,含周报窗口内的跨次重复);3) 逐篇拉详情,正文过短
              跳过;4) 写库 + LLM;5) 无论成败都进 seen 且**逐篇落盘状态**(进程中断
              也能续跑不丢进度;入库幂等以"研报库是否已有该文件名"为准——见
              _ingest_one 对 processing 残留的自愈)。同行同品种重跑需 --reset-state。
              6) 并行:线程池(PARALLEL_WORKERS=4)同时处理多篇(每篇大头是 LLM 同步
              调用;LLM 账户并发上限 5,4 并发安全)。聚合 JSON 写盘由
              research_data._FILE_LOCK 互斥;状态文件落盘由 _STATE_LOCK 互斥。
    """
    state = {} if reset_state else _load_state()
    seen = set(state.get("seen") or [])
    want = channels or {"日报", "周报"}  # 【变量】本次要接的栏目集合(缺省两频道都接)

    chosen: list[dict] = []  # 【变量】两频道独立选品的并集(按 id 去重)
    parts: list[str] = []  # 【变量】日志分频道统计片段
    if "日报" in want:
        items_daily = fetch_today_items(target_date)
        picked = select_today_items(items_daily, requested=requested)
        chosen += picked
        parts.append(f"当日日报 {len(items_daily)} 篇→命中 {len(picked)}")
    if "周报" in want:
        items_weekly = fetch_weekly_items(target_date)
        picked = select_today_items(items_weekly, requested=requested)
        chosen += picked
        parts.append(f"周报(近{WEEKLY_WINDOW_DAYS}天) {len(items_weekly)} 篇→命中 {len(picked)}")
    # 【防御】articleId 若跨频道重复(理论不可能,防御兜底)只保留首个
    uniq: dict[str, dict] = {}
    for it in chosen:
        uniq.setdefault(it["id"], it)
    chosen = list(uniq.values())

    covered = sorted({c for it in chosen for c in it.get("codes", [])})  # 【变量】覆盖品种并集
    print(f"[{SOURCE_ORG}] {target_date} " + ";".join(parts)
          + f",覆盖品种 {covered}")

    work = [it for it in chosen if it["id"] not in seen]  # 【变量】过滤 seen 后待处理列表
    collected = len(work)
    processed = skipped = 0
    errors: list[str] = []
    new_seen = set(seen)

    if dry_run:
        for item in work:
            print(f"  [DRY] {item['id']} [{item.get('reportType') or '?'}|{','.join(item['codes'])}] {item['title'][:50]}")
    else:
        # 【崩溃续跑】逐篇落盘状态(线程安全):进程中断/被杀也不丢已处理进度,重跑只接余量
        def _persist_seen(item_id):
            with _STATE_LOCK:
                new_seen.add(item_id)
                state["seen"] = sorted(new_seen)[-MAX_SEEN:]
                state["last_run"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                _save_state(state)

        def _handle(item):
            """单篇完整处理:拉详情 → 正文 → 入库 + LLM;返回 (item, ok, err)。"""
            try:
                import htfc_api  # 【调用包】天玑 API(懒导入)
                detail = htfc_api.data_of(htfc_api.get_report_info(item["id"], item["itemValue"]))
                body = html_to_text(detail.get("content") or "")
                return item, _ingest_one(item, body), None
            except Exception as e:  # 【异常】单篇失败:记录并继续,不影响其余
                return item, False, str(e)

        with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as pool:
            for item, ok, err in pool.map(_handle, work):
                if err:
                    errors.append(f"{item['id']}: {err}")
                    print(f"    ! {item['id']} 处理异常: {err}")
                elif ok:
                    processed += 1
                else:
                    skipped += 1
                _persist_seen(item["id"])

    print(f"Collected: {collected}")
    print(f"Processed: {processed}")
    return {"collected": collected, "processed": processed, "skipped": skipped,
            "errors": errors, "items": len(chosen)}


# ── CLI ─────────────────────────────────────────────────────────────────

def main() -> int:
    """CLI 入口:--date/--variety/--dry-run/--reset-state。"""
    try:
        from dotenv import load_dotenv  # 【调用包】加载 .env(setx 环境变量未覆盖新 shell)
        load_dotenv(override=False)
    except ImportError:
        pass
    ap = argparse.ArgumentParser(description="华泰期货官方天玑研报自动接入(能化 21 品种 日报+周报)")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="目标日期 YYYY-MM-DD(默认今天)")
    ap.add_argument("--variety", nargs="+", help="只接入这些品种代码(空格分隔多个),缺省=全部 21")
    ap.add_argument("--channel", choices=("日报", "周报"), help="只接该栏目(缺省=日报+周报都接)")
    ap.add_argument("--dry-run", action="store_true", help="只打印今日命中,不写库不调 LLM")
    ap.add_argument("--reset-state", action="store_true", help="清 seen 状态(强制重跑同日)")
    args = ap.parse_args()

    t0 = time.time()
    requested = None
    if args.variety:
        requested = {v.upper() for v in args.variety}
        unknown = requested - set(TARGET_VARIETIES)
        if unknown:
            print(f"不在目标品种内: {sorted(unknown)} (目标: {' '.join(TARGET_VARIETIES)})")
            return 2
    try:
        res = ingest_today(args.date, requested=requested, dry_run=args.dry_run,
                           reset_state=args.reset_state,
                           channels={args.channel} if args.channel else None)
    except ValueError as e:
        print(f"错误: {e}")
        return 1
    for err in res["errors"][:10]:
        print(f"  - {err}")
    print(f"Took: {time.time() - t0:.1f}s")
    return 0 if not res["errors"] else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 【调用】Windows 控制台 UTF-8
    raise SystemExit(main())
