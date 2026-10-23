"""库存双口径(社会库存 + 交易所仓单) 护栏 —— 2026-10-16。

【为什么需要这一层】"库存"此前**只用交易所注册仓单**表示, 实测有两个后果:
  ① **仓单归零被读成"库存极低"**: CJ 仓单自 2026-09-30 起恒为 0 → 分位 0.042 →
     判"低" ⇒ 三指标策略系统输出**偏多**(一个凭空的看多信号);
  ② **忽略社会库存这一产业链口径**: 看不到"库存向交割库转移"(实测 BU/PX/SC 三例
     社会库存低而仓单高, 正是《期货多空逻辑》要抓的信号)。

本文件钉住四件事:
  A. 双口径规则矩阵(7 条分支) —— 纯函数, 不依赖真实数据
  B. 仓单 0 值**保留原值但必须标注**(用户 2026-10-14 拍板, 不是"剔除")
  C. ★ 安全性质: 综合档位为"矛盾"时**不产出单边动作**
  D. 前端呈现契约: 双行明细 + 警示标签 + 容器 id 不变
"""

import re
from pathlib import Path

import pytest

import fund_module as fm
import quant_module as qm

TPL = Path(__file__).resolve().parent.parent / "web_template.html"


def _rows(vals):
    """造仓单行序列(日期递增, 便于 0 值/连续段断言)。"""
    return [{"date": "2026-01-%02d" % (i + 1), "qty": v, "change": None}
            for i, v in enumerate(vals)]


# 分位落在 (0.35, 0.65) → 中 = 0.60; 末位最低 → 低; 末位最高 → 高
CK_MID = [1, 2, 3, 4, 5, 7, 8, 9, 10, 6]
CK_HIGH = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
CK_LOW = [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]

SOC_LOW = {"ok": True, "grade": "低", "pct_rank": 0.1, "label": "社会库存",
           "latest": 100.0, "latest_date": "2026-10-07", "count": 300,
           "dir": "降", "chg_pct": -3.0}
SOC_HIGH = dict(SOC_LOW, grade="高", pct_rank=0.9, dir="升", chg_pct=3.0)
SOC_MID = dict(SOC_LOW, grade="中", pct_rank=0.5, dir="平", chg_pct=0.0)
SOC_NONE = {"ok": False, "error": "该品种无国君自建库存数据(社会/工厂/期货口径均无)"}


@pytest.fixture(autouse=True)
def _no_tq_side_effects():
    """★ 本文件禁碰天勤（tqsdk）。

    【为什么】只要有用例经**接口**取数（本项目取数路径会先试天勤），就会留下 pending 的
      asyncio 任务 ⇒ 刷屏 `Task was destroyed but it is pending!` + `never awaited` 警告。
      看着像真故障，实为测试卫生问题；更要紧的是这类任务可能停在别人"临时替换全局状态"
      的窗口内造成**顺序污染**（本项目 2026-10-14 踩过同型事故）。
    【★ 只桩"日线"不够】残留任务实测来自客户端内部协程 ⇒ 把 tq_source 的三个取数入口一并桩掉。
    """
    import tradingagents.dataflows.tq_source as _tqs
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_tqs, "fetch_daily_bars", lambda *a, **k: None)
        mp.setattr(_tqs, "fetch_minute_bars", lambda *a, **k: None)
        mp.setattr(_tqs, "fetch_realtime", lambda *a, **k: None)
        mp.setattr(qm, "_load_daily_bars", lambda code, days: [])
        yield


@pytest.fixture
def dual(monkeypatch):
    """把社会库存侧注入成构造值, 使规则矩阵完全不依赖真实数据。"""
    def _make(ck_vals, social):
        monkeypatch.setattr(qm, "_inv_social_block", lambda code: social)
        return qm._inv_dual("XX", _rows(ck_vals), 180, "test")

    return _make


# ===========================================================================
# A. 规则矩阵
# ===========================================================================
@pytest.mark.unit
def test_rule_both_same_grade_resonates(dual):
    d = dual(CK_LOW, SOC_LOW)
    assert d["grade"] == "低" and d["mode"] == "双口径共振", d
    d = dual(CK_HIGH, SOC_HIGH)
    assert d["grade"] == "高" and d["mode"] == "双口径共振", d


