"""scripts/backfill_publish_date.py — 研报发布日期存量回填 + 聚合 JSON 重建(零 LLM)

【模块角色】
  publish_date 列(2026-09-06 新增)落地前的存量研报只有 uploaded_at(入库时间),
  日期分组混桶(如发布 9.3/9.4 的国君研报都挤进 9.4 桶)。本工具做两件事:
    1. DB 回填:publish_date='' 的行从 structured_data 顶层 publish_date
       (LLM 第一步抽取,YYYY-MM-DD)抄进列(与 database._backfill_publish_date
       同逻辑,双保险幂等;get_db() 构造时迁移也会自动跑一次);
    2. 聚合 JSON 重建:全部 done 研报逐份重调 web_app._write_research_aggregates,
       给 external_data/{CODE}_research.json 的聚合记录补 publish_date 键 ——
       观点总览(/api/research/views)的日期分组即按发布日生效。
       2026-09-06 扩展:同一轮把行内 report_type(日报/周报,2026-09-06 列新增
       后由迁移启发式+采集器打标)也写进聚合记录,周报徽标/每日总结过滤生效。
  纯 DB/文件重写,不调 LLM、不烧额度;幂等可重跑。

【用法】(在 AgentSense 根目录跑)
  python scripts/backfill_publish_date.py            # 回填 + 重建聚合 + 打印分布
  python scripts/backfill_publish_date.py --dry-run  # 只打印回填前后的日期桶分布

【注意】
  重建聚合取的结论来自 conclusion_md 的「## {code} 结论」段(web_app.
  _daily_variety_segment),与 _process_research_report 写聚合时的口径一致。
  旧聚合记录里可能存有比 DB 更新的字段(如 report_advice),重建以 DB 行为准
  全量覆盖 —— 与 re_extract/reconclude 的 upsert 行为相同。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 【调用】把 AgentSense 根目录加进搜索路径


def _sd_publish_date(row: dict) -> str:
    """structured_data 顶层 publish_date(格式合法才返回,否则空串)。"""
    import re  # 【调用包】日期格式校验

    try:
        sd = json.loads(row.get("structured_data") or "{}")
    except (TypeError, ValueError):
        return ""
    pd = str(sd.get("publish_date") or "").strip()[:10] if isinstance(sd, dict) else ""
    return pd if re.match(r"^\d{4}-\d{2}-\d{2}$", pd) else ""


def _bucket(rows: list[dict]) -> Counter:
    """按"有效日期"(publish_date 优先,回退 uploaded_at 前 10 位)统计行数分布。"""
    c: Counter = Counter()
    for r in rows:
        d = (r.get("publish_date") or "").strip()[:10] or (r.get("uploaded_at") or "")[:10]
        c[d] += 1
    return c


def _print_buckets(title: str, buckets: Counter):
    print(title)
    for d in sorted(buckets, reverse=True):
        print(f"  {d}: {buckets[d]}")


def main() -> int:
    ap = argparse.ArgumentParser(description="研报发布日期存量回填 + 聚合 JSON 重建(零 LLM)")
    ap.add_argument("--dry-run", action="store_true", help="只打印日期桶分布,不写库不重建")
    args = ap.parse_args()

    from database import get_db  # 【调用包】懒导入(get_db 构造即触发 _migrate 自动回填)
    from web_app import (  # 【调用包】懒导入:重模块
        _daily_variety_segment,
        _write_research_aggregates,
    )

    db = get_db()
    rows = db.list_research_reports(limit=2000)
    _print_buckets("回填前日期桶分布(publish_date 优先):", _bucket(rows))

    # 1) DB 回填:迁移已兜过一遍,这里补漏并统计命中数(幂等,空串条件天然只扫未回填行)
    done_rows = []
    backfilled = 0
    with db._conn() as c:
        for r in rows:
            if r.get("status") == "done":
                done_rows.append(r)
            if not (r.get("publish_date") or "").strip():
                pd = _sd_publish_date(r)
                if pd:
                    c.execute("UPDATE research_reports SET publish_date=? WHERE id=?", (pd, r["id"]))
                    backfilled += 1
    print(f"DB 回填: 本次补写 {backfilled} 行")

    rows = db.list_research_reports(limit=2000)
    _print_buckets("回填后日期桶分布:", _bucket(rows))

    if args.dry_run:
        print("Dry-run 结束(未重建聚合)。")
        return 0

    # 2) 聚合 JSON 重建:全部 done 行按品种覆盖写(upsert 按 id 替换,不产生重复条目)
    rebuilt = failed = 0
    for r in done_rows:
        try:
            sd = json.loads(r.get("structured_data") or "{}")
        except (TypeError, ValueError):
            sd = {}
        varieties = [v for v in (sd.get("varieties") or []) if isinstance(v, dict) and v.get("variety")]
        if not varieties:
            print(f"  ! #{r['id']} 无 varieties,跳过聚合重建")
            failed += 1
            continue
        codes = [v["variety"] for v in varieties]
        md = r.get("conclusion_md") or ""
        conclusions = {code: _daily_variety_segment(md, code) for code in codes}
        _write_research_aggregates(
            r["id"],
            r.get("uploaded_at") or "",
            r.get("title") or "",
            r.get("source") or "",
            codes,
            varieties,
            conclusions,
            publish_date=(r.get("publish_date") or "").strip()[:10],
            report_type=(r.get("report_type") or "").strip(),
        )
        rebuilt += 1
    print(f"聚合 JSON 重建: {rebuilt} / 失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 【调用】Windows 控制台 UTF-8
    raise SystemExit(main())
