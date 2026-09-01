"""数据看板纯函数解析/关联分析守卫 (2026-09-01)。

数据看板后端在 web_app.py 里新增了三组纯函数(不联网, 直接 import web_app 即可测试):

  - `_inventory_points(csv_text)`  解析 get_futures_inventory 的 CSV 文本 → [{date, inventory, change}];
  - `_basis_points(csv_text)`      解析 get_futures_basis 的 CSV 文本 → [{date, spot_price, near_basis, near_basis_rate}];
  - `_dashboard_relationships(price, inventory, basis)`  价格-库存 Pearson R / 库存趋势 / 基差率 / 近 5 日背离检测。

CSV 来源都带有真实噪声: 表头是英文列名、行首可能带 '#' 的趋势/结构注释、字段可能为空。
本测试用合成 CSV/序列把这些边界锁死, 保证前端看板不会因解析问题崩掉。
"""

from datetime import date, timedelta

import pytest

from web_app import (
    _basis_points,
    _dashboard_relationships,
    _inventory_points,
    _pct,
    _pearson,
)


def _prices(values, start="2026-01-01"):
    base = date.fromisoformat(start)
    return [{"date": (base + timedelta(days=i)).isoformat(), "close": float(v)}
            for i, v in enumerate(values)]


def _inv(values, start="2026-01-01"):
    base = date.fromisoformat(start)
    return [{"date": (base + timedelta(days=i)).isoformat(), "inventory": float(v)}
            for i, v in enumerate(values)]


# ---------------------------------------------------------------------------
# 1) _inventory_points: 跳过 # 注释 / 英文表头 / 非法数值行
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_inventory_points_parses_with_comments_and_header():
    csv_text = (
        "date,inventory,change\n"
        "2026-08-01,12000,500\n"
        "2026-08-02,12400,400\n"
        "# 库存趋势: 近5日均 13000 vs 更早5日均 12000 → BUILDING (+8.3%)\n"
        "2026-08-03,12800,400\n"
        "# 合并说明: 社交库存与交易所仓单合并\n"
    )
    pts = _inventory_points(csv_text)
    assert [p["date"] for p in pts] == ["2026-08-01", "2026-08-02", "2026-08-03"]
    assert pts[0] == {"date": "2026-08-01", "inventory": 12000.0, "change": "500"}
    assert pts[-1]["inventory"] == 12800.0


@pytest.mark.unit
def test_inventory_points_skips_bad_rows_and_empty_input():
    csv_text = (
        "date,inventory,change\n"
        "2026-08-01,12000,500\n"
        "2026-08-02,ABC,400\n"          # 数值列非法 → 跳过
        "2026-08-03,,300\n"             # 空数值 → 跳过
    )
    pts = _inventory_points(csv_text)
    assert len(pts) == 1 and pts[0]["date"] == "2026-08-01"
    assert _inventory_points("") == []
    assert _inventory_points("# 只有注释\n") == []


# ---------------------------------------------------------------------------
# 2) _basis_points: 按表头列名解析, 缺列/空值 → None
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_basis_points_parses_header_driven():
    csv_text = (
        "date,symbol,spot_price,near_contract,near_contract_price,near_basis,near_basis_rate\n"
        "2026-08-01,RB,3200,RB2509,3180,20,0.625\n"
        "2026-08-02,RB,3210,RB2509,3195,15,0.467\n"
        "# 基差结构: BACKWARDATION (现货升水)\n"
    )
    pts = _basis_points(csv_text)
    assert len(pts) == 2
    assert pts[-1]["date"] == "2026-08-02"
    assert pts[-1]["spot_price"] == 3210.0
    assert pts[-1]["near_basis"] == 15.0
    assert pts[-1]["near_basis_rate"] == pytest.approx(0.467)


