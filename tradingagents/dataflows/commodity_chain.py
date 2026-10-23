"""产业链传导链（2026-10-22）——**全部为草稿（draft），待人工复核**。

【这份数据解决什么问题】
品种库里的 `related_varieties` 只给"相关品种"，**不给方向、也不标注哪些环节本系统根本没数**
（`TA -> SC, MA`：SC 是上游原油，MA 是相关/替代，混在一个列表里）。后果是模型会在
「无数据源」的环节上编数——实测报告里出现过「石脑油裂解价差走阔、聚酯开工率回升至 82%」，
这两个数我们从没提供过。

**最危险的不是"没有数据"，是"模型不知道没有数据"。**
所以本模块的第一价值不是多给信息，而是**划清哪里我们没有数**。

【coverage 四态（不可混用）】
  active   ：在活跃分析池（21 个），有完整取数 + 分析
  library  ：在品种库（62 个），能取价格，但不在日常分析池
  external ：外盘/境外现货（CBOT / BMD / LME / 东南亚胶园），本系统**未接入**
  none     ：产业链环节存在，但**没有任何数据源**

【pending 字段】
`external` 与 `none` 必须写 `pending`（缺什么数据、可能去哪取、影响多大）。
`gaps()` 会把它们汇总成「待改进清单」—— 以后接入新数据源时按这份清单补，
补完把 `coverage` 改成 `active`/`library` 即可，不需要改代码结构。

【为什么标 draft】
产业链关系是产业知识，本文件由模型起草（2026-10-22），**未经人工逐条复核**。
按项目纪律：draft 不参与结论判断，只用于「查漏」（研究内容检查 R7/R8）与留档。
复核后把 `reviewed` 改成 True，并记上复核人与日期。

【只标 1 跳】
不建完整六环。多跳因果模型不该自己推（错链比没链更糟），而查漏只需要直接上下游。
"""
from __future__ import annotations

import json
from typing import Any

# coverage 取值
ACTIVE, LIBRARY, EXTERNAL, NONE = "active", "library", "external", "none"
_COVERAGE = (ACTIVE, LIBRARY, EXTERNAL, NONE)
# 需要"有数据才能判断"的环节类型（external/none 都算缺）
_MISSING = (EXTERNAL, NONE)


def _n(node: str, cov: str, note: str = "", **pending: Any) -> dict[str, Any]:
    """造一个环节。`external`/`none` 必须给 pending.need（缺什么数据）。"""
    item: dict[str, Any] = {"node": node, "coverage": cov}
    if note:
        item["note"] = note
    if pending:
        item["pending"] = pending
    return item


