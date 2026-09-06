"""
Research Report Injection Layer for Commodity Futures.

Manually uploaded research reports (PDF/image/markdown) are analyzed by an LLM
(structure extraction + opinion conclusion) and persisted as an aggregated
JSON per variety. During run-analysis, fundamental/macro analysts can call the
`get_research_report` tool, and the basis/inventory/supply-demand functions
merge research data in as the HIGHEST-priority data source.

Priority chain: RESEARCH (人工上传研报) > EXTERNAL (外部注入 JSON) > FREE_API.

File format (~/.tradingagents/external_data/RB_research.json):
{
  "variety": "RB",
  "updated": "2026-09-01T10:00:00",
  "reports": [
    {
      "id": 1, "title": "...", "source": "...", "uploaded_at": "...",
      "direction": "看多", "confidence": 0.8,
      "conclusion": "...", "data_points": {...}
    }
  ]
}
"""

# ===========================================================================
# 【本文件在数据流中的角色】
#   这是"研报注入层"的存储与对外只读接口:把用户上传研报经 LLM 提取后的
#   结构化结论(方向/置信度/关键数据点/观点摘要)按品种聚合写成一个 JSON
#   文件,供两类消费方读取:
#     1) get_research_report 工具 —— 基本面/宏观分析师直接调用,拿到"人工
#        上传研报"的文本化摘要(优先级最高);
#     2) merge_basis_data / merge_inventory_data / get_futures_supply_demand
#        —— 基差/库存/供需三处并入研报的关键数据点,同样标注 RESEARCH。
#
# 【为什么优先级最高】
#   研报是人工上传的一手材料(机构观点/产业调研),质量与可信度高于免费
#   API 与自动注入的外部 JSON;因此用户明确要求:研报 RESEARCH > 外部
#   EXTERNAL > 免费 FREE_API。本模块只负责存取与格式化,不判断对错。
#
# 【与 database.research_reports 表的关系】
#   SQLite 表存"每份研报的完整记录"(含提取原文/结构化 JSON/结论 markdown,
#   供列表与详情展示);本文件是"面向分析的聚合视图",只保留最近 10 份的
#   摘要,供 LLM 消费。两者由上传/处理流程协同维护:落库的同时写本文件。
# ===========================================================================

import json  # 【调用包】JSON 读写(研报聚合文件解析/写盘)
import logging  # 【调用包】日志输出(读取失败/缓存失效告警)
import threading  # 【调用包】互斥锁(并行采集线程同时写同品种聚合文件防丢更新)
import time  # 【调用包】缓存 TTL 计时(60 秒缓存窗口)
from datetime import datetime  # 【调用包】时间戳生成(updated 字段)
from pathlib import Path  # 【调用包】路径对象与文件操作

logger = logging.getLogger(__name__)

# 与 external_data.py 相同的数据目录,研报文件名用 {品种}_research.json 区分。
RESEARCH_DIR = Path.home() / ".tradingagents" / "external_data"  # 【变量】研报聚合文件目录 = ~/.tradingagents/external_data
MAX_REPORTS = 10  # 【变量】每品种聚合文件最多保留最近 MAX_REPORTS 份研报摘要

_research_cache: dict[str, tuple[float, dict]] = {}  # 【变量】内存缓存:variety → (缓存时间, 聚合 dict);60 秒内复用避免反复读盘
RESEARCH_CACHE_TTL = 60  # 【变量】缓存有效期(秒):研报更新不频繁,60 秒足够
_FILE_LOCK = threading.Lock()  # 【变量】聚合 JSON 读改写互斥锁(并行采集线程/清扫同品种文件防丢更新)


