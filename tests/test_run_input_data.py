"""运行分析输入数据小看板 /api/run_input_data/<品种> 装配与降级测试(不联网,全 mock)。

覆盖 2026-09-01 新增接口(分析输入数据看板):
  1. 纯解析函数:_parse_news_text(基础/纯注释/占位时间)、_parse_macro_text(成功+UNAVAILABLE+乱文本透传)、
     _run_input_basis_points(尾注结构)、_inventory_trend(趋势词);
  2. 路由聚合:价格/基差/库存/情绪/新闻/宏观六块全 available + 精确值 + data_as_of;
  3. 逐项降级:基差 NO_DATA / 情绪文件缺失 / 宏观全不可用 / 新闻解析失败 raw 透传 / 价格抛异常。

数据源(get_futures_*)与价格/库存解析全 mock;基差/新闻/宏观走真实解析函数,校验端到端格式兼容。
"""

import json

import pytest

import web_app

# ---------------------------------------------------------------------------
# 合成数据(与 get_futures_* 真实输出格式同构)
# ---------------------------------------------------------------------------
_NEWS_TEXT = """# COMMODITY & MACRO NEWS (multi-source)
# SHMET: 3 articles | Eastmoney keyword-filtered: 2/5
2026-08-30 09:00 [SHMET] 沪铜现货升水扩大
  铜现货对主力贴水收窄,贸易商挺价情绪升温
?? [东方财富] 螺纹钢库存周报
"""

_BASIS_TEXT = """# Latest basis: 25.00 — BACKWARDATION (现货升水)
date,spot_price,dominant_contract,dominant_contract_price,dom_basis,dom_basis_rate,near_contract,near_contract_price,near_basis,near_basis_rate
2026-08-29,3250,2010,3200,50,1.54,2011,3225,25,0.77
"""

_INV_TEXT = """date,inventory,change
2026-08-29,850000.0,-12000.0
# Warehouse receipt trend: DRAINING (-1.4% vs earlier period)
"""

_MACRO_TEXT = """## GDP (季度)
  GDP 同比: 4.7%
## PMI (制造业采购经理指数)
  制造业PMI: 49.4
## 房地产景气指数
## Real Estate: UNAVAILABLE (akshare says no data)
"""


# ---------------------------------------------------------------------------
# 纯解析函数
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_parse_news_basic_items():
    items = web_app._parse_news_text(_NEWS_TEXT)
    assert len(items) == 2
    assert items[0]["time"] == "2026-08-30 09:00"
    assert items[0]["source"] == "SHMET"
    assert items[0]["title"] == "沪铜现货升水扩大"
    assert items[0]["summary"] == "铜现货对主力贴水收窄,贸易商挺价情绪升温"  # 缩进续行并入摘要
    assert items[1]["time"] == "??"  # 占位时间也能解析
    assert items[1]["source"] == "东方财富"
    assert items[1]["summary"] == ""


@pytest.mark.unit
def test_parse_news_comments_only_returns_empty():
    assert web_app._parse_news_text("# only a comment\n# another\n\n  indented stray") == []


@pytest.mark.unit
def test_parse_news_missing_brackets_skipped():
    """无 [来源] 标记的行跳过,不报错;前后正常条目仍解析。"""
    text = "2026-08-30 [SHMET] ok\nsome stray line without brackets\n?? [东财] second"
    items = web_app._parse_news_text(text)
    assert [i["title"] for i in items] == ["ok", "second"]


@pytest.mark.unit
def test_parse_macro_success_and_unavailable():
    items, raw = web_app._parse_macro_text(_MACRO_TEXT)
    assert raw == ""  # 解析成功,不透传原文
    by = {i["name"]: i for i in items}
    assert by["GDP"]["value"] == "4.7%"
    assert by["PMI"]["value"] == "49.4"
    assert by["Real Estate"]["value"] is None
    assert by["Real Estate"]["note"] == "UNAVAILABLE (akshare says no data)"
    # 房地产景气指数节无键值 → value None(不算解析失败,note 为空)
    assert by["房地产景气指数"]["value"] is None
    assert by["房地产景气指数"]["note"] == ""


@pytest.mark.unit
def test_parse_macro_gibberish_passthrough():
    """无任何 '## ' 节 → ([], 原文透传),路由侧走 raw 降级。"""
    items, raw = web_app._parse_macro_text("no ## sections here\njust raw text")
    assert items == []
    assert raw == "no ## sections here\njust raw text"


