"""研报"僵尸行"自愈 + 采集器崩溃取证的回归测试(2026-09-17)。

【背景事故】采集器(华泰/国君/东证)在**独立子进程**里 `insert_research_report` 后
紧接着同步跑 `_process_research_report`(单篇 ≈5.5 分钟 LLM)。2026-09-15~17 三天里
该子进程反复在开工 45 秒内被**原生崩溃**(faulthandler 转储、无 Python 异常行)杀死,
于是:进程一死,已 insert 的行永久停在 status='processing' —— 前端表现为"十几篇研报
一直显示处理中",而且**没有任何 error 落库、没有告警**(因为崩溃不是 Python 异常)。

暴露出的三个缺陷,本文件逐条锁死:

缺陷 1 —— `_process_research_report` 的 try 从"提取文本"才开始
    (`db.get_research_report` / 取品种都在 try 之外)。这些前置步骤一抛异常就会冒出
    函数,把行留在 processing。→ 修法:抽出 `_process_research_report_inner`,外层
    包装统一兜底 + finally 注销在跑登记。

缺陷 2 —— 僵尸回收**只在服务启动时跑一次**。采集器留下的孤儿行在"web 不重启"的
    情况下永远无人回收(实测累积 15 篇)。→ 修法:`_sweep_stuck_research_reports`
    (启动/周期共用)+ `_start_research_stuck_watchdog`(默认 2 分钟一轮)。

缺陷 3 —— 取证只留 `output[-500:]`,恰好把两头最关键的信息切掉:Python 未捕获异常的
    类型/消息在**最后一行**,致命错误的 `Fatal Python error:` 头在**最前几行**。
    结果是三个不同采集源的告警看起来"堆栈一模一样",真凶查不出来。→ 修法:完整输出
    落盘 + `_alert_excerpt` 保留头尾 + `_explain_returncode` 区分原生崩溃与普通异常。

【关键测试纪律】本文件的"不会误杀活跃任务"用例必须包含**反面**:
只测"僵尸被回收"是不够的 —— 一个"见 processing 就回收"的粗暴实现同样能过,
但它会把正在跑的任务抢成两份并行处理(双花 LLM 额度、互相覆盖聚合 JSON)。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

import scheduler
import web_app
from collector_crash_log import _Tee, enable_crash_log

UTC_FMT = "%Y-%m-%d %H:%M:%S"


def _utc_str(minutes_ago: float) -> str:
    """构造"N 分钟前"的 UTC 时间戳(与库内 datetime('now') 同域)。"""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime(UTC_FMT)


# ══════════════════════════════════════════════════════════════════
# 缺陷 3:取证 —— 告警必须保住"致命错误头"与"异常尾"
# ══════════════════════════════════════════════════════════════════


class TestEvidencePreservation:
    """告警摘录必须两头都留 —— 这是 09-17 查不出真凶的直接原因。"""

    # 真实形态:致命错误输出 = 头部的 Fatal Python error + 中间若干帧 + 尾部异常行
    FATAL_OUTPUT = (
        "Fatal Python error: Aborted\n"
        "Current thread 0x00003b7c (most recent call first):\n"
        + "".join(
            f'  File "D:\\AgentSense\\AgentSense\\web_app.py", line {3600 + i} in _f{i}\n'
            for i in range(60)
        )
        + '  File "D:\\AgentSense\\AgentSense\\research_collector_htfc.py", line 509 in ingest_today\n'
        '  File "D:\\AgentSense\\AgentSense\\research_collector_htfc.py", line 565 in <module>\n'
    )

    def test_alert_excerpt_keeps_both_ends(self):
        """头部(致命错误类型)与尾部(异常消息/最内层帧)都必须保留。"""
        out = scheduler._alert_excerpt(self.FATAL_OUTPUT)
        assert "Fatal Python error: Aborted" in out, "致命错误类型被切掉 → 判不出是原生崩溃"
        assert "line 565 in <module>" in out, "尾部最内层帧被切掉 → 定位不到崩溃位置"
        assert "中间省略" in out and "logs/research_collect_" in out, "应给出折叠提示与日志路径"

    def test_alert_excerpt_short_output_untouched(self):
        """短输出原样返回(不要为了折叠反而弄坏正常信息)。"""
        assert scheduler._alert_excerpt("Collected: 3\nProcessed: 3") == "Collected: 3\nProcessed: 3"
        assert scheduler._alert_excerpt("") == "Unknown error"

    def test_explain_returncode_distinguishes_native_crash(self):
        """原生崩溃(0xC0000005)必须与普通异常(1)在告警里一眼可辨。

        【为什么重要】两者的排障路径完全不同:普通异常能进 except,库里会落
        status=error;原生崩溃直接带走进程,**库里只会留 processing 僵尸**。
        告警若不区分,就会被当成"业务异常"一直查代码。
        """
        assert "access violation" in scheduler._explain_returncode(3221225477)
        assert "栈溢出" in scheduler._explain_returncode(3221225725)
        assert scheduler._explain_returncode(1) == "Python 未捕获异常 / 业务失败"
        assert scheduler._explain_returncode(0) == "ok"

    def test_persist_collector_output_writes_full_text(self, tmp_path, monkeypatch):
        """完整输出必须落盘(告警只留摘录,逐字复现靠日志文件)。"""
        monkeypatch.setattr(scheduler, "RESEARCH_LOG_DIR", str(tmp_path))
        path = scheduler._persist_collector_output("htfc", self.FATAL_OUTPUT, 3221225477)
        assert path and path.endswith(".log")
        body = (tmp_path / f"research_collect_htfc_{datetime.now():%Y%m%d}.log").read_text(encoding="utf-8")
        assert "Fatal Python error: Aborted" in body
        assert "line 565 in <module>" in body
        assert "returncode=3221225477" in body and "access violation" in body
        # 追加语义:同一天多轮采集不能互相覆盖(否则前一轮的崩溃现场会被后一轮抹掉)
        scheduler._persist_collector_output("htfc", "second run", 0)
        body2 = (tmp_path / f"research_collect_htfc_{datetime.now():%Y%m%d}.log").read_text(encoding="utf-8")
        assert "Fatal Python error: Aborted" in body2 and "second run" in body2

    def test_persist_failure_never_raises(self, monkeypatch):
        """落盘失败绝不能拖垮采集(取证是辅助,不能变成新的故障源)。"""
        monkeypatch.setattr(scheduler, "RESEARCH_LOG_DIR", "\x00invalid\x00path")
        assert scheduler._persist_collector_output("htfc", "x", 1) == ""


class TestCollectorCrashLog:
    """采集器侧的崩溃转储与逐写落盘。"""

    def test_enable_crash_log_tees_and_returns_path(self, tmp_path, monkeypatch):
        """stdout 必须同时进日志文件 —— 否则原生崩溃时"崩溃前已打印的东西"照样丢。"""
        import sys

        # enable_crash_log 以"自身文件所在目录/logs"为落点,把 __file__ 指到 tmp_path 下
        monkeypatch.setattr("collector_crash_log.__file__", str(tmp_path / "collector_crash_log.py"))
        saved_out, saved_err = sys.stdout, sys.stderr
        try:
            path = enable_crash_log("unit_test", keep_console=True)
            assert path and "research_collect_unit_test_" in path
            print("hello-from-collector", flush=True)  # 走 _Tee → 必须同时进文件
        finally:
            # enable_crash_log 自己 setattr 了 sys 流,monkeypatch 管不到 → 必须手动还原,
            # 否则 _Tee 会一直挂在进程上,污染后续用例的 stdout 捕获。
            sys.stdout, sys.stderr = saved_out, saved_err
        body = open(path, encoding="utf-8").read()
        assert "hello-from-collector" in body, "tee 未把 stdout 落盘 → 崩溃前输出仍会丢"
        assert "采集器启动" in body

    def test_tee_survives_broken_stream(self):
        """控制台/管道断了也不能让采集崩(写成 try/except 的全部意义)。"""
        broken = MagicMock()
        broken.write.side_effect = OSError("pipe closed")
        fh = MagicMock()
        assert _Tee(broken, fh).write("x") == 1
        fh.write.assert_called_once_with("x")


# ══════════════════════════════════════════════════════════════════
# 缺陷 1:处理入口必须兜住任何异常,不留 processing 僵尸
# ══════════════════════════════════════════════════════════════════


class TestProcessReportWrapper:
    def _fake_db(self):
        db = MagicMock()
        db.update_research_report = MagicMock()
        return db

    def test_inner_crash_still_writes_error(self, monkeypatch):
        """内层(含 try 之外的前置读库段)抛错 → 必须落 status=error,不能留 processing。"""
        db = self._fake_db()
        monkeypatch.setattr(web_app, "get_db", lambda: db)
        monkeypatch.setattr(
            web_app, "_process_research_report_inner",
            MagicMock(side_effect=RuntimeError("db locked")),
        )
        web_app._INFLIGHT_REPORTS.clear()

        web_app._process_research_report(4242)

        calls = [c.kwargs for c in db.update_research_report.call_args_list]
        assert any(c.get("status") == "error" for c in calls), "内层异常未落 error → 前端会一直转"
        assert any("db locked" in (c.get("error") or "") for c in calls), "error 应带上真实原因"

    def test_processing_started_at_recorded(self, monkeypatch):
        """处理起点必须落库 —— 它是"跨进程判僵尸"的唯一判据(采集器在别的进程)。"""
        db = self._fake_db()
        monkeypatch.setattr(web_app, "get_db", lambda: db)
        monkeypatch.setattr(web_app, "_process_research_report_inner", MagicMock())
        web_app._INFLIGHT_REPORTS.clear()

        web_app._process_research_report(7)

        starts = [c.kwargs.get("processing_started_at") for c in db.update_research_report.call_args_list]
        assert any(s for s in starts), "未写 processing_started_at → 周期回收无法判断僵尸"
        datetime.strptime(starts[0], UTC_FMT)  # 必须是可解析的 UTC 字面量

    def test_inflight_registry_released_on_both_paths(self, monkeypatch):
        """在跑登记必须在**成功与异常两条路径**上都注销,否则该行会被永久排除在回收之外。"""
        db = self._fake_db()
        monkeypatch.setattr(web_app, "get_db", lambda: db)
        web_app._INFLIGHT_REPORTS.clear()

        monkeypatch.setattr(web_app, "_process_research_report_inner", MagicMock())
        web_app._process_research_report(11)
        assert 11 not in web_app._INFLIGHT_REPORTS

        monkeypatch.setattr(
            web_app, "_process_research_report_inner",
            MagicMock(side_effect=ValueError("boom")),
        )
        web_app._process_research_report(12)
        assert 12 not in web_app._INFLIGHT_REPORTS, "异常路径漏注销 → 该行永远不会被回收"


# ══════════════════════════════════════════════════════════════════
# 缺陷 2:僵尸回收(启动扫描 + 周期扫描)
# ══════════════════════════════════════════════════════════════════


class TestStuckSweep:
    def _run_sweep(self, rows, monkeypatch, *, at_startup, inflight=(), threshold=30.0):
        """跑一次回收扫描,返回(被回收 id 列表, 置回 pending 的 id 列表)。"""
        db = MagicMock()
        db.list_research_reports.return_value = rows
        submitted: list[int] = []
        ex = MagicMock()
        ex.submit = lambda fn, rid: submitted.append(rid)
        monkeypatch.setattr(web_app, "get_db", lambda: db)
        monkeypatch.setattr(web_app, "_research_heal_executor", lambda: ex)
        monkeypatch.setattr(web_app, "_research_stuck_threshold_minutes", lambda: threshold)
        web_app._INFLIGHT_REPORTS.clear()
        for rid in inflight:
            web_app._INFLIGHT_REPORTS[rid] = time.monotonic()
        try:
            claimed = web_app._sweep_stuck_research_reports(
                reason="test", at_startup=at_startup
            )
        finally:
            web_app._INFLIGHT_REPORTS.clear()
        return claimed, submitted, db

    def test_watchdog_reclaims_stale_processing(self, monkeypatch):
        """核心场景:processing 且起点已远超阈值、且本进程没在跑它 → 回收。"""
        rows = [{"id": 1, "status": "processing", "processing_started_at": _utc_str(90),
                 "created_at": _utc_str(90)}]
        claimed, submitted, db = self._run_sweep(rows, monkeypatch, at_startup=False)
        assert claimed == [1] and submitted == [1]
        # 必须先置回 pending 再入队(前端口径:处理中/排队中与真实并发一一对应)
        db.update_research_report.assert_called_once_with(1, status="pending")

    def test_watchdog_must_not_steal_fresh_or_live_rows(self, monkeypatch):
        """反面用例 —— 一个"见 processing 就回收"的实现能过上面一测,却会误杀活跃任务。"""
        rows = [
            # ① 刚开跑 2 分钟:可能正被采集器进程处理中(跨进程,本进程看不见)
            {"id": 2, "status": "processing", "processing_started_at": _utc_str(2)},
            # ② 很老,但**本进程正在跑它**(长任务,例如 RAG 冷启动)
            {"id": 3, "status": "processing", "processing_started_at": _utc_str(600)},
            # ③ pending:只由本进程批量导入写入,排队中的行同样不在在跑登记里
            {"id": 4, "status": "pending", "created_at": _utc_str(600)},
            # ④ done:一律不碰
            {"id": 5, "status": "done", "processing_started_at": _utc_str(600)},
        ]
        claimed, submitted, _ = self._run_sweep(rows, monkeypatch, at_startup=False, inflight=(3,))
        assert claimed == [], f"误杀活跃/排队/已完成行: {claimed}"
        assert submitted == []

    def test_watchdog_age_falls_back_to_created_at(self, monkeypatch):
        """老行/采集器未写起点时必须回退 created_at,否则僵尸永远判不出来。"""
        rows = [{"id": 6, "status": "processing", "processing_started_at": "",
                 "created_at": _utc_str(240)}]
        claimed, _, _ = self._run_sweep(rows, monkeypatch, at_startup=False)
        assert claimed == [6]

    def test_watchdog_treats_unknown_age_as_stale(self, monkeypatch):
        """一个时间戳都解析不出来 → 无法证明它活着,按陈旧处理(否则会永久滞留)。"""
        rows = [{"id": 7, "status": "processing", "processing_started_at": "", "created_at": ""}]
        claimed, _, _ = self._run_sweep(rows, monkeypatch, at_startup=False)
        assert claimed == [7]

    def test_startup_reclaims_processing_and_pending_regardless_of_age(self, monkeypatch):
        """启动口径(既有约定):启动时不存在本进程的处理线程,凡中间态都是上个进程的孤儿。"""
        rows = [
            {"id": 8, "status": "processing", "processing_started_at": _utc_str(1)},
            {"id": 9, "status": "pending", "created_at": _utc_str(1)},
            {"id": 10, "status": "error", "created_at": _utc_str(999)},
        ]
        claimed, submitted, _ = self._run_sweep(rows, monkeypatch, at_startup=True)
        assert claimed == [8, 9]
        assert submitted == [8, 9]

    def test_sweep_survives_list_failure(self, monkeypatch):
        """列库失败只能留痕,不能把调度/守护线程炸掉。"""
        db = MagicMock()
        db.list_research_reports.side_effect = RuntimeError("db gone")
        monkeypatch.setattr(web_app, "get_db", lambda: db)
        assert web_app._sweep_stuck_research_reports(reason="test", at_startup=False) == []

    def test_row_age_prefers_processing_started_at(self):
        """年龄优先取处理起点 —— created_at 是"入库时刻",两者相差可能数小时。"""
        now = datetime.now(timezone.utc)
        row = {"processing_started_at": _utc_str(10), "created_at": _utc_str(600)}
        assert 9.0 < web_app._research_row_age_minutes(row, now) < 11.0

    def test_threshold_is_sane_and_env_overridable(self, monkeypatch):
        """阈值默认 30 分钟(单篇 ≈5.5 分钟,留足余量),且不允许被设成 0 那样的危险值。"""
        monkeypatch.delenv("RESEARCH_STUCK_MINUTES", raising=False)
        assert web_app._research_stuck_threshold_minutes() == 30.0
        monkeypatch.setenv("RESEARCH_STUCK_MINUTES", "45")
        assert web_app._research_stuck_threshold_minutes() == 45.0
        monkeypatch.setenv("RESEARCH_STUCK_MINUTES", "0")
        assert web_app._research_stuck_threshold_minutes() >= 5.0, "阈值下限缺失 → 会误杀活跃任务"
        monkeypatch.setenv("RESEARCH_STUCK_MINUTES", "abc")
        assert web_app._research_stuck_threshold_minutes() == 30.0


class TestMigration:
    """processing_started_at 是跨进程判僵尸的凭据,旧库必须能自动补列。"""

    def test_old_db_gets_new_column(self, tmp_path):
        import sqlite3

        import database

        db_file = tmp_path / "old.db"
        # 手工造一个"最初版本"的老库:research_reports 的第一版建表语句(此后陆续加过
        # varieties/ingest_source/publish_date/report_type/layout_text/file_deleted,以及
        # 今天这列 processing_started_at)。这样一测就能覆盖 _migrate 的全部 ALTER 分支。
        con = sqlite3.connect(db_file)
        con.execute(
            "CREATE TABLE research_reports ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " variety TEXT NOT NULL, title TEXT DEFAULT '', source TEXT DEFAULT '',"
            " filename TEXT DEFAULT '', file_path TEXT DEFAULT '',"
            " status TEXT DEFAULT 'processing', extracted_text TEXT DEFAULT '',"
            " structured_data TEXT DEFAULT '', conclusion_md TEXT DEFAULT '',"
            " direction TEXT DEFAULT '', confidence REAL, error TEXT DEFAULT '',"
            " uploaded_at TEXT DEFAULT (datetime('now')),"
            " created_at TEXT DEFAULT (datetime('now')))"
        )
        con.commit()
        con.close()

        db = database.AgentSenseDB(str(db_file))
        cols = {r[1] for r in sqlite3.connect(db_file).execute("PRAGMA table_info(research_reports)")}
        assert "processing_started_at" in cols, "旧库升级后缺列 → 周期回收判不出僵尸"

        rid = db.insert_research_report(variety="", title="t", source="s", filename="f.md", file_path="f.md")
        db.update_research_report(rid, processing_started_at="2026-09-17 01:00:00")
        got = db.get_research_report(rid)
        assert got["processing_started_at"] == "2026-09-17 01:00:00"

    def test_new_db_has_column_and_whitelist_accepts_it(self, tmp_path):
        """新库建表即含该列;且 update 白名单必须放行(否则写入被静默忽略,回收仍失效)。"""
        import sqlite3

        import database

        db_file = tmp_path / "new.db"
        db = database.AgentSenseDB(str(db_file))
        cols = {r[1] for r in sqlite3.connect(db_file).execute("PRAGMA table_info(research_reports)")}
        assert "processing_started_at" in cols
        rid = db.insert_research_report(variety="", title="t", source="s", filename="f.md", file_path="f.md")
        db.update_research_report(rid, processing_started_at="2026-09-17 02:00:00")
        assert db.get_research_report(rid)["processing_started_at"] == "2026-09-17 02:00:00", (
            "白名单未放行 → 写入被静默丢弃(这类静默失败正是本次事故的特征)"
        )


@pytest.mark.parametrize("returncode,expect", [(0, "ok"), (1, "异常"), (3221225477, "access violation")])
def test_returncode_mapping_table(returncode, expect):
    assert expect in scheduler._explain_returncode(returncode)