# 【功能】读取某品种的研报聚合 dict(带 60 秒内存缓存)。
# 【参数】variety: 品种代码(如 "RB")。
# 【返回】dict | None:研报聚合 {variety, updated, reports:[...]};无文件/损坏返回 None。
# 【关键逻辑】1) 先查 _research_cache,60 秒内命中直接返回(避免反复读盘);
#           2) 缓存未命中才读磁盘文件 {variety}_research.json;文件不存在或
#              JSON 损坏 → 记 warning 返回 None(调用方自然得到"无研报")。
def _load_research(variety: str) -> dict | None:
    now = time.time()  # 【变量】now:当前时间戳(缓存过期判断)
    cached = _research_cache.get(variety)  # 【变量】cached:内存缓存条目 (时间, dict) 或 None
    if cached and now - cached[0] < RESEARCH_CACHE_TTL:
        return cached[1]

    filepath = RESEARCH_DIR / f"{variety.upper()}_research.json"
    if not filepath.exists():
        return None
    try:
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read research data %s: %s", filepath, e)
        return None
    _research_cache[variety] = (now, data)
    return data


# 【功能】把研报聚合 dict 写盘,并刷新内存缓存。
# 【参数】variety: 品种代码;data: 研报聚合 dict。
# 【返回】无。
# 【关键逻辑】目录不存在时自动创建;写盘成功后更新 _research_cache,保证
#           upsert 后立刻可读(不被旧缓存挡住)。
def _save_research(variety: str, data: dict):
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    filepath = RESEARCH_DIR / f"{variety.upper()}_research.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    _research_cache[variety] = (time.time(), data)


# 【功能】对外只读接口:加载某品种的研报聚合 dict。
# 【参数】variety: 品种代码。
# 【返回】dict | None:聚合 dict;无数据返回 None。
# 【关键逻辑】薄转发给 _load_research(带缓存)。供 merge_* 等消费方取
#           data_points / direction / confidence 用。
def load_research_data(variety: str) -> dict | None:
    """Load the aggregated research JSON for a variety (read-only)."""
    return _load_research(variety)


# 【功能】生成研报的"人类可读文本",供 get_research_report 工具返回给 LLM。
# 【参数】variety: 品种代码。
# 【返回】str:格式化研报摘要;无数据返回 RESEARCH_NO_DATA 哨兵。
# 【关键逻辑】1) 无研报 → 返回 "RESEARCH_NO_DATA: 该品种暂无上传研报"(确定性
#              结论,不允许 LLM 编造);2) 有研报 → 输出 # RESEARCH 头 + 每份
#              的方向/置信度/结论摘要/关键数据点(置信度越高排越前)。
def get_research_report_text(variety: str) -> str:
    """Format the aggregated research reports as text for the LLM.

    Returns a "RESEARCH_NO_DATA: ..." sentinel when no research exists so the
    analyst reports honestly instead of inventing data.
    """
    data = _load_research(variety)
    if not data or not data.get("reports"):
        return "RESEARCH_NO_DATA: 该品种暂无上传研报"

    reports = data["reports"]
    lines = [
        "# RESEARCH 研报(人工上传,可信优先级最高)",
        f"# 该品种已上传 {len(reports)} 份研报,更新于 {data.get('updated', 'N/A')}",
        "# 研报为人工上传的一手材料,观点/数据可信度高于免费 API;方向与置信度供综合研判参考。",
        "# ---",
    ]
    for r in reports:
        title = r.get("title") or "未命名研报"
        direction = r.get("direction") or "中性"
        confidence = r.get("confidence")
        conf_str = f"{confidence:.2f}" if isinstance(confidence, (int, float)) else "N/A"
        # 多品种研报:标注覆盖品种,并说明以下为当前品种的部分(避免 LLM 把
        # 其它品种的数据点误当成当前品种的)。
        covers = r.get("varieties") or []
        cov_str = f" · 覆盖品种: {', '.join(covers)}" if len(covers) > 1 else ""
        # 有效日期:发布日期(publish_date)优先,老记录无此键回退 uploaded_at(入库日)
        eff_date = (r.get("publish_date") or "").strip()[:10] or str(r.get("uploaded_at") or "")[:10]
        lines.append(
            f"- [{direction} · 置信度 {conf_str}] {title}{cov_str} "
            f"(来源: {r.get('source', 'N/A')}, 日期: {eff_date})"
        )
        conclusion = (r.get("conclusion") or "").strip()
        if conclusion:
            # 结论摘要只取第一段,控制 token 占用
            first_para = conclusion.split("\n\n")[0].replace("\n", " ")[:300]
            lines.append(f"  观点: {first_para}")
        dps = r.get("data_points") or {}
        if isinstance(dps, dict) and dps:
            items = []
            for k, v in list(dps.items())[:6]:
                if isinstance(v, dict):
                    items.append(f"{k}={v.get('value', '')}{v.get('unit', '')}")
                else:
                    items.append(f"{k}={v}")
            if items:
                lines.append(f"  数据点: {'; '.join(items)}")
            # 【2026-09-03】四类基本面指标(基差/交易所仓单/开工率·负荷率/加工利润·价差)
            #   单独具名输出:不在前 6 项通用"数据点"内也能进入分析师研报文本(供分析)。
            #   研报没给该值就整条不输出 → 天然留空;若给的是 {value, unit, date, note}
            #   对象则把 unit/date/note 一并带上,便于 LLM 判断口径与时效。
            _research_typed = (
                ("basis", "基差"),
                ("warehouse_receipts", "交易所仓单"),
                ("operating_rate", "开工率/负荷率"),
                ("processing_margin", "加工利润/加工费"),
            )  # 【变量】四类指标的研报 data_points 键 → 展示名
            for dk, lbl in _research_typed:
                v = dps.get(dk)
                if v is None:
                    continue
                val = v.get("value") if isinstance(v, dict) else v
                if val is None or val == "":
                    continue
                unit_s = v.get("unit") if isinstance(v, dict) else ""
                num = f"{val}{unit_s}" if unit_s else str(val)
                meta = []
                if isinstance(v, dict):
                    for fld in ("date", "note"):
                        if v.get(fld):
                            meta.append(str(v[fld]))
                suffix = f" ({', '.join(meta)})" if meta else ""
                lines.append(f"  研报-{lbl}: {num}{suffix}")
    return "\n".join(lines)


