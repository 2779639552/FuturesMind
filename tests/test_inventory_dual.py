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
