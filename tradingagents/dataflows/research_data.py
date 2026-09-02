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
import time  # 【调用包】缓存 TTL 计时(60 秒缓存窗口)
from datetime import datetime  # 【调用包】时间戳生成(updated 字段)
from pathlib import Path  # 【调用包】路径对象与文件操作

logger = logging.getLogger(__name__)

# 与 external_data.py 相同的数据目录,研报文件名用 {品种}_research.json 区分。
RESEARCH_DIR = Path.home() / ".tradingagents" / "external_data"  # 【变量】研报聚合文件目录 = ~/.tradingagents/external_data
MAX_REPORTS = 10  # 【变量】每品种聚合文件最多保留最近 MAX_REPORTS 份研报摘要

_research_cache: dict[str, tuple[float, dict]] = {}  # 【变量】内存缓存:variety → (缓存时间, 聚合 dict);60 秒内复用避免反复读盘
RESEARCH_CACHE_TTL = 60  # 【变量】缓存有效期(秒):研报更新不频繁,60 秒足够


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
        lines.append(f"- [{direction} · 置信度 {conf_str}] {title} (来源: {r.get('source', 'N/A')}, 上传: {r.get('uploaded_at', 'N/A')})")
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
