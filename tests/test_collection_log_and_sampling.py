"""采集链路回归测试(2026-09-15 "近日只有雪球源" 事故)。

用户看到的现象是"情绪数据只有雪球源",实际背后是**两个互相独立**的缺陷,
曾长期被当成同一个问题:

缺陷 A —— 调度管道 100% 静默停摆(自 2026-09-07 起,持续 8 天)
    Database.start_collection 旧写法 `return c.lastrowid` 在 **Connection** 上取
    lastrowid 必抛 AttributeError。create_alert 在 2026-09-07 修过同一个坑,
    但 start_collection 漏改。而 scheduler._run_platform_collection 把这次调用
    放在 try **之外** —— 函数在第一行就中断,batch_collect.py 子进程从未启动:
    每日 08:00/18:00 四个平台各写一条 collection_error,collection_log 恒空
    (INSERT 随异常回滚),而外层仍照常写 pipeline_complete。

缺陷 B —— 展示层把弱源饿死(这才是用户眼睛看到的"只有雪球")
    /api/sentiment_posts 原实现是"全局按发布时间倒序 → 取前 200"。雪球是准实时源,
    发布时间最新,187~200 个名额被它一家占满;微博(全局第 281 名)、东财股吧
    (第 279 名)、抖音(第 490 名)、小红书(第 3952 名)全部落在窗口之外。
    它们的采集其实是好的,是抽样把它们挤掉了。

本文件对应两道防线:
1. lastrowid 真库 roundtrip + 采集任务在"写日志失败"时仍必须启动子进程(韧性)。
2. 分层抽样:任意截断长度下每个平台都要出现(下游 web_template.html 只渲染前 100)。
"""

import json
from unittest.mock import MagicMock, patch

import database
import scheduler
from scheduler import _run_platform_collection
from web_app import _stratified_sample


def _fake_capture(stdout: str, returncode: int = 0):
    """造一个 `_capture_run` 的替身,返回 scheduler 自己的结果壳。

    ⚠️【2026-09-30 修 · 陈旧 mock 导致的假红】
    本文件的用例原本 patch `scheduler.subprocess.run`。但生产代码在 2026-09-21
    已把子进程调用统一改走 `scheduler._capture_run()`(重定向到文件,不经 PIPE ——
    因为"无控制台父进程"下 PIPE 恒返空 stdout,详见该函数 docstring)。
    于是 `subprocess.run` 这个 patch 目标**根本没被调到**,真实 `_capture_run`
    照常执行 → 解析到的 stdout 是空串 → 条数算成 0 → 三个用例一起假红
    ("43 条被判成低数据量"这类现象)。
    修法:patch 跟着生产重构走,改打 `_capture_run`,
    返回 `scheduler._ChildResult`(调用点只读 .stdout/.stderr/.returncode)。
    """
    return lambda *a, **k: scheduler._ChildResult(stdout, "", returncode)


# ══════════════════════════════════════════════════════════════════
# 缺陷 A:start_collection 的 lastrowid bug
# ══════════════════════════════════════════════════════════════════


def test_start_collection_roundtrip(tmp_path):
    """真库写入→回读:start_collection 返回自增 id 且行真的落盘(旧 lastrowid bug 在此必挂)。"""
    db = database.AgentSenseDB(tmp_path / "test.db")

    log_id = db.start_collection("weibo", keywords_count=24)
    assert isinstance(log_id, int) and log_id > 0

    rows = db.get_collection_history(limit=10)
    assert len(rows) == 1, "INSERT 必须真的提交(旧 bug 会让整条事务随异常回滚,表恒空)"
    assert rows[0]["platform"] == "weibo"
    assert rows[0]["keywords_count"] == 24
    assert rows[0]["status"] == "running"

    # finish_collection 用同一个 id 回写,证明这个 id 是可用且指向同一行的
    db.finish_collection(log_id, posts_collected=43)
    rows = db.get_collection_history(limit=10)
    assert rows[0]["status"] == "success"
    assert rows[0]["posts_collected"] == 43


def test_finish_collection_tolerates_none_log_id(tmp_path):
    """log_id 为 None(写日志那步失败)时 finish_collection 安全空转,不抛错。"""
    db = database.AgentSenseDB(tmp_path / "test.db")
    db.finish_collection(None, 0, error="no log row")  # 不抛 = 通过