# ---------------------------------------------------------------------------
# 21 个活跃品种的传导链（draft · 2026-10-22 起草，待复核）
# ---------------------------------------------------------------------------
CHAINS: dict[str, dict[str, Any]] = {
    "SC": {
        "name": "原油",
        "reviewed": False,
        "upstream": [
            _n("OPEC+ 产量与地缘", NONE, "一次能源，无上游商品",
               need="OPEC+ 月度产量 / 地缘事件跟踪", hint="OPEC 月报、EIA", impact="high"),
        ],
        "downstream": [
            _n("FU", ACTIVE, "燃料油（直接下游产品）"),
            _n("BU", ACTIVE, "沥青"),
            _n("LU", ACTIVE, "低硫燃料油"),
            _n("PG", ACTIVE, "液化石油气"),
            _n("PX", ACTIVE, "经石脑油间接传导，非直接下游"),
        ],
    },
    "FU": {
        "name": "燃料油",
        "reviewed": False,
        "upstream": [_n("SC", ACTIVE, "直接原料")],
        "downstream": [
            _n("船燃需求（BDI）", EXTERNAL, "航运运价指数，本系统未接入",
               need="BDI / 波罗的海运价指数", hint="波交所、Clarksons", impact="high"),
            _n("中东发电需求", NONE, "季节性，夏季高峰",
               need="中东燃料油发电消费量", hint="隆众、Platts", impact="low"),
        ],
    },
    "LU": {
        "name": "低硫燃料油",
        "reviewed": False,
        "upstream": [_n("SC", ACTIVE, "直接原料")],
        "downstream": [
            _n("船燃需求（低硫化）", EXTERNAL, "与 FU 共用航运需求",
               need="低硫船燃加注量 / 高低硫价差", hint="波交所、Platts", impact="high"),
        ],
    },
    "BU": {
        "name": "石油沥青",
        "reviewed": False,
        "upstream": [_n("SC", ACTIVE, "直接原料")],
        "downstream": [
            _n("道路基建", NONE, "需求主驱动，看专项债与开工",
               need="公路建设投资 / 沥青装置开工率", hint="统计局、隆众", impact="high"),
            _n("防水卷材", NONE,
               need="防水行业开工", hint="隆众", impact="low"),
        ],
    },
    "PG": {
        "name": "液化石油气",
        "reviewed": False,
        "upstream": [
            _n("SC", ACTIVE, "油田伴生 / 炼厂副产"),
            _n("进口丙烷（CP/FEI）", EXTERNAL, "进口成本锚",
               need="沙特 CP 合同价 / FEI 丙烷", hint="Platts、Argus", impact="high"),
        ],
        "downstream": [
            _n("民用燃烧", NONE, "冬季旺季",
               need="民用燃烧消费季节性", hint="隆众", impact="medium"),
            _n("PDH 制丙烯 → PP", LIBRARY, "PP 在品种库，可取自各价格"),
        ],
    },
    "PX": {
        "name": "对二甲苯",
        "reviewed": False,
        "upstream": [
            _n("石脑油", NONE, "**断点**：PX 的直接原料，本系统无数据源",
               need="CFR 日本石脑油价格 / 石脑油裂解价差", hint="Platts、隆众、生意社", impact="high"),
            _n("MX 混二甲苯", NONE, "调油路线原料",
               need="MX 价格 / 调油经济性", hint="隆众", impact="low"),
            _n("SC", ACTIVE, "经石脑油间接传导"),
        ],
        "downstream": [_n("TA", ACTIVE, "PTA 是 PX 最大下游")],
    },
    "TA": {
        "name": "PTA",
        "reviewed": False,
        "upstream": [
            _n("PX", ACTIVE, "直接原料，1 吨 PTA 约耗 0.655 吨 PX"),
            _n("石脑油", NONE, "**断点**：PX 之上的一环，成本传导的必看项",
               need="CFR 日本石脑油价格 / 石脑油裂解价差", hint="Platts、隆众、生意社", impact="high"),
            _n("SC", ACTIVE, "经石脑油间接传导"),
        ],
        "downstream": [
            _n("聚酯（切片/瓶片/长丝）", NONE, "**断点**：PTA 最大下游，需求端核心",
               need="聚酯开工率 / 长丝短纤库存 / 瓶片现金流", hint="CCF、隆众", impact="high"),
            _n("PF 短纤", LIBRARY, "PF 在品种库，可取自各价格"),
        ],
    },
    "EG": {
        "name": "乙二醇",
        "reviewed": False,
        "upstream": [
            _n("乙烯", NONE, "油制路线原料",
               need="乙烯 CFR 东北亚价格", hint="Platts、ICIS", impact="medium"),
            _n("动力煤（煤制）", NONE, "煤制乙二醇路线成本",
               need="坑口动力煤价 / 煤制现金流", hint="隆众、CCTD", impact="medium"),
        ],
        "downstream": [
            _n("聚酯", NONE, "与 PTA 共用需求端",
               need="聚酯开工率", hint="CCF、隆众", impact="high"),
            _n("防冻液", NONE, "季节性小需求", need="防冻液产量", hint="隆众", impact="low"),
        ],
    },
    "EB": {
        "name": "苯乙烯",
        "reviewed": False,
        "upstream": [
            _n("BZ 纯苯", ACTIVE, "直接原料"),
            _n("乙烯", NONE,
               need="乙烯 CFR 东北亚价格", hint="Platts、ICIS", impact="medium"),
        ],
        "downstream": [
            _n("EPS / ABS / PS 树脂", NONE, "三大下游，需求端核心",
               need="EPS/ABS/PS 开工率与利润", hint="隆众、卓创", impact="high"),
        ],
    },
    "BZ": {
        "name": "纯苯",
        "reviewed": False,
        "upstream": [
            _n("石脑油", NONE, "**断点**：重整/裂解路线原料",
               need="CFR 日本石脑油价格", hint="Platts、隆众", impact="high"),
            _n("加氢苯", NONE, "焦化路线补充供应", need="加氢苯开工与价差", hint="隆众", impact="low"),
        ],
        "downstream": [
            _n("EB 苯乙烯", ACTIVE, "最大下游"),
            _n("苯胺 / 己内酰胺 / 苯酚", NONE,
               need="下游各品种开工率", hint="隆众、卓创", impact="medium"),
        ],
    },
    "BR": {
        "name": "丁二烯橡胶（顺丁）",
        "reviewed": False,
        "upstream": [
            _n("丁二烯", NONE, "**断点**：直接原料，占成本大头",
               need="丁二烯出厂价 / 港口库存", hint="隆众、卓创", impact="high"),
            _n("SC", ACTIVE, "经石脑油裂解间接传导"),
        ],
        "downstream": [
            _n("轮胎", NONE, "需求主驱动",
               need="全钢胎/半钢胎开工率", hint="隆众、卓创", impact="high"),
            _n("汽车产销", NONE, need="汽车产量与替换胎需求", hint="中汽协", impact="medium"),
        ],
    },
    "RU": {
        "name": "天然橡胶",
        "reviewed": False,
        "upstream": [
            _n("泰国/印尼胶园", EXTERNAL, "供给端核心，本系统未接入",
               need="泰国产区原料价 / 割胶进度 / 天气", hint="QinRex、IRCo", impact="high"),
            _n("合成胶（BR/SBR）", NONE, "替代品，与天胶价差影响需求",
               need="顺丁/丁苯价格", hint="隆众", impact="medium"),
        ],
        "downstream": [
            _n("轮胎", NONE, need="轮胎开工率与出口", hint="隆众、海关", impact="high"),
        ],
    },
    "NR": {
        "name": "20 号胶",
        "reviewed": False,
        "upstream": [
            _n("东南亚胶园", EXTERNAL, "与 RU 共用供给端",
               need="泰标/印标 20 号胶报价", hint="QinRex", impact="high"),
        ],
        "downstream": [
            _n("轮胎（出口导向）", NONE, need="轮胎出口量", hint="海关、隆众", impact="high"),
        ],
    },
    "M": {
        "name": "豆粕",
        "reviewed": False,
        "upstream": [
            _n("CBOT 大豆", EXTERNAL, "成本端核心，本系统未接入",
               need="CBOT 大豆期价 / 美豆压榨利润", hint="CME、USDA", impact="high"),
            _n("进口大豆到港", NONE, "到港量与港口库存",
               need="大豆到港量 / 港口库存 / 油厂开机率", hint="海关、Mysteel", impact="high"),
        ],
        "downstream": [
            _n("LH 生猪", ACTIVE, "最大下游，饲料需求"),
            _n("禽料 / 水产料", NONE, need="饲料产量结构", hint="饲料工业协会", impact="medium"),
        ],
    },
    "P": {
        "name": "棕榈油",
        "reviewed": False,
        "upstream": [
            _n("马来/印尼毛棕油", EXTERNAL, "定价锚在 BMD",
               need="BMD 毛棕油期价 / 马来 MPOB 库存", hint="BMD、MPOB", impact="high"),
        ],
        "downstream": [
            _n("食用油", NONE, need="餐饮与包装油需求", hint="国家粮油信息中心", impact="medium"),
            _n("生物柴油（B35/B40）", NONE, "政策驱动",
               need="印尼/马来生柴掺混政策与出口税", hint="MPOB、GAPKI", impact="high"),
        ],
    },
    "CF": {
        "name": "棉花",
        "reviewed": False,
        "upstream": [
            _n("籽棉 / 新疆种植", NONE, "产量与收购价",
               need="新疆产量预估 / 籽棉收购价 / 加工厂开机", hint="国家棉花市场监测系统", impact="high"),
        ],
        "downstream": [
            _n("纱线 / 坯布", NONE, need="纺纱开工率与即期利润", hint="TTEB、隆众", impact="high"),
            _n("服装出口", NONE, need="纺织服装出口额", hint="海关", impact="medium"),
        ],
    },
    "CJ": {
        "name": "红枣",
        "reviewed": False,
        "upstream": [
            _n("新疆枣园", NONE, "产量与品质看产区天气",
               need="新疆主产区天气 / 坐果率 / 库存", hint="新疆气象、卓创", impact="high"),
        ],
        "downstream": [
            _n("消费 / 礼品", NONE, "季节性极强",
               need="销区库存与走货速度", hint="卓创", impact="medium"),
        ],
    },
    "LH": {
        "name": "生猪",
        "reviewed": False,
        "upstream": [
            _n("M 豆粕", ACTIVE, "饲料成本之一"),
            _n("C 玉米", LIBRARY, "饲料成本主力，玉米在品种库"),
        ],
        "downstream": [
            _n("屠宰 / 白条", NONE,
               need="屠宰开工率 / 冻品库容率 / 白条价", hint="Mysteel、涌益", impact="high"),
        ],
    },
    "LC": {
        "name": "碳酸锂",
        "reviewed": False,
        "upstream": [
            _n("锂辉石（澳洲）", EXTERNAL, "硬岩路线成本",
               need="锂辉石 CIF 中国价 / 澳矿拍卖价", hint="SMM、Fastmarkets", impact="high"),
            _n("盐湖卤水（南美/青海）", NONE, "低成本供给",
               need="盐湖提锂成本与产量", hint="SMM", impact="medium"),
        ],
        "downstream": [
            _n("正极材料（铁锂/三元）", NONE,
               need="正极排产 / 磷酸铁锂开工", hint="SMM、鑫椤", impact="high"),
            _n("电池 / 新能源车", NONE, need="动力电池装机量", hint="中汽协、SNE", impact="medium"),
        ],
    },
    "SI": {
        "name": "工业硅",
        "reviewed": False,
        "upstream": [
            _n("硅石", NONE, need="硅石价格与品位", hint="SMM", impact="low"),
            _n("电力（云南/新疆）", NONE, "**断点**：成本大头是电，丰枯水期决定开工",
               need="云南丰枯水期电价 / 开工率", hint="百川、SMM", impact="high"),
        ],
        "downstream": [
            _n("PS 多晶硅", ACTIVE, "最大下游"),
            _n("有机硅", NONE, need="有机硅 DMC 开工与价格", hint="SMM", impact="medium"),
            _n("铝合金（AL）", LIBRARY, "AL 在品种库"),
        ],
    },
    "PS": {
        "name": "多晶硅",
        "reviewed": False,
        "upstream": [
            _n("SI 工业硅", ACTIVE, "直接原料"),
            _n("电力", NONE, "能耗成本占比高",
               need="多晶硅电价与能耗", hint="SMM", impact="medium"),
        ],
        "downstream": [
            _n("硅片 / 光伏组件", NONE, "**断点**：需求端核心，光伏装机驱动",
               need="硅片产量 / 组件排产 / 光伏装机", hint="硅业分会、PVInfoLink", impact="high"),
        ],
    },
}


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
def chain_of(code: str) -> dict[str, Any] | None:
    return CHAINS.get((code or "").strip().upper())


