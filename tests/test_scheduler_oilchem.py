# -*- coding: utf-8 -*-
"""隆众资讯调度接线护栏（scheduler.py 的 oilchem job 注册）。

【为什么需要】接线补丁靠"锚点替换"写入 scheduler.py（CRLF 文件），
**锚点没命中就静默不写**。光编译通过、代码"看起来在那儿"都不够 ——
必须断言**任务真的被注册进了调度器**、时点正确、且**没有挤掉既有任务**。

【做法】把 `BackgroundScheduler` 换成记录器（不真启动线程、不碰 DB）：
  · `_FakeScheduler.add_job(**kw)` 只把 job 记进列表；
  · 这样可断言 job id / name / CronTrigger 的 hour/minute / max_instances。

【关键断言】
  1. 4 个 oilchem job 存在且时点 = 采集 08:20/17:10、入库 10:00/20:00；
  2. **入库时点必须避开研报三源窗口**（08:25~09:45 / 18:15~19:35）—— 账户并发上限 5，
     研报三源已占 2~3 路，撞上必然 429。这是本设计最关键的不变量；
  3. 既有 wechat / warrant / research 任务**未被挤掉**（接线不能伤到老链路）。
"""
from __future__ import annotations

import pytest

import scheduler


class _FakeScheduler:
    """只记录 add_job 的假调度器。"""

    def __init__(self, *a, **kw):
        self.jobs: list[dict] = []
        self.started = False

    @staticmethod
    def _cron_field(trigger, name: str):
        """从 APScheduler CronTrigger 取 hour/minute 的值。

        【为什么不用 getattr(trigger,'_hour')】CronTrigger 不暴露 `_hour`；
        时点存在 `trigger.fields` 里，每个 field 有 `.name` 与 `.expressions`
        （如 `RangeExpression(10)`）。直接取私有属性会恒得 None，
        于是"时点断言"变成永真/永假的摆设。
        """
        for f in getattr(trigger, "fields", []) or []:
            if getattr(f, "name", None) != name:
                continue
            exps = list(getattr(f, "expressions", []) or [])
            if not exps:
                return None
            try:
                return int(str(exps[0]))
            except (TypeError, ValueError):
                return None
        return None

    def add_job(self, func, trigger, **kw):
        self.jobs.append({
            "func": func,
            "hour": self._cron_field(trigger, "hour"),
            "minute": self._cron_field(trigger, "minute"),
            **kw,
        })

    def start(self):
        self.started = True

    # ── 断言辅助 ──
    def by_id(self, job_id: str) -> dict | None:
        for j in self.jobs:
            if j.get("id") == job_id:
                return j
        return None

    def ids(self) -> set[str]:
        return {j.get("id") for j in self.jobs}


@pytest.fixture
def fake(monkeypatch):
    """替换 BackgroundScheduler + get_db，返回 (fake_scheduler, captured)。"""
    holder: dict = {}

    def _ctor(*a, **kw):
        holder["s"] = _FakeScheduler()
        return holder["s"]

    monkeypatch.setattr(scheduler, "BackgroundScheduler", _ctor)
    monkeypatch.setattr(scheduler, "get_db", lambda: _NullDB())
    out = scheduler.start_scheduler()
    return holder["s"], out


class _NullDB:
    def ensure_default_user(self):  # noqa: D102
        return None


class TestOilChemJobsRegistered:
    def test_collect_jobs_two_times(self, fake):
        s, _ = fake
        for t in ("08:20", "17:10"):
            j = s.by_id("oilchem_collect_%s" % t)
            assert j is not None, "缺少采集任务 oilchem_collect_%s；已注册=%s" % (t, sorted(s.ids()))
            hh, mm = t.split(":")
            assert (j["hour"], j["minute"]) == (int(hh), int(mm))
            assert j["func"] is scheduler._run_oilchem_collect

    def test_ingest_jobs_two_times(self, fake):
        s, _ = fake
        for t in ("10:00", "20:00"):
            j = s.by_id("oilchem_ingest_%s" % t)
            assert j is not None, "缺少入库任务 oilchem_ingest_%s；已注册=%s" % (t, sorted(s.ids()))
            hh, mm = t.split(":")
            assert (j["hour"], j["minute"]) == (int(hh), int(mm))
            assert j["func"] is scheduler._run_oilchem_ingest

    def test_max_instances_and_misfire(self, fake):
        """入库可能跑几十分钟 ⇒ 必须 max_instances=1，否则上一轮未完会叠加（双花 LLM）。"""
        s, _ = fake
        for t in ("10:00", "20:00"):
            j = s.by_id("oilchem_ingest_%s" % t)
            assert j.get("max_instances") == 1
            assert j.get("misfire_grace_time") == 3600
        # 采集不占 LLM，grace 可短一些（服务短暂重启仍能补跑）
        assert s.by_id("oilchem_collect_08:20").get("misfire_grace_time") == 1800

    def test_scheduler_started_and_returned(self, fake):
        s, out = fake
        assert s.started, "调度器未被 start()"
        assert out is s