@pytest.mark.unit
def test_rule_opposite_grades_conflict(dual):
    """一低一高 → 矛盾(库存转移迹象), **不是**取平均、也不是选一侧。"""
    for ck, soc in ((CK_HIGH, SOC_LOW), (CK_LOW, SOC_HIGH)):
        d = dual(ck, soc)
        assert d["grade"] == "矛盾", d
        assert "背离" in d["mode"] and "转移" in d["note"], d


@pytest.mark.unit
def test_rule_one_side_neutral_gives_mid(dual):
    """一方为"中" → 综合取中(一强一中性不下注)。"""
    d = dual(CK_MID, SOC_LOW)
    assert d["grade"] == "中" and d["mode"] == "口径分歧", d
    d = dual(CK_MID, SOC_HIGH)
    assert d["grade"] == "中", d


@pytest.mark.unit
def test_rule_single_sided_marks_which(dual):
    """只有一侧有数据时必须**点名**是哪个口径, 不能让用户以为看到了双侧。

    【★ 为什么要连 note 一起断言】第一版只断言了 `mode`, 而变异自检把 note 改成
      一句笼统的"库存档位已给出"时**护栏照样全绿**(M7 存活) —— 可 note 正是用户在
      界面上读到的那句话: 丢掉它就等于**悄悄退回单口径**, 用户再也看不到
      "缺的是社会库存" 这个关键信息。⇒ 判据要覆盖**给人看的那部分**。
    """
    d = dual(CK_LOW, SOC_NONE)
    assert d["grade"] == "低" and d["mode"] == "仅仓单口径", d
    assert "社会库存" in d["note"], "仅仓单口径的 note 未说明缺的是社会库存: " + d["note"]
    assert d["grade"] in d["note"], "note 未给出结果档位: " + d["note"]
    d = dual([], SOC_LOW)
    assert d["grade"] == "低" and d["mode"] == "仅社会库存口径", d
    assert "仓单" in d["note"], "仅社会库存口径的 note 未说明缺的是仓单: " + d["note"]
    assert d["grade"] in d["note"], "note 未给出结果档位: " + d["note"]


@pytest.mark.unit
def test_rule_no_side_gives_unknown(dual):
    d = dual([], SOC_NONE)
    assert d["grade"] == "不明" and d["mode"] == "无口径", d
    assert d["available"] == [], d


@pytest.mark.unit
def test_available_lists_present_sides(dual):
    assert sorted(dual(CK_LOW, SOC_LOW)["available"]) == ["仓单", "社会库存"]
    assert dual(CK_LOW, SOC_NONE)["available"] == ["仓单"]


# ===========================================================================
# B. 仓单 0 值: 保留原值 + 必须标注
# ===========================================================================
@pytest.mark.unit
def test_zero_is_kept_in_series_not_dropped():
    """★ 用户 2026-10-14 拍板: **保留** 0 参与分位(不剔除), 只加警示。

    剔除会改变分位分母、也会抹掉"集中注销"这一真实状态(项目已实测 PTA
    13435 → 0 → 1002)。本断言防止后人"顺手"把它改成过滤。
    """
    blk = qm._inv_ck_block(_rows([5, 4, 3, 0, 0, 0]), 180, "src")
    assert blk["latest"] == 0, "0 值被剔除/替换了"
    assert blk["count"] == 6, "0 值被排除出序列长度了"


@pytest.mark.unit
def test_zero_produces_warning_with_evidence():
    z = qm._inv_ck_zero_warn(_rows([5, 4, 3, 0, 0, 0]))
    assert z["warn"], "末位为 0 却没给警示"
    assert z["zero_streak"] == 3, z
    assert z["last_nonzero"] == 3 and z["last_nonzero_date"], z
    assert abs(z["zero_pct"] - 50.0) < 0.01, z
    # 措辞必须**不武断**: 不得断言"数据源故障"(0 也是部分品种的常态)
    assert "断供" not in z["warn"] and "故障" not in z["warn"], z["warn"]
    assert "结合社会库存" in z["warn"], z["warn"]


@pytest.mark.unit
def test_warning_silent_when_latest_nonzero():
    assert qm._inv_ck_zero_warn(_rows([5, 4, 3, 2, 1, 9]))["warn"] is None
    assert qm._inv_ck_zero_warn([])["warn"] is None


