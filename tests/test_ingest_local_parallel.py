"""本地 PDF 导入「先全批登记 → 再并行处理」测试(2026-09-14 两阶段改造)。

背景(踩过的坑):`ingest_local_pdfs.ingest_dir` 原先逐个文件串行处理——同一时间
只有一篇研报在跑,批量导入一个目录要等很久。第一版只把它改成"线程池并行",但
**建行时机**没动:仍是"worker 抓到第几个文件才建第几行",于是研报库同一时刻最多
只有 worker 数那么几行(串行时永远只有 1 行),用户看到的仍是"只有一篇在处理"。
第二版把「登记」与「处理」解耦成两阶段,本文件锁定 6 件事:

1. **整批先全部登记,再开始处理**(核心回归:修掉"只看到一两篇在处理"的观感);
2. 处理阶段真的并发了(同刻在跑 > 1),且不超过 worker 上限;
3. 明显快于串行(墙钟 ≈ 总工作量 / worker 数);
4. 单文件异常被隔离,不拖垮整批;
5. 返回结构 {matched,imported,skipped,errors,hits} 与改造前一致
   (imported 口径 = 成功登记进库的篇数);
6. dry_run 不开线程不预热;worker 数解析含钳制与非法回退。

全程 monkeypatch 掉 _stage_local_pdf / _process_queued / _warm_up_web_app,
不触网、不调 LLM、不写库。
"""

import logging
import threading
import time

import ingest_local_pdfs as m
import pytest


@pytest.fixture(autouse=True)
def _isolate_journal(tmp_path, monkeypatch):
    """把审计日志重定向到临时目录,并复位模块级"已挂 handler"标志。

    【为什么必须复位】_journal_ready 是模块级全局:某个测试一旦挂上 FileHandler,
    后续测试改 LOCAL_IMPORT_LOG 也不会重挂 —— 日志会串到别的测试的临时文件里,
    断言时有时无。每次测试前后都清零才能保证可重复(用直接赋值而非 monkeypatch,
    否则 monkeypatch 的 undo 会把标志抹回上一次的值)。
    """
    monkeypatch.setenv("LOCAL_IMPORT_LOG", str(tmp_path / "local_import.log"))
    m._journal_ready = False
    yield
    m._journal_ready = False
    for h in list(m.logger.handlers):
        if isinstance(h, logging.FileHandler):
            h.close()
            m.logger.removeHandler(h)


def _make_pdfs(tmp_path, n):
    """造 n 个文件名能命中 SC(别名「原油」)的假 PDF,返回目录。"""
    for i in range(n):
        (tmp_path / f"原油周报{i}.pdf").write_bytes(b"%PDF-1.4 fake")
    return tmp_path


def _stub_stage(fail_stems=(), skip_stems=()):
    """假 _stage_local_pdf:返回递增 report_id;按需模拟登记失败/跳过。

    【签名】第三位 copy_lock 与真实实现一致(并行登记时用它互斥 exists→copy 窗口)。
    """
    state = {"n": 0}
    lock = threading.Lock()

    def _fake(org, pdf, copy_lock=None):
        if pdf.stem in skip_stems:
            return None, "正文过短/无可提取文本"
        if pdf.stem in fail_stems:
            raise RuntimeError("boom")
        with lock:
            state["n"] += 1
            rid = state["n"]
        return rid, f"report_id={rid}"

    return _fake, state


def _stub_process(sleep=0.0):
    """假 _process_queued:暴露同刻并发峰值观测口。"""
    state = {"inflight": 0, "peak": 0, "ids": []}
    lock = threading.Lock()

    def _fake(rid):
        with lock:
            state["inflight"] += 1
            state["peak"] = max(state["peak"], state["inflight"])
            state["ids"].append(rid)
        if sleep:
            time.sleep(sleep)
        with lock:
            state["inflight"] -= 1

    return _fake, state


# ── 1. 核心回归:先全批登记,再开始处理 ──────────────────────────────────

def test_registers_whole_batch_before_any_processing(tmp_path, monkeypatch):
    """整批 6 篇必须先全部登记(stage)完,才开始处理(process)。

    这是本次改造要锁死的核心行为:研报库只有"整批都在队列里"才不会再让人以为
    "只有一篇在处理"。断言最后一次 stage 早于第一次 process。
    """
    _make_pdfs(tmp_path, 6)
    events = []
    lock = threading.Lock()
    stage, _ = _stub_stage()

    def _stage(org, pdf, copy_lock=None):
        rid, msg = stage(org, pdf)
        with lock:
            events.append(("stage", rid))
        return rid, msg

    def _proc(rid):
        with lock:
            events.append(("proc", rid))

    monkeypatch.setattr(m, "_stage_local_pdf", _stage)
    monkeypatch.setattr(m, "_process_queued", _proc)
    monkeypatch.setattr(m, "_warm_up_web_app", lambda: None)

    res = m.ingest_dir(tmp_path, "测试机构", {"SC"}, dry_run=False, workers=4)

    assert res["matched"] == 6
    assert res["imported"] == 6
    kinds = [k for k, _ in events]
    assert kinds.count("stage") == 6
    assert kinds.count("proc") == 6
    last_stage = max(i for i, (k, _) in enumerate(events) if k == "stage")
    first_proc = min(i for i, (k, _) in enumerate(events) if k == "proc")
    assert last_stage < first_proc, f"未做到「先全批登记再处理」,事件序列={kinds}"


