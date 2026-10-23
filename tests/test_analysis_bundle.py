"""「国君四源注入块」护栏（2026-10-19）。

【背景】运行分析的四位分析师里，基本面那一位此前完全不知道「数据仓库」页已经算好的
国君四源（基差 / 交易所仓单 / 期货+社会库存 / 加工利润）。本模块把关键读数**确定性**
拼进 system prompt（不做成 @tool：工具调不调由 LLM 决定，可能整轮一次都不调）。

【本文件守什么】
  1. ★ 数值口径不能错：基差率**自算**（接口的 basisPremiumRate 是小数，直接当 % 会差 100 倍）。
  2. ★ 小基数不能用百分比：仓单 1,000 → 19,590 若写 "+1855%" 会被模型读成暴涨异常。
  3. ★ 三态不可混：接口失败写"失败"、0 行写"无覆盖"，都不能写成 0。
  4. ★ 未来日期点必须剔除（全量数据集可能含计划值），并注明剔除了几个。
  5. ★ 只注入基本面节点：技术面/宏观拿库存解释 K 线是本项目明确要避免的。
  6. 超时/异常静默降级（返回 ""），绝不让数据源把分析拖挂。
"""

from __future__ import annotations

import time

import pytest

from tradingagents.dataflows import analysis_bundle as A


# ---------------------------------------------------------------------------
# 造 payload
# ---------------------------------------------------------------------------
def _ind(iid, group, label, series, unit="", pct=None, as_of=None, first=None):
    vals = [v for _, v in series]
    return {
        "id": iid, "group": group, "label": label, "unit": unit,
        "current": vals[-1] if vals else None,
        "prev": vals[-2] if len(vals) > 1 else None,
        "pct": pct, "n": len(series), "as_of": as_of or (series[-1][0] if series else ""),
        "first_date": series[0][0] if series else "",
        "last_date": series[-1][0] if series else "",
        "note": "", "series": [[d, v] for d, v in series],
    }


def _payload(**over):
    inds = [
        _ind("basis.spot_price", "基差", "现货价", [("2026-10-15", 7000), ("2026-10-16", 7000)],
             unit="元/吨", pct=77),
        _ind("basis.contract_price", "基差", "参考合约价",
             [("2026-10-15", 6520), ("2026-10-16", 6470)], unit="元/吨"),
        _ind("basis.value", "基差", "基差(现货−期货)",
             [("2026-10-15", 480), ("2026-10-16", 530)], unit="元/吨", pct=77),
        # 接口原样返回的"小数"基差率：0.08 —— 绝不能直接当 0.08% 打印
        _ind("basis.rate", "基差", "基差率", [("2026-10-15", 0.07), ("2026-10-16", 0.08)], unit="%"),
        _ind("warehouse.on_warrant", "仓单", "注册仓单",
             [(f"2026-10-{d:02d}", v) for d, v in
              [(9, 1000), (10, 1100), (11, 1200), (12, 1500), (13, 3000), (16, 19590)]],
             pct=34),
        _ind("库存.社会库存", "库存", "社会库存",
             [(f"2026-10-{d:02d}", v) for d, v in
              [(9, 1180000), (10, 1190000), (11, 1200000), (12, 1210000),
               (13, 1220000), (14, 1385241)]], pct=23),
        _ind("利润.加工利润", "利润", "加工利润",
             [("2026-10-14", 20), ("2026-10-15", 15)], pct=24),
    ]
    sources = {
        "basis": {"available": True, "rows": 4},
        "warehouse": {"available": True, "rows": 115},
        "inventory": {"available": True, "rows": 232},
        "profit": {"available": True, "rows": 1127},
    }
    out = {"ok": True, "indicators": inds, "sources": sources}
    out.update(over)
    return out


@pytest.fixture()
def fake_fetch(monkeypatch):
    """把 gtja_module.fetch_overview 换成假实现，并清空缓存。"""
    import gtja_module

    box = {"payload": _payload(), "calls": 0}

    def _fake(symbol, *a, **k):
        box["calls"] += 1
        return box["payload"]

    monkeypatch.setattr(gtja_module, "fetch_overview", _fake)
    A.clear_cache()
    yield box
    A.clear_cache()


# ---------------------------------------------------------------------------
# 1. 数值口径
# ---------------------------------------------------------------------------
def test_basis_rate_is_computed_not_taken_from_api(fake_fetch):
    """★ 基差率必须自算：530/7000 ≈ 7.57%，接口字段 0.08 是小数。"""
    block = A.build_block("TA")
    assert "7.57%" in block
    assert "0.08%" not in block


def test_small_base_uses_absolute_delta(fake_fetch):
    """★ 仓单 1,000 → 19,590：不能写 +1859%。"""
    block = A.build_block("TA")
    line = next(l for l in block.splitlines() if l.startswith("◆ 仓单"))
    # 绝对量在前、百分比降级到括号里并显式标注"基数小"
    assert "+18,590" in line
    assert "基数小" in line
    assert line.index("18,590") < line.index("%")


def test_numbers_are_grouped_and_units_kept(fake_fetch):
    block = A.build_block("TA")
    assert "1,385,241" in block          # 社会库存千分位
    assert "元/吨" in block               # 单位随行


def test_as_of_line_lists_each_source(fake_fetch):
    block = A.build_block("TA")
    assert "数据截至：" in block
    assert "基差 10-16" in block and "仓单 10-16" in block