@pytest.mark.unit
def test_zero_warning_reaches_both_consumers(dual):
    """警示必须能通过 _inv_dual 传到消费端(否则前端拿不到)。"""
    d = dual([5, 4, 3, 0, 0, 0], SOC_LOW)
    assert d["warn"], "dual 没把 warn 抛出来"
    assert d["ck"]["zero_streak"] == 3


# ===========================================================================
# C. ★ 安全性质: 矛盾不进单边
# ===========================================================================
@pytest.mark.unit
@pytest.mark.parametrize("prof,basis", [
    ("低", "贴水"), ("高", "升水"), ("中", "贴水"), ("低", "升水"), ("不明", "贴水"),
])
def test_conflict_never_yields_single_side(prof, basis):
    """两口径背离时, 无论利润/基差怎么配, 都**不得**产出单边做多/做空。

    这是本次改动的核心安全性质 —— "库存项两票都不算"必须真的落空,
    而不是靠某个具体组合恰好没触发。故按参数组合穷举。
    """
    vd = fm._strategy_verdict("矛盾", prof, basis)
    assert "单边" not in vd["action"], (prof, basis, vd)


@pytest.mark.unit
def test_conflict_still_allows_non_single_side_verdict():
    """矛盾不是"降级"(数据是有的, 只是口径打架) —— 不应出现在 degraded 里。"""
    vd = fm._strategy_verdict("矛盾", "低", "贴水")
    assert vd.get("degraded") in (None, [],), vd
    assert vd["action"] == "部分共振(2/3 偏多)", vd


@pytest.mark.unit
def test_low_and_high_still_trigger_single_side():
    """反向守卫: 别把"矛盾"的处理误伤到正常的低/高判定。"""
    assert fm._strategy_verdict("低", "低", "贴水")["action"] == "单边做多"
    assert fm._strategy_verdict("高", "高", "升水")["action"] == "单边做空"


# ===========================================================================
# C2. 工具函数同口径(防两份漂移)
# ===========================================================================
@pytest.mark.unit
def test_grade_helper_matches_fund_module():
    for r in (None, 0.0, 0.01, 0.34, 0.35, 0.36, 0.5, 0.64, 0.65, 0.66, 1.0):
        assert qm._inv_grade(r) == fm._grade(r), r


@pytest.mark.unit
def test_pct_helper_matches_fund_module():
    s = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    for cur in (1.0, 3.0, 6.0):
        assert qm._inv_pct(s, cur) == fm._pct_rank(s, cur), cur
    assert qm._inv_pct([1.0, 2.0], 1.0) is None


@pytest.mark.unit
def test_dir_helper_deadzone():
    assert qm._inv_dir(1.0) == "升" and qm._inv_dir(-1.0) == "降"
    assert qm._inv_dir(0.2) == "平" and qm._inv_dir(None) == "不明"


@pytest.mark.unit
def test_chg_skips_zero_placeholders():
    """方向计算必须跳过 0(与 _inventory_cycle._last_positive 同理由)。"""
    assert qm._inv_chg([10, 9, 8, 0, 0, 12], 1) is not None
    assert qm._inv_chg([0, 0, 0, 0, 0, 0], 1) is None


# ===========================================================================
# C3. 国君库存取数(monkeypatch 掉网络)
# ===========================================================================
@pytest.mark.unit
def test_fetch_gtja_inventory_prefers_social_label(monkeypatch):
    from tradingagents.dataflows import gtja_api

    rows = [
        {"dataType": "xx_工厂库存", "dataDate": "2026-01-01", "inventoryValue": 5},
        {"dataType": "xx_社会库存", "dataDate": "2026-01-01", "inventoryValue": 9},
        {"dataType": "yy_社会库存", "dataDate": "2026-01-01", "inventoryValue": 1},
    ]
    monkeypatch.setattr(gtja_api, "fetch_dataset", lambda *a, **k: rows)
    d = qm._fetch_gtja_inventory("XX")
    assert d["ok"] and d["label"] == "社会库存", d
    assert d["qty"] == [9.0], "品种过滤或口径优先级出错"
    assert d["latest_date"] == "2026-01-01" and d["count"] == 1