@pytest.mark.unit
def test_parse_basis_tail_structure():
    pts, structure = web_app._run_input_basis_points(_BASIS_TEXT)
    assert structure == "BACKWARDATION"  # 尾注 # Latest basis: ... — BACKWARDATION
    assert len(pts) == 1
    last = pts[-1]
    assert last["date"] == "2026-08-29"
    assert last["near_basis"] == 25.0
    assert last["near_basis_rate"] == 0.77
    assert last["dom_basis"] == 50.0
    assert last["spot_price"] == 3250.0


@pytest.mark.unit
def test_structure_from_basis_fallback():
    p = {"dom_basis": None, "near_basis": -5.0}
    assert web_app._structure_from_basis(p) == "CONTANGO"
    assert web_app._structure_from_basis({"dom_basis": 0.0, "near_basis": 0.0}) == "FLAT"
    assert web_app._structure_from_basis({"dom_basis": None, "near_basis": None}) is None


@pytest.mark.unit
def test_inventory_trend_word():
    assert web_app._inventory_trend(_INV_TEXT) == "DRAINING"
    assert web_app._inventory_trend("date,inventory,change\n2026-08-29,1,0") is None


# ---------------------------------------------------------------------------
# 路由聚合:六块全 available + 精确值 + data_as_of
# ---------------------------------------------------------------------------
def _write_sentiment(tmp_path, code="RB"):
    sent = {
        "data": {
            "social_sentiment": {
                "overall_sentiment_label": "中性", "avg_score": 0.042,
                "bullish_ratio": 0.412, "bearish_ratio": 0.482,
                "trend_label": "情绪转暖 ↑", "date_range": "2026-05-06 ~ 2026-07-30",
            },
            "daily_series": [{"date": "2026-07-30", "avg_score": 0.042}],
        }
    }
    (tmp_path / f"{code}_sentiment.json").write_text(json.dumps(sent, ensure_ascii=False), encoding="utf-8")


def _mock_all_sources(monkeypatch, tmp_path, *, price_fail=False, basis_note=None,
                      news_text=_NEWS_TEXT, macro_text=_MACRO_TEXT, inv_text=_INV_TEXT):
    """把数据源 mock 掉;基差/新闻/宏观传真实文本,走真实解析函数。"""

    def _price(*_a, **_k):
        if price_fail:
            raise RuntimeError("akshare price down")
        return "FAKE_PRICE_CSV"

    monkeypatch.setattr(web_app, "get_futures_price", _price)
    monkeypatch.setattr(web_app, "_adjusted_price_points",
                        lambda *_a, **_k: (
                            [{"date": "2026-08-29", "close": 3250.0},
                             {"date": "2026-08-30", "close": 3300.0}], None, None))
    monkeypatch.setattr(web_app, "get_futures_basis",
                        lambda *_a, **_k: (basis_note if basis_note else _BASIS_TEXT))
    monkeypatch.setattr(web_app, "get_futures_inventory", lambda *_a, **_k: inv_text)
    monkeypatch.setattr(web_app, "_inventory_points",
                        lambda *_a, **_k: [{"date": "2026-08-29", "inventory": 850000.0, "change": -12000.0}])
    monkeypatch.setattr(web_app, "get_futures_news", lambda *_a, **_k: news_text)
    monkeypatch.setattr(web_app, "get_futures_macro", lambda *_a, **_k: macro_text)
    monkeypatch.setattr(web_app, "SENTIMENT_DIR", tmp_path)
    _write_sentiment(tmp_path)


