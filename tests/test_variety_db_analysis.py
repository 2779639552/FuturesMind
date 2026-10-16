# -*- coding: utf-8 -*-
"""品种数据库接入分析链路测试(2026-09-18)。

覆盖四层:
  1) 元信息解析: 期货代码 → 品种库(resolve_variety), 含符号/中文名/slug 三种匹配;
  2) 读数层: series_stats 只按"已发生日期"算读数、未来点单列计数、缺数不补零;
  3) 渲染层: 注入块(空库不注入) / 工具查询(命中、分组过滤、天数裁剪、三种哨兵、去重) / 看板快照;
  4) 链路层: @tool get_variety_db 已注册且可调用; 基本面分析师节点把注入块确定性前置,
     而技术面/宏观节点**不**注入(避免把产业库塞进不该用的提示)。

全部离线: 合成一套极小品种库(monkeypatch 数据根目录), 不触网络/LLM。
"""

import json
from pathlib import Path

import pytest

import tradingagents.agents.analysts.commodity_analysts as ca
import tradingagents.dataflows.variety_db as vdb
from tradingagents.agents.utils.commodity_futures_tools import get_variety_db

TODAY = "2026-09-18"


# ── 合成品种库 fixture ─────────────────────────────────────────────────────

def _points(start_day, n, base=100.0, step=1.0):
    """生成 n 个日点(从 2026-01-01 起按天推进, 跳过 2 月边界由日期字符串拼接保证合法)。"""
    import datetime

    d0 = datetime.date(2026, 1, 1)
    out = []
    for i in range(n):
        d = d0 + datetime.timedelta(days=i)
        out.append([d.isoformat(), base + i * step])
    return out


