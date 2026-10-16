# -*- coding: utf-8 -*-
"""隆众资讯入库行 —— 品种归属修正（一次性存量修复，零 LLM）

【背景】接入隆众后全量质检发现系统性归属缺陷，两种机制：
  ① LLM 列出多品种、首项是池外/上游品种 ⇒ `variety` 列（= codes[0]）指错。
     但 conclusion_md 里**已含正确的池内段落** ⇒ 改列即可，不必重跑 LLM。
  ② LLM 返回空 varieties，走 `_recover_variety_from_text` 关键词兜底；
     隆众文章习惯提「橡胶/塑料」作为**下游需求方** ⇒ 兜底表误判成 RU
     （实测：基础油 ×5、磷酸 ×3、成品油 ×4、醚后 ×4 次"橡胶"）。
     这类主品种段落全是「未披露…无法判断」空转 ⇒ 标 skipped。

【判据】主品种段落里「未披露/无法判断/无法评估/均无」出现 >=7 次 ⇒ 空转 ⇒ 丢；
        否则视为实质内容 ⇒ 改归属保留。

【安全性】① 先备份原值到 JSON，可回滚；② 支持 --dry-run；③ 只动 source LIKE '隆众%' 的行；
        ④ 不碰 conclusion_md（它对多品种行本来就是完整的）。

用法:
    python repair_oilchem_variety.py            # dry-run，只打印计划
    python repair_oilchem_variety.py --apply    # 实际执行(先自动备份)
    python repair_oilchem_variety.py --rollback <备份文件>
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

SOURCES = ("隆众%",)
BOILER_RE = re.compile(r"未披露|无法判断|无法评估|均无")
CONCRETE_RE = re.compile(r"\d[\d,\.]*\s*(?:元/吨|元|万吨|吨|%|美元)")

# ═══════════════════════════════════════════════════════════════════════════
# 决策表 —— 来自对全部 24 条 done 行的**逐行人工复核**（证据见诊断文档）
#
# 【为什么用显式表而不是通用启发式】试用过"空转词计数"，但它会误杀归属本就正确的行：
#   · id=1964（钴酸锂→LC）归属正确，只是 LLM 没抽到现货价 ⇒ 空转 7 个。
#     但那是 **LLM 抽取质量问题**，不属于"归属修复"范围，不该丢。
#   · id=1970/1975/1992/1993 是**真误配**（主体品种在池外），空转 9~14 个且无具体数据。
#   ⇒ "空转"只能用来判"误配行值不值得改"，不能用来丢弃归属正确的行。
#   一次性存量修复用显式表最精确、可审计、可复跑。
# ═══════════════════════════════════════════════════════════════════════════

# 丢弃：文章主体在池外，且目标段落通篇"未披露"无可用观点
SKIP: dict[int, str] = {
    1970: "[基础油] 主体池外；BU 段落 9 个未披露、无一条具体数据",
    1975: "[磷酸] 主体池外；RU 段落 9 个未披露（'橡胶'仅为下游提及）",
    1979: "[顺酐/UPR] 主体池外；EB 段落 10 个未披露",
    1992: "[成品油/汽车] 主体池外；RU 段落 10 个未披露",
    1993: "[醚后碳四] 主体池外；RU 段落 14 个未披露",
}

# 改归属：池内段落有实质内容
REPOINT: dict[int, tuple[str, str]] = {
    1966: ("TA", "主体=涤纶长丝(聚酯下游)，池内锚点取 TA；LLM 把上游 SC 排在前"),
    1976: ("TA", "LLM 列 [DOTP,TA,PX,SC]，DOTP 池外；TA 段含 PTA 开工率/检修实质数据"),
    1980: ("LC", "LLM 列 [SH,LC]，SH(烧碱)池外；LC 段含磷酸铁锂排产对碳酸锂需求"),
    1983: ("RU", "LLM 列 [IR,RU]，IR 池外；RU 段含天然橡胶供应/轮胎开工数据"),
}

# 明确保留（含归属正确但结论稀疏者，其问题不在归属，另行处理）
KEEP_EXPLICIT: dict[int, str] = {
    1964: "归属正确(钴酸锂→LC)；结论稀疏属 LLM 抽取质量问题，不属本次修复范围",
}

# 人工覆写表：LLM 的品种顺序不可靠（1966 列成 [SC,PX,TA]，上游在前），
# 但文章主体是「涤纶长丝」(聚酯下游)，池内锚点取最贴近的 TA。
# 理由记录在案，便于复核。
OVERRIDE_PRIMARY = {  # 已被 REPOINT 取代，保留仅为兼容
    1966: "TA",   # [隆众聚焦]：成本向上、需求向下，涤纶长丝…→ 主体涤纶长丝,池内锚点 TA
}

BACKUP_DIR = Path.home() / ".tradingagents" / "oilchem" / "backups"


def db_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".tradingagents", "agentsense.db")


def connect() -> sqlite3.Connection:
    c = sqlite3.connect(db_path(), timeout=30)
    c.row_factory = sqlite3.Row
    return c


def primary_section(conclusion_md: str, variety: str) -> str:
    """取 `## <variety> 结论` 段落正文。"""
    if not conclusion_md or not variety:
        return ""
    m = re.search(r"##\s*" + re.escape(variety) + r"\s*结论([\s\S]*?)(?=\n##\s*[A-Z]{1,4}\s*结论|\Z)",
                  conclusion_md)
    return m.group(1) if m else ""


def sections(conclusion_md: str) -> list[str]:
    return re.findall(r"##\s*([A-Z]{1,4})\s*结论", conclusion_md or "")


def plan(pool: set[str]) -> list[dict]:
    """产出修正计划（不写库）。"""
    c = connect()
    rows = list(c.execute(
        "SELECT id, variety, structured_data, conclusion_md, title, status, error "
        "FROM research_reports WHERE source LIKE ? ORDER BY id", SOURCES))
    c.close()

    out = []
    for r in rows:
        rid, var = r["id"], (r["variety"] or "").strip()
        sd_raw = r["structured_data"] or "{}"
        try:
            sd = json.loads(sd_raw)
        except Exception:
            sd = {}
        vs = [str(v.get("variety") or "").upper() for v in (sd.get("varieties") or [])]
        secs = sections(r["conclusion_md"])
        inpool = [s for s in secs if s in pool]

        item = {"id": rid, "title": (r["title"] or "")[:46], "old_variety": var,
                "old_status": r["status"], "sections": secs, "inpool": inpool,
                "llm_varieties": vs, "action": "keep", "new_variety": var, "reason": ""}

        if r["status"] == "skipped":
            item["reason"] = "已是 skipped，不动"
            out.append(item)
            continue

        # ① 显式丢弃
        if rid in SKIP:
            item.update(action="skip", reason="人工复核：%s" % SKIP[rid])
            out.append(item)
            continue

        # ② 显式改归属
        if rid in REPOINT:
            target, why = REPOINT[rid]
            kept = [v for v in vs if v in pool] or list(dict.fromkeys(inpool))
            item.update(action="repoint", new_variety=target,
                        new_varieties=kept,
                        reason="人工复核：%s（%s → %s）" % (why, var or "-", target))
            out.append(item)
            continue

        # ③ 其余：归属不动，仅剔除 structured_data 里的**池外码**
        #    （池外码会产生"不可达"的聚合项，实测污染了 DOTP/UPR/IR 等 json）
        dropped = [v for v in vs if v not in pool]
        if dropped:
            item.update(action="repoint", new_variety=var,
                        new_varieties=[v for v in vs if v in pool],
                        reason="仅剔除池外码 %s" % ",".join(dropped))
        else:
            item["reason"] = KEEP_EXPLICIT.get(rid, "归属正确，无需改动")
        out.append(item)
    return out


def apply_plan(items: list[dict]) -> str:
    """执行计划。先备份原值，返回备份文件路径。"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = BACKUP_DIR / ("variety_repair_%s.json" % stamp)

    c = connect()
    # ── 备份 ──
    backup = {"ts": stamp, "rows": []}
    for it in items:
        if it["action"] == "keep":
            continue
        row = c.execute("SELECT id,variety,status,error,structured_data FROM research_reports WHERE id=?",
                        (it["id"],)).fetchone()
        backup["rows"].append({k: row[k] for k in row.keys()})
    with open(bak, "w", encoding="utf-8") as f:
        json.dump(backup, f, ensure_ascii=False, indent=1)
    print("  备份已写入: %s（%d 行）" % (bak, len(backup["rows"])))

    # ── 执行 ──
    done = 0
    for it in items:
        if it["action"] == "keep":
            continue
        rid = it["id"]
        if it["action"] == "skip":
            c.execute("UPDATE research_reports SET status='skipped', error=? WHERE id=?",
                      ("[归属质检 2026-10-14] " + it["reason"], rid))
        else:
            sets, params = ["variety=?"], [it["new_variety"]]
            if "new_varieties" in it:
                sd = json.loads(c.execute(
                    "SELECT structured_data FROM research_reports WHERE id=?", (rid,)).fetchone()[0] or "{}")
                keep = set(it["new_varieties"])
                sd["varieties"] = [v for v in (sd.get("varieties") or [])
                                   if str(v.get("variety") or "").upper() in keep]
                sets.append("structured_data=?")
                params.append(json.dumps(sd, ensure_ascii=False))
            params.append(rid)
            c.execute("UPDATE research_reports SET %s WHERE id=?" % ",".join(sets), params)
        done += 1
    c.commit()
    c.close()
    return str(bak)