@pytest.mark.unit
def test_basis_points_missing_column_and_empty_cells():
    # 品种无近月合约时, akshare 的 near_* 列缺失 → 解析必须优雅(None 而非抛错)
    csv_text = (
        "date,symbol,spot_price,dominant_contract,dominant_contract_price,dom_basis,dom_basis_rate\n"
        "2026-08-01,AO,3600,AO2601,3580,20,\n"   # dom_basis_rate 空(解析器不读该列, 不应崩)
        "2026-08-02,AO,3610,AO2601,3600,10,0.277\n"
    )
    pts = _basis_points(csv_text)
    assert len(pts) == 2
    # near_* 列不存在 → 全部 None(前端据此判断无近月基差)
    assert pts[0]["near_basis"] is None
    assert pts[0]["near_basis_rate"] is None
    assert pts[1]["near_basis"] is None
    # 仅缺近月列时, 现货价仍被读出
    assert pts[0]["spot_price"] == 3600.0
    assert _basis_points("# 只有注释\n") == []


@pytest.mark.unit
def test_basis_points_empty_cell_is_none():
    # 空单元格 → None(而非解析异常)
    csv_text = (
        "date,spot_price,near_basis,near_basis_rate\n"
        "2026-08-01,3200,20,\n"       # near_basis_rate 空
        "2026-08-02,3210,15,0.467\n"
    )
    pts = _basis_points(csv_text)
    assert len(pts) == 2
    assert pts[0]["near_basis_rate"] is None
    assert pts[1]["near_basis_rate"] == pytest.approx(0.467)


# ---------------------------------------------------------------------------
# 3) _pearson / _pct 基础
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_pearson_perfect_and_singleton():
    assert _pearson([1, 2, 3], [10, 20, 30]) == pytest.approx(1.0)
    assert _pearson([1, 2, 3], [30, 20, 10]) == pytest.approx(-1.0)
    assert _pearson([1], [2]) is None              # 长度<2
    assert _pearson([1, 1, 1], [1, 2, 3]) is None   # x 方差为 0
    assert _pearson([1, 2, 3], [1]) is None         # 长度不齐


@pytest.mark.unit
def test_pct_base_rules():
    assert _pct(110, 100) == pytest.approx(10.0)
    assert _pct(90, 100) == pytest.approx(-10.0)
    assert _pct(100, 0) is None
    assert _pct(100, None) is None


# ---------------------------------------------------------------------------
# 4) _dashboard_relationships: Pearson R
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_relationships_positive_correlation():
    n = 30
    price = _prices([100 + i for i in range(n)])
    inventory = _inv([1000 + 10 * i for i in range(n)])
    rel = _dashboard_relationships(price, inventory, [])
    assert rel["has_price"] and rel["has_inventory"]
    assert rel["price_inventory_r"] == pytest.approx(1.0, abs=1e-4)
    assert rel["price_inventory_n"] == n
    # 变化率 R 也是 +1(严格同向)
    assert rel["ret_inventory_r"] == pytest.approx(1.0, abs=1e-4)


@pytest.mark.unit
def test_relationships_negative_correlation():
    n = 30
    price = _prices([100 + i for i in range(n)])
    inventory = _inv([2000 - 10 * i for i in range(n)])
    rel = _dashboard_relationships(price, inventory, [])
    assert rel["price_inventory_r"] == pytest.approx(-1.0, abs=1e-4)
    assert rel["price_inventory_n"] == n


@pytest.mark.unit
def test_relationships_no_inventory_graceful():
    rel = _dashboard_relationships(_prices([100, 101, 102]), [], [])
    assert rel["has_price"] and not rel["has_inventory"]
    assert rel["price_inventory_r"] is None
    assert rel["inventory_trend"] is None
    assert rel["divergence"] is None


# ---------------------------------------------------------------------------
# 5) _dashboard_relationships: 库存趋势 (近5日均 vs 更早5日均, ±3% 阈值)
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_inventory_trend_building_draining_stable():
    # 强上升 → BUILDING (+~11%)
    up = _dashboard_relationships([], _inv([1000 + 30 * i for i in range(20)]), [])
    assert up["inventory_trend"] == "BUILDING"
    assert up["inventory_trend_pct"] > 3

    # 强下降 → DRAINING (-~11%)
    down = _dashboard_relationships([], _inv([2000 - 30 * i for i in range(20)]), [])
    assert down["inventory_trend"] == "DRAINING"
    assert down["inventory_trend_pct"] < -3

    # 微小抖动 → STABLE (|pct| < 3)
    flat = _dashboard_relationships([], _inv([1000 + (i % 2) for i in range(20)]), [])
    assert flat["inventory_trend"] == "STABLE"
    assert abs(flat["inventory_trend_pct"]) < 3

    # 不足 10 个点 → 无趋势
    short = _dashboard_relationships([], _inv([1000 + i for i in range(8)]), [])
    assert short["inventory_trend"] is None


