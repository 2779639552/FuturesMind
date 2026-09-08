"""告警链路回归测试(2026-09-07 静默失败事故)。

历史事故:Database.create_alert 旧写法 `return c.lastrowid` 在 Connection 上取
lastrowid 必抛 AttributeError,而调度任务的 "started" 告警写在 try 之外 ——
结果全部定时采集任务(daily pipeline / 发现报告 / 华泰 / 国君)在第一步就炸掉,
子进程从未启动,告警表恒空,失败不留任何痕迹。

本文件两道防线:
1. 真库 roundtrip:证明 create_alert 真能写入并读回(旧 bug 在此必挂)。
2. 韧性:_safe_alert 吞掉告警写入异常,采集任务绝不因告警而中断。
"""

import database
from scheduler import _run_research_collection, _safe_alert


def test_create_alert_roundtrip_and_readback(tmp_path):
    """真库写入→读回:create_alert 返回自增 id 且字段完整(旧 lastrowid bug 在此必挂)。"""
    db = database.AgentSenseDB(tmp_path / "test.db")

    alert_id = db.create_alert(
        "htfc_error",
        "华泰接入失败",
        "403 key无效",
        variety="HTFC",
        severity="error",
        data={"code": 403},
    )
    assert isinstance(alert_id, int) and alert_id > 0

    rows = db.get_alerts(limit=10)
    assert len(rows) == 1
    row = rows[0]
    assert row["alert_type"] == "htfc_error"
    assert row["variety"] == "HTFC"
    assert row["title"] == "华泰接入失败"
    assert row["severity"] == "error"
    assert row["acknowledged"] == 0

    assert db.get_unacknowledged_count() == 1
    db.acknowledge_alert(alert_id)
    assert db.get_unacknowledged_count() == 0


def test_safe_alert_swallows_write_errors():
    """告警写入抛错只打日志,绝不向调用方传播。"""
    from unittest.mock import MagicMock

    db = MagicMock()
    db.create_alert.side_effect = AttributeError("lastrowid 模拟历史 bug")

    # 不抛 = 通过;返回 None(写入失败但主流程继续)
    assert _safe_alert(db, "research_started", "title") is None


def test_research_job_survives_started_alert_failure(monkeypatch):
    """started 告警炸掉时,研报采集任务仍要继续跑子进程(历史事故的直接回归)。"""
    from unittest.mock import MagicMock, patch

    broken_db = MagicMock()
    broken_db.create_alert.side_effect = AttributeError("lastrowid")

    proc = MagicMock(returncode=0, stdout="Collected: 3\nProcessed: 3\n", stderr="")

    with patch("scheduler.get_db", return_value=broken_db), patch(
        "scheduler.subprocess.run", return_value=proc
    ) as mock_run:
        # 历史上这一步在 started 告警处直接 AttributeError,子进程从未启动
        _run_research_collection()

    assert mock_run.call_count == 1  # 子进程照常启动
    assert broken_db.create_alert.call_count >= 2  # started + complete 都尝试过(各被吞)
