"""Chroma 向量库封装:懒加载持久化客户端,embedding 由调用方算好传入。

持久目录默认 ~/.tradingagents/rag_chroma,环境变量 RAG_CHROMA_DIR 可覆盖。
collection 建为 cosine 距离空间(配合归一化向量)。

【多进程写入纪律 · 2026-09-17 事故后补】
chroma 的 PersistentClient **只在单进程内并发安全**,它假设"一个目录只有一个进程在
用"。本系统实际上有 4 类独立进程会写同一目录:
    web 的 rag_worker 子进程 / htfc 采集器 / gtja 采集器 / dz 采集器(后三者因
    _RAG_WORKER_MODE=False 是进程内直调),外加手动回填脚本与补跑。
每个进程各自持有**独立的内存 HNSW 索引**,flush 时把整个索引**整体重写**到
data_level0.bin / length.bin / link_lists.bin / index_metadata.pickle —— 后写者
覆盖先写者,sqlite 里的 embeddings_queue 水位与 HNSW 文件里的 label 表就此脱节
(2026-09-16 实测:HNSW 水位停在 seq 19162,141 条向量永久积压,index_metadata
自报 11790 个 label 而实际文件对不上 → Rust 层遍历越界 → access violation,
RAG 读一次崩一次,整个检索/问答链路全断)。
因此**所有写路径**都必须包在 _cross_process_write_lock() 里,不能只靠 _init_lock。
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

COLLECTION_NAME = "research_reports"
# 跨进程写锁文件(放在库目录内,随库一起迁移/备份)
_WRITE_LOCK_FILENAME = ".rag_write.lock"


def _hnsw_metadata() -> dict:
    """建 collection 用的 HNSW 参数。

    【为什么必须显式调小 · 2026-09-17 事故】chroma 的持久化 HNSW segment 是**分层缓冲**的
    (venv/.../segment/impl/vector/local_persistent_hnsw.py):
        _write_records 逐条累加计数器 →
        每 batch_size(默认 100) 条才 _apply_batch (= 真正 add 进 HNSW 索引) →
        每 sync_threshold(默认 1000) 条才 _persist (= 写 index_metadata.pickle + 推进 sqlite 的 max_seq_id)
    而 `close()` 只 close_file_handles(),**不 persist**。本系统是"每篇研报一次 upsert
    (3~30 块)后立刻 close"(短命 client,见 exclusive_access),单批远小于 100 ——
    于是记录长期滞留在内存 _curr_batch/_brute_force_index 里,靠"下次打开时从
    max_seq_id 之后重放"来兜。一旦 max_seq_id 因任何原因超前于 index_metadata
    (真机实测:sqlite 声明 4648 块 / HNSW 只认 1001 块,270 篇研报重新编码后
    自召回率 0/3 —— 即这些向量**根本不在检索索引里**),数据就永久丢失。

    设 batch_size=1 让每条记录**立即**进 HNSW(计数器与 id_to_label 不再有水位差),
    sync_threshold=100 让持久化足够频繁。代价是写入慢一些(RAG 调用频率低,可接受),
    换来的是"任何时刻中断都不丢"。env 可覆盖,便于按量调优。
    """
    # ⚠️ chroma 对这两个参数有硬校验:`p > 2`(见 hnsw_params.persistent_param_validators),
    #    传 1 会直接 InvalidArgumentError「Failed to parse hnsw parameters」建不出 collection。
    #    故最小合法值是 3 —— 3 已经足够把"每篇 3~30 块一次 upsert"里的绝大多数批次
    #    立刻 apply 进 HNSW,水位差最多 2 条,远好于默认的 100。
    def _int(name: str, default: int) -> int:
        try:
            v = int(os.environ.get(name) or default)
        except (TypeError, ValueError):
            return default
        return v if v > 2 else default

    batch = _int("RAG_HNSW_BATCH_SIZE", 3)
    sync = _int("RAG_HNSW_SYNC_THRESHOLD", 30)
    if sync < batch:  # chroma 要求阈值不小于批大小
        sync = batch
    return {
        "hnsw:space": "cosine",
        "hnsw:batch_size": batch,
        "hnsw:sync_threshold": sync,
    }

_client = None
_collection = None
# chroma 0.5x 的 PersistentClient 全局注册表非线程安全:多线程同时首次建连会
# 撞 _identifier_to_system KeyError,且其失败路径会 stop() 共享系统(rust bindings
# 被删),并发使用下可拖垮整个进程(2026-09-09 web_app 首采 RAG 并发索引时复现)。
# 这里用模块级锁把"首次建连+建 collection"串行化。
# ⚠️ 这**只覆盖单进程内**;跨进程的写冲突由 _cross_process_write_lock() 负责,
#    两者缺一不可(2026-09-16 的库损坏正是"只做了前者"的直接后果)。
_init_lock = threading.Lock()
# 进程内写互斥:先在同一进程里排队,避免同进程多线程互相抢文件锁
_write_lock = threading.Lock()


if os.name == "nt":  # Windows:msvcrt 文件锁,进程崩溃时由 OS 自动释放,不留死锁
    import msvcrt

    def _acquire_os_lock(fh) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)

    def _release_os_lock(fh) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

else:  # POSIX:flock(同样随 fd 关闭自动释放)
    import fcntl

    def _acquire_os_lock(fh) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _release_os_lock(fh) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def _cross_process_write_lock(timeout_s: float = 900.0):
    """串行化**跨进程**的 chroma 写操作(含其后的首次建连与队列消费)。

    锁粒度取"建连 + 一次写调用":chroma 在 upsert/delete 时会顺带消费
    embeddings_queue 并 flush HNSW,把这些动作整体关进临界区即可;读路径
    (query/count)不消费队列、不写索引文件,因此不加锁以免读被写堵住。

    超时后**记录告警并继续**而不抛异常:宁可冒一次写风险,也不让整条采集/索引
    流水线卡死(上层已有 try/except 兜底)。锁文件建不出来时(只读盘等)同样降级。
    """
    lock_path = chroma_dir() / _WRITE_LOCK_FILENAME
    with _write_lock:  # 进程内先互斥
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(lock_path, "a+b")
        except OSError as e:
            logger.warning("RAG 写锁文件不可用(%s),本次写入未加跨进程锁", e)
            yield
            return
        with fh:
            deadline = time.monotonic() + timeout_s
            acquired = False
            while True:
                try:
                    _acquire_os_lock(fh)
                    acquired = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        logger.warning("RAG 跨进程写锁等待超时 %.0fs,继续写入", timeout_s)
                        break
                    time.sleep(0.2)
            try:
                yield
            finally:
                if acquired:
                    with contextlib.suppress(Exception):
                        _release_os_lock(fh)


def chroma_dir() -> Path:
    custom = os.environ.get("RAG_CHROMA_DIR")
    if custom:
        return Path(custom)
    return Path.home() / ".tradingagents" / "rag_chroma"


def get_collection():
    """懒加载 Chroma persistent client 与 collection(进程内单例,加锁防并发首连竞态)。

    ⚠️ 【2026-09-17 起不推荐直接使用】本函数返回的是**长生命周期**的 client 上的
    collection。长持 client 会带来两个已在真机造成故障的问题:(1) 它在内存里冻结了
    一份 HNSW 索引,写时整体 flush,会用**过期状态覆盖**别的进程刚写进去的数据;
    (2) 它一打开就启动自己的 compactor 后台线程,而 compactor 会写 HNSW 文件 ——
    相当于留了个随时会动的野写者。业务代码请一律改用 exclusive_access();
    本函数只为兼容旧调用与测试保留。
    """
    global _client, _collection
    if _collection is None:
        with _init_lock:
            if _collection is None:  # 双检:等锁期间可能已被他线程建好
                _client = _new_client()
                _collection = _client.get_or_create_collection(
                    name=COLLECTION_NAME,
                    metadata=_hnsw_metadata(),
                )
    return _collection


def _new_client():
    """新建一个 PersistentClient(调用方负责 close)。"""
    import chromadb
    from chromadb.config import Settings

    return chromadb.PersistentClient(
        path=str(chroma_dir()),
        settings=Settings(anonymized_telemetry=False),  # 单机自用,不上报
    )


def _drop_singleton() -> None:
    """丢弃进程内长持的单例引用,确保下次访问走全新 client。"""
    global _client, _collection
    with _init_lock:
        _client = None
        _collection = None


@contextlib.contextmanager
def exclusive_access():
    """对 chroma 的**任何**访问(读或写)统一入口:跨进程锁 → 新开 client → 用完 close。

    三条纪律缺一不可,每条都对应一次真机故障:

    1) **跨进程锁** —— 同一时刻只允许一个进程碰这组 HNSW 文件("防同时写")。
       本系统有 4 类独立进程会访问同一目录:web 的常驻 rag_worker + htfc/gtja/dz
       三个采集器(因 _RAG_WORKER_MODE=False 是进程内直调)。
    2) **client 短命** —— 每次访问都新建,保证基于磁盘最新状态("防用过期状态写")。
       chroma 的 client 在内存里持有一份 HNSW 索引,写时把这份**内存状态整体
       flush** 到 data_level0.bin 等文件。若进程 A 的 client 停在时刻 T0,而 B 在
       T0 之后写入并释放锁,A 再写就是用 T0 的状态**覆盖** B 的成果 —— 现象是
       sqlite 的 embeddings_queue 水位一路上涨、HNSW 的 max_seq_id 却几乎不动。
       2026-09-17 重建实测:常驻 rag_worker 与回填进程交替写,直接吃掉 458 条。
    3) **用完 close()** —— PersistentClient 一打开就启动自己的 compactor 后台线程,
       而 compactor 会把 sqlite 的 WAL apply 到 HNSW 索引 —— 也就是说**"打开库"
       这个动作本身就是一次写**。不 close 就等于在后台留一个随时会动的野写者。

    代价:每次多约 0.2~0.4s 的开库/关库时间。RAG 调用频率低,完全可接受;
    需要在一个作用域内连续多次操作时,请在 with 块里复用同一个 collection。
    """
    with _cross_process_write_lock():
        _drop_singleton()
        client = _new_client()
        try:
            yield client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata=_hnsw_metadata(),
            )
        finally:
            with contextlib.suppress(Exception):
                client.close()  # 关掉 compactor,别留后台写者


def upsert_chunks(chunks: list[dict], embeddings: list[list[float]]) -> int:
    """写入/覆盖 chunk(id 幂等)。chunks 元素为 chunking.build_chunks 的产物。

    走 exclusive_access()(跨进程锁 + 短命 client),理由见该函数 docstring。
    """
    if not chunks:
        return 0
    with exclusive_access() as collection:
        collection.upsert(
            ids=[c["id"] for c in chunks],
            embeddings=embeddings,
            documents=[c["text"] for c in chunks],
            metadatas=[c["metadata"] for c in chunks],
        )
    return len(chunks)


def delete_by_report(report_id: int) -> None:
    """删除一份研报的全部向量(不存在时静默)。同为写路径。"""
    with exclusive_access() as collection:
        collection.delete(where={"report_id": report_id})


def has_report(report_id: int) -> bool:
    """该报告是否已有向量(回填跳过判断用)。"""
    with exclusive_access() as collection:
        res = collection.get(where={"report_id": report_id}, limit=1, include=[])
    return bool(res["ids"])


def get_ids(include: list[str] | None = None) -> list[str]:
    """取全部 chunk id(index_status 统计已索引报告数用)。"""
    with exclusive_access() as collection:
        res = collection.get(include=include or [])
    return list(res["ids"])


def get_metadatas() -> list[dict]:
    """取全部 chunk 的 metadata(时效保底通道挑选新研报用)。"""
    with exclusive_access() as collection:
        res = collection.get(include=["metadatas"])
    return list(res["metadatas"] or [])


def get_by_report(report_id: int, include: list[str]) -> dict:
    """取某份报告的全部 chunk(_merge_recent_reports 细节展开用)。"""
    with exclusive_access() as collection:
        return collection.get(where={"report_id": report_id}, include=include)


def get_by_reports(report_ids: list[int], include: list[str]) -> dict:
    """批量取多份报告的全部 chunk(一次开库取回)。

    【为什么需要】保底通道要覆盖"最近几天的几十份报告",逐篇 get_by_report 会各触发
    一次开库(每次都要重新加载 HNSW 索引),几十次下来 QA 首字延迟明显变长;
    一次 `$in` 查询只需开一次库。
    """
    ids = [int(r) for r in report_ids]
    if not ids:
        return {"ids": [], "documents": [], "metadatas": [], "embeddings": []}
    with exclusive_access() as collection:
        return collection.get(where={"report_id": {"$in": ids}}, include=include)


def query(
    query_embedding: list[float],
    top_k: int = 6,
    exclude_report_id: int | None = None,
) -> list[dict]:
    """余弦近邻检索。返回 [{id, text, metadata, score}],score=1-距离(越大越近)。

    exclude_report_id 在此处用 where 过滤(单报告排除,精确匹配够用);
    品种过滤因 varieties 是逗号串,由上层超采样+后过滤完成。
    """
    where = {"report_id": {"$ne": exclude_report_id}} if exclude_report_id else None
    with exclusive_access() as collection:
        res = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
            where=where,
        )
    hits = []
    ids = res["ids"][0]
    docs = res["documents"][0]
    metas = res["metadatas"][0]
    dists = res["distances"][0]
    for i in range(len(ids)):
        hits.append(
            {
                "id": ids[i],
                "text": docs[i],
                "metadata": metas[i],
                "score": 1.0 - float(dists[i]),
            }
        )
    return hits


def count() -> int:
    with exclusive_access() as collection:
        return collection.count()


def reset_for_tests() -> None:
    """测试用:丢弃进程内单例(配合 RAG_CHROMA_DIR 指向 tmp_path)。"""
    global _client, _collection
    _client = None
    _collection = None
