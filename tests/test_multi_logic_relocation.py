# -*- coding: utf-8 -*-
"""「多空逻辑归位 + 截面排行折叠 + 两处分数」回归护栏（2026-10-14 用户要求）。

【用户三项决策】
  A. 技术面量化里仍属《期货多空逻辑》口径的功能 → 归并到基本面量化
     · 「量化信号研判」整卡   · 体系方向票里的**多空逻辑系**   · 5 个多空逻辑扩展块
  B. 「多品种量化截面排行」从单品种详情 → 总览页**折叠模块**（默认收起、展开才拉数）
  C. 两处分数：技术面详情顶部「多空计分」（技术面口径）；① 三指标策略卡内「基本面信号计分」

【本文件锁死（每条都对应一次真实事故或一次真实误判风险）】
  A1. ★ 迁移的**头号坑**：品种源必须换成目标页的下拉（`_fundSym` → `#f-variety`）。
      留在 `#q-variety` 时，直接从基本面页进入 ⇒ 下拉未初始化 ⇒ `if (!sym) return`
      ⇒ 整卡恒空（本项目 2026-10-13 迁库存族时踩过一模一样的）。
  A2. 技术面 `_renderVerdict`：多空逻辑系票被排除、5 个多空逻辑扩展块全无；
      但**墨菲三块 + Carver 两块必须留着**（别把技术面的东西一起搬走）。
  A3. 基本面 `_renderFundVotes`：同一接口**切片**渲染（后端零改动、不重复计算）；
      无内容时给明确文案，**不留空白卡**。
  A4. `loadQuant` 必须摘掉 signals/ranking 调用（否则重复拉 / 给隐藏表格白拉）。
  B1. 折叠用 `max-height`（**不是 display:none**）+ 默认收起 + **展开时才拉数**。
  B2. 排行原 id 一个不改（`loadQuantRanking` 与护栏都按 id 取容器）。
  C1. 两处分数的**口径标签必须写明**：技术面那个要标"与总览截面排行同源"，
      否则用户会把技术面分数当成基本面指标（本轮曾因此专门澄清过一次）。
  C2. 基本面计分由 `loadQuantSignals` 的**同一次请求**写入（不额外拉）。
  D.  `_SEC_IDX_VER` 必须 +1（tab 内卡片有增删）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "web_template.html"

MULTI = ("ex.basis_calculation", "ex.calendar_spread", "ex.cross_commodity_spread",
         "ex.warrant_basis", "ex.reversal_signals")
TECH = ("ex.ma_crossover", "ex.volume_open_interest", "ex.trend_structure",
        "ex.shrinkage_ladder", "ex.estimate_reliability")


def _h() -> str:
    return TPL.read_bytes().decode("utf-8").replace("\r\n", "\n")


def _js(h: str) -> str:
    return h[h.index("<script>"):]


def _strip_c(s: str) -> str:
    return re.sub(r"<!--.*?-->", "", s, flags=re.S)


def _span(clean: str, tab: str) -> str:
    a = clean.index('<div id="tab-%s" class="tab">' % tab)
    d, pat, k = 0, re.compile(r"<div\b|</div>"), None
    k = a
    while k < len(clean):
        m = pat.search(clean, k)
        d += 1 if m.group(0) == "<div" else -1
        if d == 0:
            return clean[a:m.end()]
        k = m.end()
    raise AssertionError("未配平 tab-" + tab)


def _method(js: str, name: str) -> str:
    """按花括号配平取方法体（模板已压平，不能用缩进判据）。"""
    i = js.index(" " + name + "(")
    d, st = 0, False
    k = js.index("{", i)
    while k < len(js):
        ch = js[k]
        if ch == "{":
            d += 1
            st = True
        elif ch == "}":
            d -= 1
            if st and d == 0:
                return js[i:k + 1]
        k += 1
    raise AssertionError("未配平 " + name)


# ════════════════════════════════════════════════════════════════════
# A. 《期货多空逻辑》口径归位
# ════════════════════════════════════════════════════════════════════
def test_signal_card_moved_and_reads_fund_dropdown():
    """A1：信号卡迁到基本面页，且**品种源已换成 #f-variety**。"""
    h = _h()
    clean = _strip_c(h)
    q, f = _span(clean, "quant"), _span(clean, "fund")
    assert "量化信号研判" not in q, "技术面页仍有「量化信号研判」"
    assert 'id="q-signal-body"' in f and 'id="q-signal-badge"' in f, "信号卡不在基本面页"
    body = _method(_js(h), "loadQuantSignals")
    assert "this._fundSym()" in body, (
        "★ 头号坑：loadQuantSignals 仍读 #q-variety ⇒ 直接从基本面页进入时下拉未初始化"
        " ⇒ 首行 `if (!sym) return` ⇒ 整卡恒空"
    )
    assert "this._quantSym()" not in body, "loadQuantSignals 里还留着 _quantSym()"


