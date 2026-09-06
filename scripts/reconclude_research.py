"""scripts/reconclude_research.py — 研报逐品种「核心观点」只重跑工具(不重跑品种识别)

【模块角色】
  _process_research_report 全链路(提取→LLM 识别品种→结构化→逐品种观点→落库)在
  「核心观点」提示词升级后(2026-09-02 两段式:第一部分=固定六小节多角度核心观点
  ~200 字;第二部分=补回 数据支撑/与系统自动分析的潜在分歧/建议权重 三节),
  历史已 done 的研报行仍存旧口径观点。本工具对选定研报只重跑结论步骤:
    web_app.reconclude_research_report(rid)
  读已入库行的 extracted_text + structured_data(varieties),用当前
  _llm_opinion_conclusion 提示词重新生成逐品种观点,回写 conclusion_md
  (status 保持 done,不动 structured_data/标题/方向),并按品种覆盖聚合 JSON 的
  conclusion 字段(upsert 按聚合记录 id 替换,不产生重复条目)。
  消费端(研报详情/未来观点表格)零改动,直接读到新版观点。

【用法】(在 AgentSense 根目录跑)
  python scripts/reconclude_research.py --ids "47 53 54"                       # 白名单优先
  python scripts/reconclude_research.py --source 华泰期货 --date 2026-09-02 --skip-fresh  # 分层批量(跳过已刷新)
  python scripts/reconclude_research.py --date 2026-09-02 --skip-fresh --limit 10
  python scripts/reconclude_research.py --ids "47 53 54" --dry-run             # 只列选中,不调 LLM

【选择规则】
  在全表 status=='done' 行中,--ids / --date / --source 取并集(任一无则按其余筛);
  三者都缺省则拒绝执行(防止误把整库重跑)。日期取 structured_data.publish_date
  (YYYY-MM-DD),缺则回退 uploaded_at 当天;source 为子串匹配(大小写不敏感)。
  --skip-fresh:排除已按最终两段式刷新且不含"观点生成失败"占位(见 _is_final_fmt)
  的行——分层批量时自动跳过已确认的行,不重复刷新。按 id 升序输出,
  --limit 截断。退出码 0 = 全部成功(含 dry-run)。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 【调用】把 AgentSense 根目录加进搜索路径

# 【变量】新格式结论的特征小节头:最终版(2026-09-03 交易要素化,七小节两段式)
# 逐品种必含「供需格局」(第一部分首节)+「数据支撑」(第二部分附列)+
# 「交易要素与风险」(第一部分尾节),三者齐备才视为"已按当前口径刷新";
# 旧四段式(## 核心观点 开头)、旧两段式(缺交易要素节)与失败占位都不齐备。
# 另需排除"观点生成失败"占位(2026-09-02 首轮 6 并发触发账户 429 限流,部分品种
# 调用失败被写回占位)——多品种行里即使其它品种成功、标记齐全,只要含失败占位
# 仍视为"待刷新",--skip-fresh 不会跳过它。
# 【同步】与本文件刻意不 import web_app(懒加载重模块);web_app._TRADE_TITLE_HINTS
# 是"交易要素"节标题的解析端语义,改任一侧小节标题须对齐另一侧。
_NEW_FMT_MARKERS = ("## 供需格局", "## 数据支撑", "## 交易要素与风险")
_FAIL_MARKER = "观点生成失败"


def _is_final_fmt(conclusion_md: str) -> bool:
    """conclusion_md 是否已是"无失败的两段式"最终口径(供 --skip-fresh 判定)。"""
    text = conclusion_md or ""
    return all(m in text for m in _NEW_FMT_MARKERS) and _FAIL_MARKER not in text


def _eff_date(row: dict) -> str:
    """研报"当天"语义:DB publish_date 列优先,缺则 structured_data.publish_date,再回退 uploaded_at 当天。"""
    pub = (row.get("publish_date") or "").strip()
    if pub:
        return pub[:10]
    try:
        sd = json.loads(row.get("structured_data") or "{}")
        pub = (sd.get("publish_date") or "").strip()
        if pub:
            return pub[:10]
    except (TypeError, ValueError):
        pass
    return (row.get("uploaded_at") or "")[:10]


def select_rows(args) -> list[dict]:
    """按 --ids/--date/--source 并集选中 status=='done' 的研报行,id 升序。"""
    from database import get_db  # 【调用包】懒导入:避免脚本加载 web_app 重模块

    rows = [r for r in get_db().list_research_reports(None, limit=2000) if r.get("status") == "done"]

    # --ids 白名单优先:恒从全量 done 行里精确命中,不受 --skip-fresh/--limit 影响
    keep: dict[int, dict] = {}
    if args.ids:
        for i in args.ids:
            r = next((x for x in rows if x.get("id") == i), None)
            if r:
                keep[i] = r
            else:
                print(f"  ! 研报 id={i} 不存在或非 done 状态,跳过")

    # --date/--source 驱动的批量 / --all 全库:可选跳过已刷新(conclusion_md 已含最终两段式且无失败占位)
    if args.date or args.source or getattr(args, "all", False):
        pool = rows
        if getattr(args, "skip_fresh", False):
            pool = [r for r in rows if not _is_final_fmt(r.get("conclusion_md") or "")]
        for r in pool:
            if args.date and _eff_date(r) != args.date:
                continue
            if args.source and args.source.lower() not in (r.get("source") or "").lower():
                continue
            keep[r["id"]] = r

    if not args.ids and not args.date and not args.source and not getattr(args, "all", False):
        print("需要至少一个筛选参数:--ids / --date / --source(防止误把全库重跑)")
        return []

    sel = [keep[i] for i in sorted(keep)]
    if args.limit:
        sel = sel[: args.limit]
    return sel


def _reconclude_one(row: dict) -> tuple[bool, str]:
    """对单条研报只重跑观点,返回 (ok, 摘要文本)。"""
    import web_app  # 【调用包】懒导入:全链路模块较重

    rid = row["id"]
    t0 = time.time()
    res = web_app.reconclude_research_report(rid)
    secs = time.time() - t0
    if res.get("ok"):
        codes = ",".join(res.get("codes") or [])
        return True, f"ok codes=[{codes}] {secs:.1f}s"
    return False, f"FAIL {secs:.1f}s error={res.get('error', '')[:300]}"


def main() -> int:
    """CLI 入口:筛选 → dry-run 只列 / 逐条重跑 → 汇总 + 退出码。"""
    ap = argparse.ArgumentParser(description="研报逐品种「核心观点」只重跑(复用当前结论提示词)")
    ap.add_argument("--ids", help="白名单研报 id,空格分隔(如 \"47 53 54\");优先精确命中")
    ap.add_argument("--date", help="只重跑 publish_date 当天(YYYY-MM-DD)的 done 行")
    ap.add_argument("--source", help="只重跑 source 含该关键词的 done 行(子串,如 华泰期货)")
    ap.add_argument("--all", action="store_true", help="全部 status=done 研报(与 --ids/--date/--source 取并集)")
    ap.add_argument("--limit", type=int, help="最多重跑前 N 条(id 升序截断)")
    ap.add_argument("--skip-fresh", action="store_true",
                    help="批量时跳过已按最终口径刷新(含 供需格局/数据支撑/交易要素与风险 三标记)的行")
    ap.add_argument("--workers", type=int, default=1,
                    help="并发线程数(默认 1=逐条串行;如 4 可把 52 行/131 次调用压到 ~15 分钟)")
    ap.add_argument("--dry-run", action="store_true", help="只列出将被重跑的行,不调 LLM")
    args = ap.parse_args()

    if args.ids:
        args.ids = [int(x) for x in args.ids.split() if x.strip().isdigit()]

    t0 = time.time()
    sel = select_rows(args)
    print(f"选中 done 研报 {len(sel)} 条" + (" (dry-run)" if args.dry_run else ""))
    for r in sel:
        print(f"  #{r['id']} [{r.get('source') or ''}] {r.get('title') or ''}  {_eff_date(r)}")
    if args.dry_run:
        print(f"Dry-run 结束(不调 LLM)。若确认无误,去掉 --dry-run 执行。Took {time.time() - t0:.1f}s")
        return 0
    if not sel:
        print("没有选中任何行,退出。")
        return 0 if (args.date or args.source or args.ids or args.all) else 2

    # 【关键】并发硬钳 ≤4:账户 LLM API 并发上限 5,>5 必撞 429 且失败被静默写成
    # "观点生成失败"占位落库(见 2026-09-02 首轮教训),故无论传多少都压到 4。
    workers = max(1, min(4, args.workers))
    failed = 0
    by_id = {r["id"]: r for r in sel}

    def _reconclude(row_id: int) -> tuple[int, bool, str]:
        # 并发安全:每个 worker 独立 get_db(线程局部)+ reconclude 内各自新建 LLM 客户端;
        # SQLite 每操作新连接、写窗口短,4 并发无锁冲突。返回 (id, ok, 摘要)。
        ok, msg = _reconclude_one(by_id[row_id])
        return row_id, ok, msg

    results: list[tuple[int, bool, str]] = []
    if workers == 1:
        for r in sel:
            results.append(_reconclude(r["id"]))
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(_reconclude, r["id"]) for r in sel]
            # 逐个取完成结果;executor 内每任务独立,打印顺序即提交顺序,日志稳定
            results = [f.result() for f in futures]
    for i, (rid, ok, msg) in enumerate(results, 1):
        if not ok:
            failed += 1
        r = by_id[rid]
        print(f"[{i}/{len(sel)}] #{rid} [{r.get('source') or ''}] {r.get('title') or ''} -> {msg}")
    print(f"Reconcluded: {len(sel) - failed} / Failed: {failed}   (Took {time.time() - t0:.1f}s, workers={workers})")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 【调用】Windows 控制台 UTF-8
    raise SystemExit(main())
