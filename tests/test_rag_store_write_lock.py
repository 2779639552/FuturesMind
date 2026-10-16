"""RAG store 跨进程写锁回归测试。

【背景 · 2026-09-17 事故】chroma 的 PersistentClient 只在单进程内并发安全,而本系统
有 4 类独立进程会写同一目录:web 的 rag_worker 子进程 + htfc/gtja/dz 三个采集器进程
(后三者 `_RAG_WORKER_MODE=False`,进程内直调)。09-16 上午 08:25-09:12 它们并发
flush,各自把内存里的 HNSW 索引**整体重写**到 data_level0.bin 等文件,后写者覆盖
先写者 —— sqlite 的 embeddings_queue 水位停在 seq 19162、141 条向量永久积压,
index_metadata.pickle 自报 11790 个 label 却与实际文件对不上 → Rust 层遍历越界
→ access violation,RAG 读一次崩一次,检索/问答链路全断。

修复:所有写路径包 `_cross_process_write_lock()`(系统级文件锁)。
本文件锁住这个约束 —— 特别是"真进程级"互斥,单纯线程锁不能替代。
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 子进程脚本:连续两次持锁,记录每次进入/退出的相对时刻,便于父进程判重叠
_CHILD_SCRIPT = textwrap.dedent(
    """
    import json, os, sys, time
    sys.path.insert(0, %(root)r)
    os.environ["RAG_CHROMA_DIR"] = %(chroma)r
    from tradingagents.rag.store import _cross_process_write_lock

    base = time.time()
    spans = []
    for _ in range(2):
        with _cross_process_write_lock():
            enter = time.time() - base
            time.sleep(0.6)          # 持锁 0.6s
            spans.append([enter, time.time() - base])
    print(json.dumps(spans))
    """
)


def _overlap(a: list[float], b: list[float]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


class TestCrossProcessExclusion:
    """核心:两个真进程的临界区必须串行 —— 这是库不再被写坏的唯一保证。"""

    def test_two_processes_never_overlap(self, tmp_path):
        chroma = tmp_path / "chroma"
        chroma.mkdir(parents=True, exist_ok=True)

        # 同时起两个进程(用 Popen 保证真并发,而不是先跑完一个再跑另一个)
        script = _CHILD_SCRIPT % {"root": str(PROJECT_ROOT), "chroma": str(chroma)}
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(2)
        ]
        spans_per_proc = []
        for p in procs:
            out, err = p.communicate(timeout=120)
            assert p.returncode == 0, f"子进程失败: {err[-800:]}"
            spans = [json.loads(ln) for ln in out.strip().splitlines() if ln.startswith("[")]
            assert spans, f"无 JSON 输出: {out[-400:]!r}"
            spans_per_proc.append(spans[0])

        for i, span in enumerate(spans_per_proc):
            assert span[1] > span[0], f"进程{i} 临界区长度异常: {span}"

        for a in range(len(spans_per_proc)):
            for b in range(a + 1, len(spans_per_proc)):
                assert not _overlap(spans_per_proc[a][0], spans_per_proc[b][0]), (
                    "两个进程的首个临界区重叠了 —— 跨进程锁失效,chroma HNSW 索引"
                    f"会被互相覆盖写坏: {spans_per_proc[a][0]} vs {spans_per_proc[b][0]}"
                )

    def test_second_process_actually_waits(self, tmp_path):
        """被挡住的那个进程必须有可观测的等锁时长(否则锁可能形同虚设)。"""
        chroma = tmp_path / "chroma"
        chroma.mkdir(parents=True, exist_ok=True)

        script = _CHILD_SCRIPT % {"root": str(PROJECT_ROOT), "chroma": str(chroma)}
        p1 = subprocess.Popen(
            [sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        time.sleep(0.3)  # 让 p1 先拿到锁
        p2 = subprocess.Popen(
            [sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        out1, _ = p1.communicate(timeout=120)
        out2, _ = p2.communicate(timeout=120)

        def first_enter(out: str) -> float:
            for ln in out.strip().splitlines():
                if ln.startswith("["):
                    return json.loads(ln)[0][0]
            raise AssertionError("无 JSON")

        # 后起者进入临界区的时刻,应明显晚于前者(前者持锁 0.6s×2)
        assert first_enter(out2) >= first_enter(out1) + 0.5, (
            "后起进程几乎无等待就进了临界区 —— 锁没有真正阻塞"
        )


class TestInProcessExclusion:
    def test_threads_are_serialized(self, tmp_path, monkeypatch):
        """同进程多线程也互斥(避免同进程并发 flush 同一个 client)。"""
        monkeypatch.setenv("RAG_CHROMA_DIR", str(tmp_path / "chroma"))
        from tradingagents.rag import store

        order: list[str] = []
        lock_seen = threading.Lock()

        def worker(tag: str):
            with store._cross_process_write_lock():
                with lock_seen:
                    order.append(f"{tag}-enter")
                time.sleep(0.15)
                with lock_seen:
                    order.append(f"{tag}-exit")

        threads = [threading.Thread(target=worker, args=(t,)) for t in ("a", "b", "c")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 交叉进入的模式形如 a-enter a-exit b-enter ... ;出现 a-enter b-enter 即失效
        assert len(order) == 6
        for i in range(0, 6, 2):
            assert order[i].endswith("-enter") and order[i + 1].endswith("-exit")
            assert order[i].split("-")[0] == order[i + 1].split("-")[0], (
                f"线程临界区交叉: {order}"
            )


class TestDegradation:
    def test_missing_lock_dir_falls_back_instead_of_raising(self, tmp_path, monkeypatch):
        """锁文件建不出来时降级放行,不能把整条索引流水线卡死。

        构造手法:拿一个**普通文件**当父目录,路径下再建子目录必然 OSError ——
        比给 pathlib.Path 打全局补丁干净,也不会波及其他测试。
        """
        blocker = tmp_path / "not_a_dir"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setenv("RAG_CHROMA_DIR", str(blocker / "chroma"))
        from tradingagents.rag import store

        with store._cross_process_write_lock(timeout_s=1.0):
            pass  # 不抛异常即通过


class TestShortLivedClient:
    """新纪律:每次访问都必须新建 client 且用完 close。

    为什么必须(2026-09-17 重建实测):
      * 长持 client 会在内存里冻结一份 HNSW 索引,写时整体 flush —— 常驻 rag_worker
        与回填进程交替写,直接吃掉 458 条向量(HNSW 水位 4111 / sqlite 4569)。
      * PersistentClient 一打开就启动自己的 compactor 线程,而 compactor 会写 HNSW
        文件;不 close 就等于在后台留一个随时会动的野写者。
    """

    def _install_fake_client(self, monkeypatch, tmp_path):
        from tradingagents.rag import store

        created: list[dict] = []

        class FakeCollection:
            def upsert(self, **kwargs):
                return None

            def count(self):
                return 0

        class FakeClient:
            def __init__(self, path, settings=None):
                self.closed = False
                self.collection = FakeCollection()
                created.append(self)

            def get_or_create_collection(self, name, metadata=None):
                return self.collection

            def close(self):
                self.closed = True

        monkeypatch.setenv("RAG_CHROMA_DIR", str(tmp_path / "chroma"))
        monkeypatch.setattr(store, "_new_client", lambda: FakeClient("x"))
        return created

    def test_each_access_opens_a_fresh_client(self, tmp_path, monkeypatch):
        from tradingagents.rag import store

        created = self._install_fake_client(monkeypatch, tmp_path)
        with store.exclusive_access():
            pass
        with store.exclusive_access():
            pass
        assert len(created) == 2, (
            f"两次访问只建了 {len(created)} 个 client —— client 被复用了,"
            "会用过期的内存 HNSW 状态覆盖别人刚写入的数据"
        )

    def test_client_is_closed_on_exit(self, tmp_path, monkeypatch):
        from tradingagents.rag import store

        created = self._install_fake_client(monkeypatch, tmp_path)
        with store.exclusive_access():
            assert not created[-1].closed, "不该在作用域内就关掉"
        assert created[-1].closed, "退出作用域后没 close → 后台 compactor 会继续写 HNSW"

    def test_client_closed_even_on_exception(self, tmp_path, monkeypatch):
        from tradingagents.rag import store

        created = self._install_fake_client(monkeypatch, tmp_path)
        with contextlib.suppress(RuntimeError):
            with store.exclusive_access():
                raise RuntimeError("boom")
        assert created[-1].closed, "异常路径没 close → 泄漏一个带 compactor 的 client"

    def test_singleton_is_dropped_before_open(self, tmp_path, monkeypatch):
        """访问前必须先丢弃进程内旧单例,否则新建的 client 与旧引用并存。"""
        from tradingagents.rag import store

        monkeypatch.setenv("RAG_CHROMA_DIR", str(tmp_path / "chroma"))
        monkeypatch.setattr(store, "_new_client", lambda: type(
            "C", (), {"get_or_create_collection": lambda s, **k: object(), "close": lambda s: None}
        )())
        store._collection = object()  # 模拟残留的长持单例
        with store.exclusive_access():
            assert store._collection is None, "旧单例没被丢弃"


class TestWritePathsTakeLock:
    """写路径必须真的取锁 —— 靠计数而不是靠读代码。"""

    def _patch_access(self, monkeypatch, tmp_path):
        from tradingagents.rag import store

        used = {"n": 0}
        real_lock = store._cross_process_write_lock

        class FakeCollection:
            def upsert(self, **kwargs):
                return None

            def delete(self, **kwargs):
                return None

        @contextlib.contextmanager
        def counting_access():
            used["n"] += 1
            with real_lock():  # 仍然真取跨进程锁
                yield FakeCollection()

        monkeypatch.setenv("RAG_CHROMA_DIR", str(tmp_path / "chroma"))
        monkeypatch.setattr(store, "exclusive_access", counting_access)
        return used

    def test_upsert_takes_lock(self, tmp_path, monkeypatch):
        from tradingagents.rag import store

        used = self._patch_access(monkeypatch, tmp_path)
        store.upsert_chunks([{"id": "1:0", "text": "x", "metadata": {}}], [[0.0] * 512])
        assert used["n"] == 1, "upsert_chunks 没有走独占访问"

    def test_delete_takes_lock(self, tmp_path, monkeypatch):
        from tradingagents.rag import store

        used = self._patch_access(monkeypatch, tmp_path)
        store.delete_by_report(7)
        assert used["n"] == 1, "delete_by_report 没有走独占访问"

    def test_query_takes_lock(self, tmp_path, monkeypatch):
        """检索也要走同一入口 —— 打开库本身就会触发 compactor 写 HNSW。"""
        from tradingagents.rag import store

        used = {"n": 0}
        real_lock = store._cross_process_write_lock

        class FakeCollection:
            def query(self, **kwargs):
                return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

        @contextlib.contextmanager
        def counting_access():
            used["n"] += 1
            with real_lock():
                yield FakeCollection()

        monkeypatch.setenv("RAG_CHROMA_DIR", str(tmp_path / "chroma"))
        monkeypatch.setattr(store, "exclusive_access", counting_access)
        assert store.query([0.0] * 512) == []
        assert used["n"] == 1

    def test_empty_upsert_short_circuits(self, tmp_path, monkeypatch):
        """空输入不该白拿锁(索引流程里会频繁出现空 chunk)。"""
        from tradingagents.rag import store

        used = self._patch_access(monkeypatch, tmp_path)
        assert store.upsert_chunks([], []) == 0
        assert used["n"] == 0