def test_verdict_votes_exclude_multi_logic():
    """A2：技术面 `_renderVerdict` 排除多空逻辑系票，但保留表本身。"""
    js = _js(_h())
    v = _method(js, "_renderVerdict")
    # ⚠ 只断言 `"startsWith('多空逻辑')" in v` 太松 —— `_movedVotes` 那行本身就含该子串，
    #   于是"把排除条件删掉"（votes = _allVotes）也能通过（变异自检实测存活）。
    #   必须钉**排除表达式**本身。
    assert "!String(v.book || '').startsWith('多空逻辑')" in v, (
        "技术面未真正排除多空逻辑系票（只算了 _movedVotes 而没从 votes 里剔除）"
    )
    assert "_movedVotes" in v, "未记录被迁走的票（就无法给出指向说明）"
    assert "已迁至「基本面量化" in v, "未告知用户那些票去哪了（用户会以为丢数据）"
    assert "各体系方向票" in v, "整张票表被误删 —— 只该删多空逻辑系那几行"


def test_multi_logic_ext_blocks_relocated_not_deleted():
    """A2/A3：5 个多空逻辑扩展块**搬走**（不是删掉），且技术面保留墨菲/Carver 五块。"""
    js = _js(_h())
    tech_v = _method(js, "_renderVerdict")
    fund_v = _method(js, "_renderFundVotes")
    for k in MULTI:
        assert k not in tech_v, "技术面 _renderVerdict 仍含 %s" % k
        assert k in fund_v, "基本面 _renderFundVotes 缺 %s（内容被删了而不是搬走）" % k
    for k in TECH:
        assert k in tech_v, "技术面的 %s 被误搬走（它是墨菲/Carver 口径，不该动）" % k


def test_fund_votes_slices_same_payload_without_backend_change():
    """A3：基本面视图是**切片**同一接口（零后端改动、零重复计算），且无内容不留空白。"""
    h = _h()
    js = _js(h)
    body = _method(js, "loadFundVotes")
    assert "/api/quant/verdict" in body, "未复用 verdict 接口"
    assert "?variety=" in body, "未带品种参数"
    fund_v = _method(js, "_renderFundVotes")
    assert "startsWith('多空逻辑')" in fund_v, "未按 book 前缀取多空逻辑系"
    assert "暂无多空逻辑口径" in fund_v, (
        "无内容时没有明确文案 ⇒ 会留一张空白卡，用户以为页面坏了"
    )


def test_loadquant_dropped_moved_loaders():
    """A4：`loadQuant` 摘掉 signals/ranking（否则重复拉 / 给隐藏表格白拉）。"""
    js = _js(_h())
    body = _method(js, "loadQuant")
    code = re.sub(r"//[^\n]*", "", body)
    assert "this.loadQuantSignals();" not in code, "loadQuant 仍调 signals（两页各拉一次）"
    assert "this.loadQuantRanking(" not in code, (
        "loadQuant 仍调 ranking ⇒ 会给已折叠(隐藏)的表格白拉全池 21 品种 × 多源"
    )
    lf = _method(js, "loadFund")
    assert "this.loadQuantSignals();" in lf, "loadFund 未接 signals"
    assert "this.loadFundVotes();" in lf, "loadFund 未接 loadFundVotes"


