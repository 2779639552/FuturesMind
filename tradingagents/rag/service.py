"""RAG 编排:索引 / 检索 / 上下文格式化。

不 import database.py / web_app.py —— 报告行由调用方以 dict 传入
(database.get_research_report 的 row 直接可用)。
"""

from __future__ import annotations

import contextlib
import logging
import os

from . import embedding, store
from .chunking import build_chunks

logger = logging.getLogger(__name__)

# 品种过滤:Chroma where 只能精确匹配,varieties 是逗号串 → 超采样后过滤;
# 无过滤时也超采样 —— 给去重留余量(同一报告的多块挤进 raw top-k 时,
# 不超采样会导致去重后命中数远少于 top_k,2026-09-08 真机:原油问题 6 块只出 2 篇)。
#
# 【2026-09-17 上调 3→5】用户实测"搜 PTA 近日情况找不到 09-16 的研报":
# 目标篇(579 华泰聚酯日报 09-16)的原始余弦约 0.689,恰好在 raw 第 21 名,
# 而 fetch_k=top_k×3=18 —— 差 3 个身位被切掉,保底通道又正好失效(见下),
# 于是整篇消失。放大到 5 倍(30)给"语义相关但排名偏后"的新研报留出余量;
# 超采样只扩大候选池,最终仍由 top_k+去重决定输出,不会把噪声放进回答。
_OVERSAMPLE_FACTOR = 5
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


def index_reports_batch(rows: list[dict]) -> int:
    """批量索引多份研报:合并切块后**一次**编码、**一次** upsert。

    【为什么】(2026-09-22)store.upsert_chunks 每次都是 exclusive_access()(跨进程锁 +
    短命 client),单篇调用 = 每篇一次开库/关库(实测 56~74 ms)+ 一次 HNSW flush。
    回填/补跑原先逐篇 index_report,610 篇 = 610 次开库。合并后开库/关库/锁开销
    降为常数级,编码也走 sentence-transformers 的批量路径(对短文本复用 tokenizer 缓存)。
    语义与逐篇等价:chunk id 是 `report_id:index` 幂等 upsert,顺序不影响结果。
    空文本行跳过,返回实际写入 chunk 总数。
    """
    chunks_all: list[dict] = []
    for row in rows:
        chunks = build_chunks(row)
        if chunks:
            chunks_all.extend(chunks)
    if not chunks_all:
        return 0
    embeddings = embedding.encode([c["embedding_text"] for c in chunks_all])
    return store.upsert_chunks(chunks_all, embeddings)


def delete_report(report_id: int) -> None:
    store.delete_by_report(report_id)


def report_indexed(report_id: int) -> bool:
    """该报告是否已有向量(回填跳过判断用)。"""
    return store.has_report(report_id)


def index_status() -> dict:
    """向量数 / 已索引报告数 / 是否启用(供状态路由)。"""
    import chromadb  # noqa: F401  到这一步说明依赖在

    n_chunks = store.count()
    report_ids = set()
    if n_chunks:
        for chunk_id in store.get_ids():
            try:
                report_ids.add(int(chunk_id.split(":", 1)[0]))
            except ValueError:
                continue
    return {"chunks": n_chunks, "reports": len(report_ids)}


