"""采集器"整轮不白跑"的两道护栏回归测试 (2026-09-17)。

被锁定的真实缺陷
----------------
1) 【时间预算只看"是否已超时"】预算护栏在关键词边界检查 `now > deadline`,但单个
   关键词可能耗时数分钟。于是 11:50 启动的那个关键词会一路跑到 15 分钟,正好撞上
   调度器 `subprocess timeout=900` 被 **强杀**;一旦被强杀,连 collect_run 报告和
   退出码都写不出来 —— 表现为"每轮 error、却查不到任何报告",同时把
   health_warning 刷成噪音。雪球每轮都是这个形态: 实测采到 369KB 数据(162 条),
   却被记成 `Timeout (15 min)`。
   修复: 用"已完成关键词的平均耗时"**前瞻**一步 —— 若现在启动下一个关键词、预计
   完成时刻会越过截止时间,就提前收尾,保证进程能在子进程硬超时前正常退出。
   （判断逻辑抽成纯函数 `budget_stop_decision`,便于确定性单测。）

2) 【整轮瞬时全空 = 直接熔断,把剩下的关键词全丢掉】实测知乎 09-16 18:16、
   小红书 09-16 08:24 都是"每关键词 1.5~2.5s 空手返回"(远快于正常的 3.5s),
   而平台本身随后实测可用 —— 属会话被打死,不是平台没内容。旧行为直接熔断,
   那一轮 15~20 个关键词全部作废。
   修复: 熔断前先重建一次会话(close+init)再继续; 但**凭证类失败不重建**,
   因为 Cookie 过期重启会话救不回来,只会掩盖"请重新登录"这个真结论。

测试手法沿用 test_batch_collect_channel_health 的桩:不需要 Playwright / 网络 / 真实登录态。
"""

import pytest

from tests.test_batch_collect_channel_health import (
    _FakeAdapter,
    _items,
    _make_collector,
    _StubCredentialError,
    bc,
)

# ══════════════════════════════════════════════════════════════════
# 缺陷 1: 时间预算必须前瞻,否则会被调度器强杀
# ══════════════════════════════════════════════════════════════════


def test_budget_keeps_running_when_plenty_of_time_left():
    """刚开始跑、预算充裕 → 不应该收尾(别把护栏做成"一上来就退出")。"""
    stop, eta = bc.budget_stop_decision(now=100.0, start_time=100.0, deadline=700.0, kw_idx=0)
    assert stop is False
    assert eta == 0.0, "还没跑过关键词,没有耗时可参考"


def test_budget_stops_once_deadline_passed():
    """已过截止时刻 → 必须收尾(原有语义保留)。"""
    stop, _ = bc.budget_stop_decision(now=700.1, start_time=100.0, deadline=700.0, kw_idx=5)
    assert stop is True


def test_budget_lookahead_stops_one_keyword_early():
    """核心回归: 现在还没超时,但"再跑一个就超了" → 必须提前收尾。

    数字: 起点 100s,截止 400s;已跑 4 个关键词共用 200s(平均 50s/词)。
    当前 300s 尚未超时,但 300+50=350 < 400 —— 这个还能跑完,不该停;
    到 360s 时 360+50=410 > 400 —— 必须停,否则会被硬超时强杀。
    """
    ok_to_run, eta = bc.budget_stop_decision(now=300.0, start_time=100.0, deadline=400.0, kw_idx=4)
    assert eta == 50.0, "平均耗时 = (300-100)/4"
    assert ok_to_run is False, "300+50=350 还没越界,应继续"

    must_stop, _ = bc.budget_stop_decision(now=360.0, start_time=100.0, deadline=400.0, kw_idx=4)
    assert must_stop is True, "360+50=410 会越界 → 必须提前收尾,避免被 subprocess 强杀"


