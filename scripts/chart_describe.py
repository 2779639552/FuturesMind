"""scripts/chart_describe.py — 位图图表视觉重述批量回填(方案三补充:32 份位图图表研报)

【模块角色】
  Phase A(pdf_layout.extract_layout_text)只覆盖矢量图表;32/76 份研报的图表是
  整块位图,轴数字在像素里。本工具把位图区域渲染成 PNG 交给**视觉模型**重述成
  2~3 句中文(主题/趋势/明确可读的数值,读不清禁编造),追加到 layout_text 的
  「图表视觉重述」节 → backfill --force 后可被 RAG 检索。

  【核心逻辑在 tradingagents/dataflows/chart_vision.py】(web_app 上传钩子共用,
  本脚本只是批量 CLI 壳:选行 + 并发 + 统计)。视觉后端/提示词/追加重述节的
  组装见该模块 docstring。

【用法】(在 AgentSense 根目录跑)
  python scripts/chart_describe.py --ids 178      # 先试单份看质量
  python scripts/chart_describe.py                # 全量:done+.pdf 且 layout 无重述节
  python scripts/chart_describe.py --force        # 重算(含已重述的)

【注意】
  layout_text 每次从 PDF 现算(幂等,重述节不叠加);workers ≤4(LLM 并发上限);
  单图失败只跳过不占位。跑完需 python scripts/backfill_rag_index.py --force 重建。
"""

from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 【调用】把 AgentSense 根目录加进搜索路径

from tradingagents.dataflows.chart_vision import (  # noqa: E402
    SECTION_MARK,
    build_layout_with_vision,
)


def _process_report(row: dict, force: bool, max_charts: int = 0) -> dict:
    """单份:跳过已重述 → build_layout_with_vision(empty_marker=True 幂等占位) → 落库。"""
    from database import get_db

    rid = int(row["id"])
    path = Path(row.get("file_path") or "")
    db = get_db()
    if not force and SECTION_MARK in (row.get("layout_text") or ""):
        return {"rid": rid, "status": "skipped"}
    if not path.exists():
        return {"rid": rid, "status": "missing"}
    layout = build_layout_with_vision(path, max_charts=max_charts, empty_marker=True)
    if layout is None:
        return {"rid": rid, "status": "no_bitmap"}
    db.update_research_report(rid, layout_text=layout[:60000])
    return {"rid": rid, "status": "done"}


def main() -> int:
    ap = argparse.ArgumentParser(description="位图图表视觉重述 → layout_text(零 LLM 额度,本地 Ollama 默认)")
    ap.add_argument("--ids", default="", help="只处理指定 report_id,如 178,132")
    ap.add_argument("--force", action="store_true", help="已重述的报告也重算")
    ap.add_argument("--workers", type=int, default=2, help="并发数(≤4)")
    ap.add_argument("--max-charts", type=int, default=0,
                    help="每份报告最多重述的图表数(0=全部;8≈web_app 钩子上限,省一半时长)")
    ap.add_argument("--dry-run", action="store_true", help="只统计位图图表数,不调视觉模型")
    args = ap.parse_args()

    from database import get_db
    from tradingagents.dataflows.pdf_layout import bitmap_chart_regions

    db = get_db()
    wanted = {int(x) for x in args.ids.split(",") if x.strip()}
    rows = [
        r
        for r in db.list_research_reports(limit=2000)
        if r.get("status") == "done"
        and (r.get("file_path") or "").lower().endswith(".pdf")
        and (not wanted or int(r.get("id") or 0) in wanted)
    ]
    # 【关键逻辑】新报告优先(id 递增 ≈ 上传时间序):检索价值随时间衰减,
    # 批量中断时最有用的报告已处理完
    rows.sort(key=lambda r: -int(r["id"]))
    print(f"待处理 {len(rows)} 份(backend={os.environ.get('RAG_VISION_BACKEND', 'ollama')}, "
          f"model={os.environ.get('RAG_VISION_MODEL', 'qwen3-vl:4b')}, dry_run={args.dry_run})")

    if args.dry_run:
        total = 0
        for r in rows:
            n = len(bitmap_chart_regions(Path(r["file_path"])))
            if args.max_charts:
                n = min(n, args.max_charts)
            total += n
            if n:
                print(f"  id={r['id']} 图表 {n} 个")
        print(f"合计位图图表 {total} 个")
        return 0

    stats: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 4))) as ex:
        for res in ex.map(lambda r: _process_report(r, args.force, args.max_charts), rows):
            stats[res["status"]] = stats.get(res["status"], 0) + 1
            if res["status"] == "done":
                print(f"  id={res['rid']}: 完成")

    print(f"完成: {stats}")
    print("下一步: python scripts/backfill_rag_index.py --force 重建向量索引")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 【调用】Windows 控制台 UTF-8
    raise SystemExit(main())