@pytest.mark.unit
def test_fetch_gtja_inventory_skips_non_numeric(monkeypatch):
    from tradingagents.dataflows import gtja_api

    rows = [
        {"dataType": "xx_社会库存", "dataDate": "2026-01-01", "inventoryValue": "N/A"},
        {"dataType": "xx_社会库存", "dataDate": "2026-01-02", "inventoryValue": None},
        {"dataType": "xx_社会库存", "dataDate": "2026-01-03", "inventoryValue": 7},
    ]
    monkeypatch.setattr(gtja_api, "fetch_dataset", lambda *a, **k: rows)
    d = qm._fetch_gtja_inventory("XX")
    assert d["ok"] and d["qty"] == [7.0], d


@pytest.mark.unit
def test_fetch_gtja_inventory_empty_and_error_paths(monkeypatch):
    from tradingagents.dataflows import gtja_api

    monkeypatch.setattr(gtja_api, "fetch_dataset", lambda *a, **k: [])
    d = qm._fetch_gtja_inventory("XX")
    assert not d["ok"] and "无国君自建库存数据" in d["error"], d

    def _boom(*a, **k):
        raise RuntimeError("net down")

    monkeypatch.setattr(gtja_api, "fetch_dataset", _boom)
    d = qm._fetch_gtja_inventory("XX")
    assert not d["ok"] and "RuntimeError" in d["error"], d


@pytest.mark.unit
def test_social_block_degrades_without_series(monkeypatch):
    monkeypatch.setattr(qm, "_fetch_gtja_inventory",
                        lambda code: {"ok": False, "error": "无数据"})
    b = qm._inv_social_block("XX")
    assert b["ok"] is False and b["error"], b


# ===========================================================================
# D. 四象限交叉验证
# ===========================================================================
@pytest.mark.unit
def test_cross_matrix():
    assert qm._inv_cross("升", SOC_HIGH)["mode"] == "双口径共振"
    assert "背离" in qm._inv_cross("升", SOC_LOW)["mode"]
    assert qm._inv_cross("升", SOC_MID)["mode"] == "口径分歧"
    assert qm._inv_cross("升", SOC_NONE)["mode"] == "仅仓单口径"
    assert qm._inv_cross("不明", SOC_LOW)["mode"] == "仅社会库存口径"


# ===========================================================================
# D2. 前端呈现契约
# ===========================================================================
@pytest.mark.unit
def test_frontend_stock_block_has_dual_rows():
    h = TPL.read_bytes().decode("utf-8")
    clean = re.sub(r"<!--.*?-->", "", h, flags=re.S)
    assert "const dualLine = (label, o)" in clean, "①卡库存块缺双行明细构造"
    assert "dualLine('社会库存', soc)" in clean, "缺社会库存行"
    assert "dualLine('交易所仓单', ckv)" in clean, "缺交易所仓单行"
    assert "inv.mode" in clean, "①卡未呈现口径关系(共振/背离)"
    assert "inv.grade === '低' ? '偏多'" in clean, "档位→方向词映射丢了"


@pytest.mark.unit
def test_frontend_shows_zero_warning():
    h = TPL.read_bytes().decode("utf-8")
    assert "ckv.warn" in h, "①卡未渲染仓单 0 值警示"
    assert "var(--amber)" in h.split("ckv.warn")[1][:220], "警示未用琥珀色(与普通灰字区分)"


@pytest.mark.unit
def test_frontend_cycle_card_has_social_and_cross():
    h = TPL.read_bytes().decode("utf-8")
    assert "const socLine = so.ok" in h, "四象限缺社会库存行"
    assert "const crossLine = xc.mode" in h, "四象限缺交叉验证行"
    assert "${socLine}${crossLine}" in h, "四象限正文未插入双口径两行"


@pytest.mark.unit
def test_frontend_old_single_source_wording_gone():
    """旧口径文案必须清除 —— 留着会让用户以为库存还是只看仓单。"""
    h = TPL.read_bytes().decode("utf-8")
    assert "库存(仓单分位)" not in h
    assert "库存(仓单) 分位" not in h


