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


def _bulk_backfill(rows: list[dict], flush_size: int) -> int:
    """攒批写入:把多篇研报的 chunk 攒到 flush_size 再一次性 upsert。

    【为什么需要 · 2026-09-17】逐篇 index_report 是"每篇一次开库+关库"。
    本机 420 篇实测 23 分钟才写完 1201/4650 条 —— 瓶颈不在 embedding,而在
    每次开库都要重新加载 HNSW 索引 + 起停 compactor。攒批后开库次数从 ~420
    降到 ~6,同样的量级只需几分钟。

    幂等:非 force 时先一次性取回已索引的 report_id 集合(只读一次库),跳过它们。
    """
    from tradingagents.rag import embedding, store

    from tradingagents.rag.chunking import build_chunks

    indexed = set()
    for cid in store.get_ids():
        head = str(cid).split(":", 1)[0]
        if head.isdigit():
            indexed.add(int(head))
    todo = [r for r in rows if int(r["id"]) not in indexed]
    print(f"批量模式: 已索引 {len(indexed)} 篇,待索引 {len(todo)} 篇(flush_size={flush_size})")

    buf_chunks: list[dict] = []
    buf_texts: list[str] = []
    written = 0

    def _flush() -> None:
        nonlocal written
        if not buf_chunks:
            return
        vecs = embedding.encode(buf_texts)
        written += store.upsert_chunks(buf_chunks, list(vecs))
        print(f"  flush -> 累计 {written} 个向量(待索引还剩 {len(todo)} 篇已处理)", flush=True)
        buf_chunks.clear()
        buf_texts.clear()

    for row in todo:
        try:
            chunks = build_chunks(row)
        except Exception as e:  # noqa: BLE001 单篇切块失败不中断
            print(f"  build_chunks 失败 report_id={row['id']}: {e}")
            continue
        if not chunks:
            continue
        buf_chunks.extend(chunks)
        buf_texts.extend(c["embedding_text"] for c in chunks)
        if len(buf_chunks) >= flush_size:
            _flush()
    _flush()
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description="存量研报向量索引回填(本地 embedding,零 LLM)")
    ap.add_argument("--force", action="store_true", help="忽略已索引判断,全量覆盖重建")
    ap.add_argument("--variety", default="", help="只回填主品种为该代码的研报(如 RB)")
    ap.add_argument(
        "--bulk",
        nargs="?",
        type=int,
        const=800,
        default=0,
        help="攒批写入模式,可指定每批 chunk 数(默认 800);推荐用于全量重建",
    )
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

    if args.bulk:
        written = _bulk_backfill(rows, args.bulk)
        print(f"回填完成: 本次写入 {written} 个向量")
    else:
        def progress(i: int, total: int, report_id: int):
            if i % 20 == 0 or i == total:
                print(f"  进度 {i}/{total} (report_id={report_id})")

        stats = service.backfill_all(rows, force=args.force, progress_cb=progress)
        print(f"回填完成: 新索引 {stats['done']} / 跳过已索引 {stats['skipped']} / 失败 {stats['failed']}")

    status = service.index_status()
    print(f"索引现状: {status['chunks']} 个向量 / {status['reports']} 份报告")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 【调用】Windows 控制台 UTF-8
    raise SystemExit(main())
