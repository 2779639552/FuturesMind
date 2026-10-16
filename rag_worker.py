"""RAG worker 子进程:把 torch / sentence-transformers / chromadb 全部隔离在本进程。

【为什么存在】web 主进程内做 embedding 推理 + chromadb 查询时,原生层
(chromadb rust 绑定 / torch 线程池)偶发 access violation,直接带走整个
web 服务(2026-09-10 真机:两天崩 3 次,fault handler 记录 access violation)。
本 worker 由 web_app._rag_call 按需拉起,走 stdin/stdout JSON 行协议;
worker 崩了 web 主进程不受影响,下次调用自动重启,已等待的请求收到错误。

协议(每行一个 JSON 对象,UTF-8):
  请求: {"id": <int>, "op": "<名称>", "args": {...}}
  响应: {"id": <int>, "ok": true, "result": ...} | {"id": <int>, "ok": false, "error": "..."}
  op 列表: ping / status / retrieve / context / format_context /
           index_report / delete_report / backfill

运行方式:python rag_worker.py —— 由 web_app 以 subprocess.PIPE 方式拉起,
【不要手动运行】:真 stdout 已专用于协议应答,普通 print 被重定向到 stderr;
运行日志在 rag_worker.err.log(启动/模型加载/进度条/异常栈都在这里)。
"""

from __future__ import annotations

import faulthandler  # 【调用包】原生崩溃(access violation)时把线程栈写进 stderr 日志
import json
import os
import sys
import traceback

faulthandler.enable(file=sys.stderr)

# 【协议通道】先把"真 stdout"复制一份专用于协议应答,再把 stdout 重定向到
# stderr —— service / chromadb / tqdm 的杂散输出就不会污染 JSON 行协议。
_PROTO_OUT = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
os.dup2(2, 1)

# 【2026-09-11 修复·根因】stdin 强制 UTF-8:Windows 下 Python stdin 默认 GBK,
# web 端以 text=True/encoding="utf-8" 写入的中文 JSON 会被 GBK 误读 → 中文损坏成
# 孤立 surrogate(\udc9f) → tokenizer 报 TextEncodeInput(RAG 检索全失败)。
try:
    sys.stdin.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001 重配置失败时靠 embedding._sanitize_texts 兜底
    pass

# 脚本位于 AgentSense 根目录,保证 import database / tradingagents 可用
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv  # noqa: E402  【调用包】.env(RAG_EMBED_MODEL 等)

# 【2026-09-11 修复】显式指定 .env 路径并强制覆盖:无参 load_dotenv() 默认 override=False,
# 若父进程(web)注入的 RAG_EMBED_MODEL 为空/旧值,worker 会继承空值 → get_embedder 回退
# 默认模型名 BAAI/bge-small-zh-v1.5 → 走 HF 缓存 → 缓存 config 损坏报 JSONDecodeError,
# 表现为研报问答 RAG 全链路 500(真机:2026-09-11 RAG 不可用根因)。
_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(_ENV_PATH, override=True)  # 兜底直跑场景;父进程注入的同名键将被 .env 真实值覆盖

_service = None  # 【变量】tradingagents.rag.service 懒加载(chromadb+torch 都在这条链上)


def _get_service():
    """首次调用才加载 chromadb/embedding 全家桶,冷启动几十秒属正常。"""
    global _service
    if _service is None:
        from tradingagents.rag import service as _svc  # 【调用包】重依赖,worker 专属

        _service = _svc
        print("rag_worker: tradingagents.rag.service loaded", file=sys.stderr, flush=True)
    return _service


def _json_default(o):
    """numpy 标量/数组(hits 的 score 等)转原生类型,保证 JSON 可序列化。"""
    try:
        import numpy as _np  # noqa: PLC0415  【调用包】延迟导入

        if isinstance(o, _np.generic):
            return o.item()
        if isinstance(o, _np.ndarray):
            return o.tolist()
    except Exception:  # noqa: BLE001
        pass
    return str(o)


def _respond(req_id, ok: bool, *, result=None, error: str | None = None) -> None:
    body = {"id": req_id, "ok": ok}
    body["result" if ok else "error"] = result if ok else error
    _PROTO_OUT.write(json.dumps(body, ensure_ascii=False, default=_json_default) + "\n")
    _PROTO_OUT.flush()


# ── op 实现:与 web_app 原进程内调用一一对应,行为等价 ────────────────────

def _op_ping(_args):
    return "pong"


def _op_status(_args):
    return _get_service().index_status()


def _op_health(args):
    """抽样自召回体检(见 service.health_check):验证"块数"之外的可检索性。"""
    return _get_service().health_check(sample=int(args.get("sample", 30)))