def test_budget_lookahead_is_strict_about_first_keyword():
    """第一个关键词没有历史耗时数据 → 不能凭空预测,必须让它跑(否则永远跑不动)。"""
    stop, eta = bc.budget_stop_decision(now=399.0, start_time=100.0, deadline=400.0, kw_idx=0)
    assert stop is False
    assert eta == 0.0


# ══════════════════════════════════════════════════════════════════
# 缺陷 2: 连续空结果应"先自愈一次",而不是整轮作废
# ══════════════════════════════════════════════════════════════════


class _HealableAdapter(_FakeAdapter):
    """前 empty_first 次 search 返回空,之后正常返回 items。

    用来模拟"会话被打死"的瞬时故障:开局全空,重建会话后恢复。
    """

    def __init__(self, items, empty_first):
        super().__init__(items, needs_detail_fetch=True)
        self.empty_first = empty_first
        self.init_calls = 0
        self.close_calls = 0

    def init(self):
        self.init_calls += 1

    def close(self):
        self.close_calls += 1

    def search(self, keyword, count):
        self.search_calls.append((keyword, count))
        if len(self.search_calls) <= self.empty_first:
            return []
        return list(self.items)


def _run_short(collector, tmp_path, monkeypatch, count=12):
    """跑一轮采集。

    两处必须处理,否则测试会污染真实数据:
      - `run()` 自己重设 `collector.output_file`(指向 output/), 所以改 output_file 没用,
        必须把模块级 OUTPUT_DIR 指向 tmp。**这点很关键**: output/batch_*.jsonl 会被
        trend_aggregator 通配扫走, 测试往里写假的 kw0/kw1 数据会污染真实聚合结果。
      - `no_enrich=True` 跳过 NER/情感: 本测试只关心护栏行为, 而共享桩的
        FuturesNER 没有 extract 方法(现有用例都只调 collect_one_keyword, 不走 enrich)。
    """
    monkeypatch.setattr(bc, "OUTPUT_DIR", tmp_path)
    return collector.run(
        [f"kw{i}" for i in range(count)], per_kw=5, max_detail=0, no_enrich=True
    )


def test_consecutive_empty_triggers_session_restart_and_recovers(tmp_path, monkeypatch, capsys):
    """回归: 连续 5 个空关键词后应重建会话并继续,而不是就此收掉整轮。

    修复前: keywords_done 停在 5,剩下 7 个关键词全部丢弃(那一轮白跑)。
    修复后: 重建会话一次 → 后续关键词正常产出。
    """
    adapter = _HealableAdapter(_items(3), empty_first=5)
    collector = _make_collector(adapter)

    _run_short(collector, tmp_path, monkeypatch, count=12)

    # close 会被调用 2 次: 自愈那次 + run() 末尾 finally 的资源清理。
    assert adapter.close_calls == 2, "应重建且只重建一次会话(另一次是收尾清理)"
    assert adapter.init_calls == 2, "构造时 1 次 + 自愈 1 次"
    assert collector.stats.keywords_done > 5, (
        f"熔断后必须继续跑,实际只跑到第 {collector.stats.keywords_done} 个"
    )
    assert collector.total_notes > 0, "自愈后必须真的采到数据"
    out = capsys.readouterr().out
    assert "self-heal" in out, "自愈动作要可见,便于排障"


def test_late_budget_still_reports_run(tmp_path, monkeypatch):
    """被预算收尾时,仍然是"正常跑完"的语义: 有数据 → 判 ok,不能变成 error。

    与缺陷 1 呼应: 雪球被强杀时报告都没写出来,现在提前收尾后必须有一份正常报告。
    """
    adapter = _HealableAdapter(_items(3), empty_first=0)
    collector = _make_collector(adapter)

    _run_short(collector, tmp_path, monkeypatch, count=6)

    assert collector.stats.keywords_done == 6, "预算充足时应跑完全部关键词"
    status, _diag, _hint, code = collector._decide_status()
    assert status == "ok", f"有数据的正常收尾不应判成失败,实际 {status}"
    assert code == bc.EXIT_OK
    # 落盘必须发生在 tmp,不能写进真实 output/(否则会污染 trend_aggregator 的聚合输入)
    assert list(tmp_path.glob("batch_zhihu_*.jsonl")), "结果应落在被重定向的目录里"