@pytest.mark.unit
def test_frontend_container_ids_unchanged():
    """别的护栏按 id 断言, 改口径不得顺手改 id。"""
    h = TPL.read_bytes().decode("utf-8")
    for i in ("f-inv-badge", "chart-f-inv", "f-cycle-body",
              "f-strategy-body", "f-strategy-score", "f-strategy-badge"):
        assert ('id="%s"' % i) in h, i


@pytest.mark.unit
def test_frontend_placeholder_markers():
    """★ 模板里出现 `$ {`/全角标点混排这类笔误会让 JS 静默出错, 但 node --check 能查;
    这里补一条"没有占位残渣"的轻量守卫。"""
    h = TPL.read_bytes().decode("utf-8")
    assert "$ {" not in h, "模板串里出现 `$ {` 空格残渣"
    assert "undefined}" not in h.split("const invSub")[1][:600], "库存块附近有 undefined} 残渣"


# ===========================================================================
# E. 社会库存**序列**及其在 ④ 卡图上的呈现（2026-10-16 用户报"只有仓单的图"）
#    ⚠ 行为级验证（真 ECharts option 的网格/序列/阶梯对齐）在 probe/_e2e_soc_chart.mjs。
#      这里守**数据结构契约**与**口径纪律**（后端给不给序列、前端画不画、轴怎么分）。
# ===========================================================================
@pytest.mark.unit
def test_social_block_carries_series(monkeypatch):
    """★ `_inv_social_block` 必须回传**序列** —— 只给汇总值就画不出那条线（用户报的缺陷）。

    事故：图标题写着「仓单库存 · 社会库存双口径」，但图上只有仓单。根因**不是**没取到数
    （`_fetch_gtja_inventory` 早就返回 qty+dates），而是本函数把序列**丢了**。
    """
    monkeypatch.setattr(qm, "_fetch_gtja_inventory", lambda code: {
        "ok": True, "label": "社会库存", "qty": [10.0, 12.0, 11.0], "dates": ["2026-01-01", "2026-02-01", "2026-03-01"],
        "count": 3, "latest": 11.0, "latest_date": "2026-03-01", "source": "s", "span": "a~b",
        "chg_pct": 10.0, "dir": "升"})
    b = qm._inv_social_block("XX")
    assert b["ok"] is True
    rows = b.get("rows")
    assert isinstance(rows, list) and len(rows) == 3, "没回传序列（rows）：%r" % (rows,)
    assert rows[-1] == {"date": "2026-03-01", "qty": 11.0, "change": -1.0}, rows[-1]
    assert rows[0]["change"] is None, "首期没有前值，change 应为 None（不能记 0 冒充）"
    # 既有字段一个不少（本改动是纯增量）
    for k in ("latest", "latest_date", "pct_rank", "grade", "count", "source", "span", "chg_pct", "dir"):
        assert k in b, "丢了既有字段 %s" % k


@pytest.mark.unit
def test_social_series_window_by_date_not_count():
    """★ 序列窗口按**日期**切（最近 N 年），不按固定条数。

    【为什么】社会/工厂库存的频率**因品种而异**（实测周频≈52 点/年、月频≈11 点/年）⇒
    固定"最近 N 条"在月频品种上会跨十几年、在日频品种上又不够长。按日期切既省流量又不随频率漂。
    """
    assert qm._SOC_ROWS_YEARS >= 2, "窗口太短（图上那根轴是 180 天，需要覆盖到窗口起点之前的最后一个读数）"
    # 取不到日期 ⇒ 全传（宁可多传也不丢数据）
    bad = ["x", "y"]
    assert qm._social_keep(bad) == 2, "日期解析失败时应全量保留（不能静默丢）"
    assert qm._social_keep([]) == 0
    # 正常情况：只保留最近 N 年内的尾部
    import datetime as _dt
    today = _dt.date(2026, 10, 16)
    dates = [str(today - _dt.timedelta(days=30 * k)) for k in range(0, 200)]   # 由近到远? 不, 见下
    dates = sorted(dates)                                                     # 升序
    keep = qm._social_keep(dates)
    assert 0 < keep < len(dates), "没切窗口（keep=%d / %d）" % (keep, len(dates))
    cut = str(_dt.date(2026 - qm._SOC_ROWS_YEARS, 10, 16))
    assert all(d >= cut for d in dates[-keep:]), "保留了窗口外的点"
    assert len(dates) - keep == 0 or dates[-(keep + 1)] < cut, "切点不对"
    # ★ 还要断言**块里真的用了它** —— 只测函数自身的话，把块里那行改成 `keep = len(dates)`
    #   护栏照样绿（变异自检实测存活）⇒ 判据必须落到"调用点"。
    src = (Path(__file__).resolve().parent.parent / "quant_module.py").read_text(encoding="utf-8")
    assert "    keep = _social_keep(dates)" in src, \
        "_inv_social_block 没调用 _social_keep（窗口不生效 ⇒ 每次都回传上千个点）"