def nodes(code: str) -> list[dict[str, Any]]:
    """该品种链条上的全部环节（上游在前）。"""
    ch = chain_of(code) or {}
    return list(ch.get("upstream") or []) + list(ch.get("downstream") or [])


def missing_nodes(code: str) -> list[dict[str, Any]]:
    """本品种链条上**没有数据源**的环节（external / none）。"""
    return [n for n in nodes(code) if n.get("coverage") in _MISSING]


def gaps(only_active: bool = True, impact: str | None = None) -> list[dict[str, Any]]:
    """汇总「待改进环节」清单（供留档 / 数据源接入 TODO）。

    【为什么这是本模块的主要产出】这些环节不是"暂时没取到"，而是**系统里根本没有**。
      留档下来，以后接入新数据源（隆众、CCF、SMM、Platts…）时按清单补，
      补完把 `coverage` 从 none/external 改成 active/library 即可，结构不用动。
    """
    out: list[dict[str, Any]] = []
    for code, ch in CHAINS.items():
        if only_active and code not in _ACTIVE_POOL:
            continue
        for direction in ("upstream", "downstream"):
            for n in ch.get(direction) or []:
                if n.get("coverage") not in _MISSING:
                    continue
                p = n.get("pending") or {}
                if impact and p.get("impact") != impact:
                    continue
                out.append({
                    "variety": code,
                    "variety_name": ch.get("name", code),
                    "direction": direction,
                    "node": n["node"],
                    "coverage": n["coverage"],
                    "need": p.get("need", ""),
                    "hint": p.get("hint", ""),
                    "impact": p.get("impact", "medium"),
                })
    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda r: (order.get(r["impact"], 9), r["variety"], r["direction"]))
    return out


