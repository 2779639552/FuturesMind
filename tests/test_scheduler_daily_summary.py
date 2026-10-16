"""研报「采集完成 → 自动生成日报」调度护栏(2026-10-12)。

【要防的回归】用户需求:每日采集完成后自动生成日报。
1) 函数存在:_run_daily_summary 必须实现;
2) 错峰链注册:start_scheduler 的 research_times 循环里必须注册 +95min 日报任务
   (RAG 索引之后)——漏注册则"自动生成日报"完全不生效(静默,无告警);
3) 行为:当天仍有 pending/processing 研报 ⇒ 跳过(等晚轮重试),全部 done 后
   调用 _generate_daily_summary 并写 info 告警;当天无日报研报 ⇒ ok:False → warning。

不真调 LLM(_generate_daily_summary 打桩);轮询时钟用 FakeNow 快速推进。
"""

import datetime
import time

import pytest

import scheduler
import web_app


def _mk_fake_db(rows):
    class _DB:
        def list_research_reports(self, limit=None):
            return [dict(r) for r in rows]
    return _DB()


def _row(publish_date=None, status="done"):
    """构造研报行；publish_date 缺省 = **真实今天**。

    【为什么不能硬编码日期】被测的 _run_daily_summary 用 date.today() 取"当天"，
    若这里写死 "2026-10-12"，跨到第二天起 today 就对不上 → pending 判定为空 →
    误走"生成"分支 → 测试必然变红（2026-10-13 实测踩到：跨天后本用例开始失败）。
    取真实当天与实现同口径，测试即与运行日期解耦。
    """
    return {"id": 1,
            "publish_date": publish_date or datetime.date.today().isoformat(),
            "status": status}


class _FakeNow(datetime.datetime):
    """每调用一次 now() 前进 1 分钟,让 10 分钟轮询在 ~10 轮内快速走完。"""
    _t = None

    @classmethod
    def now(cls, tz=None):
        if cls._t is None:
            cls._t = datetime.datetime(2026, 10, 12, 10, 0, 0)
        else:
            cls._t = cls._t + datetime.timedelta(minutes=1)
        return cls._t


@pytest.fixture(autouse=True)
def _fast_clock(monkeypatch):
    """把函数内 `from datetime import datetime` 拿到的 now() 换成快速推进时钟 + sleep 空转。"""
    monkeypatch.setattr(datetime, "datetime", _FakeNow)
    monkeypatch.setattr(time, "sleep", lambda s: None)


def test_daily_summary_job_function_exists():
    """scheduler 必须提供 _run_daily_summary(自动化日报入口)。"""
    assert callable(getattr(scheduler, "_run_daily_summary", None))


def test_daily_summary_registered_in_research_chain():
    """错峰链必须注册 +95min 日报任务(RAG 索引后)——漏注册=完全不生效。"""
    src = open(scheduler.__file__, encoding="utf-8").read()
    assert "(95, _run_daily_summary, \"research_daily_\", \"Daily research summary\")" in src
    # 必须排在 RAG 索引(+75)之后:日报要等最后入队的研报处理完
    assert src.index("_run_daily_summary") > src.index("_run_rag_index")


def test_daily_summary_defers_while_reports_processing(monkeypatch):
    """当天仍有 processing 研报 ⇒ 跳过生成,记 daily_summary_deferred(等晚轮)。"""
    alerts = []
    monkeypatch.setattr(scheduler, "_safe_alert",
                        lambda db, atype, title, detail, severity=None: alerts.append(atype))
    called = []
    monkeypatch.setattr(web_app, "_generate_daily_summary",
                        lambda *a, **kw: called.append(a) or {"ok": True})
    db = _mk_fake_db([_row(status="processing"), _row(status="done")])
    monkeypatch.setattr(scheduler, "get_db", lambda: db)

    scheduler._run_daily_summary()

    assert "daily_summary_deferred" in alerts, alerts
    assert called == [], "仍有研报在处理时不应生成日报"


def test_daily_summary_generates_when_all_done(monkeypatch):
    """当天研报全部 done ⇒ 调用 _generate_daily_summary 并记 complete 告警。"""
    alerts = []
    monkeypatch.setattr(scheduler, "_safe_alert",
                        lambda db, atype, title, detail, severity=None: alerts.append(atype))
    calls = []
    monkeypatch.setattr(web_app, "_generate_daily_summary",
                        lambda *a, **kw: calls.append(a) or {"ok": True, "cached": False, "reports": 3})
    db = _mk_fake_db([_row(status="done"), _row(status="done")])
    monkeypatch.setattr(scheduler, "get_db", lambda: db)

    scheduler._run_daily_summary()

    assert calls, "全部 done 后必须调用 _generate_daily_summary"
    assert "daily_summary_complete" in alerts, alerts


def test_daily_summary_skipped_when_no_reports(monkeypatch):
    """当天无日报研报 ⇒ _generate_daily_summary 返回 ok:False ⇒ 记 skipped 不抛错。"""
    alerts = []
    monkeypatch.setattr(scheduler, "_safe_alert",
                        lambda db, atype, title, detail, severity=None: alerts.append(atype))
    monkeypatch.setattr(web_app, "_generate_daily_summary",
                        lambda *a, **kw: {"ok": False, "error": "在库没有已完成分析的日报研报"})
    db = _mk_fake_db([])  # 空库
    monkeypatch.setattr(scheduler, "get_db", lambda: db)

    scheduler._run_daily_summary()

    assert "daily_summary_skipped" in alerts, alerts