def test_platform_collection_starts_subprocess_even_if_log_write_fails():
    """写日志炸掉时,采集子进程仍必须启动(历史事故的直接回归)。

    旧代码把 start_collection 放在 try 之外,它抛 AttributeError 会让整个函数
    在第一行中断 —— 子进程从未启动,采集静默停摆 8 天。
    """
    broken_db = MagicMock()
    broken_db.start_collection.side_effect = AttributeError(
        "'sqlite3.Connection' object has no attribute 'lastrowid'"
    )
    fake = _fake_capture("  Total notes: 43 (unique: 43)\n")

    with patch("scheduler.get_db", return_value=broken_db), patch(
        "scheduler._capture_run", side_effect=fake
    ) as mock_run:
        _run_platform_collection("weibo")

    assert mock_run.call_count == 1, "写日志失败绝不能阻止 batch_collect.py 启动"


# ══════════════════════════════════════════════════════════════════
# 缺陷 A 的调用方闭环:调度器要消费 COLLECT_RUN 报告与语义化退出码
# ══════════════════════════════════════════════════════════════════


def _run_with_stdout(stdout: str, returncode: int = 0):
    """跑一次 _run_platform_collection(子进程被替换成给定输出),返回 (db, 告警类型列表)。"""
    db = MagicMock()
    db.start_collection.return_value = 1

    with patch("scheduler.get_db", return_value=db), patch(
        "scheduler._capture_run", side_effect=_fake_capture(stdout, returncode)
    ):
        _run_platform_collection("douyin")

    alerts = [c.args[0] for c in db.create_alert.call_args_list]
    return db, alerts


def test_collect_run_report_auth_failed_is_actionable():
    """auth_failed 必须变成"需重新登录"的可区分告警,而不是笼统的 collection_failed。

    旧代码只认人话行 "Total notes:",丢掉报告里的 diagnosis/fix_hint,
    于是"凭证过期"和"代码异常"长得一模一样 —— 报警了,但报了没用的警。
    """
    report = {
        "platform": "douyin",
        "status": "auth_failed",
        "notes": 0,
        "diagnosis": "5/5 个关键词被判定为凭证失效",
        "fix_hint": "运行 python douyin_login.py 扫码重新登录",
    }
    stdout = (
        "  Total notes: 0 (unique: 0)\n"
        f"COLLECT_RUN: {json.dumps(report, ensure_ascii=False)}\n"
    )
    db, alerts = _run_with_stdout(stdout, returncode=3)

    assert "collection_auth_failed" in alerts
    assert "collection_failed" not in alerts, "不要把可自助修复的凭证问题混进笼统失败"
    # 修复建议必须透传出去(用户才知道下一步做什么)
    last = db.create_alert.call_args_list[-1]
    assert "重新登录" in last.args[2]


def test_collect_run_report_blocked_is_distinct():
    """blocked(被拦截/接口变更)是另一类故障,不应与凭证失效混为一条。"""
    report = {
        "platform": "weibo",
        "status": "blocked",
        "notes": 0,
        "diagnosis": "24/24 个关键词搜索全部失败",
        "fix_hint": "疑似反爬或接口变更,需更新适配器",
    }
    stdout = f"COLLECT_RUN: {json.dumps(report, ensure_ascii=False)}\n"
    _, alerts = _run_with_stdout(stdout, returncode=4)

    assert "collection_blocked" in alerts
    assert "collection_auth_failed" not in alerts


def test_notes_parsed_from_report_without_human_line():
    """人话行缺失时,条数仍要从 COLLECT_RUN.notes 拿到(否则被误报为低数据量)。"""
    report = {"platform": "weibo", "status": "ok", "notes": 43, "diagnosis": "采集到 43 条"}
    db, alerts = _run_with_stdout(f"COLLECT_RUN: {json.dumps(report)}\n", returncode=0)

    assert "low_data" not in alerts, "43 条不该被判成低数据量"
    # finish_collection(log_id, posts_count) —— 第二个位置参数应为 43
    assert db.finish_collection.call_args_list[-1].args[1] == 43


