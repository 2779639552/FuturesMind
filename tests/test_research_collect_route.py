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
    """注入 4 个采集器 fake 模块(记录调用,不触真实采集/LLM/网络)。

    记录元组含 requested(品种筛选集),校验路由透传;gtja fake 需带
    TARGET_VARIETIES(路由用它做品种代码白名单校验)。东证采集器也必须桩掉:
    漏桩会在 source=all 分支触发真 MCP 网络 + 真 LLM(2026-09-08 全量回归
    曾因此卡 20 分钟)。
    """
    fx = types.ModuleType("research_collector")
    fx.ingest_all = lambda dry_run=False: calls.append(("fx", dry_run)) or {"collected": 1}
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
        lambda days=1, dry_run=False, **kw:
        calls.append(("dz", days, dry_run)) or {"collected": 4}
    )
    monkeypatch.setitem(sys.modules, "research_collector", fx)
    monkeypatch.setitem(sys.modules, "research_collector_htfc", htfc)
    monkeypatch.setitem(sys.modules, "research_collector_gtja", gtja)
    monkeypatch.setitem(sys.modules, "research_collector_dongzheng", dz)


def test_collect_all_runs_both_collectors(monkeypatch):
    """source=all(缺省):发现报告 + 华泰天玑 + 国君 + 东证繁微 四源都被调用。"""
    calls = []
    _fake_collectors(monkeypatch, calls)

    resp = _client().post("/api/research/collect", json={"source": "all"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "started"
    assert body["source"] == "all"
    assert _wait_idle()
    assert ("fx", False) in calls
    assert ("htfc", time.strftime("%Y-%m-%d"), None, False) in calls
    assert ("gtja", 1, None, False) in calls
    assert ("dz", 1, False) in calls


def test_collect_htfc_only_skips_fxbaogao(monkeypatch):
    """source=htfc:只调华泰天玑,不碰发现报告/国君/东证。"""
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


def test_collect_varieties_filters_htfc_gtja_and_skips_fxbaogao(monkeypatch):
    """带 varieties:requested 透传给华泰/国君;发现报告被排除(无法预判品种)。"""
    calls = []
    _fake_collectors(monkeypatch, calls)

    resp = _client().post("/api/research/collect", json={"varieties": ["MA", "ta", " UR "]})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["varieties"] == ["MA", "TA", "UR"]  # 大小写/空白归一后返回
    assert _wait_idle()
    assert calls == [
        ("htfc", time.strftime("%Y-%m-%d"), {"MA", "TA", "UR"}, False),
        ("gtja", 1, {"MA", "TA", "UR"}, False),
    ]


def test_collect_unknown_variety_400(monkeypatch):
    """未知品种代码直接 400,不启动采集。"""
    calls = []
    _fake_collectors(monkeypatch, calls)

    resp = _client().post("/api/research/collect", json={"varieties": ["MA", "XX"]})
    assert resp.status_code == 400
    assert _wait_idle()
    assert calls == []  # 未启动任何采集器


def test_collect_blank_varieties_means_all(monkeypatch):
    """varieties 全为空白串 → 视为全部品种(requested=None,发现报告照跑)。"""
    calls = []
    _fake_collectors(monkeypatch, calls)

    resp = _client().post("/api/research/collect", json={"source": "all", "varieties": ["", "  "]})
    assert resp.status_code == 200
    assert _wait_idle()
    assert ("fx", False) in calls
    assert ("htfc", time.strftime("%Y-%m-%d"), None, False) in calls