def rollback(path: str) -> None:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    c = connect()
    for row in data["rows"]:
        c.execute("UPDATE research_reports SET variety=?, status=?, error=?, structured_data=? WHERE id=?",
                  (row["variety"], row["status"], row["error"], row["structured_data"], row["id"]))
    c.commit()
    c.close()
    print("  已回滚 %d 行（来自 %s）" % (len(data["rows"]), path))


def main() -> int:
    ap = argparse.ArgumentParser(description="隆众入库行品种归属修正")
    ap.add_argument("--apply", action="store_true", help="实际执行（默认只 dry-run）")
    ap.add_argument("--rollback", metavar="BACKUP", help="按备份文件回滚")
    a = ap.parse_args()

    if a.rollback:
        rollback(a.rollback)
        return 0

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from tradingagents.dataflows.commodity_futures import ACTIVE_VARIETIES as POOL
    pool = set(POOL)

    items = plan(pool)
    print("=" * 96)
    print("隆众入库行品种归属修正  |  池内品种 %d 个  |  %s"
          % (len(pool), "【实际执行】" if a.apply else "【DRY-RUN 仅预览】"))
    print("=" * 96)
    for it in items:
        tag = {"keep": "  保持", "repoint": "🔧改归属", "skip": "❌ 丢弃"}[it["action"]]
        if it["action"] == "keep":
            print("%s id=%-5s [%-5s] %s" % (tag, it["id"], it["old_variety"] or "-", it["title"]))
        else:
            print("%s id=%-5s [%s → %s] %s" % (tag, it["id"], it["old_variety"] or "-",
                                              it.get("new_variety") or "skipped", it["title"]))
            print("           原因: %s" % it["reason"])

    from collections import Counter
    cnt = Counter(it["action"] for it in items)
    print("-" * 96)
    print("汇总: 保持 %d | 改归属 %d | 丢弃 %d | 共 %d"
          % (cnt["keep"], cnt["repoint"], cnt["skip"], len(items)))

    if a.apply:
        bak = apply_plan(items)
        print("\n  已执行。回滚命令: python repair_oilchem_variety.py --rollback %s" % bak)
    else:
        print("\n  （dry-run，未改动。加 --apply 执行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