# ---------------------------------------------------------------------------
# 6) _dashboard_relationships: 近 5 日背离检测四象限
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_divergence_healthy_up():
    price = _prices([100, 101, 102, 103, 104, 105, 106, 107, 108, 109])   # 近5日上涨
    inventory = _inv([1000, 990, 980, 970, 960, 950, 940, 930, 920, 910])  # 近5日去库
    div = _dashboard_relationships(price, inventory, [])["divergence"]
    assert div["label"] == "健康上涨"
    assert div["price_chg_pct"] > 0 and div["inventory_chg_pct"] < 0


@pytest.mark.unit
def test_divergence_healthy_down():
    price = _prices([109, 108, 107, 106, 105, 104, 103, 102, 101, 100])   # 近5日下跌
    inventory = _inv([900, 910, 920, 930, 940, 950, 960, 970, 980, 990])   # 近5日累库
    div = _dashboard_relationships(price, inventory, [])["divergence"]
    assert div["label"] == "健康下跌"
    assert div["price_chg_pct"] < 0 and div["inventory_chg_pct"] > 0


@pytest.mark.unit
def test_divergence_virtual_rally():
    price = _prices([100, 102, 104, 106, 108, 110, 112, 114, 116, 118])   # 上涨
    inventory = _inv([1000, 1010, 1020, 1030, 1040, 1050, 1060, 1070, 1080, 1090])  # 累库
    div = _dashboard_relationships(price, inventory, [])["divergence"]
    assert div["label"] == "背离-虚涨"


@pytest.mark.unit
def test_divergence_oversold():
    price = _prices([118, 116, 114, 112, 110, 108, 106, 104, 102, 100])   # 下跌
    inventory = _inv([1090, 1080, 1070, 1060, 1050, 1040, 1030, 1020, 1010, 1000])  # 去库
    div = _dashboard_relationships(price, inventory, [])["divergence"]
    assert div["label"] == "背离-超跌"


@pytest.mark.unit
def test_divergence_needs_6_points():
    # 价格/库存各只有 5 个点(边界) → 不满足 len>=6, 无背离
    rel = _dashboard_relationships(_prices([100, 101, 102, 103, 104]),
                                   _inv([1000, 990, 980, 970, 960]), [])
    assert rel["divergence"] is None


# ---------------------------------------------------------------------------
# 7) _dashboard_relationships: 基差解析 + 基差-价格 R
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_basis_latest_and_basis_price_r():
    price = _prices([100 + i for i in range(30)])
    basis = [
        {"date": price[i]["date"], "spot_price": 100 + i, "near_basis": 5 + i, "near_basis_rate": (5 + i) / 100}
        for i in range(30)
    ]
    rel = _dashboard_relationships(price, [], basis)
    assert rel["has_basis"]
    assert rel["basis_latest"] == pytest.approx(34.0)             # 5 + 29
    assert rel["basis_rate_latest"] == pytest.approx(0.34, abs=1e-4)
    # 基差与价格完全线性正相关
    assert rel["basis_price_r"] == pytest.approx(1.0, abs=1e-4)


@pytest.mark.unit
def test_constant_series_r_is_none_not_crash():
    # 回归:2026-09-01 发现 round(_pearson(...)) 在序列方差为 0(恒定值)时对 None 调 round 抛
    #   TypeError。恒定基差/恒定价格都必须返回 None,而不是崩溃。
    price = _prices([100 + i for i in range(30)])
    const_basis = [
        {"date": price[i]["date"], "spot_price": 100, "near_basis": 5.0, "near_basis_rate": 0.05}
        for i in range(30)
    ]
    rel = _dashboard_relationships(price, [], const_basis)
    assert rel["basis_price_r"] is None  # 基差恒定 → R 无意义 → None

    const_price = _prices([100] * 8)
    inv_pts = _inv([1000, 990, 980, 970, 960, 950, 940, 930])
    rel2 = _dashboard_relationships(const_price, inv_pts, [])
    assert rel2["price_inventory_r"] is None  # 价格恒定 → R 无意义 → None,且不抛错