# ===========================================================================
# 【机构/研报群体多空汇总 —— 确定性纯函数(无 LLM)】
#   用途: 1) get_research_view_summary 工具(情绪分析师"机构/研报群体"第 2 群体
#         取数,与 get_futures_sentiment 的"散户/社媒群体"并列);
#         2) 运行分析结果区「机构(研报) vs 散户(社媒) 观点对比」卡的数据生产;
#         3) 历史报告持久化的 markdown 段。
#   口径: 按该品种聚合文件里在库(最近 MAX_REPORTS 份)研报的 direction 全量计数,
#         direction 缺省视为中性;客观性过滤不在此层(基本面是否采纳另由提示词把关)。
# ===========================================================================

_DIR_GROUP = {"看多": "bull", "偏多": "bull", "看空": "bear", "偏空": "bear", "中性": "neutral"}  # 【变量】研报方向 → 三向分组键


# 【功能】研报方向字符串归一为三向分组键。
# 【参数】direction: 研报方向(如 "看多"/"偏多"/"看空"/"中性"/None/未知)。
# 【返回】str: "bull" | "bear" | "neutral"。
def _group_direction(direction) -> str:
    """Map a research report direction string to a 3-way group key."""
    return _DIR_GROUP.get((direction or "").strip(), "neutral")


# 【功能】结论全文 → 一行纯文本摘要(用于情绪工具/对比卡的"一句观点")。
# 【参数】text: 研报 conclusion 全文(markdown 或纯文本)。
# 【返回】str: 去掉标题符/空行后的首个非空行;无内容返回 ""。
def _plain_first_line(text: str) -> str:
    """Return the first plain-text content line of a markdown conclusion.

    跳过结构行,取真正的观点/叙事句:
    - markdown 标题(以 # 开头);
    - 【…】整行模板标记(如 【第一部分 · 多角度核心观点】,含加粗变体 **【…】**)。
    若全文只有标题/标记行,退回最后一个剥离 # 后的文本,保证非空。
    """
    fallback = ""
    for raw in (text or "").splitlines():
        ln = raw.strip().strip("*").strip()
        if not ln:
            continue
        if ln.startswith("#"):
            fallback = ln.lstrip("#").strip().strip("*").strip()
            continue
        if ln.startswith("【") and ln.endswith("】"):
            continue  # 【…】整行标记是结构说明(第几部分), 不是观点内容
        return ln[:120]
    return fallback[:120]