@pytest.mark.unit
def test_inventory_api_exposes_social_rows(monkeypatch):
    """接口必须把 `social.rows` 透出（前端才有东西可画）。

    【为什么把取数桩掉】本用例要验的是**装配**（接口有没有把 social 块原样透出），
      不是"国君数据集现在还连着" ⇒ 桩成受控序列：确定、秒回、无网络副作用。
      （真数据的端到端在 probe/_e2e_soc_chart.mjs，那边会断言"非空点数 > 0"。）
    """
    import os
    os.environ["AGENTSENSE_AUTH_DISABLE"] = "1"
    monkeypatch.setattr(qm, "_fetch_gtja_inventory", lambda code: {
        "ok": True, "label": "社会库存", "qty": [3.0, 4.0, 9.0],
        "dates": ["2026-01-01", "2026-02-01", "2026-03-01"],
        "count": 3, "latest": 9.0, "latest_date": "2026-03-01",
        "source": "stub", "span": "a~b", "chg_pct": 200.0, "dir": "升"})
    monkeypatch.setattr(qm, "_fetch_inventory", lambda code, days, batch=False: {
        "ok": True, "rows": [{"date": "2026-03-01", "qty": 100.0, "change": 5.0}],
        "latest": 100.0, "source": "stub", "count": 1})
    import web_app
    c = web_app.app.test_client()
    d = c.get("/api/quant/inventory?variety=CF&days=180").get_json()
    assert d.get("ok"), d
    so = d.get("social") or {}
    assert so.get("ok"), "该品种社会库存应可得"
    assert isinstance(so.get("rows"), list) and len(so["rows"]) == 3, \
        "social.rows 没透出（图上画不出社会库存）"
    r0 = so["rows"][0]
    for k in ("date", "qty", "change"):
        assert k in r0, "social.rows 缺字段 %s" % k
    assert d["inventory"]["rows"], "仓单行也应在（这条桩只为隔离网络，不改装配）"


@pytest.mark.unit
def test_frontend_passes_social_to_chart():
    """★★ **根因判据**：`loadFundInventory` 必须把 `d.social` 一起交给渲染函数。

    【为什么单列一条】后端给了序列、前端也写了双口径渲染，但只要这一行不传参，
    图上仍然只有仓单 —— 而这**不会报错**（函数有默认行为/少一个参数不报错）。
    判据必须落到"传参"这个具体动作上。
    """
    h = TPL.read_bytes().decode("utf-8")
    assert "_renderFundInventory(d.inventory || {}, d.social || {})" in h, \
        "loadFundInventory 没把 social 块交给渲染函数（图会退回只有仓单）"
    assert "_renderFundInventory(s, so) {" in h, "渲染函数签名没接收社会库存块"


@pytest.mark.unit
def test_frontend_chart_three_shapes():
    """★ 三形态齐备：双口径两栏 / 只有仓单（保持原单栏）/ 只有社会库存（降级也要画）。

    【为什么"只有仓单"必须保留原形态】BZ/CJ 等品种**国君库存完全没有** ⇒
    它们的观感应与改动前一致，而不是出现一张空着半边的双栏图。
    """
    h = TPL.read_bytes().decode("utf-8")
    i = h.index("_renderFundInventory(s, so) {")
    body = h[i:i + 9000]
    # 【2026-10-21】社会库存有 ≥2 个年份时走季节性图，下方只画仓单 ⇒ 判据变量改为
    #   `socForDual`（= 参与"同框对照"的社会库存；已有季节性时为空）。语义没变。
    assert "if (ckRows.length && !socForDual.length)" in body, "缺「只有仓单」分支（BZ/CJ 会变成半空的图）"
    assert "if (!ckRows.length)" in body, "缺「只有社会库存」分支（仓单常年为 0 时它常是唯一口径）"
    assert body.count("grid: [{") >= 1, "缺双栏 grid 定义"


