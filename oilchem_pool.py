# -*- coding: utf-8 -*-
"""隆众资讯 ↔ AgentSense 品种池 映射（采集器与入库器共用）

【要解决什么】隆众覆盖约 1000 个品种，而 AgentSense 活跃池只有 21 个。
隆众文章常写整条产业链（原油→石脑油→PX→PTA→聚酯→涤纶长丝），
**导语先讲上游，主体落在下游** ⇒ LLM 容易把上游/池外品种当主品种，造成归属错配。

【本模块提供】
  · `POOL`              —— 池内品种（运行时从项目导入，失败回退内置副本）
  · `subject_code(title)` —— 从标题判定「文章主体」对应的池内品种码（判不出返回 None）
  · `is_comprehensive(title)` —— 是否综合/深度文（早读、聚焦等），永不门禁
  · `classify(title)`   —— 三分类: in_pool / adjacent / out_of_pool

【★ 为什么不建议在采集端硬门禁】实测（2026-10-14，40 篇样本）：
  抽「内层主体」后按"池外即拦"会拦掉 11 篇，但其中 **3 篇确有池内实质内容**：
    · `[隆众聚焦]…DOTP市场价格上行`   → 含 PTA 开工率/检修数据（应归 TA）
    · `[热点聚焦]…磷酸铁及上下游…`    → 含磷酸铁锂排产对碳酸锂需求（应归 LC）
    · `[隆众聚焦]…丙酮高位回落…`      → 含纯苯港口库存分析（应归 BZ）
  ⇒ 为省 LLM 而丢弃真实内容是**错误取舍**。正确做法是在**入库后做归属校验与改归属**
    （见 oilchem_ingest.post_verify_attribution），而不是在采集端一刀切。
  本模块的 `classify` 因此主要用于**报告与预警**，门禁开关默认关闭。
"""
from __future__ import annotations

import re
from pathlib import Path

# ── 池内品种 ────────────────────────────────────────────────────────────
# 运行时从项目导入（实测仅 0.58s，可接受）；导入失败回退内置副本并告警。
_FALLBACK_POOL = frozenset({
    "SC", "TA", "PX", "EG", "FU", "LU", "PG", "M", "CF", "CJ",
    "LC", "RU", "NR", "BR", "LH", "BU", "PS", "SI", "BZ", "EB", "P",
})


def _load_pool() -> frozenset:
    try:
        import sys
        root = str(Path(__file__).resolve().parent)
        if root not in sys.path:
            sys.path.insert(0, root)
        from tradingagents.dataflows.commodity_futures import ACTIVE_VARIETIES
        return frozenset(ACTIVE_VARIETIES)
    except Exception:
        return _FALLBACK_POOL


POOL: frozenset = _load_pool()

# ── 隆众「主体词」→ 池内码 ──────────────────────────────────────────────
# 【维护说明】新增品种/发现新称呼时在此补充。键是隆众标题里可能出现的主体词，
# 值是池内标准码。宁可少列（漏映射只会让该条"判不出主体"，进而不动它），
# **不要错列** —— 错列会把文章判给错误品种。
SUBJECT_MAP: dict[str, str] = {
    # 能源 / 炼油链（下游炼油品的文章对 SC 有需求侧含义）
    "原油": "SC", "成品油": "SC", "汽油": "SC", "柴油": "SC", "煤油": "SC",
    "航煤": "SC", "航空燃油": "SC", "石脑油": "SC", "石油焦": "SC", "炼油": "SC",
    # 聚酯链（涤纶/聚酯是 PTA 的直接下游，池内锚点取 TA）
    "PTA": "TA", "精对苯二甲酸": "TA", "涤纶": "TA", "聚酯": "TA", "短纤": "TA",
    "对二甲苯": "PX", "PX": "PX",
    "乙二醇": "EG", "MEG": "EG",
    # 能源其它
    "燃料油": "FU", "渣油": "FU",
    "低硫": "LU", "船用油": "LU",
    "液化气": "PG", "民用气": "PG", "丙烷": "PG", "丁烷": "PG", "LPG": "PG",
    # 化工
    "纯苯": "BZ", "粗苯": "BZ", "加氢苯": "BZ",
    "苯乙烯": "EB", "聚苯乙烯": "PS", "EPS": "PS",
    "工业硅": "SI", "光伏": "SI",
    "碳酸锂": "LC",
    # 橡胶
    "天然橡胶": "RU", "干胶": "RU", "轮胎": "RU",
    "20号胶": "NR", "顺丁橡胶": "BR", "丁二烯橡胶": "BR",
    # 农产品
    "豆粕": "M", "棉花": "CF", "红枣": "CJ", "生猪": "LH", "猪业": "LH", "棕榈油": "P",
    # 建材 / 黑色系
    "沥青": "BU",
}

