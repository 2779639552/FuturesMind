# -*- coding: utf-8 -*-
"""清理"同一份研报被导入两次"的重复记录（DB + 聚合 JSON）。

判定（严）：归一后 source 相同 + publish_date 相同 + title 相同 + **extracted_text 完全相同**。
  · 只删"正文一模一样"的，标题相同但正文不同的（如发现报告 2026-09-02 那组，5067 vs 5080 字节）
    **一律不动**，避免误删两份不同批次/不同品种段的日报。
保留策略：varieties 覆盖更全 → conclusion_md 更长 → id 更小（先入库）。

用法：python probe/_dedup_identical_reports.py [--write]
"""
from __future__ import annotations

import collections
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import web_app as W  # noqa: E402

DB = Path(os.path.expanduser("~/.tradingagents/agentsense.db"))
AGG_DIR = Path(os.path.expanduser("~/.tradingagents/external_data"))


def main() -> int:
    write = "--write" in sys.argv
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    rows = con.execute("""SELECT id, variety, varieties, source, title, publish_date, report_type,
                                 status, extracted_text, conclusion_md
                          FROM research_reports WHERE status IN ('done','summarized')""").fetchall()

    groups: dict[tuple, list] = collections.defaultdict(list)
    for r in rows:
        txt = r["extracted_text"] or ""
        if not txt.strip():
            continue
        key = (W._normalize_source(r["source"] or ""),
               (r["publish_date"] or "")[:10],
               (r["title"] or "").strip(),
               hashlib.md5(txt.encode("utf-8")).hexdigest())   # ← 正文 md5 必须相同
        groups[key].append(r)

    plan = []      # [(keep_row, [drop_rows])]
    for key, g in groups.items():
        if len(g) < 2:
            continue
        keep = max(g, key=lambda r: (len((r["varieties"] or "").split(",")),
                                     len(r["conclusion_md"] or ""),
                                     -int(r["id"])))
        drops = [r for r in g if r["id"] != keep["id"]]
        plan.append((keep, drops))

    print("可安全去重的组数:", len(plan), " 待删记录:", sum(len(d) for _, d in plan))
    for keep, drops in plan:
        print("\n  保留 id=%s vars=%s | 删除 %s" % (keep["id"], keep["varieties"],
                                                  [d["id"] for d in drops]))
        print("    《%s》 %s %s" % ((keep["title"] or "")[:40], keep["publish_date"], keep["report_type"]))

    if not plan:
        return 0
    if not write:
        print("\n(dry-run：未改动；加 --write 执行)")
        return 0

    shutil.copy2(DB, str(DB) + ".bak_dedup_all_20261021")
    drop_ids = [int(d["id"]) for _, ds in plan for d in ds]
    cur = con.cursor()
    for rid in drop_ids:
        cur.execute("DELETE FROM research_reports WHERE id=?", (rid,))
    con.commit()
    print("\nDB 已删除 %d 条（备份 %s.bak_dedup_all_20261021）" % (len(drop_ids), DB))

    n_files = 0
    for f in sorted(AGG_DIR.glob("*_research.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        reps = data.get("reports") or []
        kept = [r for r in reps if int(r.get("id") or 0) not in drop_ids]
        if len(kept) != len(reps):
            data["reports"] = kept
            f.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            n_files += 1
            print("  %s 删除 %d 条" % (f.name, len(reps) - len(kept)))
    print("聚合文件改动:", n_files)

    # 复检
    left = con.execute(
        "SELECT COUNT(*) FROM research_reports WHERE id IN (%s)" % ",".join("?" * len(drop_ids)),
        drop_ids).fetchone()[0]
    print("复检：待删 id 仍存在 %d 条（应为 0）" % left)
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