def health_check(sample: int = 30, seed: int = 20260917) -> dict:
    """抽样自召回体检:把报告的正文块**重新编码**后去检索,看能否召回它自己。

    【为什么必须这样验】2026-09-17 真机:库的 count() / get_ids() 都报 4648 块、
    状态接口一切"正常",但其中 270 篇(64%)的向量**根本不在检索索引里** ——
    `get(include=["embeddings"])` 对它们抛 chroma InternalError("Error finding id"),
    重新编码后自召回率 0/3。可见"块数对不对"完全证明不了"检不检得出来"。

    【判据】只有"正文 → 重新编码 → 检索 → 命中自己"才是端到端判据。重编码带
    一点量化误差,但同篇命中时余弦实测 ≥0.95,远高于任何跨篇相似度,判定无歧义。

    【返回】{"sampled": 抽样篇数, "hit": 命中篇数, "recall": 命中率,
             "failed": [未命中 report_id...], "error": 可选错误说明}
    """
    import random
    from datetime import date

    try:
        rids = sorted(
            {
                int(cid.split(":", 1)[0])
                for cid in store.get_ids()
                if str(cid).split(":", 1)[0].isdigit()
            }
        )
        if not rids:
            return {"sampled": 0, "hit": 0, "recall": None, "failed": [],
                    "error": "索引为空", "checked_at": date.today().isoformat()}
        rnd = random.Random(seed)
        picked = rnd.sample(rids, min(max(1, sample), len(rids)))
        hit, failed, errors = 0, [], []
        for rid in picked:
            ok = False
            try:
                one = store.get_by_report(rid, include=["documents"])
                docs = one.get("documents")
                docs = [] if docs is None else list(docs)
                if docs:
                    vec = embedding.encode([str(docs[0])])[0]
                    res = store.query(list(vec), top_k=1)
                    if res:
                        ok = str(res[0]["id"]).split(":", 1)[0] == str(rid)
            except Exception as e:  # noqa: BLE001  单篇异常计为未命中
                errors.append(f"{rid}:{type(e).__name__}")
            if ok:
                hit += 1
            else:
                failed.append(rid)
        return {
            "sampled": len(picked),
            "hit": hit,
            "recall": round(hit / len(picked), 4),
            "failed": failed[:50],
            "error": ("; ".join(errors[:5]) or None),
            "checked_at": date.today().isoformat(),
        }
    except Exception as e:  # noqa: BLE001  体检失败不抛,交给调用方展示
        logger.warning("RAG 自召回体检失败", exc_info=True)
        return {"sampled": 0, "hit": 0, "recall": None, "failed": [],
                "error": f"{type(e).__name__}: {e}"}


def _match_variety(meta: dict, code: str) -> bool:
    if not meta:  # 【容错】ChromaDB 偶发返回 metadata=None,直接判不匹配(2026-09-10)
        return False
    if meta.get("variety") == code:
        return True
    return code in (meta.get("varieties") or "").split(",")