def _recent_meta_provider():
    """保底通道元数据注入(2026-09-22):SQLite 轻量 4 列,替代全库拉 Chroma metadata。

    【为什么】见 service._merge_recent_reports;每次检索都全库 get_metadatas() 占 72%
    耗时且随库线性恶化。DB 直查只取 id/publish_date/varieties/report_type 四列。
    """
    from database import get_db  # 【调用包】sqlite,轻量

    out: dict[int, dict] = {}
    try:
        for r in get_db().list_done_report_meta():
            out[int(r["id"])] = {
                "publish_date": r["publish_date"] or "",
                "varieties": r["varieties"] or "",
                "report_type": r["report_type"] or "",
            }
    except Exception as e:  # noqa: BLE001  注入失败回退旧路径(service 层兜底)
        print(f"DIAG recent_meta_provider failed: {e}", file=sys.stderr, flush=True)
        return {}
    # 【2026-09-24 归档】补 summarized 行(同 web_app._rag_recent_meta_provider):
    # research_summaries 每组首篇 rid → publish_date/varieties, 保底通道覆盖归档日期。
    try:
        for s in get_db().list_research_summaries():
            try:
                rids = json.loads(s.get("report_ids") or "[]")
            except Exception:
                continue
            if not rids:
                continue
            rid0 = int(rids[0])
            var = s.get("variety") or ""
            pd = s.get("publish_date") or ""
            if rid0 in out:
                # 【2026-09-24】同一 rid 可能是多品种研报被拆成多组(TA/PX/PF...)
                # 的首篇 ⇒ varieties 做并集, 否则被后写组覆盖成单个品种,
                # _merge_recent_reports 按品种过滤时会把该 rid 整行滤掉(TA 卡片
                # 进不了保底, 整月复盘 9 月上旬覆盖不到)。
                merged = sorted({x for x in (out[rid0].get("varieties") or "").split(",") if x} | {var})
                out[rid0]["varieties"] = ",".join(merged)
                if not out[rid0].get("publish_date") or pd > out[rid0].get("publish_date", ""):
                    out[rid0]["publish_date"] = pd
            else:
                out[rid0] = {"publish_date": pd, "varieties": var, "report_type": "总结"}
    except Exception:  # noqa: BLE001  summarized 补注失败不影响主通道
        pass
    return out


def _op_retrieve(args):
    # 【2026-09-11 修复】query 类型防御:web 端经 JSON 传参,若 query 非 str
    # (如 list/None/数字),[question] 会变成嵌套结构 → SentenceTransformer.encode
    # 报 TextEncodeInput(真机:RAG 检索全失败根因)。统一转 str 且空串给占位,
    # 避免把非 str 传给 tokenizer。
    q = args.get("query")
    if not isinstance(q, str):
        q = str(q) if q is not None else ""
    # 【2026-09-11 诊断】打印模型实际加载路径到 stderr,定位 TextEncodeInput 来源。
    try:
        from tradingagents.rag import embedding as _emb

        _m = _emb.get_embedder()
        _cfg = getattr(_m[0], "config", None) if len(_m) else None
        print(
            f"DIAG retrieve query={q!r} type={type(q).__name__} "
            f"model_path={getattr(_cfg, '_name_or_path', '?')}",
            file=sys.stderr, flush=True,
        )
    except Exception as _e:  # noqa: BLE001 诊断失败不影响主流程
        print(f"DIAG retrieve failed: {_e}", file=sys.stderr, flush=True)
    return _get_service().retrieve_hits(
        q,
        top_k=int(args.get("top_k", 4)),
        variety=args.get("variety"),
        report_type=args.get("report_type"),
        # 【2026-09-14 合并 QA 模块】日期范围筛选(闭区间,YYYY-MM-DD);
        # web 端 QA 问答经 _rag_call("retrieve", ...) 传下来,空值即不限。
        date_from=args.get("date_from") or None,
        date_to=args.get("date_to") or None,
        recent_meta_provider=_recent_meta_provider,
    )


def _op_context(args):
    q = args.get("query_text")
    if not isinstance(q, str):
        q = str(q) if q is not None else ""
    return _get_service().context_for_variety(
        q,
        variety=args.get("variety"),
        limit=int(args.get("limit", 4)),
        exclude_report_id=args.get("exclude_report_id"),
        recent_meta_provider=_recent_meta_provider,
    )


def _op_format_context(args):
    return _get_service().format_context(args["hits"])


def _op_index_report(args):
    from database import get_db  # noqa: PLC0415  【调用包】sqlite,轻量

    rid = int(args["report_id"])
    row = get_db().get_research_report(rid)
    if not row or row.get("status") != "done":
        return 0
    return _get_service().index_report(row)


def _op_delete_report(args):
    _get_service().delete_report(int(args["report_id"]))
    return True


def _op_backfill(args):
    """等价 web_app._rag_index_pending:done 研报增量补索引(幂等)。"""
    from datetime import datetime, timedelta  # noqa: PLC0415

    from database import get_db  # noqa: PLC0415

    svc = _get_service()
    rows = [r for r in get_db().list_research_reports(limit=5000) if r.get("status") == "done"]
    since_days = int(args.get("since_days") or 0)
    if since_days > 0:
        cutoff = (datetime.now() - timedelta(days=since_days)).strftime("%Y-%m-%d")
        rows = [r for r in rows if (r.get("created_at") or "")[:10] >= cutoff]
    stats = svc.backfill_all(rows, force=bool(args.get("force")))
    status = svc.index_status()
    stats.update({"total": len(rows), "chunks": status["chunks"], "reports": status["reports"]})
    return stats


OPS = {
    "ping": _op_ping,
    "status": _op_status,
    "health": _op_health,
    "retrieve": _op_retrieve,
    "context": _op_context,
    "format_context": _op_format_context,
    "index_report": _op_index_report,
    "delete_report": _op_delete_report,
    "backfill": _op_backfill,
}


def main() -> None:
    print("rag_worker ready", file=sys.stderr, flush=True)
    for line in sys.stdin:  # 单线程顺序处理:天然串行,规避原生库并发竞态
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as e:  # noqa: BLE001
            _respond(None, False, error=f"bad request: {e}")
            continue
        rid, op, args = req.get("id"), req.get("op"), req.get("args") or {}
        try:
            fn = OPS.get(op)
            if fn is None:
                raise ValueError(f"unknown op: {op}")
            _respond(rid, True, result=fn(args))
        except Exception as e:  # noqa: BLE001  单请求失败不影响 worker 存活
            traceback.print_exc(file=sys.stderr)
            _respond(rid, False, error=f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
