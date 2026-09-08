"""scripts/reextract_layout.py — 存量研报版面感知重提取(零 LLM,纯 CPU)

【模块角色】
  2026-09-08 方案三:PDF 图表信息纳入 RAG。linear 文本倾倒(extracted_text)会把
  图表撕成轴数字块并被过滤,图表主题丢失。tradingagents/dataflows/pdf_layout.py
  用 get_drawings 矢量聚类把图表聚成【图】块、表格聚成【表】块,产出 layout_text。
  本工具给存量 done 研报回填 layout_text 列(新上传由 _process_research_report
  自动落库,无需本脚本);只处理 .pdf 原件,.md 研报无图表直接跳过。

【用法】(在 AgentSense 根目录跑)
  python scripts/reextract_layout.py            # 遍历 done+.pdf,回填 layout_text
  python scripts/reextract_layout.py --dry-run  # 只统计,不写库
  python scripts/reextract_layout.py --id 176   # 只处理指定 report_id

【注意】
  纯 PyMuPDF 矢量解析,零 LLM 零联网,~1s/份;回填后需跑
  python scripts/backfill_rag_index.py --force 重建向量索引才生效。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 【调用】把 AgentSense 根目录加进搜索路径


def main() -> int:
    ap = argparse.ArgumentParser(description="存量研报 layout_text 回填(版面感知提取,零 LLM)")
    ap.add_argument("--dry-run", action="store_true", help="只统计可处理份数,不写库")
    ap.add_argument("--id", type=int, default=0, help="只处理指定 report_id")
    args = ap.parse_args()

    from database import get_db  # 【调用包】数据库实例(读列表/写 layout_text)
    from tradingagents.dataflows.pdf_layout import extract_layout_text

    db = get_db()
    rows = [
        r
        for r in db.list_research_reports(limit=2000)
        if r.get("status") == "done"
        and (not args.id or int(r.get("id") or 0) == args.id)
        and (r.get("file_path") or "").lower().endswith(".pdf")
    ]
    print(f"待处理 done+.pdf 研报 {len(rows)} 份(dry_run={args.dry_run})")

    ok = empty = missing = 0
    for i, r in enumerate(rows, 1):
        rid = int(r["id"])
        path = Path(r.get("file_path") or "")
        if not path.exists():
            missing += 1
            print(f"  [{i}] id={rid} 文件缺失 {path.name}")
            continue
        text = extract_layout_text(path).strip()
        if not text:
            empty += 1
            print(f"  [{i}] id={rid} 提取为空 {path.name}")
            continue
        if not args.dry_run:
            db.update_research_report(rid, layout_text=text[:60000])
        ok += 1
        if i % 20 == 0 or i == len(rows):
            print(f"  进度 {i}/{len(rows)} (report_id={rid})")

    print(f"完成: 成功 {ok} / 空提取 {empty} / 文件缺失 {missing}")
    if not args.dry_run:
        print("下一步: python scripts/backfill_rag_index.py --force 重建向量索引")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 【调用】Windows 控制台 UTF-8
    raise SystemExit(main())