def _recency_factor(publish_date: str) -> float:
    """按发布日期算时效权重(今日=1,越旧越接近 0,指数衰减)。

    【为什么需要】纯向量检索无时间概念(2026-09-09 实测:库里 09-06 的长周报
    chunk 占 51%,问"最新观点"仍把 08-30/09-06 的老片段排前面,LLM 因此把
    "最新"误判成 9 月 6 日)。这里给新片段加权,让同语义下新研报优先。
    【参数】publish_date: yyyy-mm-dd(缺/非法按最旧处理,权重 0)。
    【返回】0.0~1.0。
    【可调】环境变量 RAG_RECENCY_HALFLIFE 半衰期天数(默认 10)。
    """
    import os
    from datetime import date, datetime

    try:
        d = datetime.strptime((publish_date or "")[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return 0.0
    age = (date.today() - d).days
    if age < 0:
        age = 0
    try:
        half_life = float(os.environ.get("RAG_RECENCY_HALFLIFE") or 10)
    except ValueError:
        half_life = 10.0
    if half_life <= 0:
        return 1.0
    return 0.5 ** (age / half_life)


def _merge_recent_reports(
    hits: list[dict],
    qvec: list[float],
    top_k: int,
    variety: str | None = None,
    report_type: str | None = None,
    exclude_report_id: int | None = None,
    recent_meta_provider=None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """把"日期最新的几份研报"并入候选,纯语义召回之外的保底通道。

    【为什么需要】2026-09-09 真机:库里 09-06 的长周报 chunk 占 51%(单篇 100+ 块),
    而当日新日报只有 6~8 块;问"碳酸锂最新观点"时新日报连候选池(fetch_k=3×top_k)
    都进不去,导致问答永远引老报告、模型把"最新"误判成 9 月 6 日。这里按发布日期
    倒序取最近的 N 份(默认 3,env RAG_RECENT_REPORTS 可调)强制入池,再与语义结果
    一起按时效加权分排序 —— 新研报相关性够就自然排前,不至于被长报告淹没。
    【返回】合并去重后的候选列表(已按加权分降序)。

    【2026-09-22 性能改造】recent_meta_provider: 可选可调用对象,返回
    {report_id: {publish_date, varieties, report_type}},由调用方从 SQLite 直查
    (database.list_done_report_meta,只取 4 列)。提供时**不再全库拉 metadata**
    (store.get_metadatas() 每次把全部 6,416 条 metadata 拉一遍,占检索 72% 耗时且
    随库规模线性恶化);为 None 时回退旧逻辑(独立脚本/测试不注入也能跑)。

    【2026-09-22 窗口感知】date_from/date_to(YYYY-MM-DD,闭区间,与 retrieve_hits
    的 _in_range 同语义):指定时**先把 latest 限定到窗口内**再取"最新 N ∪ 近 recent_days"。
    【为什么】否则保底通道永远只保"全局最新∪近3天",用户指定旧区间(如 8.1-8.20)时
    窗口内的目标研报既不在保底候选、语义池(top_k×5)又可能被近期日报占满 → 指定
    历史区间时召回稀少(库越大越明显,2026-09-22 实测当前库尚不触发,属防御性修复)。
    """
    import os

    try:
        n_reports = int(os.environ.get("RAG_RECENT_REPORTS") or 8)
    except ValueError:
        n_reports = 8
    if n_reports <= 0:
        return hits

    try:
        # 【2026-09-17】不再直接拿 store.get_collection()(长持 client 会用过期内存
        # 状态整体 flush,覆盖别的进程刚写入的向量);改走 store 的短命访问封装。
        latest: dict[int, str] = {}
        if recent_meta_provider is not None:
            # 【新路径】调用方注入的 DB 元数据(轻量 4 列),零全库扫描。
            meta_map = recent_meta_provider() or {}
            for rid, m in meta_map.items():
                rid = int(rid or 0)
                if not rid or rid == exclude_report_id:
                    continue
                if variety and not _match_variety(m, variety.strip().upper()):
                    continue
                if report_type and (m.get("report_type") or "") != report_type:
                    continue
                d = (m.get("publish_date") or "")[:10]
                if d and (rid not in latest or d > latest[rid]):
                    latest[rid] = d
        else:
            for meta in store.get_metadatas():
                rid = int(meta.get("report_id", 0) or 0)
                if not rid or rid == exclude_report_id:
                    continue
                if variety and not _match_variety(meta, variety.strip().upper()):
                    continue
                if report_type and meta.get("report_type") != report_type:
                    continue
                d = (meta.get("publish_date") or "")[:10]
                if d and (rid not in latest or d > latest[rid]):
                    latest[rid] = d
        if date_from or date_to:
            # 【窗口感知】指定日期范围时,保底候选只从窗口内选(与 _in_range 同语义:
            # publish_date 为空的行被排除,YYYY-MM-DD 字典序即时间序)。
            latest = {
                rid: d for rid, d in latest.items()
                if (not date_from or d >= date_from) and (not date_to or d <= date_to)
            }
        have = {int(h["metadata"].get("report_id", 0)) for h in hits}
        # 【2026-09-17 新增"最近 N 天窗口"】原实现只取"**全库最新** n_reports 篇",
        # 语义是"最新"而非"最近":今天(09-17)全库最新的 8 篇全是当天日报,于是
        # 09-16 的研报(问"近日情况"时用户正是要找它)**一篇都不在候选池里** ——
        # 门槛设多低都没用,因为候选里根本没有它。改为:
        #   最新 n_reports 篇  ∪  最近 recent_days 天内发布的报告(env RAG_RECENT_DAYS,
        #   默认 3 天),整体按发布日期降序、并以 cap 封顶防止全库扫描。
        ordered = sorted(latest, key=lambda rid: latest[rid], reverse=True)
        try:
            recent_days = int(os.environ.get("RAG_RECENT_DAYS") or 3)
        except ValueError:
            recent_days = 3
        cutoff = ""
        if recent_days > 0:
            from datetime import date, timedelta

            cutoff = (date.today() - timedelta(days=recent_days)).isoformat()
        cap = max(n_reports, n_reports * 8)
        today_str = date.today().isoformat() if recent_days > 0 else ""
        newest: list[int] = []
        fresh_rids: set[int] = set()   # 近 recent_days 的保底候选
        today_rids: set[int] = set()   # 今天发布的保底候选(提权用, 2026-09-22)
        # 【2026-09-24 宽窗口均匀采样】指定较宽日期区间(如整月复盘)时, 保底通道
        # 只取"窗口内最新 n_reports 份"会把早期日期(常为已归档 summary 卡片)全挤出。
        # 窗口按日期等分, 每段取该段最新 1 个 rid, 与"最新 n ∪ 近3天"合并去重。
        # 【为什么覆盖 summarized】归档总结卡片的 rid = research_summaries 组首篇
        #   (provider 已补注), 其 publish_date = 组日期 ⇒ 早期段必然选中卡片 rid,
        #   get_by_reports 按 report_id 取块会取到 kind=summary 的卡片块。
        sampled_rids: list[int] = []
        if date_from and date_to:
            try:
                from datetime import date as _d, timedelta as _td
                d0, d1 = _d.fromisoformat(date_from), _d.fromisoformat(date_to)
                span = max(1, (d1 - d0).days + 1)
                if span >= 4:  # 宽窗口(≥4 天)才采样, 窄窗口保持原行为
                    n_seg = min(span, n_reports * 4)  # 每 ~1 天 1 段(封顶 n_reports*4), 保证复盘覆盖每个归档交易日
                    for i in range(n_seg):
                        a = d0 + _td(days=i * span // n_seg)
                        b = d0 + _td(days=(i + 1) * span // n_seg - 1) if i < n_seg - 1 else d1
                        # 【2026-09-24】**不排除 have**: 语义池里该 rid 的高分块
                        # (多品种研报的 PF/PR 卡片)不代表目标品种卡片已覆盖 —— rid 在
                        # have 就把整个 rid 排除, 会导致低分的短卡片(如 09-02 TA)两头
                        # 落空(语义池 40 名外 + 保底被 have 挡)。品种过滤在采样前已完成,
                        # 保底 TA 块不会与语义池串味; 重复块由 id 唯一化 + top_k 截断兜底。
                        seg = [rid for rid, dd in latest.items()
                               if a.isoformat() <= dd <= b.isoformat()]
                        if seg:
                            sampled_rids.append(max(seg, key=lambda rid: latest[rid]))
            except Exception:  # noqa: BLE001  采样失败回退原逻辑
                sampled_rids = []
        for rid in ordered:
            if rid in have:
                continue
            fresh = bool(cutoff) and latest[rid] >= cutoff
            if fresh:
                fresh_rids.add(rid)
                if latest[rid] == today_str:
                    today_rids.add(rid)
            if len(newest) < n_reports or (fresh and len(newest) < cap):
                newest.append(rid)
            if len(newest) >= cap:
                break
        if sampled_rids:
            # 宽窗口采样段优先, 其后补最新 n 份; 去重保序
            newest = list(dict.fromkeys(sampled_rids + newest))
        if not newest:
            return hits
        # 【相关性门槛】新研报也得和问题沾边才入池,否则"最新的 N 份"会把无关品种
        # 的日报硬塞进来(2026-09-09 实测:问碳酸锂却混进 09-09 纯苯苯乙烯日报,
        # 相似度仅 0.45,而 09-09 碳酸锂日报是 0.75)。默认 0.55,env RAG_RECENT_MIN_SIM 可调。
        #
        # 【2026-09-17 两处修正】
        # ① variety 已指定时门槛降为 0:上面 latest 已经用 _match_variety 精确筛过,
        #    池子里**只有目标品种**,"防无关品种混入"的前提不存在了。此时还卡 0.65,
        #    会把"同品种但换了个说法的新日报"误杀 —— 这正是"搜 PTA 近日情况拿不到
        #    09-16 华泰聚酯日报"的第二个元凶(该篇对问句余弦 0.676,与 0.65 只差 0.026)。
        # ② 逐篇容错:此前整段共用一个 try,任一 report 取 embeddings 抛错(库残缺时
        #    会抛 chroma InternalError "Error finding id")就把**整条保底通道**废掉、
        #    直接 return hits。改为单篇失败 continue,坏了一篇不影响其余新研报入池。
        try:
            min_sim = float(os.environ.get("RAG_RECENT_MIN_SIM") or 0.55)
        except ValueError:
            min_sim = 0.55
        if variety:
            min_sim = 0.0
        # 【取向量:先批量,失败再退化为逐篇(逐篇内再单篇容错)】
        # 批量是为了省开库次数 —— 窗口打开后候选可达数十篇,逐篇各开一次库会把
        # QA 首字延迟拉长;退化路径则保住"库局部残缺时不至于整条通道报废"。
        _inc = ["documents", "metadatas", "embeddings"]
        records: dict = {"documents": [], "metadatas": [], "embeddings": []}
        try:
            got = store.get_by_reports(newest, include=_inc)
            for key in records:
                val = got.get(key)
                records[key] = [] if val is None else list(val)
        except Exception as e:  # noqa: BLE001  批量失败 → 退化为逐篇
            logger.warning("RAG 保底通道批量取向量失败,退化为逐篇: %s", e)
            for rid in newest:
                try:
                    one = store.get_by_report(rid, include=_inc)
                except Exception as e2:  # noqa: BLE001  单篇失败只跳过该篇
                    logger.warning("RAG 保底通道取报告 %s 向量失败,跳过: %s", rid, e2)
                    continue
                for key in records:
                    val = one.get(key)
                    records[key].extend([] if val is None else list(val))
        # 【2026-09-22 修复】今天发布的保底候选提权:
        # 用户问"近日情况"时,今天的短日报与问句语义分常显著低于老周报
        # (真机实测 0.44~0.49 vs 0.55),且 _apply_recency_boost 对"今天"
        # 内容归一化到原分(recency=1 → ×1,零提升)、只衰减老内容 → 新日报
        # 被挤出 top_k("LU近日情况"拿不到当天 LU 日报的根因)。只对**今天**
        # 的保底候选乘 (1 + RAG_RECENT_BOOST, 默认 0.8);近 3 天但非今天的
        # 候选不加成(它们语义分通常已够进 top_k, 避免过度提权霸榜)。
        try:
            recent_boost = float(os.environ.get("RAG_RECENT_BOOST") or 0.8)
        except ValueError:
            recent_boost = 0.8
        for bidx, (doc, meta, emb) in enumerate(zip(records["documents"], records["metadatas"], records["embeddings"])):
            if not meta or emb is None:
                continue
            # 【2026-09-24 归档】保底块按品种过滤: 一篇多品种研报被分到多组,
            # 其首篇 rid 下挂多张卡片(PF/PR/PX/TA...), get_by_reports 全取回。
            # 不过滤会把无关品种的卡片(白名单外长 conclusion 版尤甚)混入排序、
            # 挤出目标品种卡片(整月复盘实测 TA 只覆盖 3 天)。
            # 仅当块带 varieties 字段才过滤(缺失时放行, 兼容无品种标注的旧块/测试)。
            if variety and meta.get("varieties") and not _match_variety(meta, variety.strip().upper()):
                continue
            sim = float(sum(a * b for a, b in zip(qvec, emb)))  # 已 L2 归一化 → 点积=余弦
            if sim < min_sim:
                continue
            rid = int(meta.get("report_id", 0) or 0)
            if recent_boost > 0 and rid in today_rids:
                sim *= (1 + recent_boost)
            # 【2026-09-24 修复】保底块 id 唯一化: 同 rid 下多张卡片块若共用
            # "rid:recent" 会 id 重复, 下游按 id 去重时 score 低的块(短卡片)
            # 被同 rid 高分的无关品种块挤掉。追加块序号保证唯一。
            hits.append({"id": f"{rid}:recent:{bidx}", "text": doc, "metadata": meta, "score": sim})
    except Exception as e:  # noqa: BLE001  保底通道失败不影响主检索
        logger.warning("RAG recent-reports recall failed: %s", e)
        return hits
    # 【2026-09-17】此处**不再**做时效加权:调用方 retrieve_hits 会在本函数返回后
    # 统一加权一次。此前 merge 内外各加权一次,新片段被 (1+W·r)/(1+W) 连乘两遍
    # (旧片段同样被双重衰减),排序与"按一次加权"不等价。
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits


def _temporal_sampling(
    hits: list[dict],
    date_from: str,
    date_to: str,
    top_k: int,
) -> list[dict]:
    """【2026-09-22 整月复盘】宽日期窗口的时间分层采样。

    【为什么】纯语义检索只回"与问句最像"的块(实测: 问"PTA 九月复盘", top_k=6
    只覆盖 09-16 起 5 个交易日, 9 月上半月全部丢失; top_k=20 仍缺 09-01~07)。
    "最像"≠"时间均匀覆盖" —— 复盘/全月类宽查询需要全时段的演进脉络。
    【做法】把 [date_from, date_to] 等分成 max(2, min(top_k, 跨度)) 段, 每段
    保底取该段内最高分 1 块(hits 已按 score 降序, 首个命中即最高), 合并返回。
    一次检索、零额外耗时; 候选池已由调用方按跨度放大(fetch_k)。
    【边界】跨度 <4 天不触发(窄窗口语义池足够); 某段候选池空缺则留空。
    """
    from datetime import datetime, timedelta

    try:
        d0 = datetime.strptime(date_from[:10], "%Y-%m-%d").date()
        d1 = datetime.strptime(date_to[:10], "%Y-%m-%d").date()
        span = (d1 - d0).days + 1
    except (ValueError, TypeError):
        return hits
    if span < 4 or not hits:
        return hits
    n_buckets = max(2, min(top_k, span))
    picked: list[dict] = []
    for i in range(n_buckets):
        s = d0 + timedelta(days=span * i // n_buckets)
        e = d0 + timedelta(days=span * (i + 1) // n_buckets) - timedelta(days=1)
        s_str, e_str = s.isoformat(), e.isoformat()
        for h in hits:  # hits 按 score 降序 → 首个命中即该段最高分
            pd = (h["metadata"].get("publish_date") or "")[:10]
            if s_str <= pd <= e_str:
                picked.append(h)
                break
    return picked or hits


def _apply_recency_boost(hits: list[dict]) -> list[dict]:
    """对检索结果做时效加权并重排(score 为加权后的值,供前端展示)。

    【算法】weighted = score * (1 + W * recency) / (1 + W),W 由
    RAG_RECENCY_WEIGHT 控制(默认 0.35,今日片段加权最大,半衰期外的
    老片段基本不加)。乘性加权而非加分,避免把低相似度的垃圾片段硬顶上来。
    最后除以 (1 + W) 归一化:score 上限回到 1.0,前端按百分比展示不会
    超过 100%(2026-09-10 修复:此前今日片段最高 1.35 被展示成 135%);
    所有片段除以同一常数,相对排序不变。
    """
    import os

    try:
        weight = float(os.environ.get("RAG_RECENCY_WEIGHT") or 0.35)
    except ValueError:
        weight = 0.35
    if weight <= 0:
        return hits
    denom = 1 + weight
    for h in hits:
        base = float(h.get("score", 0.0))
        h["score"] = base * (1 + weight * _recency_factor(
            (h.get("metadata") or {}).get("publish_date")
        )) / denom
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits


def retrieve_hits(
    question: str,
    top_k: int = 6,
    variety: str | None = None,
    report_type: str | None = None,
    exclude_report_id: int | None = None,
    dedupe_reports: bool = True,
    max_per_report: int = _MAX_PER_REPORT,
    date_from: str | None = None,
    date_to: str | None = None,
    recent_meta_provider=None,
) -> list[dict]:
    """检索 top_k 个片段;variety/report_type/日期范围在应用层后过滤。

    dedupe_reports=True(默认)按 report_id 去重、每篇最多保留 max_per_report 个
    得分最高的片段 —— 一篇研报切块多、多块同时排进前 k 时,不去重会出现"同一篇
    引用多次"(2026-09-08 用户实测反馈);但每篇只留 1 块又会把长报告的其他方面
    (如宏观段)全部挡在外面(同日实测:原油问答只检回表格块),故默认留 2 块。
    date_from/date_to(YYYY-MM-DD,闭区间):Chroma where 不支持日期比较,走应用层
    后过滤(YYYY-MM-DD 字典序即时间序);publish_date 为空的块在设了范围时被排除
    (语义:日期不明的研报不纳入范围筛选,前端/prompt 已提示)。
    【来源】2026-09-14 合并研报问答(QA)模块:多轮问答支持日期范围筛选。
    """
    question = (question or "").strip()
    if not question:
        return []
    fetch_k = top_k * _OVERSAMPLE_FACTOR
    # 【2026-09-22 整月复盘】宽日期窗口(跨度>=4 天)时放大候选池: 语义检索只回
    # "最像问句"的块, 早段研报可能连 fetch_k 候选池都进不去(实测 top20 仍漏
    # 09-01~07), 分层采样才"无米下锅"。按天数放大 fetch_k(封顶 300)。
    if date_from and date_to:
        from datetime import datetime

        try:
            _span = (
                datetime.strptime(date_to[:10], "%Y-%m-%d").date()
                - datetime.strptime(date_from[:10], "%Y-%m-%d").date()
            ).days + 1
        except (ValueError, TypeError):
            _span = 0
        if _span >= 4:
            fetch_k = max(fetch_k, min(_span * 3, 300))
    qvec = embedding.encode([question])[0]
    hits = store.query(qvec, top_k=fetch_k, exclude_report_id=exclude_report_id)
    hits = _merge_recent_reports(  # 【调用函数】最新研报保底召回(纯语义召回会漏掉短日报)
        hits, qvec, top_k, variety=variety, report_type=report_type,
        exclude_report_id=exclude_report_id, recent_meta_provider=recent_meta_provider,
        date_from=date_from, date_to=date_to,
    )
    # 【调用函数】时效加权:新研报优先(见 _apply_recency_boost 说明)。
    # 【2026-09-17】从"merge 之前"移到"merge 之后"统一做一次:保底通道新加的候选
    # 是原始余弦、未经加权,原先在 merge 前加权会让"老块已衰减、新块未加权"同池比较,
    # 且 merge 内部还会再乘一次 —— 现在全池只乘一次,口径统一。
    hits = _apply_recency_boost(hits)
    if variety:
        code = variety.strip().upper()
        hits = [h for h in hits if _match_variety(h["metadata"], code)]
    if report_type:
        hits = [h for h in hits if h["metadata"].get("report_type") == report_type]
    if date_from or date_to:
        # 【2026-09-14 合并 QA 模块】日期范围后过滤(YYYY-MM-DD 字典序即时间序);
        # publish_date 为空的块在设了范围时排除(日期不明的不纳入范围筛选)。
        def _in_range(meta: dict) -> bool:
            pd = meta.get("publish_date") or ""
            if not pd:
                return False
            if date_from and pd < date_from:
                return False
            return not (date_to and pd > date_to)

        hits = [h for h in hits if _in_range(h["metadata"])]
    # 【2026-09-22 整月复盘】宽窗口时间分层采样: 把日期范围等分成 top_k 段,
    # 每段保底取最高分 1 块 —— "语义最像"≠"时间均匀覆盖", 宽查询(复盘/全月)
    # 时保证全时段都有代表, 而不是只给"月底快照"。
    if date_from and date_to:
        hits = _temporal_sampling(hits, date_from, date_to, top_k)
    if dedupe_reports:
        # ── 按**篇**归并排序 + 公平分配(2026-09-17 重写) ────────────────────
        # 【旧实现的缺陷 · 块多者通吃】旧写法是"块级排序 → 每篇最多留 2 块 →
        # 再按块分展平排序 → 截断 top_k 块"。因为排序单位始终是**块**,一篇切块
        # 多的长报告会霸占大量候选槽位。真机实测问"PTA今日情况":raw top20 里
        # rid=485 占 5 个槽、rid=44 占 4 个、rid=22 占 4 个 —— 20 个槽只覆盖 7 篇;
        # 而 09-16 的华泰聚酯日报(579,短日报全文仅 4 块)块分 0.689,按**篇**算
        # 明明排第 3(时效加权后 0.671 > 44 的 0.629),却因为"块排名"被挤出 30 名池。
        # 用户看到的"搜 PTA 近日情况找不到 09-16 的研报",一半是这个结构问题。
        # 【新算法】三步:
        #   ① 按 report_id 归并,篇分 = 该篇最高块分,篇内保留得分最高的 max_per_report 块;
        #   ② 按**篇分**降序 —— 至此一篇只占**一个**排名位,块多不再有优势;
        #   ③ 公平分配预算:先给每篇 1 块(让引用尽量来自不同研报),预算还有富余
        #      才回头补同篇的第 2 块。这样 top_k=6 覆盖 6 篇而不是 3 篇。
        buckets: dict[int, list[dict]] = {}
        for h in hits:  # store.query 已按 score 降序 → 篇内天然"高分块在前"
            rid = int(h["metadata"].get("report_id", 0))
            bucket = buckets.setdefault(rid, [])
            if len(bucket) < max(1, max_per_report):
                bucket.append(h)
        ranked = sorted(buckets.values(), key=lambda b: b[0]["score"], reverse=True)
        budget = max(1, top_k)
        picked: list[dict] = []
        for bucket in ranked:  # 第一轮:每篇 1 块
            if budget <= 0:
                break
            picked.append(bucket[0])
            budget -= 1
        if budget > 0:  # 第二轮:预算有余才补同篇第 2 块(候选篇数不足 top_k 时)
            for bucket in ranked:
                for h in bucket[1:]:
                    if budget <= 0:
                        break
                    picked.append(h)
                    budget -= 1
                if budget <= 0:
                    break
        # 【2026-09-17 补丁】两轮分配决定的是"**选哪几块**",但**展示顺序**必须
        # 回到全局 score 降序:第二轮补进来的"同篇第 2 块"分数可能高于第一轮
        # 某篇的第 1 块(实测 [高, 低, 中] —— rid1 的 B 块分高于 rid2 的 A 块),
        # 于是返回结果不再降序。下游 format_context 是按顺序编号 [1][2][3] 注入
        # prompt 的,模型会按位置轻重来读;顺序错乱等于把高分块降格。这里用稳定
        # 排序(sorted 保序)只重排顺序、不改变选中集合,公平分配的覆盖面不受影响。
        hits = sorted(picked, key=lambda h: h["score"], reverse=True)
    return hits[:top_k]


def format_context(hits: list[dict], header: str = CONTEXT_HEADER) -> str:
    """把检索结果拼成可注入提示词的文本块;无命中返回 ""。

    【2026-09-09】头部强制带上今天日期:纯片段里没有"当前时间"概念,模型会按
    片段中最晚的日期反推"今天"(实测把 9 月 6 日当成今天),必须显式告知。
    """
    if not hits:
        return ""
    from datetime import date

    header = f"{header}(今天是 {date.today():%Y-%m-%d},片段日期均不晚于今天)"
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
    recent_meta_provider=None,
) -> str:
    """分析流程注入入口:检索并格式化,任何异常/未启用返回 ""(绝不拖垮主流程)。"""
    try:
        hits = retrieve_hits(
            query_text, top_k=limit, variety=variety, exclude_report_id=exclude_report_id,
            recent_meta_provider=recent_meta_provider,
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

    【2026-09-22 批量化】原实现逐篇 report_indexed()(每篇一次 exclusive_access 开库)+
    逐篇 index_report()(又一次开库/关库),610 篇 = 1220 次开库。现改为:
      ① 一次性 store.get_ids() 取全库已索引 rid 集合,跳过判定零开库;
      ② 待写行按 RAG_BACKFILL_BATCH(默认 16)分组,一次 index_reports_batch upsert。
    语义与逐篇等价(chunk id 幂等);批量失败时降级为逐篇重试,保住"单份失败不中断回填"。
    force 仍每篇先删净再写(真重建)。
    """
    batch_size = _backfill_batch_size()
    done = skipped = failed = 0
    total = len(rows)
    indexed_rids = _indexed_report_ids()
    pending: list[dict] = []

    def _flush(batch: list[dict]) -> None:
        nonlocal done, failed
        try:
            index_reports_batch(batch)
            done += len(batch)
        except Exception:  # noqa: BLE001  批量失败(多为个别坏行)降级逐篇
            for r in batch:
                try:
                    index_report(r)
                    done += 1
                except Exception:  # noqa: BLE001  单份失败不中断回填
                    failed += 1
                    logger.warning("RAG 回填失败 report_id=%s", r.get("id"), exc_info=True)

    for i, row in enumerate(rows, 1):
        report_id = int(row["id"])
        if not force and report_id in indexed_rids:
            skipped += 1
        else:
            if force:
                # 真重建:切块/过滤规则变化后,旧行的 chunk id 集合可能与新行不同,
                # 只 upsert 会残留已消失的旧 chunk —— 先删净再写。
                with contextlib.suppress(Exception):
                    store.delete_by_report(report_id)
            pending.append(row)
            if len(pending) >= batch_size:
                _flush(pending)
                pending = []
        if progress_cb:
            progress_cb(i, total, report_id)
    if pending:
        _flush(pending)
    return {"done": done, "skipped": skipped, "failed": failed}


def _backfill_batch_size() -> int:
    try:
        v = int(os.environ.get("RAG_BACKFILL_BATCH") or "16")
    except (TypeError, ValueError):
        return 16
    return v if v > 0 else 16


def _indexed_report_ids() -> set[int]:
    """全库已索引的 report_id 集合(一次开库取回,替代逐篇 report_indexed)。"""
    try:
        ids = store.get_ids()
    except Exception:  # noqa: BLE001  取不到就按全未索引处理(逐篇路径会兜底)
        return set()
    out: set[int] = set()
    for cid in ids:
        head = str(cid).split(":", 1)[0]
        if head.isdigit():
            out.add(int(head))
    return out
