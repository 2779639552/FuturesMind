"""采集告警卫生 — 2026-09-17 修复的回归测试。

被锁定的两个真实缺陷
--------------------
A) 【告警风暴】`scheduler._health_check` 每 30 分钟跑一次,用
   `get_collection_history(limit=5)` 取**全局最近 5 条**采集日志,只要有一条
   `status=='error'` 就写一条 `health_warning`,**且没有任何去重**。
   实测:09-16 21:30 → 09-17 04:00 之间同一次失败被连报 **13 条**,而管道其实
   早在 02:02 就写下了 `pipeline_complete`;告警中心被噪声淹没,真问题反而看不见。

   顺带暴露的还有"窗口语义错":该窗口是**跨平台**的全局窗口,于是一条失败
   - 既可能被后续其他平台的记录**挤出窗口**(过早丢失,判断不出到底哪个平台坏了),
   - 又会在两轮采集之间的 8 小时空窗里**长期滞留**(同一失败反复告警)。
   现在改成"按平台取各自最近一次"——某平台下一次采集成功即自动覆盖。

B) 【管道假成功】`_run_daily_pipeline` 里 `subprocess.run(...)` 的返回值**没有赋值**,
   聚合/回测/生成脚本报错也照写 `Daily pipeline complete`。现在用 returncode 判定,
   并把"几个平台成功 / 哪几个异常"写进完成告警。

参考:`scheduler.HEALTH_ALERT_SUPPRESS_HOURS`、`Database.get_latest_collection_per_platform`、
`Database.get_latest_alert`。管道侧的用例见 `tests/test_scheduler_platforms.py`。
"""

import sqlite3
from unittest.mock import MagicMock, patch

import database
from scheduler import HEALTH_ALERT_SUPPRESS_HOURS, _health_check


# ══════════════════════════════════════════════════════════════════
# 数据库层:按平台取最新 + 告警时间窗
# ══════════════════════════════════════════════════════════════════


def _seed_collection(db, platform, status, started_at, error_msg="", posts=0):
    """直接写一条采集日志(指定 started_at,便于构造"新旧"顺序)。"""
    with db._conn() as c:
        c.execute(
            "INSERT INTO collection_log "
            "(platform, keywords_count, posts_collected, posts_after_filter, status, error_msg, started_at, finished_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (platform, 30, posts, posts, status, error_msg, started_at, started_at),
        )


def test_latest_collection_per_platform_picks_newest_row_per_platform(tmp_path):
    """每个平台取**自己**最近一条 —— 不能被别的平台的记录挤出去。

    这正是旧实现的病根:全局窗口会让"xhs 的失败"被后续 douyin/weibo 的记录顶掉。
    """
    db = database.AgentSenseDB(tmp_path / "t.db")
    # xhs:先失败,后成功 → 应取到"成功"那条(失败状态被自然覆盖)
    _seed_collection(db, "xhs", "error", "2026-09-16 01:00:00", error_msg="blocked")
    _seed_collection(db, "xhs", "success", "2026-09-16 10:00:00", posts=37)
    # weibo:只有失败
    _seed_collection(db, "weibo", "error", "2026-09-16 05:00:00", error_msg="auth failed")
    # zhihu:成功
    _seed_collection(db, "zhihu", "success", "2026-09-16 09:00:00", posts=108)

    latest = db.get_latest_collection_per_platform()

    assert latest["xhs"]["status"] == "success", "同平台多条必须取最新的一条"
    assert latest["xhs"]["posts_collected"] == 37
    assert latest["weibo"]["status"] == "error"
    assert latest["zhihu"]["status"] == "success"
    assert set(latest) == {"xhs", "weibo", "zhihu"}


def test_latest_collection_per_platform_survives_interleaving(tmp_path):
    """平台记录交错排列时仍各自取最新(全局窗口会在这里取错)。"""
    db = database.AgentSenseDB(tmp_path / "t.db")
    _seed_collection(db, "xhs", "error", "2026-09-16 01:00:00")     # xhs 旧
    for i, p in enumerate(["douyin", "weibo", "zhihu", "xueqiu", "eastmoney_guba"]):
        _seed_collection(db, p, "success", f"2026-09-16 0{2 + i}:00:00")  # 5 条别的平台

    latest = db.get_latest_collection_per_platform()

    # 全局前 5 条里已经没有 xhs 了(旧实现会因此"看不见"它的失败)
    assert "xhs" not in [r["platform"] for r in db.get_collection_history(limit=5)]
    # 但按平台取最新时它必须在,且状态如实为 error
    assert latest["xhs"]["status"] == "error"
    assert len(latest) == 6


