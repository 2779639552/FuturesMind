"""手动研报采集路由(/api/research/collect)单元测试。

覆盖:状态查询 GET、source 非法 400、进行中 409、启动后后台线程按 source
跑对应采集器(以 fake 模块注入 sys.modules,不触真实采集/LLM/网络)。
"""

import sys
import time
import types

import pytest

import web_app


@pytest.fixture(autouse=True)
def _reset_flag():
    """进入/离开用例都复位并发标志,避免污染其他用例。"""
    web_app._research_collecting = False
    yield
    web_app._research_collecting = False


def _client():
    return web_app.app.test_client()


def _wait_idle(timeout=5.0) -> bool:
    """等后台采集线程结束(标志回落 False);超时返回 False。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not web_app._research_collecting:
            return True
        time.sleep(0.02)
    return False


def test_collect_status_get():
    resp = _client().get("/api/research/collect")
    assert resp.status_code == 200
    assert resp.get_json() == {"collecting": False}


def test_collect_bad_source_400():
    resp = _client().post("/api/research/collect", json={"source": "xxx"})
    assert resp.status_code == 400


def test_collect_busy_409():
    web_app._research_collecting = True
    resp = _client().post("/api/research/collect", json={})
    assert resp.status_code == 409


def _fake_collectors(monkeypatch, calls):
    """注入 3 个采集器 fake 模块(记录调用,不触真实采集/LLM/网络)。

    记录元组含 requested(品种筛选集),校验路由透传。东证采集器也必须桩掉:
    漏桩会在 source=all 分支触发真 MCP 网络 + 真 LLM(2026-09-08 全量回归
    曾因此卡 20 分钟)。2026-09-09 发现报告源剔除,不再需要 research_collector 桩。
    """
    htfc = types.ModuleType("research_collector_htfc")
    htfc.ingest_today = (
        lambda date, requested=None, dry_run=False:
        calls.append(("htfc", date, requested, dry_run)) or {"collected": 2}
    )
    gtja = types.ModuleType("research_collector_gtja")
    gtja.ingest_recent = (
        lambda days=1, requested=None, dry_run=False:
        calls.append(("gtja", days, requested, dry_run)) or {"collected": 3}
    )
    gtja.TARGET_VARIETIES = ("MA", "TA", "UR", "SA", "FG")
    dz = types.ModuleType("research_collector_dongzheng")
    dz.ingest_recent = (
        lambda days=1, dry_run=False, include_dynamics=False, **kw:
        calls.append(("dz", days, dry_run, include_dynamics)) or {"collected": 4}
    )
    monkeypatch.setitem(sys.modules, "research_collector_htfc", htfc)
    monkeypatch.setitem(sys.modules, "research_collector_gtja", gtja)
    monkeypatch.setitem(sys.modules, "research_collector_dongzheng", dz)

    # 【2026-09-17】采集收尾的 RAG 补索引也必须桩掉。
    # _run() 在 not dry_run 时会调 _rag_index_pending(since_days=3);测试环境里
    # _RAG_WORKER_MODE 为 False ⇒ 它会**就地加载 embedding 模型 + 逐篇打开 chroma 库**,
    # 实测 15.7 秒(done=0 / skipped=181,时间全花在重复开库的"已索引?"判定上),
    # 远超 _wait_idle 的 5 秒超时 ⇒ 4 个用例假失败(04/07/08/10)。
    # 本文件的契约是"不触真实采集/LLM/网络",它同样属于不该被触发的重 IO。
    monkeypatch.setattr(
        web_app,
        "_rag_index_pending",
        lambda *a, **k: {"done": 0, "skipped": 0, "failed": 0, "total": 0},
    )


def test_collect_all_runs_all_collectors(monkeypatch):
    """source=all(缺省):华泰天玑 + 国君 + 东证繁微 三源都被调用(2026-09-09 起)。"""
    calls = []
    _fake_collectors(monkeypatch, calls)

    resp = _client().post("/api/research/collect", json={"source": "all"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "started"
    assert body["source"] == "all"
    assert _wait_idle()
    assert ("htfc", time.strftime("%Y-%m-%d"), None, False) in calls
    assert ("gtja", 1, None, False) in calls
    assert ("dz", 3, False, True) in calls
    #   ↑ 2026-09-14 修复:东证补上 include_dynamics=True(动态快评原本一条都没采),
    #     回看窗口 1→3 天(--days 3,避免漏周末档)。见 scheduler/web_app 采集分支注释。


def test_collect_fxbaogao_source_removed_400(monkeypatch):
    """2026-09-09 发现报告源剔除:source=fxbaogao 直接 400,不启动任何采集。"""
    calls = []
    _fake_collectors(monkeypatch, calls)

    resp = _client().post("/api/research/collect", json={"source": "fxbaogao"})
    assert resp.status_code == 400
    assert _wait_idle()
    assert calls == []


def test_collect_htfc_only(monkeypatch):
    """source=htfc:只调华泰天玑,不碰国君/东证。"""
    calls = []
    _fake_collectors(monkeypatch, calls)

    resp = _client().post("/api/research/collect", json={"source": "htfc", "dry_run": True})
    assert resp.status_code == 200
    assert _wait_idle()
    assert calls == [("htfc", time.strftime("%Y-%m-%d"), None, True)]


def test_collect_gtja_only_skips_others(monkeypatch):
    """source=gtja:只调国君云 API 采集器(近 2 天窗口 days=1)。"""
    calls = []
    _fake_collectors(monkeypatch, calls)

    resp = _client().post("/api/research/collect", json={"source": "gtja"})
    assert resp.status_code == 200
    assert _wait_idle()
    assert calls == [("gtja", 1, None, False)]


def test_collect_varieties_filters_htfc_gtja_and_skips_dz(monkeypatch):
    """带 varieties:requested 透传给华泰/国君;东证被排除(无法预判品种)。"""
    calls = []
    _fake_collectors(monkeypatch, calls)

    resp = _client().post("/api/research/collect", json={"varieties": ["SC", "ta", " EG "]})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["varieties"] == ["EG", "SC", "TA"]  # 大小写/空白归一 + 排序后返回
    assert _wait_idle()
    assert calls == [
        ("htfc", time.strftime("%Y-%m-%d"), {"SC", "TA", "EG"}, False),
        ("gtja", 1, {"SC", "TA", "EG"}, False),
    ]


def test_collect_unknown_variety_400(monkeypatch):
    """未知品种代码直接 400,不启动采集(MA 已随 2026-09-09 品种池收缩出池)。"""
    calls = []
    _fake_collectors(monkeypatch, calls)

    resp = _client().post("/api/research/collect", json={"varieties": ["MA", "XX"]})
    assert resp.status_code == 400
    assert _wait_idle()
    assert calls == []  # 未启动任何采集器


def test_collect_blank_varieties_means_all(monkeypatch):
    """varieties 全为空白串 → 视为全部品种(requested=None)。"""
    calls = []
    _fake_collectors(monkeypatch, calls)

    resp = _client().post("/api/research/collect", json={"source": "all", "varieties": ["", "  "]})
    assert resp.status_code == 200
    assert _wait_idle()
    assert ("htfc", time.strftime("%Y-%m-%d"), None, False) in calls
