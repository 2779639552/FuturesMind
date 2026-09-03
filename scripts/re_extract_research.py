"""scripts/re_extract_research.py — 研报「结构化」存量重提取工具(置信度 + 四类基本面)

【模块角色】
  2026-09-03 之前入库的研报,是旧版提取提示词处理的:置信度无语义区间 → 行级
  confidence 全默认 0.5(或抄示例 0.0),且四类基本面(基差/交易所仓单/开工率/
  加工利润)根本没进 structured_data,只在读时靠 _heuristic_fund_from_text 从
  总结文本里捞。本工具对选定研报重跑第一步结构化提取:
    web_app.re_extract_research_report(rid)
  用修复后的提示词(_llm_extract_structured,含 confidence 语义区间 3c + 四键 3b)
  重新逐品种提取,按已入库品种代码集合并回写:
    · confidence → 真实值(模型确实给不出才为 None,前端显示"—(未给)",不再 0.5 冒充);
    · basis/warehouse_receipts/operating_rate/processing_margin → 直接进结构化
      data_points(仍缺的键再从研报原文确定性补漏,同新研报入库路径);
  方向/评级/逐品种结论文本一律不动(re_extract 内部按 id 取回原样写回,观点不变)。

【用法】(在 AgentSense 根目录跑)
  python scripts/re_extract_research.py --ids "47 53 54"                        # 白名单优先
  python scripts/re_extract_research.py --all --skip-extracted --workers 4      # 重提取存量未提取行
  python scripts/re_extract_research.py --source 华泰期货 --limit 10            # 分层批量
  python scripts/re_extract_research.py --ids "47" --dry-run                    # 只列选中,不调 LLM

【选择规则】
  在全表 status=='done' 行中,--ids / --date / --source 取并集(任一无则按其余筛);
  三者都缺省则拒绝执行(防止误把整库重跑)。日期取 structured_data.publish_date
  (YYYY-MM-DD),缺则回退 uploaded_at 当天;source 为子串匹配(大小写不敏感)。
  --skip-extracted:排除"已带结构化四键或非默认行级置信度"的行(见 _is_extracted),
  分层批量时自动跳过已提取确认的行,不重复调用。按 id 升序输出,--limit 截断。
  退出码 0 = 全部成功(含 dry-run)。

【并发边界】LLM 账户并发上限=5 → 实测超 5 必撞 429,--workers 上限钳到 4(默认 4)。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 【调用】把 AgentSense 根目录加进搜索路径

# 【变量】行级置信度"默认占位"集合:旧提示词时代全表只出这两种,都不代表模型判断。
_DEFAULT_CONF = {None, 0.0, 0.5}
_FUND_KEYS = ("basis", "warehouse_receipts", "operating_rate", "processing_margin")


def _is_extracted(row: dict) -> bool:
    """该行是否已做过"置信度/四键"结构化重提取(供 --skip-extracted 判定)。

    【判定】已提取 = 行级置信度不是默认占位,或 structured_data 里任一品种已带
    任一四键(存量目标行四键全无)。仅靠行级 0.5 无法区分"新模型中性值 0.5"与
    "旧默认 0.5",故以四键是否落地为主信号——目标存量行四键必然全无。
    """
    conf = row.get("confidence")
    try:
        sd = json.loads(row.get("structured_data") or "{}")
    except (TypeError, ValueError):
        sd = {}
    items = sd.get("varieties")
    if not isinstance(items, list):
        items = []
    has_key = any(
        (isinstance(i, dict) and any(_fund_value(i.get(k)) is not None for k in _FUND_KEYS))
        for i in items if isinstance(i, dict)
    )
    return (conf not in _DEFAULT_CONF) or has_key


def _fund_value(v):
    """研报单个指标值归一化:{value,unit,date,note} 对象或标量都行;无值/空 → None。

    与 web_app._fund_value 同语义,仅作脚本内 skip-extracted 判定用,避免 import 重模块。
    """
    if isinstance(v, dict):
        val = v.get("value")
        return None if (val is None or val == "") else val
    return None if (v is None or v == "") else v


def _eff_date(row: dict) -> str:
    """研报"当天"语义:structured_data.publish_date(YYYY-MM-DD),缺则回退 uploaded_at 当天。"""
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

    # --ids 白名单优先:恒从全量 done 行里精确命中,不受 --skip-extracted/--limit 影响
    keep: dict[int, dict] = {}
    if args.ids:
        for i in args.ids:
            r = next((x for x in rows if x.get("id") == i), None)
            if r:
                keep[i] = r
            else:
                print(f"  ! 研报 id={i} 不存在或非 done 状态,跳过")

    # --date/--source 驱动的批量 / --all 全库:可选跳过已结构化提取(见 _is_extracted)
    if args.date or args.source or getattr(args, "all", False):
        pool = rows
        if getattr(args, "skip_extracted", False):
            pool = [r for r in rows if not _is_extracted(r)]
        for r in pool:
            if args.date and _eff_date(r) != args.date:
                continue
            if args.source and args.source.lower() not in (r.get("source") or "").lower():
                continue
            keep[r["id"]] = r

    if not args.ids and not args.date and not args.source and not getattr(args, "all", False):
        print("需要至少一个筛选参数:--ids / --date / --source / --all(防止误把全库重跑)")
        return []

    sel = [keep[i] for i in sorted(keep)]
    if args.limit:
        sel = sel[: args.limit]
    return sel


def _re_extract_one(row: dict) -> tuple[bool, str]:
    """对单条研报重跑结构化提取,返回 (ok, 摘要文本)。"""
    import web_app  # 【调用包】懒导入:全链路模块较重

    rid = row["id"]
    t0 = time.time()
    res = web_app.re_extract_research_report(rid)
    secs = time.time() - t0
    if res.get("ok"):
        codes = ",".join(res.get("codes") or [])
        return True, f"ok codes=[{codes}] {secs:.1f}s"
    return False, f"FAIL {secs:.1f}s error={res.get('error', '')[:300]}"


def main() -> int:
    """CLI 入口:筛选 → dry-run 只列 / 逐条重提取 → 汇总 + 退出码。"""
    ap = argparse.ArgumentParser(
        description="研报「结构化」存量重提取(置信度真实化 + 基差/仓单/开工率/加工利润入 structured_data)"
    )
    ap.add_argument("--ids", help="白名单研报 id,空格分隔(如 \"47 53 54\");优先精确命中")
    ap.add_argument("--date", help="只重提取 publish_date 当天(YYYY-MM-DD)的 done 行")
    ap.add_argument("--source", help="只重提取 source 含该关键词的 done 行(子串,如 华泰期货)")
    ap.add_argument("--all", action="store_true", help="全部 status=done 研报(与 --ids/--date/--source 取并集)")
    ap.add_argument("--limit", type=int, help="最多重提取前 N 条(id 升序截断)")
    ap.add_argument("--skip-extracted", action="store_true",
                    help="批量时跳过已带结构化四键或非默认行级置信度的行(存量目标行四键必全无)")
    ap.add_argument("--workers", type=int, default=4,
                    help="并发线程数(默认 4;LLM 账户并发上限=5,超过必撞 429,故钳到 4)")
    ap.add_argument("--dry-run", action="store_true", help="只列出将被重提取的行,不调 LLM")
    args = ap.parse_args()

    if args.ids:
        args.ids = [int(x) for x in args.ids.split() if x.strip().isdigit()]

    workers = max(1, min(4, args.workers))  # 【并发边界】超 4 一律钳到 4

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

    failed = 0
    by_id = {r["id"]: r for r in sel}

    def _re_extract(row_id: int) -> tuple[int, bool, str]:
        # 并发安全:每个 worker 独立 get_db(线程局部)+ re_extract 内各自新建 LLM 客户端;
        # SQLite 每操作新连接、写窗口短,≤4 并发无锁冲突。返回 (id, ok, 摘要)。
        ok, msg = _re_extract_one(by_id[row_id])
        return row_id, ok, msg

    results: list[tuple[int, bool, str]] = []
    if workers == 1:
        for r in sel:
            results.append(_re_extract(r["id"]))
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(_re_extract, r["id"]) for r in sel]
            # 逐个取完成结果;executor 内每任务独立,打印顺序即提交顺序,日志稳定
            results = [f.result() for f in futures]
    for i, (rid, ok, msg) in enumerate(results, 1):
        if not ok:
            failed += 1
        r = by_id[rid]
        print(f"[{i}/{len(sel)}] #{rid} [{r.get('source') or ''}] {r.get('title') or ''} -> {msg}")
    print(f"Re-extracted: {len(sel) - failed} / Failed: {failed}   (Took {time.time() - t0:.1f}s, workers={workers})")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 【调用】Windows 控制台 UTF-8
    raise SystemExit(main())