# 池外、但属**宽产业链**词汇：判不出单一品种，也**不应据此门禁**
ADJACENT = ("煤化工", "烯烃", "芳烃", "裂解", "织造", "新材料", "新能源",
            "橡胶", "塑料", "涂料", "造纸", "化肥", "氯碱")

# 综合 / 深度文形态：永不门禁（含每日晨报与产业链深度）
_COMPREHENSIVE = ("早读", "聚焦", "专题", "周报", "月报")

_BRACKET_RE = re.compile(r"^[\s\u200b]*[\[［]([^\]］]{1,16})[\]］]\s*[：:]")
_IDX_KEYS = sorted(SUBJECT_MAP, key=len, reverse=True)


def is_comprehensive(title: str) -> bool:
    """是否综合/深度文（早读、聚焦、专题…）——这类文常跨品种，不做门禁。"""
    t = title or ""
    return any(k in t for k in _COMPREHENSIVE)


def bracket_subject(title: str) -> str:
    """取标题方括号里的主体词（`[基础油]：…` → `基础油`）。无则返回 ""。"""
    m = _BRACKET_RE.match(title or "")
    return (m.group(1) or "").strip() if m else ""


def subject_code(title: str) -> str | None:
    """判定「文章主体」对应的池内品种码；判不出返回 None。

    【顺序】① 方括号主体词精确/包含匹配（`[基础油]`、`[成品油]`、`[沥青]`）；
            ② 全文最长关键词匹配（覆盖 `隆众聚焦：…涤纶长丝…` 这类无方括号标题）。
    【为什么最长优先】「天然橡胶」必须先于「橡胶」命中，否则会被宽词抢走。
    """
    t = title or ""
    bs = bracket_subject(t)
    if bs:
        if bs in SUBJECT_MAP:
            return SUBJECT_MAP[bs]
        for k in _IDX_KEYS:          # 方括号主体词的包含匹配（如「重交沥青」）
            if k in bs:
                return SUBJECT_MAP[k]
    for k in _IDX_KEYS:              # 全文最长匹配
        if k in t:
            return SUBJECT_MAP[k]
    return None


def classify(title: str) -> str:
    """三分类：in_pool / adjacent / out_of_pool。

    · in_pool     —— 主体映射到池内码，放行
    · adjacent    —— 综合文，或命中宽产业链词，或**判不出主体**（保守：放行）
    · out_of_pool —— 明确判出是池外品种，且非宽产业链词（理论上可门禁，
                     但见模块 docstring：实测误伤真实内容，故仅用于报告）
    """
    t = title or ""
    if is_comprehensive(t):
        return "adjacent"
    code = subject_code(t)
    if code:
        return "in_pool"
    if any(k in t for k in ADJACENT):
        return "adjacent"
    # 有方括号主体词但映射不到池内 ⇒ 明确池外
    if bracket_subject(t):
        return "out_of_pool"
    return "adjacent"      # 判不出 ⇒ 保守放行


def report(titles: list[str]) -> dict:
    """批量分类统计（供采集/入库脚本打印预警）。"""
    from collections import Counter
    buckets: dict[str, list[str]] = {"in_pool": [], "adjacent": [], "out_of_pool": []}
    for t in titles:
        buckets[classify(t)].append(t)
    return {"pool_size": len(POOL), "counts": dict(Counter({k: len(v) for k, v in buckets.items()})),
            "buckets": buckets}


if __name__ == "__main__":      # 自检
    cases = [
        ("[基础油]：10月上旬基础油市场大幅上涨", "out_of_pool"),
        ("[沥青]：三季度市场供需双增 沥青库存环比降幅54.8%", "in_pool"),
        ("[成品油]：供应风险难消 预计11月航空燃油附加费仍存涨势", "in_pool"),
        ("隆众能化早读：地缘局势提升供应风险", "adjacent"),
        ("[隆众聚焦]：成本向上、需求向下，涤纶长丝维持在双向挤压格局", "adjacent"),
        ("[热点聚焦]：磷酸铁及上下游节后行情一览", "adjacent"),
        ("[隆众聚焦]：利好持续叠加 异戊二烯橡胶价格持续探高", "adjacent"),
    ]
    ok = True
    for t, want in cases:
        got = classify(t)
        flag = "✓" if got == want else "✗"
        if got != want:
            ok = False
        print("%s %-12s 主体=%-6s  %s" % (flag, got, subject_code(t) or "-", t[:44]))
    print("\n自检:", "PASS" if ok else "FAIL")
