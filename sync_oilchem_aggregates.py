# -*- coding: utf-8 -*-
"""聚合 JSON 与 DB 对账同步（品种归属修正的收尾，零 LLM）

【要解决什么】`repair_oilchem_variety.py` 改完 DB 的 variety/structured_data 后，
`external_data/{CODE}_research.json` 里还留着**陈旧的品种条目**：
  · 被标 skipped 的行（1970/1975/1979/1992/1993）→ 仍挂在 RU/BU/EB 等文件里；
  · 被改归属的行（1976 的 DOTP、1980 的 SH、1983 的 IR）→ 仍挂在**旧品种**文件里；
  · 被剔除的池外码（1968 的 AU、1981/1988/1995 的 MA/PP、1991 的 A/MMA/MIBK）
    → 仍挂在那些**池外品种**文件里。
不清理的话，按品种检索仍会捞出已作废的观点（= 修复只做了一半）。

【做法】以 DB 为唯一真值，两步：
  ① `sweep_orphan_reports(valid_ids)`：丢掉 report_id 已不是 done 的条目（复用项目自带函数）；
  ② 对 `source LIKE '隆众%'` 的行做**精确对账**：某行的 structured_data.varieties 里没有的品种，
     就把该行从那个品种的聚合里摘掉（调 `remove_research_report`）。
     ⚠️ 只对隆众行做精确对账 —— 其他来源不动，避免过度删除。

【⚠️ 作用域严格限定】只处理 TARGET_IDS（= repair 脚本刚改过的那批行）。
  实测 dry-run 时会扫描出**大量与本改动无关的历史孤儿**（UR/V/Y/ZC/ZN 等池外品种、
  report_id 953~1535 的老行）——那是项目**既有**的不一致，清理它远超本次范围且风险不可控。
  ⇒ 用 `--all` 才会处理全部孤儿；默认只动本次改动波及的行。既有的历史孤儿只在报告里提示。

【安全性】① 先整体备份 external_data 到 zip；② 支持 --dry-run；③ 只动隆众相关条目。

用法:
    python sync_oilchem_aggregates.py            # dry-run（仅本次改动范围）
    python sync_oilchem_aggregates.py --apply
    python sync_oilchem_aggregates.py --all      # 连历史孤儿一起清（谨慎）
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sqlite3
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# 本次归属修正波及的行（见 repair_oilchem_variety.py 的决策表与 --apply 输出）
TARGET_IDS = {
    1966, 1968, 1970, 1975, 1976, 1979, 1980, 1981,
    1983, 1988, 1991, 1992, 1993, 1995,
}


def db_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".tradingagents", "agentsense.db")


def main() -> int:
    ap = argparse.ArgumentParser(description="聚合 JSON 与 DB 对账同步")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--source-like", default="隆众%", help="精确对账的行来源(默认 隆众%%)")
    ap.add_argument("--all", action="store_true",
                    help="连历史孤儿(非本次改动)一起清理 —— 谨慎，会动到其他来源")
    a = ap.parse_args()

    from tradingagents.dataflows import research_data as rd

    # ── 读 DB 真值 ──
    # ⚠️★ valid 必须 = done + summarized。**别写成只算 done** ——
    #   项目里 `summarized`（885 行）是 done **之后**的合法状态，实测 884/885 有真实结论。
    #   若只认 done，会把 885 条合法条目当孤儿 ⇒ 一旦跑 --all 就误删大量真实数据。
    #   该口径与项目自带 `_repair_research_aggregates`（web_app.py:7066）逐字一致。
    VALID_STATUS = ("done", "summarized")
    c = sqlite3.connect(db_path(), timeout=30)
    c.row_factory = sqlite3.Row
    valid_ids = {r[0] for r in c.execute(
        "SELECT id FROM research_reports WHERE status IN (?,?)", VALID_STATUS)}
    # 隆众有效行 → 其应有的品种集合
    # ⚠️★ 必须**同时**取 `variety` 列与 structured_data.varieties：
    #   实测 id=1964（钴酸锂→LC）的 structured_data.varieties = **[] 空**（LLM 没输出，
    #   由 `_recover_variety_from_text` 关键词兜底直接设了 variety=LC）。
    #   只看 varieties 会把这条**合法**的 LC 聚合项误判成孤儿（实测踩过一次假阳性）。
    oc_varieties: dict[int, set[str]] = {}
    for r in c.execute("SELECT id, variety, structured_data FROM research_reports "
                       "WHERE status IN (?,?) AND source LIKE ?",
                       (*VALID_STATUS, a.source_like)):
        vs: set[str] = set()
        if (r["variety"] or "").strip():
            vs.add(str(r["variety"]).strip().upper())      # ← 权威主品种
        try:
            sd = json.loads(r["structured_data"] or "{}")
        except Exception:
            sd = {}
        vs |= {str(v.get("variety") or "").upper() for v in (sd.get("varieties") or [])}
        vs.discard("")
        oc_varieties[r["id"]] = vs
    c.close()

    print("=" * 84)
    print("聚合 JSON 对账同步 | 有效 done 行 %d | 隆众 done 行 %d | %s"
          % (len(valid_ids), len(oc_varieties), "【实际执行】" if a.apply else "【DRY-RUN】"))
    print("=" * 84)

    research_dir = rd.RESEARCH_DIR
    files = sorted(research_dir.glob("*_research.json"))
    print("扫描 %d 个聚合文件\n" % len(files))

    plan: list[tuple[str, int, str]] = []   # (code, report_id, 原因)
    unrelated = 0                            # 与本次改动无关的历史孤儿计数(只报告不动)

    for fp in files:
        code = (fp.stem.replace("_research", "")).upper()
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        reports = data.get("reports") if isinstance(data, dict) else None
        if not isinstance(reports, list):
            continue
        for rep in reports:
            rid = rep.get("id")
            if rid is None:
                continue
            in_scope = rid in TARGET_IDS
            # ① 非 done ⇒ 孤儿
            if rid not in valid_ids:
                if in_scope or a.all:
                    plan.append((code, rid, "report 已非 done(被 skipped/删除)"))
                else:
                    unrelated += 1
                continue
            # ② 隆众行精确对账:该行不该出现在这个品种里
            if rid in oc_varieties and code not in oc_varieties[rid]:
                if in_scope or a.all:
                    plan.append((code, rid, "该行的 varieties 已不含 %s" % code))
                else:
                    unrelated += 1

    if not a.all:
        print("  作用域: 仅本次改动波及的 %d 行（--all 可扩到全部孤儿）" % len(TARGET_IDS))
    if unrelated:
        print("  ⚠️ 另有 %d 条**与本次改动无关的历史孤儿**（池外品种/早期行）——"
              "属项目既有不一致，本次不动；如需清理加 --all（谨慎）" % unrelated)
    print()

    if not plan:
        print("对账干净，无需清理。")
        return 0

    print("待清理条目 %d 个：" % len(plan))
    for code, rid, why in plan:
        print("  %-6s report_id=%-6s %s" % (code, rid, why))

    if not a.apply:
        print("\n（dry-run，未改动。加 --apply 执行）")
        return 0

    # ── 备份 ──
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = Path.home() / ".tradingagents" / "oilchem" / "backups" / ("external_data_%s.zip" % stamp)
    bak.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bak, "w", zipfile.ZIP_DEFLATED) as z:
        for fp in files:
            z.write(fp, fp.name)
    print("\n  备份: %s（%d 个文件）" % (bak, len(files)))

    # ── 执行 ──
    from collections import defaultdict
    by_code: dict[str, list[int]] = defaultdict(list)
    for code, rid, _ in plan:
        by_code[code].append(rid)

    removed = 0
    for code, rids in by_code.items():
        for rid in rids:
            try:
                rd.remove_research_report(code, rid)   # 自带缓存清理 + 删空文件
                removed += 1
            except Exception as e:  # noqa: BLE001
                print("  ! 移除失败 %s/%s: %s" % (code, rid, e))
    print("  已移除条目: %d" % removed)

    # ── 兜底:按 valid_ids 扫一遍剩余孤儿(含非隆众来源) ──
    if a.all:
        n = rd.sweep_orphan_reports(valid_ids)
        print("  额外孤儿清扫: %d 条" % n)
    else:
        print("  （未做全库孤儿清扫；作用域外的历史孤儿保持原样）")
    print("\n  完成。回滚: 解压 %s 覆盖回 %s" % (bak, research_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