# ── 2/3. 并发与加速 ──────────────────────────────────────────────────────
def test_phase1_registration_is_also_parallel(tmp_path, monkeypatch):
    """登记阶段本身也并行:慢登记(每篇 0.2s)整批 8 篇应远快于串行 1.6s。

    这是 2026-09-14 第二轮踩的坑:阶段一原先写死串行,而"抽正文"对 4 MB 级周报
    单篇偶发要数十秒 —— 10 篇拉了 13 分钟才登记完,期间研报库只多出寥寥数行,
    用户看到的仍是"只有一篇在处理"。登记不调 LLM,没有理由不并行。
    """
    _make_pdfs(tmp_path, 8)
    state = {"inflight": 0, "peak": 0}
    lock = threading.Lock()

    def _slow_stage(org, pdf, copy_lock=None):
        with lock:
            state["inflight"] += 1
            state["peak"] = max(state["peak"], state["inflight"])
        time.sleep(0.2)
        with lock:
            state["inflight"] -= 1
        return 1, "report_id=1"

    monkeypatch.setattr(m, "_stage_local_pdf", _slow_stage)
    monkeypatch.setattr(m, "_process_queued", _stub_process()[0])
    monkeypatch.setattr(m, "_warm_up_web_app", lambda: None)

    t0 = time.time()
    res = m.ingest_dir(tmp_path, "测试机构", {"SC"}, dry_run=False, workers=4)
    elapsed = time.time() - t0

    assert res["matched"] == 8 and res["imported"] == 8
    assert state["peak"] >= 2, f"登记阶段未并发(peak={state['peak']})"
    assert state["peak"] <= 4, f"登记并发超过 worker 上限(peak={state['peak']})"
    assert elapsed < 1.0, f"登记未见加速:elapsed={elapsed:.2f}s(串行约 1.6s)"


def test_journal_records_queue_skip_and_end(tmp_path, monkeypatch):
    """审计日志要落盘:QUEUE/SKIP 逐篇 + 批次汇总。

    没有这份日志时(web_app 无 basicConfig → logger.info 被丢),一次导入到底登记了
    哪几篇、哪些被幂等跳过、每篇花多久,重启后只能翻数据库倒推 —— 2026-09-14 的
    排查就卡在这里。
    """
    _make_pdfs(tmp_path, 4)
    stage, _ = _stub_stage(skip_stems={"原油周报1"})
    monkeypatch.setattr(m, "_stage_local_pdf", stage)
    monkeypatch.setattr(m, "_process_queued", _stub_process()[0])
    monkeypatch.setattr(m, "_warm_up_web_app", lambda: None)

    res = m.ingest_dir(tmp_path, "测试机构", {"SC"}, dry_run=False, workers=2)
    assert res["imported"] == 3 and res["skipped"] == 1

    log = tmp_path / "local_import.log"
    assert log.exists(), "未生成审计日志"
    text = log.read_text(encoding="utf-8")
    assert "==== start" in text
    assert text.count("QUEUE id=") == 3
    assert "SKIP 原油周报1.pdf" in text
    assert "==== end matched=4 imported=3 skipped=1 errors=0 ====" in text
    assert "took=" in text  # 单篇处理墙钟(排查"一篇要多久"的直接证据)


def test_stage_local_pdf_accepts_positional_lock():
    """_stage_local_pdf 第三位必须是 copy_lock(并行登记靠它互斥 exists→copy 窗口)。

    这条只锁签名形状:调用点以位置参传锁,改签名会静默退化成"每次新建锁"= 不互斥。
    """
    import inspect

    params = list(inspect.signature(m._stage_local_pdf).parameters)
    assert params[:3] == ["org", "pdf", "copy_lock"]