def test_ok_status_with_zero_notes_still_warns_low_data():
    """语义不变:OK 但条数 < 10 时仍旧告警低数据量。"""
    report = {"platform": "weibo", "status": "ok", "notes": 2, "diagnosis": "采集到 2 条"}
    _, alerts = _run_with_stdout(f"COLLECT_RUN: {json.dumps(report)}\n", returncode=0)

    assert "low_data" in alerts


def test_legacy_output_without_report_still_parses():
    """旧脚本/旧格式(没有 COLLECT_RUN 行)仍走人话行兜底解析,保持向后兼容。"""
    db, alerts = _run_with_stdout("  Total notes: 50 (unique: 50)\n", returncode=0)

    assert "low_data" not in alerts
    assert db.finish_collection.call_args_list[-1].args[1] == 50


# ══════════════════════════════════════════════════════════════════
# 缺陷 B:分层抽样必须让每个平台都露脸
# ══════════════════════════════════════════════════════════════════


def _mk(platform: str, times: list[str]) -> list[dict]:
    return [{"platform": platform, "time": t} for t in times]


def _realistic_corpus() -> list[dict]:
    """模拟真实分布:雪球刷屏(200 条最新),其余五家各 50 条**且整体更旧**。

    关键在"更旧":真实场景里雪球是准实时源,最新一批帖子的时间戳比微博/股吧
    的新很多(实测雪球 17:30 vs 微博 09:00、股吧 09:09、抖音 4 天前、小红书 2 月前),
    所以全局排序后前 200 名被雪球一家占满 —— 修复前其余五家一条都进不来。
    """
    items = _mk("xueqiu", [f"2026-09-15 10:{i:02d}" for i in range(60)])
    items += _mk("xueqiu", [f"2026-09-14 12:{i:02d}" for i in range(60)])
    items += _mk("xueqiu", [f"2026-09-13 12:{i:02d}" for i in range(60)])
    items += _mk("xueqiu", [f"2026-09-12 12:{i:02d}" for i in range(20)])
    for plat in ("weibo", "zhihu", "eastmoney_guba", "douyin", "xhs"):
        items += _mk(plat, [f"2026-09-11 0{i // 12}:{i % 12:02d}" for i in range(50)])
    items.sort(key=lambda x: x["time"], reverse=True)
    return items


ALL_PLATFORMS = {"xueqiu", "weibo", "zhihu", "eastmoney_guba", "douyin", "xhs"}


def test_prefix_before_fix_would_be_single_platform():
    """把修复前的行为写进断言,坐实"这不是采集故障,是抽样饿死弱源"。"""
    items = _realistic_corpus()
    legacy_top200 = items[:200]
    assert {i["platform"] for i in legacy_top200} == {"xueqiu"}


def test_stratified_sample_covers_every_platform_at_any_prefix():
    """任意截断长度下六个平台都必须出现(下游只渲染前 100 条)。"""
    out = _stratified_sample(_realistic_corpus(), 200)

    assert {i["platform"] for i in out} == ALL_PLATFORMS
    for n in (6, 20, 100, len(out)):
        cover = {i["platform"] for i in out[:n]}
        assert cover == ALL_PLATFORMS, f"前 {n} 条只覆盖了 {sorted(cover)}"


def test_stratified_sample_does_not_let_one_platform_dominate():
    """分层后单一平台不应再占掉大半名额。"""
    out = _stratified_sample(_realistic_corpus(), 200)

    top = max(sum(1 for i in out if i["platform"] == p) for p in ALL_PLATFORMS)
    assert top <= len(out) // 2, f"某平台占了 {top}/{len(out)},分层没生效"


def test_stratified_sample_passthrough_when_under_limit():
    """条数不足上限时原样返回(不重排、不丢条目)。"""
    items = _mk("weibo", ["2026-09-15 10:00", "2026-09-14 10:00"])
    assert _stratified_sample(items, 200) == items


def test_stratified_sample_handles_empty_and_single_platform():
    assert _stratified_sample([], 200) == []
    only = _mk("xueqiu", ["2026-09-15 10:00", "2026-09-14 10:00"])
    assert _stratified_sample(only, 200) == only
