"""数据卡护栏（2026-10-22）。

【它守什么】
把国君四源从「文本」升级为「带口径的结构化事实」后，最容易出的错不是算错数，
而是**把不同口径的同名指标并列摆出来，却不说明它们不可比**。

2026-10-22 实测（TA）正好踩到这一点：模型在报告里自己写下
    「注入块 7.89%（以现货价为分母） vs API 8.56%（以期货价为分母）——
      同一绝对值、分母口径不同，不可混比」
它推对了，但**是靠猜**。本文件的职责就是让系统把这句话写在字段里：

  1. 基差率的两个分母必须**并列成两张卡**，且互相标注 `not_comparable_with`；
  2. 每张卡必须带 `source / as_of / method`（来源+时点+算法），缺一个就是不合格；
  3. 数值缺失时**不产卡**，绝不写 0 占位（三态纪律在卡片层同样成立）；
  4. 库存双口径（交易所 vs 社会）不可相加，必须互标；
  5. 文本块与数据卡必须消费**同一份** overview（只取一次数），否则两边会对不上号。
"""
from __future__ import annotations

import pytest

from tradingagents.dataflows import analysis_bundle as A


# ---------------------------------------------------------------------------
# 造 payload（与 test_analysis_bundle 同构，精简版）
# ---------------------------------------------------------------------------
def _ind(iid, group, label, series, unit="", pct=None):
    vals = [v for _, v in series]
    return {
        "id": iid, "group": group, "label": label, "unit": unit,
        "current": vals[-1] if vals else None,
        "prev": vals[-2] if len(vals) > 1 else None,
        "pct": pct, "n": len(series),
        "as_of": series[-1][0] if series else "",
        "first_date": series[0][0] if series else "",
        "last_date": series[-1][0] if series else "",
        "note": "", "series": [[d, v] for d, v in series],
    }


def _payload(drop: set[str] | None = None):
    drop = drop or set()
    inds = [
        _ind("basis.spot_price", "基差", "现货价", [("2026-10-15", 7000), ("2026-10-16", 7000)],
             unit="元/吨", pct=77),
        _ind("basis.contract_price", "基差", "参考合约价",
             [("2026-10-15", 6520), ("2026-10-16", 6470)], unit="元/吨"),
        _ind("basis.value", "基差", "基差(现货−期货)",
             [("2026-10-15", 480), ("2026-10-16", 530)], unit="元/吨", pct=77),
        # 6 个点：`_delta(series, 5)` 需要 back+1 个点才算得出来
        _ind("warehouse.on_warrant", "仓单", "注册仓单",
             [("2026-10-09", 1000), ("2026-10-10", 1100), ("2026-10-11", 1200),
              ("2026-10-12", 1500), ("2026-10-13", 3000), ("2026-10-16", 19590)], pct=34),
        _ind("库存.交易所库存", "库存", "期货库存", [("2026-10-15", 61000), ("2026-10-16", 61200)], pct=38),
        _ind("库存.社会库存", "库存", "社会库存", [("2026-10-15", 1380000), ("2026-10-16", 1385241)], pct=23),
        _ind("利润.加工利润", "利润", "加工利润", [("2026-10-15", 15), ("2026-10-16", 23)], pct=25),
    ]
    inds = [i for i in inds if i["id"] not in drop]
    return {
        "ok": True,
        "indicators": inds,
        "sources": {
            "basis": {"available": True, "rows": 2},
            "warehouse": {"available": True, "rows": 2},
            "inventory": {"available": True, "rows": 2},
            "profit": {"available": True, "rows": 2},
        },
    }


@pytest.fixture
def fake(monkeypatch):
    import gtja_module

    box = {"payload": _payload(), "calls": 0}

    def _fake(symbol, *a, **k):
        box["calls"] += 1
        return box["payload"]

    monkeypatch.setattr(gtja_module, "fetch_overview", _fake)
    A.clear_cache()
    yield box
    A.clear_cache()


def _by_id(cards, cid):
    return next((c for c in cards if c["id"] == cid), None)


# ---------------------------------------------------------------------------
# 1. 基差率：两个分母必须并列且互斥
# ---------------------------------------------------------------------------
def test_two_basis_rate_cards_are_mutually_exclusive(fake):
    cards = A.build_cards("TA")
    spot_card = _by_id(cards, "gtja.basis.rate_spot")
    fut_card = _by_id(cards, "gtja.basis.rate_fut")

    assert spot_card, "缺「现货分母」基差率卡 —— 注入块用的就是这个口径"
    assert fut_card, "缺「期货分母」基差率卡 —— 研报引用时模型会分不清"

    # 同一个基差，两个分母：530/7000=7.571% vs 530/6470=8.192%
    assert round(spot_card["value"], 2) == 7.57
    assert round(fut_card["value"], 2) == 8.19
    assert spot_card["value"] != fut_card["value"], "两个分母算出了同一个数，口径没真正分开"

    assert "gtja.basis.rate_fut" in spot_card["not_comparable_with"]
    assert "gtja.basis.rate_spot" in fut_card["not_comparable_with"]
    assert spot_card.get("convertible_to", {}).get("gtja.basis.rate_fut"), \
        "两个口径必须给出换算关系，否则模型只能二选一或猜"


def test_rate_cards_not_comparable_with_absolute_basis(fake):
    cards = A.build_cards("TA")
    abs_card = _by_id(cards, "gtja.basis.abs")
    assert abs_card["value"] == 530
    # 价差有单位，不能与百分比比
    assert "gtja.basis.rate_spot" in abs_card["not_comparable_with"]
    assert "gtja.basis.spot" in abs_card["not_comparable_with"]