# ===========================================================================
# 【研报宏观事件确定性汇总 —— 无 LLM】
#   用途: 宏观/新闻分析师与情绪面分析师的系统提示前置注入(2026-09-04)。
#   背景: 研报第一步 LLM 结构化提取产出 key_events(事件/详情/多空影响判定),
#         此前只落库与前端展示,从未进任何分析师 —— 宏观面缺"机构近期在跟踪
#         什么事件"的一手信号,情绪面缺"机构观点背后的驱动事件"。
#   口径: 1) 宏观共性事件 = 同一归一化事件文本被 ≥2 个品种的近期研报提及
#            (品种日报只写本品种,多品种共提 → 大概率是宏观级驱动);
#         2) 本品种事件 = 目标品种聚合文件里近期研报的 key_events 全量,
#            并挂上该研报的方向/置信度(事件与观点绑定)。
# ===========================================================================

_MACRO_EVENT_MAX_COMMON = 8  # 【变量】宏观共性事件条数上限(防提示词膨胀)
_MACRO_EVENT_MAX_VARIETY = 10  # 【变量】本品种事件条数上限
_MACRO_EVENT_DETAIL_LEN = 80  # 【变量】事件详情截断长度(字符)
_IMPACT_CN = {"bullish": "利多", "bearish": "利空", "neutral": "中性"}  # 【变量】事件影响 → 中文


# 【功能】扫描聚合目录里现存的品种代码(宏观共性事件的跨品种扫描用)。
# 【返回】list[str]: 品种代码列表(如 ["MA","RB",...]);目录不存在/异常返回 []。
def _research_varieties_on_disk() -> list[str]:
    try:
        return sorted(
            p.name[: -len("_research.json")]
            for p in RESEARCH_DIR.glob("*_research.json")
            if p.name.endswith("_research.json")
        )
    except OSError:
        return []


def _norm_event_key(event: str) -> str:
    """事件文本归一(去空白),跨品种同事件判定用。"""
    return "".join((event or "").split())