class TestNoLLMCollision:
    """★ 最关键的不变量：入库时点不得落入研报三源窗口。"""

    # 研报三源窗口（htfc 08:25 / gtja 08:40 / dz 08:50 / RAG 09:25 / 日报 09:45，
    # 以及晚间对应 +15/+30/+40/+75/+95 分钟）—— 实际占用区间。
    BUSY = [(8 * 60 + 25, 9 * 60 + 45), (18 * 60 + 15, 19 * 60 + 35)]

    def _to_min(self, t: str) -> int:
        h, m = t.split(":")
        return int(h) * 60 + int(m)

    @pytest.mark.parametrize("t", ["10:00", "20:00"])
    def test_ingest_avoids_research_windows(self, fake, t):
        s, _ = fake
        j = s.by_id("oilchem_ingest_%s" % t)
        assert j is not None
        at = self._to_min(t)
        for lo, hi in self.BUSY:
            assert not (lo <= at <= hi), (
                "入库 %s 落入研报三源 LLM 窗口(%02d:%02d~%02d:%02d) ⇒ 必然 429"
                % (t, lo // 60, lo % 60, hi // 60, hi % 60))

    def test_all_oilchem_times_are_valid_hhmm(self, fake):
        s, _ = fake
        for j in s.jobs:
            jid = j.get("id") or ""
            if jid.startswith("oilchem_"):
                assert j["hour"] is not None and j["minute"] is not None, \
                    "job %s 的 CronTrigger 取不到 hour/minute" % jid


class TestNoRegression:
    """接线不得挤掉既有任务。"""

    def test_existing_jobs_still_registered(self, fake):
        s, _ = fake
        ids = s.ids()
        for jid in ("warrant_collect_08:00", "warrant_collect_16:40",
                    "wechat_collect_08:30", "wechat_collect_18:30",
                    "wechat_ingest_09:05", "wechat_ingest_19:05",
                    "health_check", "research_file_cleanup",
                    "research_aggregate_repair", "douyin_morning", "douyin_evening"):
            assert jid in ids, "既有任务 %s 被挤掉了；已注册=%s" % (jid, sorted(ids))

    def test_explicit_times_override(self, monkeypatch):
        """显式传参时以传入值为准（web_app 主启动路径就是这么传的）。"""
        holder: dict = {}

        def _ctor(*a, **kw):
            holder["s"] = _FakeScheduler()
            return holder["s"]

        monkeypatch.setattr(scheduler, "BackgroundScheduler", _ctor)
        monkeypatch.setattr(scheduler, "get_db", lambda: _NullDB())
        scheduler.start_scheduler(
            oilchem_collect_times=["07:00"],
            oilchem_ingest_times=["11:00"],
        )
        s = holder["s"]
        assert s.by_id("oilchem_collect_07:00") is not None
        assert s.by_id("oilchem_ingest_11:00") is not None
        # 默认时点不应再出现
        assert s.by_id("oilchem_collect_08:20") is None
        assert s.by_id("oilchem_ingest_10:00") is None

    def test_defaults_come_from_module_constants(self):
        """默认时点必须取自模块常量，便于与 web_app 注释/文档对齐。"""
        assert scheduler.OILCHEM_COLLECT_TIMES == ["08:20", "17:10"]
        assert scheduler.OILCHEM_INGEST_TIMES == ["10:00", "20:00"]

    def test_state_dir_is_isolated_from_wechat(self):
        """隆众状态目录必须与公众号分开 —— 混用会互相污染水位线/结果文件。"""
        assert scheduler.OILCHEM_STATE_DIR != scheduler.WECHAT_STATE_DIR
        assert scheduler.OILCHEM_STATE_DIR.endswith("oilchem")