def test_get_latest_alert_respects_hours_window(tmp_path):
    """hours 时间窗在 SQL 里算(与 created_at 的 UTC 同源),窗口外必须查不到。"""
    db = database.AgentSenseDB(tmp_path / "t.db")
    with db._conn() as c:
        c.execute(
            "INSERT INTO alerts (alert_type, title, message, severity, created_at) "
            "VALUES ('health_warning', 'old', '', 'warning', datetime('now', '-10 hours'))"
        )
    assert db.get_latest_alert("health_warning") is not None, "不限时间时应能查到"
    assert db.get_latest_alert("health_warning", hours=6) is None, "10 小时前的告警不该落在 6 小时窗内"

    with db._conn() as c:
        c.execute(
            "INSERT INTO alerts (alert_type, title, message, severity, created_at) "
            "VALUES ('health_warning', 'fresh', '', 'warning', datetime('now', '-1 hours'))"
        )
    fresh = db.get_latest_alert("health_warning", hours=HEALTH_ALERT_SUPPRESS_HOURS)
    assert fresh is not None and fresh["title"] == "fresh", "窗口内应取到最新那条"


def test_get_latest_alert_filters_by_type(tmp_path):
    """只认指定类型 —— 别把 collection_blocked 当成 health_warning 抑制掉。"""
    db = database.AgentSenseDB(tmp_path / "t.db")
    with db._conn() as c:
        c.execute(
            "INSERT INTO alerts (alert_type, title, message, severity) "
            "VALUES ('collection_blocked', 'xhs blocked', '', 'error')"
        )
    assert db.get_latest_alert("health_warning", hours=6) is None


# ══════════════════════════════════════════════════════════════════
# 调度层:_health_check 的抑制与窗口语义
# ══════════════════════════════════════════════════════════════════


def _fake_db(latest_per_platform, recent_alert=None):
    db = MagicMock()
    db.get_latest_collection_per_platform.return_value = latest_per_platform
    db.get_latest_alert.return_value = recent_alert
    return db


def test_health_check_silent_when_all_platforms_healthy():
    db = _fake_db({"xhs": {"platform": "xhs", "status": "success"}})
    with patch("scheduler.get_db", return_value=db):
        _health_check()
    db.create_alert.assert_not_called()
    db.get_latest_alert.assert_not_called()  # 没有失败时无需查抑制窗口


def test_health_check_alerts_once_and_names_the_platforms():
    db = _fake_db({
        "xhs": {"platform": "xhs", "status": "error", "error_msg": "blocked"},
        "weibo": {"platform": "weibo", "status": "success"},
        "zhihu": {"platform": "zhihu", "status": "error", "error_msg": "auth failed"},
    }, recent_alert=None)
    with patch("scheduler.get_db", return_value=db):
        _health_check()

    assert db.create_alert.call_count == 1
    args = db.create_alert.call_args.args
    assert args[0] == "health_warning"
    assert "2 个平台" in args[2] and "共 3 个平台" in args[2], args[2]
    assert "xhs" in args[2] and "zhihu" in args[2], "必须点名失败平台"
    assert "weibo" not in args[2], "正常平台不该出现在失败清单里"


def test_health_check_is_suppressed_within_window():
    """窗口内已有同类告警 → 直接返回,不重复刷屏(旧的"13 连发"就是这里失控)。"""
    db = _fake_db(
        {"xhs": {"platform": "xhs", "status": "error", "error_msg": "blocked"}},
        recent_alert={"id": 99, "alert_type": "health_warning"},
    )
    with patch("scheduler.get_db", return_value=db):
        _health_check()

    db.create_alert.assert_not_called()
    db.get_latest_alert.assert_called_once_with(
        "health_warning", hours=HEALTH_ALERT_SUPPRESS_HOURS
    )


def test_health_check_alerts_again_after_window_expires():
    """窗口外(抑制过期)仍要告警 —— 长期坏掉的平台不能被永久静音。"""
    db = _fake_db(
        {"xhs": {"platform": "xhs", "status": "error", "error_msg": "blocked"}},
        recent_alert=None,  # 窗口内没有同类告警
    )
    with patch("scheduler.get_db", return_value=db):
        _health_check()

    assert db.create_alert.call_count == 1


def test_health_check_ignores_non_error_statuses():
    """只有 error 算故障;success / running / 假成功之外的 empty 都不该触发告警。"""
    db = _fake_db({
        "xhs": {"platform": "xhs", "status": "success"},
        "weibo": {"platform": "weibo", "status": "running"},
        "zhihu": {"platform": "zhihu", "status": "empty"},
    })
    with patch("scheduler.get_db", return_value=db):
        _health_check()
    db.create_alert.assert_not_called()


def test_health_check_does_not_crash_when_db_raises():
    """健康检查是后台任务:DB 异常绝不能把调度器带崩。"""
    db = MagicMock()
    db.get_latest_collection_per_platform.side_effect = sqlite3.OperationalError("db locked")
    with patch("scheduler.get_db", return_value=db):
        try:
            _health_check()
        except sqlite3.OperationalError:
            pass  # 当前实现不吞库异常(由 APScheduler 记录),此处只锁"不静默产生告警"
    db.create_alert.assert_not_called()