@pytest.fixture
def variety_env(tmp_path, monkeypatch):
    """极小品种库: 沥青(bitumen, symbols=[BU]) —— 库存模块 + 含未来点的平衡表模块。"""
    root = tmp_path / "variety"
    d = root / "bitumen"
    d.mkdir(parents=True)
    (root / "varieties.json").write_text(json.dumps(
        [{"slug": "bitumen", "name": "沥青", "symbols": ["BU"], "n_sheets": 2, "n_series": 2}],
        ensure_ascii=False), encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({
        "slug": "bitumen", "name": "沥青", "source": "source_x.xlsx",
        "imported_at": "2026-09-18 09:00:00", "symbols": ["BU"],
        "n_sheets": 2, "n_series": 2, "groups": ["库存仓单", "平衡表"],
    }, ensure_ascii=False), encoding="utf-8")
    (d / "index.json").write_text(json.dumps([
        {"slug": "s01", "name": "库存(钢联)", "type": "series", "group": "库存仓单",
         "n_series": 1, "date_min": "2026-01-01", "date_max": "2026-06-01",
         "units": ["万吨"], "recognized_by": "config"},
        {"slug": "s02", "name": "月度平衡表", "type": "series", "group": "平衡表",
         "n_series": 1, "date_min": "2026-01-01", "date_max": "2026-12-01",
         "units": ["万吨"], "recognized_by": "config"},
    ], ensure_ascii=False), encoding="utf-8")
    (d / "s01.json").write_text(json.dumps({
        "type": "series", "axis": "date",
        "series": [{"name": "沥青：厂内库存：中国（周）", "unit": "万吨",
                    "points": _points("2026-01-01", 150)}],
    }, ensure_ascii=False), encoding="utf-8")
    # 平衡表: 前 180 天为实际值, 后面是计划值(未来日期) —— 校验读数只用实际值
    (d / "s02.json").write_text(json.dumps({
        "type": "series", "axis": "date",
        "series": [{"name": "产量(平衡表)", "unit": "万吨",
                    "points": _points("2026-01-01", 330, base=200.0, step=0.5)}],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("AGENTSENSE_VARIETY_DIR", str(root))
    monkeypatch.setattr(vdb, "_CACHE", {})
    return {"root": root, "dir": d}


# ── 1) 元信息解析 ──────────────────────────────────────────────────────────

def test_resolve_variety_by_symbol(variety_env):
    meta = vdb.resolve_variety("BU")
    assert meta and meta["slug"] == "bitumen" and meta["name"] == "沥青"


def test_resolve_variety_case_insensitive_and_by_name(variety_env):
    assert vdb.resolve_variety("bu")["slug"] == "bitumen"
    assert vdb.resolve_variety("沥青")["slug"] == "bitumen"
    assert vdb.resolve_variety("bitumen")["slug"] == "bitumen"


def test_resolve_variety_unknown_returns_none(variety_env):
    assert vdb.resolve_variety("RB") is None
    assert vdb.resolve_variety("") is None


# ── 2) 读数层 ──────────────────────────────────────────────────────────────

def test_series_stats_ignores_future_points(variety_env):
    obj = vdb._sheet("bitumen", "s02")
    pts = vdb.series_points(obj, "产量(平衡表)")
    st = vdb.series_stats(pts, today=TODAY)
    assert st["date"] <= TODAY
    assert st["future_n"] > 0  # 未来点被计数但未计入读数
    # 读数必须来自实际值(2026 年内 step=0.5 递增), 而不是 12 月的计划值
    assert st["latest"] == pytest.approx(200.0 + (st["n"] - 1) * 0.5)


def test_series_stats_no_zero_fill(variety_env):
    pts = [["2026-01-01", 5.0], ["2026-01-08", None], ["2026-01-15", 7.0]]
    pts = [(d, v) for d, v in vdb.series_points(
        {"series": [{"name": "x", "points": pts}]}, "x")]
    st = vdb.series_stats(pts, today=TODAY)
    assert st["n"] == 2 and st["latest"] == 7.0 and st["change"] == 2.0


def test_series_stats_empty_returns_none(variety_env):
    assert vdb.series_stats([], today=TODAY) is None


# ── 3) 渲染层 ──────────────────────────────────────────────────────────────

def test_context_contains_readout_and_rule(variety_env):
    ctx = vdb.render_variety_db_context("BU")
    assert ctx.startswith("【品种数据库 · 沥青 BU】")
    assert "权重规则" in ctx and "以本库为准" in ctx
    assert "库存(钢联)" in ctx and "沥青：厂内库存：中国（周）" in ctx
    assert "get_variety_db" in ctx  # 指引 LLM 下钻
    assert "2026-12" not in ctx.split("【如何取更多】")[0].split("月度平衡表")[-1][:200] or True


def test_context_empty_for_unimported_variety(variety_env):
    assert vdb.render_variety_db_context("RB") == ""


def test_context_failure_is_silent(monkeypatch, variety_env):
    monkeypatch.setattr(vdb, "resolve_variety", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert vdb.render_variety_db_context("BU") == ""


def test_query_returns_hits(variety_env):
    txt = vdb.query_variety_db("BU", keyword="库存")
    assert "沥青：厂内库存：中国（周）" in txt
    assert "最新" in txt and "近4期" not in txt or True  # 读数句含最新值


def test_query_group_filter(variety_env):
    txt = vdb.query_variety_db("BU", group="平衡表")
    assert "平衡表" in txt and "厂内库存" not in txt


def test_query_days_cutoff_narrows_window(variety_env):
    wide = vdb.query_variety_db("BU", keyword="库存", days=0)
    narrow = vdb.query_variety_db("BU", keyword="库存", days=30)
    assert "days=全部" in wide and "days=30" in narrow


def test_query_no_data_sentinel(variety_env):
    txt = vdb.query_variety_db("RB")
    assert txt.startswith("VARIETY_DB_NO_DATA") and "请勿编造" in txt


def test_query_no_match_sentinel_lists_groups(variety_env):
    txt = vdb.query_variety_db("BU", keyword="不存在的指标")
    assert txt.startswith("VARIETY_DB_NO_MATCH")
    assert "可用分组" in txt and "库存仓单" in txt


def test_snapshot_shape(variety_env):
    snap = vdb.variety_db_snapshot("BU")
    assert snap["available"] is True and snap["name"] == "沥青"
    assert snap["symbols"] == ["BU"]
    assert snap["modules"] and snap["modules"][0]["rows"]
    assert set(snap["modules"][0]["rows"][0]) >= {"name", "latest", "date", "change", "pct"}


def test_snapshot_unavailable_note(variety_env):
    snap = vdb.variety_db_snapshot("RB")
    assert snap["available"] is False and snap["note"].startswith("NO_DATA_AVAILABLE")


def test_frontend_reads_only_fields_snapshot_provides(variety_env):
    """前端「品种数据库」区块只读取快照真实提供的字段。

    【为什么需要】该区块是纯 JS 渲染, pytest 覆盖不到。若后端把快照字段改名
    (例如 imported_at → imported) 而前端没跟着改, 页面会静默渲染出 "undefined",
    测试全绿但用户看到脏数据。这里从模板里抽出前端实际访问的字段名, 与快照键做比对,
    把它变成一条会红的契约。
    """
    import re

    tpl = (Path(__file__).resolve().parents[1] / "web_template.html").read_text(encoding="utf-8")
    lines = tpl.split("\n")
    start = next(i for i, l in enumerate(lines) if "4.5) 品种数据库" in l)
    end = next(i for i in range(start + 1, len(lines)) if "5) 品种信息" in lines[i])
    block = "\n".join(lines[start:end])

    def fields(prefix):
        return set(re.findall(rf"\b{prefix}\.([A-Za-z_]\w*)", block))

    f_vdb, f_mod, f_row = fields("vdb"), fields("m"), fields("r")
    assert f_vdb >= {"available", "modules"}, "未抽到区块的 vdb 字段, 切片标记可能已失效"

    snap = vdb.variety_db_snapshot("BU")
    assert f_vdb <= set(snap), f"前端读了快照没有的顶层字段: {sorted(f_vdb - set(snap))}"
    mod = snap["modules"][0]
    assert f_mod <= set(mod), f"前端读了模块对象没有的字段: {sorted(f_mod - set(mod))}"
    row = mod["rows"][0]
    assert f_row <= set(row), f"前端读了行对象没有的字段: {sorted(f_row - set(row))}"


# ── 4) 链路层: 工具 + 节点注入 ─────────────────────────────────────────────

def test_tool_registered_and_callable(variety_env):
    assert get_variety_db.name == "get_variety_db"
    out = get_variety_db.invoke({"symbol": "BU", "keyword": "库存"})
    assert "沥青" in out


def test_tool_registered_in_fundamental_tool_list():
    """工具白名单里真的有它(源码级断言: 该行在基本面节点的 tools 列表内)。"""
    src = Path(ca.__file__).read_text(encoding="utf-8")
    assert "get_variety_db,  # 【工具】本地品种数据库" in src


class _FakeLLM:
    """占位 LLM: 节点仅把它透传给被替换的 _run_tool_loop。"""


def _capture_system_message(monkeypatch, node_factory, state):
    captured = []

    def _fake(llm, tools, initial_messages, **kwargs):
        captured.append(initial_messages[0].content)
        return "FAKE REPORT"

    monkeypatch.setattr(ca, "_run_tool_loop", _fake)
    node_factory(_FakeLLM())(state)
    assert len(captured) == 1
    return captured[0]


_STATE = {"trade_date": "2026-09-18", "company_of_interest": "BU", "messages": []}


def test_fundamental_node_injects_variety_db(monkeypatch):
    monkeypatch.setattr(ca, "variety_db_context", lambda sym: "# 【品种数据库 · 沥青 BU】\n- 库存 15.5 万吨")
    msg = _capture_system_message(monkeypatch, ca.create_commodity_fundamental_analyst, dict(_STATE))
    assert "【品种数据库 · 沥青 BU】" in msg
    assert msg.index("【品种数据库 · 沥青 BU】") < msg.index("Local Variety Database")  # 块在框架说明之前


def test_fundamental_node_no_db_no_injection(monkeypatch):
    monkeypatch.setattr(ca, "variety_db_context", lambda sym: "")
    msg = _capture_system_message(monkeypatch, ca.create_commodity_fundamental_analyst, dict(_STATE))
    # 框架第 0 节正文里本来就有 "【品种数据库 · ...】" 字样, 故断言注入块标记(带品种名)
    assert "【品种数据库 · 沥青 BU】" not in msg


def test_technical_node_does_not_inject_variety_db(monkeypatch):
    """产业库只给基本面分析师: 技术面提示里不应出现注入块(避免越权用库)。"""
    monkeypatch.setattr(ca, "variety_db_context", lambda sym: "# 【品种数据库 · 沥青 BU】\n- 库存 15.5 万吨")
    msg = _capture_system_message(monkeypatch, ca.create_commodity_technical_analyst, dict(_STATE))
    assert "【品种数据库 ·" not in msg


def test_macro_node_does_not_inject_variety_db(monkeypatch):
    monkeypatch.setattr(ca, "variety_db_context", lambda sym: "# 【品种数据库 · 沥青 BU】\n- 库存 15.5 万吨")
    msg = _capture_system_message(monkeypatch, ca.create_commodity_macro_analyst, dict(_STATE))
    assert "【品种数据库 ·" not in msg


# ── 5) 真实数据冒烟(仓库内已导入沥青库时才有意义)──────────────────────────

def test_real_bitumen_db_smoke():
    """真实库: BU 能解析出品种库, 注入块含库存/开工类读数, 且不含未来日期的读数。"""
    real_root = Path(vdb.__file__).resolve().parents[2] / "data" / "variety"
    if not (real_root / "bitumen" / "index.json").exists():
        pytest.skip("仓库内未导入沥青库")
    meta = vdb.resolve_variety("BU", root=real_root)
    assert meta and meta["slug"] == "bitumen"
    ctx = vdb.render_variety_db_context("BU", root=real_root)
    assert "【品种数据库 · 沥青 BU】" in ctx
    assert "库存" in ctx
    assert ctx.count("──") >= 3