# ════════════════════════════════════════════════════════════════════
# B. 截面排行 → 总览折叠模块
# ════════════════════════════════════════════════════════════════════
def test_rank_moved_to_overview_collapsible():
    """B1：排行在总览页、可折叠、默认收起、展开才拉数。"""
    h = _h()
    clean = _strip_c(h)
    ov = clean[clean.index('<div id="q-overview-view">'):][:20000]
    assert 'id="q-rank-table"' in ov, "排行表不在总览页内"
    dv = clean[clean.index('<div id="q-detail-view"'):][:20000]
    assert "多品种量化截面排行" not in dv, "单品种详情视图内仍有排行表"
    assert 'id="q-rank-body" data-open="0"' in h, "折叠容器不是默认收起"
    assert "max-height:0;overflow:hidden" in h, (
        "折叠未用 max-height —— 用 display:none 会让内部元素量不到尺寸"
    )
    js = _js(h)
    tg = _method(js, "toggleQovRank")
    assert "display:none" not in re.sub(r"//[^\n]*", "", tg), "toggleQovRank 用了 display:none"
    assert "this._qovRankLoaded" in tg and "this.loadQuantRanking(0)" in tg, (
        "★ 未做惰性加载 —— 首拉十余秒，收起状态不该占后台配额"
    )
    assert "this._qovRankOpen =" in tg, "折叠状态未记录（重进总览会丢）"
    # ★ 交互必须**真的绑上** —— 本轮实测踩到：只在 HTML 里写了 id、忘了绑事件，
    #   于是 `toggleQovRank` 定义得好好的、四条静态断言全过，**点标题却毫无反应**。
    #   静态护栏抓不到这类，是浏览器 E2E 抓出来的；这里补一条静态兜底。
    assert 'onclick="App.toggleQovRank()"' in h or "q-rank-head').onclick" in js, (
        "★ #q-rank-head 没有绑定点击事件 ⇒ 函数存在但点了没反应（本轮真实踩过）"
    )


def test_rank_ids_preserved():
    """B2：排行原 id 一个不改（loadQuantRanking 与护栏按 id 取容器）。"""
    h = _h()
    for cid in ("q-rank-table", "q-rank-metric", "q-rank-order", "q-rank-note", "q-rank-badge"):
        assert 'id="%s"' % cid in h, "排行 id 被删/改名: %s" % cid


# ════════════════════════════════════════════════════════════════════
# C. 两处分数（口径必须写明 —— 本轮曾专门澄清过一次）
# ════════════════════════════════════════════════════════════════════
def test_detail_score_is_technical_scope_and_labelled():
    """C1：技术面详情顶部的分数要标明"与总览截面排行同源"，否则会被当成基本面指标。"""
    h = _h()
    assert 'id="q-detail-score"' in h, "详情顶部没有计分徽标容器"
    js = _js(h)
    fn = _method(js, "_updateDetailScore")
    assert "与总览「截面排行」同源" in fn, "没有口径标注（用户会误读成基本面分数）"
    assert "_qovData" in fn, "未复用总览已加载的数据（会多打一次全池排行请求）"
    # 两处调用：点行进详情 + 切品种
    assert "this._updateDetailScore(code);" in js, "qOpenDetail 未调用"
    assert "this._updateDetailScore(sym);" in js, "loadQuant 未调用"


def test_strategy_card_score_is_fund_scope():
    """C2：① 卡的分数是**基本面**口径，且由同一次 signals 请求写入。"""
    h = _h()
    assert 'id="f-strategy-score"' in h, "① 三指标策略卡没有信号计分行"
    assert "基本面信号计分" in h, "① 卡计分未标明是基本面口径"
    body = _method(_js(h), "loadQuantSignals")
    assert "f-strategy-score" in body, (
        "计分不是由 loadQuantSignals 写入 ⇒ 要么两处数字不一致，要么多打一次请求"
    )
    # 标签要说明它是哪几个维度、以及利润为何不计分
    assert "库存+基差" in body and "利润" in body, "① 卡计分未说明口径构成"


def test_sec_index_version_bumped():
    """D：tab 内卡片有增删 ⇒ `_SEC_IDX_VER` 必须 +1。

    【2026-10-16】新增「全品种基本面截面表」并把基本面页拆成两视图 ⇒ 27 升 28；
    同日删「价格-库存关联分析」小模块 ⇒ 29 升 30。
    """
    h = _h()
    # 【2026-10-16】删「价格-库存关联分析」小模块 ⇒ 29 升 30；
    #   同日新增「采集通道状态巡检条」⇒ 30 升 31。
    # 【2026-10-21】基本面页重构为 A/B 两层 ⇒ 再 +1
    # 【2026-10-21】A9 数据计算面板迁入 ⇒ 再 +1
    assert "_SEC_IDX_VER: '33'," in h, "_SEC_IDX_VER 未随本次卡片增删 +1"
    assert "_SEC_IDX_VER: '32'," not in h, "旧版本号残留"