# ---------------------------------------------------------------------------
# 2. 每张卡必须有出处三件套
# ---------------------------------------------------------------------------
def test_every_card_has_provenance(fake):
    cards = A.build_cards("TA")
    assert cards, "没产出任何卡片"
    for c in cards:
        assert c.get("source"), f"{c['id']} 缺 source"
        assert c.get("as_of"), f"{c['id']} 缺 as_of（不知道是哪天的数）"
        assert c.get("method"), f"{c['id']} 缺 method（不知道这个数怎么算的）"
        assert c.get("grade") in ("A", "B"), f"{c['id']} 证据等级非法"
        assert c.get("unit") is not None
        assert c["id"] not in (c.get("not_comparable_with") or []), \
            f"{c['id']} 把自己列进了「不可比」清单（手写清单按前缀共用，应收敛掉自身）"


def test_card_carries_data_age(fake):
    """数据日期距今多少天必须写在卡上 —— 库存是周频，可能比价格落后十几天。"""
    from datetime import date

    cards = A.build_cards("TA")
    expect = (date.today() - date(2026, 10, 16)).days
    for c in cards:
        assert c.get("age_days") == expect, f"{c['id']} 没带 age_days 或算错"


def test_warehouse_unit_is_not_price_unit(fake):
    """仓单是吨/手，不能继承基差的「元/吨」。"""
    cards = A.build_cards("TA")
    wh = _by_id(cards, "gtja.warehouse.qty")
    assert "元" not in wh["unit"], f"仓单单位错成价格单位: {wh['unit']}"


def test_as_of_comes_from_series_not_today(fake):
    cards = A.build_cards("TA")
    for c in cards:
        assert c["as_of"] == "2026-10-16", f"{c['id']} 的 as_of 不是数据日期（写成了今天？）"


# ---------------------------------------------------------------------------
# 3. 缺失不产卡（三态纪律）
# ---------------------------------------------------------------------------
def test_missing_indicator_produces_no_card(fake, monkeypatch):
    import gtja_module

    fake["payload"] = _payload(drop={"warehouse.on_warrant"})
    monkeypatch.setattr(gtja_module, "fetch_overview", lambda s, *a, **k: fake["payload"])
    A.clear_cache()

    cards = A.build_cards("TA")
    assert _by_id(cards, "gtja.warehouse.qty") is None, "取不到仓单却产出了卡片（会变成 0 占位）"
    assert _by_id(cards, "gtja.basis.abs") is not None, "其余卡不该受牵连"


def test_failed_overview_returns_empty(fake, monkeypatch):
    import gtja_module

    monkeypatch.setattr(gtja_module, "fetch_overview", lambda s, *a, **k: {"ok": False})
    A.clear_cache()
    assert A.build_cards("TA") == []


# ---------------------------------------------------------------------------
# 4. 库存双口径不可相加
# ---------------------------------------------------------------------------
def test_inventory_dual_cards_not_comparable(fake):
    cards = A.build_cards("TA")
    fut = _by_id(cards, "gtja.inventory.futures")
    soc = _by_id(cards, "gtja.inventory.social")
    assert fut and soc, "双口径库存没有并列成两张卡"
    assert "gtja.inventory.social" in fut["not_comparable_with"]
    assert "gtja.inventory.futures" in soc["not_comparable_with"]
    assert soc["grade"] == "B", "社会库存来自第三方机构，等级不该是 A"


# ---------------------------------------------------------------------------
# 5. 文本块与数据卡共用一次取数
# ---------------------------------------------------------------------------
def test_block_and_cards_share_one_fetch(fake):
    A.build_block("TA")
    A.build_cards("TA")
    assert fake["calls"] == 1, (
        f"文本块与数据卡各自取了一次数（{fake['calls']} 次）—— 窗口边缘会取到不同快照，两边对不上"
    )


def test_cards_are_cached(fake):
    A.build_cards("TA")
    n = fake["calls"]
    A.build_cards("TA")
    assert fake["calls"] == n, "重复调用没有走缓存"


# ---------------------------------------------------------------------------
# 7. 口径提示：进提示词的那一行
# ---------------------------------------------------------------------------
def test_block_contains_caliber_hint(fake):
    block = A.build_block("TA")
    assert "◆ 口径提示：" in block, "注入块没带口径提示 —— 模型又只能靠猜分母"
    assert "现货分母 7.57%" in block and "期货分母 8.19%" in block
    assert "不可直接比较" in block
    # 测试 payload 里期现货同一天，不应误报"滞后"
    assert "周频" not in block or "库存" in block


def test_hint_does_not_call_price_weekly(fake):
    """价格是日频，不能被滞后提示写成「周频」。

    ⚠ 断言要盯住滞后句的主语（`{label}截至 ...`），不能全串搜 "现货价" ——
      换算说明里本来就会出现「现货价/期货价」，那样会误伤。
    """
    hint = A._caliber_hint(A.build_cards("TA"))
    if "周频" in hint:
        assert "现货价截至" not in hint and "期货价截至" not in hint, f"把日频价格标成周频: {hint}"


def test_block_stays_near_budget_with_hint(fake):
    block = A.build_block("TA")
    assert len(block) <= A.MAX_CHARS + 200, (
        f"块 {len(block)} 字，口径提示把预算撑破了（MAX_CHARS={A.MAX_CHARS}）"
    )
def test_warehouse_small_base_uses_absolute_delta(fake):
    cards = A.build_cards("TA")
    wh = _by_id(cards, "gtja.warehouse.qty")
    note = wh.get("note") or ""
    assert "5日变化" in note
    assert "+18,590" in note.replace(" ", ""), f"小基数应给绝对变化量，实际: {note}"
    assert "基数小" in note, "小基数还在用百分比描述 —— 会被模型读成暴涨异常"
