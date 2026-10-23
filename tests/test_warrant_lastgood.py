# -*- coding: utf-8 -*-
"""仓单采集：单所失败不许把该所数据整块丢掉（last-good 快照兜底）。

背景（2026-10-21 用户报障）
--------------------------
「为什么郑商所与广期所的仓单数据没有了」——16:40 那次自动采集，两所同时报
`RuntimeError: 最近 12 天内未找到仓单日报数据`（瞬时访问失败），而
`site.build(results)` 只吃**本次** results ⇒ 两个所从 overview.json 里直接消失，
页面「四所」变两所、零提示。单独重跑两所都一次成功（25 / 5 个品种）。

修法：成功即落每所快照；失败读回上次快照参与 build，并在 overview 里透出
`stale / fetch_error / date`，让前端能明说"这一所用的是哪天数据、为什么没更新"。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import warrant_collect as wc          # noqa: E402
from warrant import site as site_builder  # noqa: E402


SNAP = {
    "exchange": "CZCE",
    "date": "2026-10-20",
    "product": [{"product_code": "CF", "product_name": "棉花", "qty": 100, "unit": "张"}],
    "detail": [],
    "summary": [],
}


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """把快照目录与输出目录都指到临时目录，避免污染真实数据。"""
    monkeypatch.setattr(wc, "LASTGOOD_DIR", str(tmp_path / "lastgood"))
    monkeypatch.setattr(wc, "OUT_DIR", str(tmp_path / "out"))
    return tmp_path


@pytest.mark.unit
def test_lastgood_roundtrip(sandbox):
    wc.save_lastgood("CZCE", SNAP)
    assert (sandbox / "lastgood" / "CZCE.json").exists()
    got = wc.load_lastgood("CZCE")
    assert got["date"] == "2026-10-20"
    assert got["product"][0]["product_code"] == "CF"


@pytest.mark.unit
def test_lastgood_missing_returns_none(sandbox):
    assert wc.load_lastgood("GFEX") is None


@pytest.mark.unit
def test_failed_collector_falls_back_to_lastgood(sandbox, monkeypatch):
    """★ 核心回归：采集抛异常时，结果里仍要有该所的上次快照（否则页面直接少一所）。"""
    wc.save_lastgood("CZCE", SNAP)

    class Boom:
        @staticmethod
        def collect(**kw):
            raise RuntimeError("CZCE: 最近 12 天内未找到仓单日报数据")

    monkeypatch.setitem(wc.COLLECTORS, "CZCE", Boom)
    monkeypatch.setattr(wc, "ORDER", ["CZCE"])
    monkeypatch.setattr(sys, "argv", ["warrant_collect", "--only", "CZCE",
                                      "--no-unify", "--no-site"])
    rc = wc.main()
    assert rc == 1, "有交易所失败时退出码必须非 0（调度器要靠它告警）"

    latest = json.load(open(sandbox / "out" / "latest.json", encoding="utf-8"))
    cz = latest["results"]["CZCE"]
    assert cz["ok"] is False
    assert cz.get("fallback_date") == "2026-10-20", \
        "采集失败没回落到上次快照 ⇒ 页面会少一所: %r" % cz
    assert "未找到仓单日报" in cz["error"]


@pytest.mark.unit
def test_failed_without_snapshot_still_reports(sandbox, monkeypatch):
    """没有历史快照时只能缺数据，但摘要里必须留痕（不许静默）。"""

    class Boom:
        @staticmethod
        def collect(**kw):
            raise RuntimeError("GFEX: 连接超时")

    monkeypatch.setitem(wc.COLLECTORS, "GFEX", Boom)
    monkeypatch.setattr(wc, "ORDER", ["GFEX"])
    monkeypatch.setattr(sys, "argv", ["warrant_collect", "--only", "GFEX",
                                      "--no-unify", "--no-site"])
    wc.main()
    latest = json.load(open(sandbox / "out" / "latest.json", encoding="utf-8"))
    gf = latest["results"]["GFEX"]
    assert gf["ok"] is False and "连接超时" in gf["error"]
    assert "fallback_date" not in gf


@pytest.mark.unit
def test_site_build_exposes_stale_flag(sandbox, monkeypatch):
    """overview 必须带 stale / fetch_error / date —— 否则前端无法说明"这是旧数据"。"""
    monkeypatch.setattr(site_builder, "WEB_DIR", str(sandbox / "web"))
    stale = dict(SNAP)
    stale["_fetch"] = {"ok": False, "stale": True, "error": "RuntimeError: 12 天内无日报"}
    fresh = {
        "exchange": "DCE", "date": "2026-10-21",
        "product": [{"product_code": "a", "product_name": "豆一", "qty": 5, "unit": "手"}],
        "detail": [], "summary": [],
        "_fetch": {"ok": True, "stale": False},
    }
    site_builder.build({"CZCE": stale, "DCE": fresh}, "2026-10-21")

    ov = json.load(open(sandbox / "web" / "overview.json", encoding="utf-8"))
    by = {e["code"]: e for e in ov["exchanges"]}
    assert by["CZCE"]["stale"] is True
    assert by["CZCE"]["date"] == "2026-10-20", "要如实给出该所的数据日期"
    assert "12 天内无日报" in (by["CZCE"]["fetch_error"] or "")
    assert by["DCE"]["stale"] is False
    assert list(ov["fetch_errors"].keys()) == ["CZCE"]


@pytest.mark.unit
def test_collector_writes_lastgood_on_success(sandbox, monkeypatch):
    """成功采集也要落快照，否则第一次失败时没有可回落的。"""
    ok = {"exchange": "SHFE", "date": "2026-10-21",
          "product": [{"product_code": "cu", "product_name": "铜", "qty": 1, "unit": "吨"}],
          "detail": [], "summary": []}

    class Good:
        @staticmethod
        def collect(**kw):
            return dict(ok)

    monkeypatch.setitem(wc.COLLECTORS, "SHFE", Good)
    monkeypatch.setattr(wc, "ORDER", ["SHFE"])
    monkeypatch.setattr(sys, "argv", ["warrant_collect", "--only", "SHFE",
                                      "--no-unify", "--no-site"])
    assert wc.main() == 0
    got = wc.load_lastgood("SHFE")
    assert got and got["date"] == "2026-10-21"