def test_ingest_dir_runs_files_in_parallel(tmp_path, monkeypatch):
    """8 个文件 / 4 worker / 每个 0.2s:应观测到多文件同刻在跑,且总耗时显著低于串行。"""
    _make_pdfs(tmp_path, 8)
    monkeypatch.setattr(m, "_stage_local_pdf", _stub_stage()[0])
    fake_proc, state = _stub_process(sleep=0.2)
    monkeypatch.setattr(m, "_process_queued", fake_proc)
    monkeypatch.setattr(m, "_warm_up_web_app", lambda: None)

    t0 = time.time()
    res = m.ingest_dir(tmp_path, "测试机构", {"SC"}, dry_run=False, workers=4)
    elapsed = time.time() - t0

    # 统计口径不变
    assert res["matched"] == 8
    assert res["imported"] == 8
    assert res["skipped"] == 0
    assert res["errors"] == []
    assert len(res["hits"]) == 8
    assert all(isinstance(name, str) for name, _codes in res["hits"])
    # 处理阶段真的并发了,且不超 worker 上限
    assert state["peak"] >= 2, f"未观测到并发(peak={state['peak']})"
    assert state["peak"] <= 4, f"并发超过 worker 上限(peak={state['peak']})"
    assert sorted(state["ids"]) == list(range(1, 9))  # 每篇都被处理且只处理一次
    # 串行需 8*0.2=1.6s;4 路并行理论 0.4s,给足调度余量取 1.0s 上限
    assert elapsed < 1.0, f"未见加速:elapsed={elapsed:.2f}s(串行约 1.6s)"


def test_ingest_dir_single_worker_is_serial(tmp_path, monkeypatch):
    """workers=1 时退化为串行(peak 应为 1),作为并发的反证。"""
    _make_pdfs(tmp_path, 4)
    monkeypatch.setattr(m, "_stage_local_pdf", _stub_stage()[0])
    fake_proc, state = _stub_process(sleep=0.05)
    monkeypatch.setattr(m, "_process_queued", fake_proc)
    monkeypatch.setattr(m, "_warm_up_web_app", lambda: None)

    res = m.ingest_dir(tmp_path, "测试机构", {"SC"}, dry_run=False, workers=1)
    assert res["imported"] == 4
    assert state["peak"] == 1


# ── 4. 异常隔离 ─────────────────────────────────────────────────────────

def test_ingest_dir_isolates_per_file_errors(tmp_path, monkeypatch):
    """登记抛异常的文件:记入 errors 且不占 imported/skipped,其余照常登记并处理。"""
    _make_pdfs(tmp_path, 5)
    stage, _ = _stub_stage(fail_stems={"原油周报2"}, skip_stems={"原油周报1"})
    monkeypatch.setattr(m, "_stage_local_pdf", stage)
    monkeypatch.setattr(m, "_process_queued", _stub_process()[0])
    monkeypatch.setattr(m, "_warm_up_web_app", lambda: None)

    res = m.ingest_dir(tmp_path, "测试机构", {"SC"}, dry_run=False, workers=3)

    assert res["matched"] == 5
    assert res["imported"] == 3   # idx 0/3/4 登记成功
    assert res["skipped"] == 1    # idx 1 正文过短/幂等
    assert len(res["errors"]) == 1 and "boom" in res["errors"][0]
    assert res["errors"][0].startswith("原油周报2.pdf")


def test_ingest_dir_process_exception_does_not_stop_batch(tmp_path, monkeypatch):
    """处理阶段某篇抛异常:记入 errors,其余篇目照常完成(池不被拖垮)。"""
    _make_pdfs(tmp_path, 4)

    def _proc(rid):
        if rid == 2:
            raise RuntimeError("llm down")

    monkeypatch.setattr(m, "_stage_local_pdf", _stub_stage()[0])
    monkeypatch.setattr(m, "_process_queued", _proc)
    monkeypatch.setattr(m, "_warm_up_web_app", lambda: None)

    res = m.ingest_dir(tmp_path, "测试机构", {"SC"}, dry_run=False, workers=2)
    assert res["imported"] == 4   # 4 篇都已登记入库
    assert len(res["errors"]) == 1 and "llm down" in res["errors"][0]


def test_ingest_dir_warmup_failure_short_circuits(tmp_path, monkeypatch):
    """预热导入 web_app 失败时:不开线程池,把错误作为唯一 error 返回(不冒险并行)。"""
    _make_pdfs(tmp_path, 3)
    called = {"stage": 0, "proc": 0}

    def _stage(org, pdf, copy_lock=None):
        called["stage"] += 1
        return 1, "x"

    monkeypatch.setattr(m, "_stage_local_pdf", _stage)
    monkeypatch.setattr(m, "_process_queued", lambda rid: called.__setitem__("proc", called["proc"] + 1))
    monkeypatch.setattr(m, "_warm_up_web_app", lambda: "预热导入 web_app 失败: boom")

    res = m.ingest_dir(tmp_path, "测试机构", {"SC"}, dry_run=False, workers=4)
    assert called == {"stage": 0, "proc": 0}
    assert res["matched"] == 3 and res["imported"] == 0
    assert len(res["errors"]) == 1 and "boom" in res["errors"][0]