# ---------------------------------------------------------------------------
# 2. 三态 / 边界
# ---------------------------------------------------------------------------
def test_failed_source_is_not_written_as_zero(fake_fetch):
    p = _payload()
    p["sources"]["profit"] = {"available": False, "error": "GTJAError"}
    fake_fetch["payload"] = p
    block = A.build_block("TA")
    assert "利润 失败" in block
    assert "0" not in block.split("三态：")[1].split("\n")[0].replace("0/4", "")


def test_empty_source_is_wu_fugai_not_failure(fake_fetch):
    p = _payload()
    p["sources"]["basis"] = {"available": True, "rows": 0}
    p["indicators"] = [i for i in p["indicators"] if i["group"] != "基差"]
    fake_fetch["payload"] = p
    block = A.build_block("TA")
    assert "基差 无覆盖" in block
    assert "基差 失败" not in block


def test_all_empty_returns_blank(fake_fetch):
    fake_fetch["payload"] = {"ok": True, "indicators": [], "sources": {}}
    assert A.build_block("TA") == ""


def test_fetch_failure_returns_blank(monkeypatch):
    import gtja_module

    monkeypatch.setattr(gtja_module, "fetch_overview", lambda *a, **k: {"ok": False, "error": "no key"})
    A.clear_cache()
    assert A.build_block("TA") == ""


def test_exception_is_swallowed(monkeypatch):
    import gtja_module

    def _boom(*a, **k):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(gtja_module, "fetch_overview", _boom)
    A.clear_cache()
    assert A.build_block("TA") == ""


def test_future_points_are_dropped_and_counted(fake_fetch):
    p = _payload()
    for ind in p["indicators"]:
        if ind["id"] == "库存.社会库存":
            ind["series"] += [["2099-01-01", 999999]]
    fake_fetch["payload"] = p
    block = A.build_block("TA")
    assert "999999" not in block and "999,999" not in block
    assert "未来日期点" in block


# ---------------------------------------------------------------------------
# 3. 缓存与超时
# ---------------------------------------------------------------------------
def test_context_is_cached(fake_fetch):
    A.gtja_context("TA")
    A.gtja_context("TA")
    A.gtja_context("TA")
    assert fake_fetch["calls"] == 1, "重复调用必须命中缓存, 否则每次分析都打上游"


def test_timeout_degrades_to_explicit_block(monkeypatch):
    """超时不再返回 "" —— 必须写出「本次缺席」，否则模型会把失败当「没有这一节」。

    【2026-10-22 改】原断言是 `== ""`（静默降级）。体检证明静默是错的：
      块缺席时提示词里没有任何痕迹，分析师会把它当成「本来就没有四源读数」。
      改成显式降级块，同时仍然不能把分析卡住。
    """
    monkeypatch.setattr(A, "WAIT_SECONDS", 0.05)
    monkeypatch.setattr(A, "build_block", lambda s: time.sleep(0.3) or "SLOW")
    A.clear_cache()
    t0 = time.time()
    out = A.gtja_context("RB")
    assert "超时" in out and "未纳入" in out, "超时必须写出来，不能静默返回空串"
    assert time.time() - t0 < 0.5, "降级不能变成同步等待，仍要卡在 WAIT_SECONDS 附近返回"


# ---------------------------------------------------------------------------
# 4. 只注入基本面节点（真实提示捕获）
# ---------------------------------------------------------------------------
class _Capture(dict):
    pass


def _capture_prompt(monkeypatch, mod, factory_name, state):
    captured = {}

    def _fake_loop(llm, tools, initial_messages, **kw):
        captured["prompt"] = initial_messages[0].content
        captured["tools"] = [t.name for t in tools]
        return "REPORT"

    monkeypatch.setattr(mod, "_run_tool_loop", _fake_loop)
    monkeypatch.setattr(mod, "variety_db_context", lambda s: "")
    monkeypatch.setattr(mod, "fundamental_methodology_context", lambda s: "")
    monkeypatch.setattr(mod, "technical_methodology_context", lambda s: "")
    monkeypatch.setattr(mod, "macro_methodology_context", lambda s: "")
    node = getattr(mod, factory_name)(llm=object())
    node(state)
    return captured


def test_fundamental_prompt_contains_gtja_block(monkeypatch):
    from tradingagents.agents.analysts import commodity_analysts as mod

    monkeypatch.setattr(mod, "gtja_context", lambda s: "【国君四源·TEST】基差 530")
    cap = _capture_prompt(monkeypatch, mod, "create_commodity_fundamental_analyst",
                          {"trade_date": "2026-10-19", "company_of_interest": "TA",
                           "messages": [], "past_context": ""})
    assert "【国君四源·TEST】" in cap["prompt"]
    # 块必须在框架说明之前（先看到数据，再读怎么用）
    assert cap["prompt"].index("【国君四源·TEST】") < cap["prompt"].index("You are a commodity futures fundamental analyst")


def test_technical_prompt_has_no_gtja_block(monkeypatch):
    """★ 技术面节点不得拿到库存/仓单数据（否则会拿库存解释 K 线）。"""
    from tradingagents.agents.analysts import commodity_analysts as mod

    monkeypatch.setattr(mod, "gtja_context", lambda s: "【国君四源·TEST】")
    cap = _capture_prompt(monkeypatch, mod, "create_commodity_technical_analyst",
                          {"trade_date": "2026-10-19", "company_of_interest": "TA",
                           "messages": [], "past_context": ""})
    assert "国君四源" not in cap["prompt"]