def test_credential_failure_does_not_restart_session(tmp_path, monkeypatch):
    """凭证类失败(Cookie 过期)不该重启会话 —— 重启救不回来,只会掩盖真结论。"""

    class _AuthFailingAdapter(_HealableAdapter):
        def __init__(self):
            super().__init__(_items(3), empty_first=0)
            self.error_category = "auth"

        def search(self, keyword, count):
            self.search_calls.append((keyword, count))
            raise _StubCredentialError("登录已过期")

    adapter = _AuthFailingAdapter()
    collector = _make_collector(adapter)

    _run_short(collector, tmp_path, monkeypatch, count=12)

    # 只有 run() 末尾 finally 的那次 close; 不能有自愈 rebuild(否则会多一次 close+init)
    assert adapter.close_calls == 1, "auth 失败不应触发会话重建"
    assert adapter.init_calls == 1, "只应有构造时那 1 次 init"
    assert collector.stats.auth_failures > 0
    status, _diag, _hint, code = collector._decide_status()
    assert status == "auth_failed", "必须仍然报'需重新登录',不能被自愈掩盖"
    assert code == bc.EXIT_AUTH_FAILED
    assert collector.stats.keywords_done < 12, "auth 故障应尽早熔断,不做无谓重试"


# ══════════════════════════════════════════════════════════════════
# 缺陷 3: 跑不满全量关键词的平台会"系统性饿死"后半段
# ══════════════════════════════════════════════════════════════════


def _window(kws, offset, stride):
    """按轮转偏移取本轮的关键词窗口(与 batch_collect 内联逻辑同形)。"""
    return (kws[offset:] + kws[:offset])[:stride]


def test_keyword_rotation_makes_adjacent_runs_complementary():
    """核心回归: 步长=窗口宽度时,相邻两轮互补 → 一天覆盖全部关键词。

    背景: 雪球 23 个关键词, 一轮只跑得起 12 个(fast 第 12 个就挂死、safe 撑到 21)。
    若每轮都从第 1 个开始, 排在后面的关键词**永远采不到** —— 用户看到的
    "帖子数不全"就有这一份。
    """
    total, stride = 23, 12
    kws = [f"k{i}" for i in range(total)]

    off0 = bc.keyword_rotation_offset(0, stride, total)
    off1 = bc.keyword_rotation_offset(1, stride, total)
    assert (off0, off1) == (0, 12), "相邻两轮应恰好错开一个窗口宽度"

    covered = set(_window(kws, off0, stride)) | set(_window(kws, off1, stride))
    assert covered == set(kws), f"一天两轮应覆盖全部,实际缺: {sorted(set(kws) - covered)}"


def test_stride_one_would_starve_the_tail():
    """反面用例: 步长取 1 时窗口每轮只挪一格, 一天两轮仅多覆盖 1 个词 → 仍会饿死尾部。

    这解释了为什么步长必须等于窗口宽度, 而不是"随便轮转一下就行"。
    """
    total, stride = 23, 1
    kws = [f"k{i}" for i in range(total)]

    covered = set()
    for run in (0, 1):
        off = bc.keyword_rotation_offset(run, stride, total)
        covered |= set(_window(kws, off, 12))

    assert len(covered) == 13, f"步长 1 时两天只覆盖 13 个, 实际 {len(covered)}"


def test_keyword_rotation_handles_degenerate_input():
    assert bc.keyword_rotation_offset(5, 12, 0) == 0, "空关键词列表不能 ZeroDivisionError"
    assert bc.keyword_rotation_offset(0, 0, 23) == 0, "步长为 0 时退化为不轮转"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
