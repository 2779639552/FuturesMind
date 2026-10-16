# -*- coding: utf-8 -*-
"""scripts/reprocess_stuck_reports.py — 重跑卡在 processing 的研报(僵尸回收工具)。

【背景】服务进程被强杀(重启/崩溃)时,正在跑 LLM 文档处理的研报状态永远停在
  processing,前端表现为"文档处理停了好久"。本工具对这些行重新触发
  _process_research_report 全链路(提取→LLM→结构化→观点→落库),幂等安全:
  processing 状态会被覆盖,done/error 同样可重跑。

【用法】(在 AgentSense 根目录,venv python)
  python scripts/reprocess_stuck_reports.py                # 处理所有 processing 超 30 分钟的
  python scripts/reprocess_stuck_reports.py --ids "251 254 255"
  python scripts/reprocess_stuck_reports.py --dry-run      # 只列出,不执行
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 【调用】AgentSense 根目录入搜索路径

STUCK_MINUTES = 30  # 【变量】processing 超过该分钟数才视为僵尸,避免误伤正在处理的


def pick_stuck(ids_whitelist: list[int] | None, dry: bool) -> list[dict]:
    from database import get_db  # 【调用包】懒导入 DB

    rows = get_db().list_research_reports(None, limit=2000)
    stuck = [r for r in rows if (r.get("status") or "") == "processing"]
    if ids_whitelist:
        stuck = [r for r in stuck if r.get("id") in ids_whitelist]
    else:
        cutoff = datetime.now() - timedelta(minutes=STUCK_MINUTES)
        def _old(r):
            try:
                return datetime.fromisoformat((r.get("created_at") or "")[:19]) < cutoff
            except ValueError:
                return True
        stuck = [r for r in stuck if _old(r)]
    return sorted(stuck, key=lambda r: r.get("id") or 0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="", help="空格分隔的研报 id 白名单,缺省=全部超时僵尸")
    ap.add_argument("--dry-run", action="store_true", help="只列出,不执行")
    ap.add_argument("--workers", type=int, default=3, help="并发数(默认 3,勿超账户上限 5)")
    args = ap.parse_args()

    whitelist = [int(x) for x in args.ids.split()] if args.ids.strip() else None
    stuck = pick_stuck(whitelist, args.dry_run)
    print("待重跑 %d 篇: %s" % (len(stuck), [r["id"] for r in stuck]))
    if not stuck:
        return 0
    if args.dry_run:
        for r in stuck:
            print("  #%s [%s] %s" % (r["id"], r.get("source"), (r.get("title") or "")[:40]))
        return 0

    import web_app  # 【调用】重模块:加载 config/LLM 客户端工厂(不会启动 server)
    from web_app import _process_research_report

    done_lock = threading.Lock()
    results: list[tuple[int, str]] = []

    def _one(rid: int):
        t0 = time.time()
        try:
            _process_research_report(rid)
            from database import get_db
            st = (get_db().get_research_report(rid) or {}).get("status") or "?"
        except Exception as e:  # noqa: BLE001
            st = "exception:%s" % e
        with done_lock:
            results.append((rid, st))
            print("  #%s -> %s (%.0fs)" % (rid, st, time.time() - t0), flush=True)

    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 4))) as ex:
        list(ex.map(_one, [r["id"] for r in stuck]))

    ok = sum(1 for _, st in results if st == "done")
    print("完成: %d/%d done" % (ok, len(results)))
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
