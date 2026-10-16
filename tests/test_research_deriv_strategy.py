"""来源观点对比表「衍生策略」列(2026-09-15 用户需求)单元测试。

【用户现象】单品种总结的观点对比表只给一个方向标签(如永安 2026-09-13《合成橡胶周报》
  BR = 看空),把同一篇研报里的组合思路("期现与月差均可考虑逢低正套""短期多天胶空合成,
  长期仍以做缩为主")整个吃掉了 → 用户看到的"观点"比研报窄。
【用户定的口径】把「单边方向」与「衍生策略(正套/跨品种)」分成两列。

【本文件锁死四件事】
  A. `_extract_deriv_strategy` 只认**动作词**(正套/反套/跨期/做缩做扩/多X空Y/期权/套保…),
     不收「月差/价差/对冲」这类在结论里几乎都是**驱动**的词;
  B. 三类实测误报必须继续被挡住:① "偏多但空间有限"(多X空Y 误匹配)② "驱动对冲、方向不明"
     ③ "因…内外价差走阔 → 推演…"(驱动描述);外加否定句"…不足以驱动方向性头寸";
  C. `_row_variety_advice` 从 structured_data 按品种取 operation_advice,脏数据一律空串兜底;
  D. 链路透传:两条 collect 的条目、合并(同机构多篇 deriv 去重拼接)、列表路由 report_rows。
"""

import json

import pytest

import web_app


# ---------------------------------------------------------------------------
# 桩件
# ---------------------------------------------------------------------------
def _row(rid=1, date="2026-09-13", codes="BR", status="done", direction="看空",
         confidence=0.8, conclusion=None, publish_date=None, report_type="周报",
         structured=None, source="永安期货研究中心能化团队"):
    """最小研报行(字段与 DB 行一致;structured_data 是 JSON 字符串)。"""
    return {
        "id": rid,
        "title": f"研报{rid}",
        "source": source,
        "variety": codes.split(",")[0],
        "varieties": codes,
        "uploaded_at": f"{date} 07:00:00",
        "publish_date": publish_date or date,
        "report_type": report_type,
        "status": status,
        "direction": direction,
        "confidence": confidence,
        "conclusion_md": conclusion,
        "structured_data": json.dumps(structured, ensure_ascii=False) if structured else None,
    }


class _FakeDB:
    """最小 DB 桩:list_research_reports 返回预置行。"""

    def __init__(self, rows):
        self._rows = rows

    def list_research_reports(self, variety=None, limit=50, ingest_source=None):
        return [dict(r) for r in self._rows]


# 【真实语料】永安 2026-09-13《合成橡胶周报》BR 段的策略原句(取自 structured_data
# 的 operation_advice,与库里一致)——这是用户报障的那个案例,必须一直被正确抽出。
_YONGAN_BR_ADVICE = (
    "当前价格下负反馈逐步出现，单边偏择机高空，短期也可观望待情绪回落；"
    "期现与月差均可考虑逢低正套；品种间估值来看RU-BR靠近上边界，但驱动来看顺丁基本面变差，"
    "短期多天胶空合成，长期仍以做缩为主。"
)


# ---------------------------------------------------------------------------
# A. 核心提取语义
# ---------------------------------------------------------------------------
def test_deriv_from_operation_advice_real_case():
    """真实案例:永安 BR 周报 → 抽出正套/跨品种三条,且不含纯单边的"单边偏择机高空"。"""
    got = web_app._extract_deriv_strategy(_YONGAN_BR_ADVICE, {})
    assert got == "期现与月差均可考虑逢低正套; 短期多天胶空合成; 长期仍以做缩为主"
    assert "高空" not in got  # 单边方向归方向列,不进衍生策略列


@pytest.mark.parametrize("advice,expect", [
    ("关注11-01反套; 1-5正套", "关注11-01反套; 1-5正套"),
    ("多PR空PTA", "多PR空PTA"),
    ("卖出看涨期权", "卖出看涨期权"),
    ("产业端逢高卖出套保", "产业端逢高卖出套保"),
    ("buy the spread on the far month", "buy the spread on the far month"),  # 英文研报
])
def test_deriv_accepts_real_strategy_sentences(advice, expect):
    assert web_app._extract_deriv_strategy(advice, {}) == expect