@pytest.mark.unit
def test_route_all_blocks_available(monkeypatch, tmp_path):
    _mock_all_sources(monkeypatch, tmp_path)
    c = web_app.app.test_client()
    r = c.get("/api/run_input_data/RB")
    assert r.status_code == 200
    d = r.get_json()
    # 六块全 available
    for k in ("price", "basis", "inventory", "sentiment", "news", "macro"):
        assert d[k]["available"] is True, k
    # 价格:最新收盘 3300 + 涨跌%(较前值 +1.54%)
    assert d["price"]["latest_close"] == 3300.0
    assert d["price"]["date"] == "2026-08-30"
    assert abs(d["price"]["change_pct"] - 1.53846) < 1e-3
    # 基差:真实解析函数 → 近月率/结构
    assert d["basis"]["near_basis_rate"] == 0.77
    assert d["basis"]["structure"] == "BACKWARDATION"
    # 库存:真实趋势解析 + mock 序列
    assert d["inventory"]["inventory"] == 850000.0
    assert d["inventory"]["trend"] == "DRAINING"
    # 情绪:本地 JSON 读入
    assert d["sentiment"]["label"] == "中性"
    assert d["sentiment"]["score"] == 0.042
    assert d["sentiment"]["data_end"] == "2026-07-30"
    # 新闻:真实解析 → 2 条
    assert len(d["news"]["items"]) == 2
    assert d["news"]["items"][0]["source"] == "SHMET"
    # 宏观:真实解析 → 4 节(2 有值 + 1 空 + 1 UNAVAILABLE)
    assert len(d["macro"]["items"]) == 4
    assert d["macro"]["items"][0]["value"] == "4.7%"
    # data_as_of = 各来源最新日期 max(价格 08-30)
    assert d["_meta"]["data_as_of"] == "2026-08-30"


# ---------------------------------------------------------------------------
# 逐项降级(接口恒 200,单项只降级该项)
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_route_basis_no_data_degrades_only_basis(monkeypatch, tmp_path):
    _mock_all_sources(monkeypatch, tmp_path, basis_note="NO_DATA_AVAILABLE: no basis for WR")
    c = web_app.app.test_client()
    d = c.get("/api/run_input_data/WR").get_json()
    assert d["basis"]["available"] is False
    assert "NO_DATA_AVAILABLE" in d["basis"]["note"]
    assert d["price"]["available"] is True
    assert d["inventory"]["available"] is True


@pytest.mark.unit
def test_route_sentiment_missing_file(monkeypatch, tmp_path):
    _mock_all_sources(monkeypatch, tmp_path)
    # 不写 AP 的情绪文件 → 该项降级
    c = web_app.app.test_client()
    d = c.get("/api/run_input_data/AP").get_json()
    assert d["sentiment"]["available"] is False
    assert "无情绪数据(AP)" in d["sentiment"]["note"]
    assert d["price"]["available"] is True


@pytest.mark.unit
def test_route_macro_all_unavailable(monkeypatch, tmp_path):
    macro_all_dead = """## GDP: UNAVAILABLE (x)
## PMI: UNAVAILABLE
## Real Estate: UNAVAILABLE
"""
    _mock_all_sources(monkeypatch, tmp_path, macro_text=macro_all_dead)
    c = web_app.app.test_client()
    d = c.get("/api/run_input_data/RB").get_json()
    assert d["macro"]["available"] is False
    assert "全部不可用" in d["macro"]["note"]
    assert d["price"]["available"] is True  # 宏观降级不拖累其他


@pytest.mark.unit
def test_route_news_parse_failure_raw_passthrough(monkeypatch, tmp_path):
    junk = "some random text line\nno source markers here at all"
    _mock_all_sources(monkeypatch, tmp_path, news_text=junk)
    c = web_app.app.test_client()
    d = c.get("/api/run_input_data/RB").get_json()
    assert d["news"]["available"] is False
    assert "解析失败" in d["news"]["note"]
    assert d["news"]["raw"] == junk  # 原文透传,前端 details/pre 展示


@pytest.mark.unit
def test_route_price_exception_degrades_only_price(monkeypatch, tmp_path):
    _mock_all_sources(monkeypatch, tmp_path, price_fail=True)
    c = web_app.app.test_client()
    d = c.get("/api/run_input_data/RB").get_json()
    assert d["price"]["available"] is False
    assert d["price"]["note"].startswith("DATA_ERROR")
    assert d["basis"]["available"] is True
    assert d["news"]["available"] is True


@pytest.mark.unit
def test_route_inventory_no_data_degrades_only_inventory(monkeypatch, tmp_path):
    _mock_all_sources(monkeypatch, tmp_path, inv_text="NO_DATA_AVAILABLE: no inv for WR")
    c = web_app.app.test_client()
    d = c.get("/api/run_input_data/WR").get_json()
    assert d["inventory"]["available"] is False
    assert "NO_DATA_AVAILABLE" in d["inventory"]["note"]
    assert d["basis"]["available"] is True