def summarize_research_macro_events(variety: str, days: int = 3) -> str:
    """Deterministically summarize macro events + views from recent research reports.

    【参数】variety: 品种代码(如 "SC");days: 回看天数(按研报 uploaded_at 过滤,默认 3)。
    【返回】str: 注入用文本(含使用说明);无任何事件返回 ""(调用方不注入)。
    【关键逻辑】1) 扫全部品种聚合文件,收集 days 天内研报的 key_events;
              2) 归一化事件文本按跨品种计数,≥2 品种 → 宏观共性事件(多空票数);
              3) 目标品种研报事件逐条列出并挂该研报方向/置信度(事件与观点绑定);
              4) 全程读缓存化聚合 JSON,无 LLM、零网络,失败吞掉返回 ""。
    """
    code = (variety or "").upper().strip()
    try:
        cutoff = (datetime.now().timestamp()) - days * 86400
    except Exception:  # 时间计算失败按"不过滤"处理
        cutoff = 0.0

    # event_key → {"event", "impacts": Counter, "varieties": set, "latest": (uploaded_at, source, title)}
    common: dict[str, dict] = {}
    # 本品种事件: [{event, detail, impact, source, uploaded_at, title, direction, confidence}]
    variety_events: list[dict] = []

    for v in _research_varieties_on_disk():
        data = _load_research(v)
        for r in (data or {}).get("reports") or []:
            # 回看窗口与"最近提及"比较都用有效日期(发布日 publish_date 优先,
            # 老聚合记录无此键回退 uploaded_at 入库日)——按真实发布日判定时效
            uploaded = (r.get("publish_date") or "").strip()[:10] or str(r.get("uploaded_at") or "")
            try:
                if uploaded and datetime.strptime(uploaded[:10], "%Y-%m-%d").timestamp() < cutoff:
                    continue
            except ValueError:
                pass  # 日期异常的研报不过滤(宁多勿漏)
            dps = r.get("data_points") or {}
            events = dps.get("key_events")
            if not isinstance(events, list):
                continue
            for ev in events:
                if not isinstance(ev, dict) or not str(ev.get("event") or "").strip():
                    continue
                item = {
                    "event": str(ev.get("event")).strip(),
                    "detail": str(ev.get("detail") or "").strip(),
                    "impact": _IMPACT_CN.get(str(ev.get("impact") or "neutral").lower(), "中性"),
                    "source": str(r.get("source") or ""),
                    "uploaded_at": uploaded[:10],
                    "title": str(r.get("title") or ""),
                    "direction": str(r.get("direction") or "中性"),
                    "confidence": r.get("confidence"),
                }
                if v == code:
                    variety_events.append(item)
                key = _norm_event_key(item["event"])
                bucket = common.setdefault(key, {"event": item["event"], "impacts": {}, "varieties": set(), "latest": item})
                bucket["impacts"][item["impact"]] = bucket["impacts"].get(item["impact"], 0) + 1
                bucket["varieties"].add(v)
                if item["uploaded_at"] >= bucket["latest"]["uploaded_at"]:
                    bucket["latest"] = item

    if not variety_events and not common:
        return ""

    lines = [
        f"# RESEARCH 宏观事件(近{days}天研报确定性提取,无 LLM 加工)",
        "# 事件与影响判定来自各家期货公司研报的原文提取,属机构一手跟踪信号;",
        "# 影响票数是各研报的主观判定投票,不是客观数据 —— 引用时注明「研报观点」。",
    ]
    macro_common = [b for b in common.values() if len(b["varieties"]) >= 2]
    macro_common.sort(key=lambda b: -sum(b["impacts"].values()))
    if macro_common:
        lines.append("## 宏观共性事件(≥2 个品种的研报共同提及,宏观级驱动)")
        for b in macro_common[:_MACRO_EVENT_MAX_COMMON]:
            votes = "/".join(f"{k}x{n}" for k, n in sorted(b["impacts"].items(), key=lambda x: -x[1]))
            latest = b["latest"]
            lines.append(
                f"- {b['event']}({votes}; 提及品种: {','.join(sorted(b['varieties']))};"
                f" 最近: {latest['source']} {latest['uploaded_at']})"
            )
    if variety_events:
        lines.append(f"## {code} 品种研报事件与观点(事件 ↔ 该研报方向/置信度)")
        for e in variety_events[:_MACRO_EVENT_MAX_VARIETY]:
            conf = e["confidence"]
            conf_s = f"{conf:.2f}" if isinstance(conf, (int, float)) else "未给"
            detail = e["detail"][:_MACRO_EVENT_DETAIL_LEN]
            lines.append(
                f"- {e['event']}[{e['impact']}] {detail}"
                f"(研报: {e['source']} {e['uploaded_at']}《{e['title'][:40]}》;"
                f" 该研报方向: {e['direction']} · 置信度 {conf_s})"
            )
    return "\n".join(lines)


