"""scripts/backfill_rag_index.py — 存量研报向量索引回填(零 LLM,本地 embedding)

【模块角色】
  研报 RAG(tradingagents/rag,2026-09-08 新增)落地前的存量 done 研报没有向量。
  本工具遍历 research_reports 全部 done 行逐份 index_report() 进 Chroma
  (~/.tradingagents/rag_chroma)。chunk id = {report_id}:{chunk_index},
  upsert 幂等 —— 重跑安全;--force 忽略"已索引"判断全量覆盖。

【用法】(在 AgentSense 根目录跑)
  python scripts/backfill_rag_index.py                 # 回填未索引的 done 研报
  python scripts/backfill_rag_index.py --force         # 全量重建(覆盖写)
  python scripts/backfill_rag_index.py --variety RB    # 只回填主品种为 RB 的研报

【注意】
  首次运行会下载 embedding 模型 BAAI/bge-small-zh-v1.5(~95MB),
  离线/慢速网络先 set HF_ENDPOINT=https://hf-mirror.com,
  或提前下好模型后 set RAG_EMBED_MODEL=<本地模型目录>。
  依赖未安装(chromadb / sentence-transformers)时报错退出。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 【调用】把 AgentSense 根目录加进搜索路径


def main() -> int:
    ap = argparse.ArgumentParser(description="存量研报向量索引回填(本地 embedding,零 LLM)")
    ap.add_argument("--force", action="store_true", help="忽略已索引判断,全量覆盖重建")
    ap.add_argument("--variety", default="", help="只回填主品种为该代码的研报(如 RB)")
    args = ap.parse_args()

    from tradingagents.rag import is_available

    if not is_available():
        print("RAG 依赖未安装:需要 chromadb 与 sentence-transformers。")
        return 2

    from database import get_db  # 【调用包】懒导入:重模块
    from tradingagents.rag import service

    db = get_db()
    rows = [
        r
        for r in db.list_research_reports(limit=2000)
        if r.get("status") == "done"
        and (not args.variety or (r.get("variety") or "").strip().upper() == args.variety.upper())
    ]
    print(f"待回填 done 研报 {len(rows)} 份(force={args.force})")

    def progress(i: int, total: int, report_id: int):
        if i % 20 == 0 or i == total:
            print(f"  进度 {i}/{total} (report_id={report_id})")

    stats = service.backfill_all(rows, force=args.force, progress_cb=progress)
    print(f"回填完成: 新索引 {stats['done']} / 跳过已索引 {stats['skipped']} / 失败 {stats['failed']}")
    status = service.index_status()
    print(f"索引现状: {status['chunks']} 个向量 / {status['reports']} 份报告")
    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 【调用】Windows 控制台 UTF-8
    raise SystemExit(main())