@pytest.mark.unit
def test_frontend_axes_are_separate_per_caliper():
    """★★ 两口径**各用各的轴**（量纲不可比，实测同品种可差数量级）。

    若把社会库存挂到仓单那条轴上：EG（社会 13 / 仓单 6226）、TA（138.5 万 / 1002）
    这类品种的线会**贴地或冲顶**，图看着像坏了；更糟的是诱导读者比较大小。
    """
    h = TPL.read_bytes().decode("utf-8")
    i = h.index("_renderFundInventory(s, so) {")
    body = h[i:i + 9000]
    assert "type: 'line', xAxisIndex: 0, yAxisIndex: 0" in body, "注册仓单应走 yAxis 0"
    assert "type: 'line', xAxisIndex: 0, yAxisIndex: 1" in body, "★ 社会库存没挂到**另一条轴**上（量纲不可比）"
    assert "只看走势与背离" in body, "tooltip 没提示两口径量纲不同"
    # 轴名按口径各自着色（一眼分辨哪条轴属于谁）
    assert "yVal((so.label || '社会库存'), SOC_C, 0, true)" in body, "社会库存那条轴没按口径着色"


@pytest.mark.unit
def test_frontend_social_line_is_stepped_not_interpolated():
    """★★ 社会库存线是**阶梯**（`step:'end'`），且注释写明"绝不插值"。

    【为什么】它是周频（≈26 点/半年），而仓单轴是日频（≈119 点）。把周读数**插值**成日读数
    会造出根本不存在的数；阶梯表达"该读数一直有效到下一期"才是如实的。
    """
    h = TPL.read_bytes().decode("utf-8")
    # ⚠ 范围要**从方法上方的注释块起算** —— 口径纪律（"绝不插值"）写在方法头之上，
    #   只从 `_renderFundInventory(s, so) {` 起算会漏（第一版就这么写，误报缺说明）。
    i = h.index("// 【功能】渲染「仓单库存 · 社会库存双口径」图")
    body = h[i:i + 10000]
    # ⚠ 判据必须钉**双口径那条**社会库存线 —— `step: 'end'` 在「只有社会库存」那个降级分支里
    #   也出现一次 ⇒ 只查 `"step: 'end'" in body` 会被"另一处"骗过（变异自检实测存活）。
    #   故断言"该 series 的完整片段"（行名 + 轴 + 数据 + 阶梯 连在一起）。
    # ⚠ 也不能用 `\n` 直接拼（模板是 CRLF）⇒ 用 `\s*` 吃掉行尾与缩进。
    assert re.search(
        r"name: '社会库存', type: 'line', xAxisIndex: 0, yAxisIndex: 1, data: _step\('qty'\),\s*"
        r"showSymbol: false, step: 'end',", body), \
        "★ 双口径那条社会库存线不是阶梯（会被插值成日频假读数）"
    assert "绝不插值" in body, "缺「不插值」的口径说明（后人容易「顺手」改成 smooth）"
    # 对齐语义：取"最后一个 ≤ 该日期的读数"
    assert "while (k < socForDual.length && String(socForDual[k].date) <= String(dates[i]))" in body, \
        "阶梯前推的判据不对（可能把未来读数提前画上）"


@pytest.mark.unit
def test_frontend_social_change_bars_are_not_stepped():
    """★ 社会库存的**变化**柱不摊成每天（周度变化前推成 7 根等高柱 = "天天变化这么多"，误导）。"""
    h = TPL.read_bytes().decode("utf-8")
    i = h.index("_renderFundInventory(s, so) {")
    body = h[i:i + 9000]
    assert "const socChg = new Array(dates.length).fill(null);" in body, "缺社会库存变化柱"
    assert "落在\"该读数之后的首个仓单日\"" in body or "落在“该读数之后的首个仓单日”" in body \
        or "首个仓单日" in body, "缺「变化只落在首个交易日」的说明"