def test_deriv_te_source_wins_over_advice():
    """交易要素行(研报自报字段)优先于策略原句:先命中即返回,单元格保持短。"""
    te = {"形态与区间": "单边看空, —", "头寸": "期现、月间正套", "头寸范围": "—"}
    assert web_app._extract_deriv_strategy(_YONGAN_BR_ADVICE, te) == "期现、月间正套"


def test_deriv_strips_noise_prefixes():
    """「单边:」「理由:」等前缀剥掉(读起来才是策略,而不是小标题)。"""
    assert web_app._extract_deriv_strategy("单边：BZ及EB逢低谨慎做多套保", {}) \
        == "BZ及EB逢低谨慎做多套保"
    assert web_app._extract_deriv_strategy("关注月差机会，理由：1-5正套为主", {}) == "1-5正套为主"


def test_deriv_empty_when_only_single_side():
    """只有单边方向/头寸 → 空串(前端显示 —),不硬凑。"""
    te = {"方向": "看空", "形态与区间": "单边看空, —", "头寸": "轻仓逢高试空"}
    assert web_app._extract_deriv_strategy("", te) == ""
    assert web_app._extract_deriv_strategy("", {}) == ""
    assert web_app._extract_deriv_strategy("", {"头寸": "—"}) == ""


# ---------------------------------------------------------------------------
# B. 实测误报防护(每一条都来自真实研报,回归时必须继续被挡住)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,why", [
    ("方向偏多但上方空间受限", "多X空Y 曾把「多但空间」误匹配成多空组合"),
    ("偏多但空间有限", "同上"),
    ("综述：中性观望，驱动对冲，趋势不明。", "「对冲」在这里是行情多空抵消,不是策略"),
    ("理由：多空驱动对冲、方向不明", "同上(理由前缀 + 驱动对冲)"),
    ("因仓单低位+地缘溢价+内外价差走阔 → 推演短期情绪面仍偏强", "「价差」是驱动不是策略"),
    ("但“需求利空+套保压力”压制上方 → 推演盘面偏弱震荡 → 看空", "「套保压力」是利空因素不是建议"),
    ("仅期权情绪数据不足以驱动方向性头寸", "否定句不是策略"),
    ("风险:RU-BR价差回归及轮胎负荷变化", "风险披露不是策略"),
])
def test_deriv_rejects_real_false_positives(text, why):
    te = {"头寸": text}  # 塞进交易要素行这个"可信源",仍必须被规则挡住
    assert web_app._extract_deriv_strategy(text, te) == "", why


def test_deriv_truncates_long_result():
    """超长(多片段)按句读边界截断,绝不是无限长单元格。"""
    advice = "；".join(f"关注{i}-{i+1}正套机会把握节奏" for i in range(1, 20))
    got = web_app._extract_deriv_strategy(advice, {})
    assert len(got) <= 121 and got.endswith("…")


# ---------------------------------------------------------------------------
# C. _row_variety_advice:从 structured_data 按品种取 operation_advice
# ---------------------------------------------------------------------------
def test_row_variety_advice_picks_matching_variety():
    """多品种研报:两个品种各自的操作建议分开取,不串味。"""
    row = _row(structured={"varieties": [
        {"variety": "BR", "operation_advice": "单边偏择机高空"},
        {"variety": "RU", "operation_advice": "短期多天胶空合成"},
    ]})
    assert web_app._row_variety_advice(row, "BR") == "单边偏择机高空"
    assert web_app._row_variety_advice(row, "RU") == "短期多天胶空合成"
    assert web_app._row_variety_advice(row, "br") == "单边偏择机高空"  # 大小写不敏感
    assert web_app._row_variety_advice(row, "SC") == ""              # 不在本篇 → 空


