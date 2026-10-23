# -*- coding: utf-8 -*-
"""清理重复入库：中信期货《资金情绪爆发，橡胶板块节前领涨商品——周报20260927》导入两次。

证据：
  · 1345 与 1496 的 extracted_text 长度与 md5 **完全相同**（b37f8beb…，30293 字节）
    ⇒ 同一份 PDF，一个用原始中文文件名入库，一个被重命名成 `20260927_2.pdf` 再入库；
  · 来源写法不同（中信期货 / 中信期货研究所），`_normalize_source` 归一后都是"中信期货"，
    所以观点总览里出现两家同名行、内容一模一样。

保留 1496（varieties 覆盖 RU,NR,BR 更全、conclusion_md 更长），删除 1345：
  ① DB 删行；② 所有 {CODE}_research.json 里删除该 id 的条目。
物理 PDF 不删（file_deleted 保持原样，避免误删原件）。

用法：python probe/_dedup_zhongxin_rubber_20260927.py [--write]
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

DROP_ID = 1345
KEEP_ID = 1496
DB = Path(os.path.expanduser("~/.tradingagents/agentsense.db"))
AGG_DIR = Path(os.path.expanduser("~/.tradingagents/external_data"))


def main() -> int:
    write = "--write" in sys.argv
    con = sqlite3.connect(str(DB))
    cur = con.cursor()
    row = cur.execute("SELECT id, title, varieties, source, status FROM research_reports WHERE id=?", (DROP_ID,)).fetchone()
    if not row:
        print("DB 里已无 id=%d，可能已清理过" % DROP_ID)
        return 0
    print("待删行:", row)

    # ① 聚合 JSON
    hit_files = []
    for f in sorted(AGG_DIR.glob("*_research.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        reps = data.get("reports") or []
        kept = [r for r in reps if int(r.get("id") or 0) != DROP_ID]
        if len(kept) != len(reps):
            hit_files.append((f.name, len(reps) - len(kept)))
    print("聚合文件命中:", hit_files)

    if not write:
        print("(dry-run：未改动；加 --write 执行)")
        return 0

    shutil.copy2(DB, str(DB) + ".bak_dedup_20261021")
    cur.execute("DELETE FROM research_reports WHERE id=?", (DROP_ID,))
    con.commit()
    print("DB 已删除 id=%d（备份 %s.bak_dedup_20261021）" % (DROP_ID, DB))

    for fname, n in hit_files:
        f = AGG_DIR / fname
        data = json.loads(f.read_text(encoding="utf-8"))
        data["reports"] = [r for r in (data.get("reports") or []) if int(r.get("id") or 0) != DROP_ID]
        f.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print("  %s 删除 %d 条" % (fname, n))

    # 验证
    left = cur.execute("SELECT COUNT(*) FROM research_reports WHERE id IN (?,?)", (DROP_ID, KEEP_ID)).fetchone()[0]
    print("验证：DB 中该两条只剩 %d 条（应为 1）" % left)
    for code in ("BR", "RU"):
        fp = AGG_DIR / f"{code}_research.json"
        if not fp.exists():
            continue
        reps = json.loads(fp.read_text(encoding="utf-8")).get("reports") or []
        n = sum(1 for r in reps if "资金情绪爆发" in str(r.get("title") or ""))
        print("  聚合 %s：同名条目 %d 条" % (code, n))
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
