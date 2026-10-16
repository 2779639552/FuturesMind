# -*- coding: utf-8 -*-
"""仓单历史时序 (warrant/history.py + /api/warrant/history) 测试。

【为什么】2026-09-17 新增「仓单历史走势」: 分品种 / 分仓库的每日仓单变化
曲线。数据全部来自 out/ 按日归档 JSON 离线聚合 + 郑商所本地 xlsx 回填 ——
聚合口径必须严格, 否则曲线会"看起来对但数字错":

  1. CZCE 机构预报行(kind=forecast)不能混入品种/仓库时序;
  2. DCE/GFEX detail 的汇总占位行(warehouse_code=wh_code_order / 空名)剔除;
  3. 某品种某日缺数据 → 该日期键缺省(前端折线留空), 不补 0 不造数;
  4. 回填只针对「本地有 xlsx 而 out/ 没有解析产物」的日期;
  5. 四所合计只加主单位品种(上期所千克/桶不与吨相加), 被排除的计数;
  6. 缓存按文件集 (名, mtime, 大小) 失效, 新采集落盘后自动重建;
  7. API 仓库级时序按「最新非空仓单量」降序。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from warrant import history  # noqa: E402


# ── 造数工具 ──────────────────────────────────────────────────────────────
def _prod_row(ex, code, name, qty, change, unit, kind=None, **extra):
    r = {"exchange": ex, "qty": qty, "change": change, "unit": unit}
    if ex == "CZCE":
        r.update({"product_code": code, "product_name": name,
                  "kind": kind or "warrant"})
    else:
        r.update({"variety_code": code, "variety": name})
    r.update(extra)
    return r


def _wh_row(ex, code, wh_name, qty, change, wtype="仓库", wcode="001", **extra):
    r = {"qty": qty, "change": change}
    if ex == "CZCE":
        r.update({"product_code": code, "warehouse_name": wh_name,
                  "warehouse_no": wcode, "kind": "warrant"})
    else:
        r.update({"variety_code": code, "warehouse_name": wh_name,
                  "warehouse_code": wcode, "warehouse_type": wtype})
    r.update(extra)
    return r


def _write_out(out_dir, date, ex, product, summary=None, detail=None):
    (out_dir / f"{date}_{ex}_product.json").write_text(
        json.dumps(product, ensure_ascii=False), encoding="utf-8")
    if ex == "CZCE" and summary is not None:
        (out_dir / f"{date}_{ex}_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    elif ex != "CZCE" and detail is not None:
        (out_dir / f"{date}_{ex}_detail.json").write_text(
            json.dumps(detail, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def hist_env(tmp_path, monkeypatch):
    """隔离的 out/raw 目录 + 清空进程缓存, 每个用例独立。"""
    out = tmp_path / "out"
    raw_root = tmp_path / "raw"
    raw_czce = raw_root / "czce"
    out.mkdir(parents=True)
    raw_czce.mkdir(parents=True)
    monkeypatch.setattr(history, "OUT_DIR", str(out))
    monkeypatch.setattr(history, "RAW_DIR", str(raw_root))
    monkeypatch.setattr(history, "_CACHE", {"key": None, "data": None})
    return {"out": out, "raw": raw_czce}


# ── 1. 基础聚合: 品种 / 仓库时序 + 日期并集 ──────────────────────────────
def test_basic_aggregation(hist_env):
    _write_out(hist_env["out"], "20260911", "CZCE",
               [_prod_row("CZCE", "SR", "白糖", 100, -10, "张")],
               summary=[_wh_row("CZCE", "SR", "藁城永安", 60, -5)])
    _write_out(hist_env["out"], "20260914", "CZCE",
               [_prod_row("CZCE", "SR", "白糖", 120, 20, "张")],
               summary=[_wh_row("CZCE", "SR", "藁城永安", 80, 20)])
    _write_out(hist_env["out"], "20260914", "SHFE",
               [_prod_row("SHFE", "cu", "铜", 31400, -1545, "吨")],
               detail=[_wh_row("SHFE", "cu", "国储天威", 429, 0, "仓库")])

    h = history.build()
    assert h["dates"] == ["2026-09-11", "2026-09-14"]
    sr = h["products"]["CZCE|SR"]
    assert sr["qty"] == {"2026-09-11": 100, "2026-09-14": 120}
    assert sr["change"] == {"2026-09-11": -10, "2026-09-14": 20}
    assert sr["unit"] == "张" and sr["name"] == "白糖"
    wh = h["warehouses"]["CZCE|SR"]["藁城永安"]
    assert wh["qty"] == {"2026-09-11": 60, "2026-09-14": 80}
    assert wh["change"] == {"2026-09-11": -5, "2026-09-14": 20}
    cu_wh = h["warehouses"]["SHFE|cu"]["国储天威"]
    assert cu_wh["qty"] == {"2026-09-14": 429}


# ── 2. 缺数日: 键缺省(不补 0), 折线留空 ──────────────────────────────────
def test_missing_date_is_gap_not_zero(hist_env):
    _write_out(hist_env["out"], "20260911", "CZCE",
               [_prod_row("CZCE", "SR", "白糖", 100, -10, "张")])
    # 0914 只有 SHFE(模拟广期所 0916 空壳被拦后的缺口形态)
    _write_out(hist_env["out"], "20260914", "SHFE",
               [_prod_row("SHFE", "cu", "铜", 31400, -1545, "吨")])
    h = history.build()
    assert h["dates"] == ["2026-09-11", "2026-09-14"]
    assert "2026-09-14" not in h["products"]["CZCE|SR"]["qty"]  # 缺日无键
    assert "2026-09-11" not in h["products"]["SHFE|cu"]["qty"]


# ── 3. CZCE 机构预报行(kind=forecast)剔除 ────────────────────────────────
def test_czce_forecast_rows_excluded(hist_env):
    _write_out(hist_env["out"], "20260911", "CZCE",
               [_prod_row("CZCE", "SR", "白糖", 100, -10, "张"),
                _prod_row("CZCE", "SR", "白糖", 999, 999, "张", kind="forecast")],
               summary=[_wh_row("CZCE", "SR", "藁城永安", 60, -5),
                        _wh_row("CZCE", "SR", "预报占位", 888, 888, kind="forecast")])
    h = history.build()
    assert h["products"]["CZCE|SR"]["qty"]["2026-09-11"] == 100
    assert list(h["warehouses"]["CZCE|SR"].keys()) == ["藁城永安"]


# ── 4. DCE/GFEX 汇总占位行与空名行剔除 ───────────────────────────────────
def test_placeholder_warehouse_rows_excluded(hist_env):
    _write_out(hist_env["out"], "20260917", "GFEX",
               [_prod_row("GFEX", "lc", "碳酸锂", 43583, -1932, "手")],
               detail=[_wh_row("GFEX", "lc", "象屿速传上海", 4893, -46, "厂库", "005"),
                       _wh_row("GFEX", "lc", "", 43583, -1932, "", "wh_code_order")])
    _write_out(hist_env["out"], "20260917", "DCE",
               [_prod_row("DCE", "a", "豆一", 73806, 0, "手")],
               detail=[_wh_row("DCE", "a", "中粮贸易", 4855, 0, "仓库", "002"),
                       _wh_row("DCE", "a", "  ", 999, 0, "仓库", "003")])
    h = history.build()
    assert list(h["warehouses"]["GFEX|lc"].keys()) == ["象屿速传上海"]
    assert list(h["warehouses"]["DCE|a"].keys()) == ["中粮贸易"]


# ── 5. CZCE xlsx 回填: 只补 out/ 缺失的日期 ──────────────────────────────
def test_czce_backfill_only_missing_dates(hist_env, monkeypatch):
    _write_out(hist_env["out"], "20260917", "CZCE",
               [_prod_row("CZCE", "SR", "白糖", 29498, -434, "张")])
    # 本地放两个 xlsx: 一个日期 out/ 已有(不回填), 一个没有(回填)
    (hist_env["raw"] / "20260917_Whsheet.xlsx").write_bytes(b"x")
    (hist_env["raw"] / "20260821_Whsheet.xlsx").write_bytes(b"x")
    called = []

    def fake_parse(path, date_cn):
        called.append(date_cn)
        return {"product": [_prod_row("CZCE", "SR", "白糖", 22567, 0, "张")],
                "summary": [_wh_row("CZCE", "SR", "藁城永安", 1624, 0)]}

    monkeypatch.setattr(history, "_czce_parse_file", fake_parse)
    h = history.build()
    assert called == ["2026-08-21"]          # 已有 out 产物的日期不重复回填
    assert h["backfilled"] == ["2026-08-21"]
    assert h["dates"] == ["2026-08-21", "2026-09-17"]
    assert h["products"]["CZCE|SR"]["qty"]["2026-08-21"] == 22567


def test_czce_backfill_failure_is_isolated(hist_env, monkeypatch):
    """单个 xlsx 解析失败不拖垮整体(只少这一天的回填)。"""
    (hist_env["raw"] / "20260821_Whsheet.xlsx").write_bytes(b"x")
    monkeypatch.setattr(history, "_czce_parse_file", lambda *a: None)
    h = history.build()
    assert h["backfilled"] == []
    assert h["dates"] == []


# ── 6. 四所合计: 主单位口径, 非主单位品种排除并计数 ──────────────────────
def test_ex_totals_main_unit_only(hist_env):
    _write_out(hist_env["out"], "20260917", "SHFE",
               [_prod_row("SHFE", "cu", "铜", 31400, 0, "吨"),
                _prod_row("SHFE", "ag", "白银", 1000, 0, "千克"),
                _prod_row("SHFE", "sc", "原油", 500, 0, "桶")],
               detail=[])
    _write_out(hist_env["out"], "20260917", "CZCE",
               [_prod_row("CZCE", "SR", "白糖", 29498, 0, "张")])
    h = history.build()
    sh = h["ex_totals"]["SHFE"]
    assert sh["qty"]["2026-09-17"] == 31400        # 千克/桶不与吨相加
    assert sh["excluded"] == 2
    assert h["ex_totals"]["CZCE"]["qty"]["2026-09-17"] == 29498
    assert h["ex_totals"]["CZCE"]["excluded"] == 0


# ── 7. 缓存: 文件集不变命中缓存, 新文件落盘后重建 ────────────────────────
def test_cache_invalidation(hist_env):
    _write_out(hist_env["out"], "20260917", "CZCE",
               [_prod_row("CZCE", "SR", "白糖", 100, 0, "张")])
    h1 = history.get()
    assert h1["products"]["CZCE|SR"]["qty"]["2026-09-17"] == 100
    # 模拟新一天采集落盘
    _write_out(hist_env["out"], "20260918", "CZCE",
               [_prod_row("CZCE", "SR", "白糖", 120, 20, "张")])
    h2 = history.get()
    assert h2["dates"] == ["2026-09-17", "2026-09-18"]
    assert h2["products"]["CZCE|SR"]["qty"]["2026-09-18"] == 120
    # 未变化时返回同一对象(命中缓存)
    assert history.get() is h2


# ── 8. API: 仓库级时序按最新非空仓单量降序 ───────────────────────────────
def test_api_history_warehouses_sorted_by_latest(monkeypatch):
    import web_app

    fake = {
        "dates": ["2026-09-16", "2026-09-17"],
        "products": {"CZCE|SR": {"exchange": "CZCE", "code": "SR", "name": "白糖",
                                 "unit": "张",
                                 "qty": {"2026-09-16": 100, "2026-09-17": 90},
                                 "change": {"2026-09-16": 0, "2026-09-17": -10}}},
        "ex_totals": {"CZCE": {"unit": "张",
                               "qty": {"2026-09-16": 100, "2026-09-17": 90},
                               "excluded": 0}},
        "warehouses": {"CZCE|SR": {
            # 甲: 最新 60; 乙: 最新 80 但 16 日更大 —— 排序只看「最新非空」
            "甲库": {"type": "仓库", "qty": {"2026-09-16": 100, "2026-09-17": 60},
                     "change": {}},
            "乙库": {"type": "仓库", "qty": {"2026-09-16": 30, "2026-09-17": 80},
                     "change": {}},
            "丙库": {"type": "仓库", "qty": {}, "change": {}},   # 全空 → 排最后
        }},
        "backfilled": [],
    }
    monkeypatch.setattr(history, "get", lambda: fake)
    client = web_app.app.test_client()

    # 不带品种参数 → 只返回品种级, 无 warehouses 键
    r = client.get("/api/warrant/history")
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True
    assert d["dates"] == fake["dates"]
    assert "warehouses" not in d

    # 带品种参数 → 仓库级按最新非空降序(乙 80 > 甲 60 > 丙 无数据)
    r2 = client.get("/api/warrant/history?exchange=CZCE&code=SR")
    d2 = r2.get_json()
    names = [w["name"] for w in d2["warehouses"]]
    assert names == ["乙库", "甲库", "丙库"]

    # 未知名 → warehouses 为空数组, 不 404
    r3 = client.get("/api/warrant/history?exchange=CZCE&code=XX")
    assert r3.status_code == 200
    assert r3.get_json()["warehouses"] == []


def test_api_history_aggregate_failure_degrades(monkeypatch):
    """历史聚合抛异常 → 接口 200 + ok:false(不 500 整页)。"""
    import web_app

    def boom():
        raise RuntimeError("disk gone")

    monkeypatch.setattr(history, "get", boom)
    client = web_app.app.test_client()
    r = client.get("/api/warrant/history")
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is False
    assert d["dates"] == [] and d["products"] == {}
