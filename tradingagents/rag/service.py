"""RAG 编排:索引 / 检索 / 上下文格式化。

不 import database.py / web_app.py —— 报告行由调用方以 dict 传入
(database.get_research_report 的 row 直接可用)。
"""

from __future__ import annotations

import logging

from . import embedding, store
from .chunking import build_chunks

logger = logging.getLogger(__name__)

# 品种过滤:Chroma where 只能精确匹配,varieties 是逗号串 → 超采样后过滤;
# 无过滤时也超采样 —— 给去重留余量(同一报告的多块挤进 raw top-k 时,
# 不超采样会导致去重后命中数远少于 top_k,2026-09-08 真机:原油问题 6 块只出 2 篇)。
_OVERSAMPLE_FACTOR = 3
# 去重时每篇研报最多保留的 chunk 数:1 块会把长报告的其他方面(如宏观段)
# 全部挡在外面,2 块兼顾多主题问答与引用多样性。
_MAX_PER_REPORT = 2

CONTEXT_HEADER = "【历史研报参考片段】(检索自近期同品种研报,仅作背景,严禁照抄过期观点)"


def index_report(row: dict) -> int:
    """把一份研报(row dict)切块并向量化入库,返回写入 chunk 数。"""
    chunks = build_chunks(row)
    if not chunks:
        return 0
    embeddings = embedding.encode([c["embedding_text"] for c in chunks])
    return store.upsert_chunks(chunks, embeddings)


def delete_report(report_id: int) -> None:
    store.delete_by_report(report_id)


def report_indexed(report_id: int) -> bool:
    """该报告是否已有向量(回填跳过判断用)。"""
    res = store.get_collection().get(where={"report_id": report_id}, limit=1, include=[])
    return bool(res["ids"])


def index_status() -> dict:
    """向量数 / 已索引报告数 / 是否启用(供状态路由)。"""
    import chromadb  # noqa: F401  到这一步说明依赖在

    collection = store.get_collection()
    n_chunks = collection.count()
    report_ids = set()
    if n_chunks:
        res = collection.get(include=[])
        for chunk_id in res["ids"]:
            try:
                report_ids.add(int(chunk_id.split(":", 1)[0]))
            except ValueError:
                continue
    return {"chunks": n_chunks, "reports": len(report_ids)}


def _match_variety(meta: dict, code: str) -> bool:
    if meta.get("variety") == code:
        return True
    return code in (meta.get("varieties") or "").split(",")


def retrieve_hits(
    question: str,
    top_k: int = 6,
    variety: str | None = None,
    report_type: str | None = None,
    exclude_report_id: int | None = None,
    dedupe_reports: bool = True,
    max_per_report: int = _MAX_PER_REPORT,
) -> list[dict]:
    """检索 top_k 个片段;variety/report_type 在应用层后过滤。

    dedupe_reports=True(默认)按 report_id 去重、每篇最多保留 max_per_report 个
    得分最高的片段 —— 一篇研报切块多、多块同时排进前 k 时,不去重会出现"同一篇
    引用多次"(2026-09-08 用户实测反馈);但每篇只留 1 块又会把长报告的其他方面
    (如宏观段)全部挡在外面(同日实测:原油问答只检回表格块),故默认留 2 块。
    """
    question = (question or "").strip()
    if not question:
        return []
    fetch_k = top_k * _OVERSAMPLE_FACTOR
    qvec = embedding.encode([question])[0]
    hits = store.query(qvec, top_k=fetch_k, exclude_report_id=exclude_report_id)
    if variety:
        code = variety.strip().upper()
        hits = [h for h in hits if _match_variety(h["metadata"], code)]
    if report_type:
        hits = [h for h in hits if h["metadata"].get("report_type") == report_type]
    if dedupe_reports:
        buckets: dict[int, list[dict]] = {}
        for h in hits:  # store.query 已按 score 降序
            rid = int(h["metadata"].get("report_id", 0))
            if len(buckets.setdefault(rid, [])) < max(1, max_per_report):
                buckets[rid].append(h)
        hits = sorted(
            (h for bucket in buckets.values() for h in bucket),
            key=lambda h: h["score"],
            reverse=True,
        )
    return hits[:top_k]


def format_context(hits: list[dict], header: str = CONTEXT_HEADER) -> str:
    """把检索结果拼成可注入提示词的文本块;无命中返回 ""。"""
    if not hits:
        return ""
    lines = [header]
    for i, h in enumerate(hits, 1):
        m = h["metadata"]
        lines.append(
            f"[{i}] {m.get('publish_date') or '日期未知'} {m.get('source') or ''}"
            f"《{m.get('title') or '无标题'}》: {h['text']}"
        )
    return "\n".join(lines)


def context_for_variety(
    query_text: str,
    variety: str | None = None,
    limit: int = 4,
    exclude_report_id: int | None = None,
) -> str:
    """分析流程注入入口:检索并格式化,任何异常/未启用返回 ""(绝不拖垮主流程)。"""
    try:
        hits = retrieve_hits(
            query_text, top_k=limit, variety=variety, exclude_report_id=exclude_report_id
        )
        return format_context(hits)
    except Exception:  # noqa: BLE001  RAG 失败不允许影响研报主链路
        logger.warning("RAG 上下文检索失败", exc_info=True)
        return ""


def backfill_all(
    rows: list[dict],
    force: bool = False,
    progress_cb=None,
) -> dict:
    """回填存量研报向量。幂等:已索引且非 force 的行跳过。

    rows 为 status==done 的研报行 dict 列表(调用方从 DB 取)。
    """
    done = skipped = failed = 0
    total = len(rows)
    for i, row in enumerate(rows, 1):
        report_id = int(row["id"])
        try:
            if not force and report_indexed(report_id):
                skipped += 1
            else:
                if force:
                    # 真重建:切块/过滤规则变化后,旧行的 chunk id 集合可能与新行不同,
                    # 只 upsert 会残留已消失的旧 chunk —— 先删净再写。
                    store.delete_by_report(report_id)
                index_report(row)
                done += 1
        except Exception:  # noqa: BLE001  单份失败不中断回填
            failed += 1
            logger.warning("RAG 回填失败 report_id=%s", report_id, exc_info=True)
        if progress_cb:
            progress_cb(i, total, report_id)
    return {"done": done, "skipped": skipped, "failed": failed}