_ACTIVE_POOL = ("BR", "BU", "BZ", "CF", "CJ", "EB", "EG", "FU", "LC", "LH", "LU",
                "M", "NR", "P", "PG", "PS", "PX", "RU", "SC", "SI", "TA")


def coverage_stats() -> dict[str, int]:
    total = sum(len(chain_of(c).get("upstream") or []) + len(chain_of(c).get("downstream") or [])
                for c in _ACTIVE_POOL if chain_of(c))
    stat = {k: 0 for k in _COVERAGE}
    for c in _ACTIVE_POOL:
        for n in nodes(c):
            stat[n.get("coverage", NONE)] = stat.get(n.get("coverage", NONE), 0) + 1
    stat["total"] = total
    return stat


def report_markdown() -> str:
    """生成「待改进环节」留档报告（Markdown）。"""
    rows = gaps()
    stat = coverage_stats()
    lines = [
        "# 产业链待改进环节清单",
        "",
        "> 自动生成，勿手工编辑。数据源：`tradingagents/dataflows/commodity_chain.py`。",
        "> 状态：**draft**（模型起草，未经人工逐条复核）。",
        "",
        "## 覆盖统计（21 个活跃品种）",
        "",
        "| 环节类型 | 数量 | 含义 |",
        "|---|---|---|",
        f"| active | {stat.get('active', 0)} | 在活跃池，有完整取数与分析 |",
        f"| library | {stat.get('library', 0)} | 在品种库，能取价格，但不在日常分析池 |",
        f"| external | {stat.get('external', 0)} | 外盘/境外现货，本系统未接入 |",
        f"| none | {stat.get('none', 0)} | 环节存在，但没有任何数据源 |",
        f"| **合计** | **{stat.get('total', 0)}** | |",
        "",
        f"其中**无数据源**（external + none）共 **{len(rows)}** 个环节，按影响程度排序如下。",
        "接入后把 `coverage` 改成 `active` 或 `library`，本清单会自动减少。",
        "",
        "## 待改进清单",
        "",
        "| 影响 | 品种 | 环节 | 方向 | 缺什么数据 | 可去哪取 |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        d = "上游" if r["direction"] == "upstream" else "下游"
        lines.append(
            f"| {r['impact']} | {r['variety']} {r['variety_name']} | {r['node']} | {d} "
            f"| {r['need']} | {r['hint']} |"
        )
    lines += [
        "",
        "## 按数据源归类（便于谈数据接入）",
        "",
    ]
    by_hint: dict[str, list[str]] = {}
    for r in rows:
        by_hint.setdefault(r["hint"] or "未指定", []).append(f"{r['variety']}:{r['node']}")
    for hint, items in sorted(by_hint.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"- **{hint}**（{len(items)} 项）：{'、'.join(items)}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover - 手运行入口
    import sys

    if "--json" in sys.argv:
        print(json.dumps({"stats": coverage_stats(), "gaps": gaps()},
                         ensure_ascii=False, indent=1))
    elif "--write" in sys.argv:
        from datetime import date
        from pathlib import Path

        p = Path(__file__).resolve().parents[2] / "docs" / f"chain-gaps-{date.today().isoformat()}.md"
        p.write_text(report_markdown(), encoding="utf-8")
        print(f"-> {p}")
    else:
        print(report_markdown())