@pytest.mark.parametrize("structured", [
    None,                                                 # 旧行无结构化数据
    "{不是 json",                                         # 脏 JSON
    {"varieties": ["BR"]},                                # 旧式字符串数组
    {"varieties": [{"variety": "BR"}]},                   # 有品种无 operation_advice
    [],                                                   # 顶层不是 dict
])
def test_row_variety_advice_degrades_quietly(structured):
    row = _row(structured=structured)
    assert web_app._row_variety_advice(row, "BR") == ""


# ---------------------------------------------------------------------------
# D. 链路透传:collect / 合并 / 路由
# ---------------------------------------------------------------------------
_BR_CONCLUSION = (
    "## BR 结论\n\n## 交易要素与风险\n方向:看空；形态与区间:单边看空, —；"
    "头寸:轻仓逢高试空；头寸范围:—；风险:丁二烯价格走强。\n\n"
)


def test_collect_items_carry_deriv_for_both_links():
    """每日链路与单品种链路的条目都带 deriv(单品种链路为后续喂 prompt 留好字段)。"""
    row = _row(conclusion=_BR_CONCLUSION,
               structured={"varieties": [{"variety": "BR", "operation_advice": _YONGAN_BR_ADVICE}]})
    daily = web_app._collect_daily_report_items([row], "2026-09-13", "周报")
    vari = web_app._collect_variety_report_items([row], "2026-09-13", "BR", "周报")
    for items in (daily, vari):
        assert len(items) == 1
        assert items[0]["deriv"] == "期现与月差均可考虑逢低正套; 短期多天胶空合成; 长期仍以做缩为主"


def test_merge_joins_deriv_of_same_source_multi_reports():
    """同机构同品种多篇:各篇衍生策略去重拼接(不因合并而丢信息),上限 120 字。"""
    recs = [
        {"code": "BR", "source": "永安", "rdate": "2026-09-13", "ts": "2026-09-13 07:00:00",
         "rid": 2, "order": 0, "title": "b", "direction": "看空", "conf_num": 0.8,
         "text": "正文二", "trade": "", "deriv": "期现正套"},
        {"code": "BR", "source": "永安", "rdate": "2026-09-13", "ts": "2026-09-12 07:00:00",
         "rid": 1, "order": 1, "title": "a", "direction": "看空", "conf_num": 0.6,
         "text": "正文一", "trade": "", "deriv": "多天胶空合成"},
        # 与 rid=2 逐字相同 → 判为冗余,只留一份
        {"code": "BR", "source": "永安", "rdate": "2026-09-13", "ts": "2026-09-11 07:00:00",
         "rid": 3, "order": 2, "title": "c", "direction": "看空", "conf_num": 0.7,
         "text": "正文三", "trade": "", "deriv": "期现正套"},
    ]
    merged = web_app._merge_same_source_items(recs, flatten=True, text_cap=200, trade_cap=120)
    assert len(merged) == 1
    assert merged[0]["deriv"] == "期现正套；多天胶空合成"  # 新→旧,恒用「；」连接(单行)
    assert "\n" not in merged[0]["deriv"]


def test_route_report_rows_expose_deriv(monkeypatch, tmp_path):
    """列表路由:report_rows 带上 deriv,前端「衍生策略」列才有数据。"""
    monkeypatch.setattr(web_app, "RESEARCH_VARIETY_DIR", tmp_path / "rv")
    row = _row(conclusion=_BR_CONCLUSION,
               structured={"varieties": [{"variety": "BR", "operation_advice": _YONGAN_BR_ADVICE}]})
    monkeypatch.setattr(web_app, "get_db", lambda: _FakeDB([row]))
    body = web_app.app.test_client().get(
        "/api/research/variety-summary/2026-09-13?report_type=周报").get_json()
    rows = body["varieties"][0]["report_rows"]
    assert rows[0]["deriv"] == "期现与月差均可考虑逢低正套; 短期多天胶空合成; 长期仍以做缩为主"
    assert rows[0]["direction"] == "看空"  # 方向列保持独立、不被衍生策略污染


def test_frontend_template_has_deriv_column():
    """前端模板:来源观点对比表必须有「衍生策略」列头(与方向列并列)。"""
    html = web_app.TEMPLATE_PATH.read_text(encoding="utf-8")
    assert "衍生策略" in html
    assert "r.deriv" in html