# 【功能】确定性汇总某品种在库研报的方向/置信度(机构/研报群体视角)。
# 【参数】variety: 品种代码(如 "RB")。
# 【返回】dict: {count, updated, counts:{bull,neutral,bear}, conf_avg:{bull,neutral,bear}|None,
#          score(多-空), net_dir(看多|中性|看空), items:[{id,source,title,uploaded_at,
#          direction,group,confidence,one_line}]};无研报返回 count=0 的骨架(非 None)。
def summarize_research_views(variety: str) -> dict:
    """Deterministically aggregate the institutional (research) directional views.

    No LLM involved: reads the variety's aggregated research JSON and tallies each
    in-library report's direction/confidence. Returns an empty skeleton
    (count == 0) rather than None so callers can uniformly handle the no-data case.
    """
    data = _load_research(variety)
    reports = (data or {}).get("reports") or []
    empty = {
        "count": 0,
        "updated": "",
        "counts": {"bull": 0, "neutral": 0, "bear": 0},
        "conf_avg": {"bull": None, "neutral": None, "bear": None},
        "score": 0,
        "net_dir": "",
        "items": [],
    }
    if not reports:
        return empty

    counts = {"bull": 0, "neutral": 0, "bear": 0}
    confs = {"bull": [], "neutral": [], "bear": []}
    items = []
    for r in reports:
        g = _group_direction(r.get("direction"))
        counts[g] += 1
        c = r.get("confidence")
        if isinstance(c, (int, float)) and not isinstance(c, bool):
            confs[g].append(float(c))
        items.append(
            {
                "id": r.get("id"),
                "source": r.get("source") or "未知",
                "title": r.get("title") or "未命名研报",
                "uploaded_at": r.get("uploaded_at") or "",
                "direction": r.get("direction") or "中性",
                "group": g,
                "confidence": c,
                "one_line": _plain_first_line(r.get("conclusion") or "")[:120],
            }
        )

    conf_avg = {
        k: (round(sum(v) / len(v), 3) if v else None) for k, v in confs.items()
    }
    if counts["bull"] > counts["bear"]:
        net_dir = "看多"
    elif counts["bear"] > counts["bull"]:
        net_dir = "看空"
    else:
        net_dir = "中性"
    return {
        "count": len(reports),
        "updated": (data or {}).get("updated", ""),
        "counts": counts,
        "conf_avg": conf_avg,
        "score": counts["bull"] - counts["bear"],
        "net_dir": net_dir,
        "items": items,
    }


# 【功能】机构/研报群体的方向聚合文本,供 get_research_view_summary 工具返回。
# 【参数】variety: 品种代码(如 "RB")。
# 【返回】str: 带计数的研报多空汇总文本;无研报返回 RESEARCH_VIEW_NO_DATA 哨兵
#          (明确告知情绪分析师"机构侧无数据",禁止其编造机构观点)。
def format_research_views_text(variety: str) -> str:
    """Format the institutional (research) group's directional views as text."""
    s = summarize_research_views(variety)
    if s["count"] == 0:
        return "RESEARCH_VIEW_NO_DATA: 该品种暂无上传研报(机构/研报群体无数据)"

    lines = [
        "# RESEARCH INSTITUTIONAL VIEWS (机构/研报群体, 按方向全量计数)",
        f"# 在库研报 {s['count']} 份 · 更新于 {s['updated'] or 'N/A'}",
        "# 研报方向是机构主观观点, 已由情绪分析师按机构群体计数; 客观数据由基本面分析师另行处理。",
        f"# 方向统计: 看多/偏多 {s['counts']['bull']} 份 | 中性 {s['counts']['neutral']} 份 | 看空/偏空 {s['counts']['bear']} 份",
        "# ---",
    ]
    for it in s["items"]:
        conf = f"{it['confidence']:.2f}" if isinstance(it["confidence"], (int, float)) and not isinstance(it["confidence"], bool) else "N/A"
        lines.append(f"- [{it['direction']} · 置信度 {conf}] {it['title']} (来源: {it['source']})")
        if it["one_line"]:
            lines.append(f"  观点摘要: {it['one_line']}")
    return "\n".join(lines)


# 【功能】把一条新研报摘要插入聚合文件头部,并截断到最近 MAX_REPORTS 份。
# 【参数】variety: 品种代码;record: 研报摘要 dict(含 id/title/direction/
#           confidence/conclusion/data_points 等)。
# 【返回】无。
# 【关键逻辑】1) 读现有聚合(无则新建);2) 新记录插到 reports 列表头(最新在前);
#           3) 只保留前 MAX_REPORTS(10)份;4) updated 刷新为当前时间并写盘。
def upsert_research_report(variety: str, record: dict):
    """Insert (or refresh) one report record at the head of the variety's
    aggregated research JSON, trimming to the most recent MAX_REPORTS."""
    with _FILE_LOCK:  # 【关键】并行采集线程同时 upsert 同品种 → 读改写必须互斥,否则丢更新
        data = _load_research(variety) or {}
        reports = data.get("reports") or []
        # 同 id 更新(覆盖),否则新插入头部
        reports = [r for r in reports if r.get("id") != record.get("id")]
        reports.insert(0, record)
        data.update(
            {
                "variety": variety.upper(),
                "updated": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "reports": reports[:MAX_REPORTS],
            }
        )
        _save_research(variety, data)