# ── 5/6. dry_run、空目录与 worker 解析 ──────────────────────────────────

def test_ingest_dir_dry_run_lists_without_threads_or_warmup(tmp_path, monkeypatch):
    """dry_run:只列命中清单,不登记不处理不预热,返回结构不变。"""
    _make_pdfs(tmp_path, 3)
    called = {"stage": 0, "proc": 0, "warm": 0}

    monkeypatch.setattr(
        m, "_stage_local_pdf",
        lambda org, pdf, copy_lock=None: called.__setitem__("stage", called["stage"] + 1) or (1, "x"),
    )
    monkeypatch.setattr(
        m, "_process_queued",
        lambda rid: called.__setitem__("proc", called["proc"] + 1),
    )
    monkeypatch.setattr(m, "_warm_up_web_app", lambda: called.__setitem__("warm", called["warm"] + 1))

    res = m.ingest_dir(tmp_path, "测试机构", {"SC"}, dry_run=True)
    assert res["matched"] == 3 and res["imported"] == 0 and res["skipped"] == 0
    assert res["errors"] == []
    assert len(res["hits"]) == 3
    assert called == {"stage": 0, "proc": 0, "warm": 0}


def test_ingest_dir_empty_dir_returns_zero_shape(tmp_path, monkeypatch):
    """空目录:直接返回零统计,不预热不开池。"""
    warm = {"n": 0}
    monkeypatch.setattr(m, "_warm_up_web_app", lambda: warm.__setitem__("n", warm["n"] + 1))

    res = m.ingest_dir(tmp_path, "测试机构", set(), dry_run=False, workers=4)
    assert res == {"matched": 0, "imported": 0, "skipped": 0, "errors": [], "hits": []}
    assert warm["n"] == 0


def test_ingest_dir_all_skipped_never_opens_pool(tmp_path, monkeypatch):
    """整批都登记不上(正文过短/幂等):imported=0 且完全不进入处理阶段。"""
    _make_pdfs(tmp_path, 3)
    processed = {"n": 0}
    monkeypatch.setattr(m, "_stage_local_pdf", lambda org, pdf, copy_lock=None: (None, "目标已存在(幂等跳过)"))
    monkeypatch.setattr(m, "_process_queued", lambda rid: processed.__setitem__("n", processed["n"] + 1))
    monkeypatch.setattr(m, "_warm_up_web_app", lambda: None)

    res = m.ingest_dir(tmp_path, "测试机构", {"SC"}, dry_run=False, workers=4)
    assert res["matched"] == 3 and res["imported"] == 0 and res["skipped"] == 3
    assert processed["n"] == 0


def test_ingest_dir_variety_filter_still_applies(tmp_path, monkeypatch):
    """品种过滤不变:未命中请求品种的文件不进清单。"""
    (tmp_path / "原油周报.pdf").write_bytes(b"x")   # 命中 SC
    (tmp_path / "螺纹钢周报.pdf").write_bytes(b"x")  # 不命中 SC(文件名探测段是「螺纹钢」)
    monkeypatch.setattr(m, "_stage_local_pdf", _stub_stage()[0])
    monkeypatch.setattr(m, "_process_queued", _stub_process()[0])
    monkeypatch.setattr(m, "_warm_up_web_app", lambda: None)

    res = m.ingest_dir(tmp_path, "测试机构", {"SC"}, dry_run=False, workers=2)
    assert res["matched"] == 1
    assert res["hits"][0][0] == "原油周报.pdf"


def test_resolve_workers_clamps_and_falls_back(monkeypatch):
    """worker 解析:缺省 4、显式值钳到上限、非法值/环境变量非法一律回退缺省。"""
    monkeypatch.delenv("LOCAL_IMPORT_WORKERS", raising=False)
    assert m._resolve_workers() == m.DEFAULT_WORKERS
    assert m._resolve_workers(2) == 2
    assert m._resolve_workers(99) == m.MAX_WORKERS
    assert m._resolve_workers(0) == m.DEFAULT_WORKERS
    assert m._resolve_workers(-3) == m.DEFAULT_WORKERS

    monkeypatch.setenv("LOCAL_IMPORT_WORKERS", "3")
    assert m._resolve_workers() == 3
    monkeypatch.setenv("LOCAL_IMPORT_WORKERS", "not-a-number")
    assert m._resolve_workers() == m.DEFAULT_WORKERS


def test_default_workers_stay_under_llm_account_cap():
    """缺省 worker 数必须 ≤ LLM 账户并发上限 5(每篇同时只挂 1 个 LLM 调用)。"""
    assert m.DEFAULT_WORKERS <= 5
    assert m.MAX_WORKERS <= 5