# 【功能】从聚合文件中删除某份研报摘要(按 id)。
# 【参数】variety: 品种代码;report_id: 研报数据库主键。
# 【返回】无。
# 【关键逻辑】1) 读聚合;2) 过滤掉 id 相等的记录;3) 若删空则直接移除整个
#           聚合文件(避免留下空壳);4) 仍有剩余则刷新 updated 并写盘。
#           由 web_app 的删除接口调用,保证聚合 JSON 与数据库记录同步。
def remove_research_report(variety: str, report_id: int):
    """Remove one report record from the variety's aggregated research JSON."""
    with _FILE_LOCK:  # 【关键】与 upsert/sweep 互斥,防并行读改写丢更新
        data = _load_research(variety)
        if not data or not data.get("reports"):
            return
        reports = [r for r in data["reports"] if r.get("id") != report_id]
        if len(reports) == len(data["reports"]):
            return  # id 不存在,无需改动
        if reports:
            data.update(
                {
                    "variety": variety.upper(),
                    "updated": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                    "reports": reports,
                }
            )
            _save_research(variety, data)
        else:
            # 删空了 → 移除整个聚合文件并清缓存
            filepath = RESEARCH_DIR / f"{variety.upper()}_research.json"
            if filepath.exists():
                filepath.unlink()
            _research_cache.pop(variety, None)


# 【功能】全量清扫聚合 JSON 里的孤儿研报条目(DB 已删但聚合未同步的残留)。
# 【参数】valid_ids: 数据库 research_reports 现存主键集合。
# 【返回】int: 清掉的孤儿条目数。
# 【关键逻辑】逐个 *_research.json 过滤 reports 里 id 不在 valid_ids 的条目;
#           还有剩余则写盘(刷新 updated),删空则移除整个文件;变更的品种
#           清内存缓存,保证下一次读到干净数据。由 web_app 启动时与删除
#           研报后调用 —— 根治"研报删了还出现在观点总览/分析师取数"的漂移。
def sweep_orphan_reports(valid_ids: set[int]) -> int:
    """Drop aggregate-JSON entries whose report id is no longer in the DB."""
    removed = 0
    if not RESEARCH_DIR.is_dir() or not valid_ids:
        return 0
    for filepath in RESEARCH_DIR.glob("*_research.json"):
        with _FILE_LOCK:  # 【关键】与 upsert/remove 互斥,防并行读改写丢更新
            try:
                data = json.loads(filepath.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue  # 损坏文件交给读取路径的 warning,不动它
            reports = data.get("reports") if isinstance(data, dict) else None
            if not isinstance(reports, list) or not reports:
                continue
            kept = [r for r in reports if r.get("id") in valid_ids]
            if len(kept) == len(reports):
                continue  # 无孤儿,不写盘
            removed += len(reports) - len(kept)
            variety = (data.get("variety") or filepath.stem.replace("_research", "")).upper()
            if kept:
                data["reports"] = kept
                data["updated"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                _save_research(variety, data)
            else:
                # 删空了 → 移除整个聚合文件并清缓存(与 remove_research_report 同口径)
                filepath.unlink(missing_ok=True)
                _research_cache.pop(variety, None)
    return removed


# 【功能】给一段 API 文本加"研报数据源"标注头,供 merge_* 拼接使用。
# 【参数】api_text: 原文本(如免费接口返回的基差 CSV / 库存 CSV);note: 附加说明。
# 【返回】str:加了 "# DATA_SOURCE: RESEARCH" 头的文本。
# 【关键逻辑】明确告诉 LLM:以下数据来自研报,优先级最高,可与下方 API 数据对比。
def annotate_research(api_text: str, note: str = "") -> str:
    """Prepend a RESEARCH data-source header to a content string."""
    header = (
        "# DATA_SOURCE: RESEARCH (研报上传, 可信优先级最高)\n"
        f"# {note}\n"
        "# 研报为人工上传的一手材料,若与下方免费 API 数据分歧,优先采信研报数据。\n"
        "# ---\n"
    )
    return header + api_text
