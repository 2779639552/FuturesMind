# -*- coding: utf-8 -*-
"""基本面量化 · A/B 两层信息架构（2026-10-21 按效果图重构）。

【用户要求】「按照之前的效果图进行改造」——`docs/fund-ia-preview-2026-10-21.html`：
  A 数据底座（A1–A9，含仓单 A6.1–6.3） / B 信号研判（B1–B4），**只一套编号**。

【本文件守什么】
  1. 两区存在且顺序是 A 在前、B 在后；
  2. 编号唯一 —— 页面上不允许再出现第二套「① 仓单总览」；
  3. 拆分后所有图表容器 id 一个不少（拆卡最容易把 id 搬丢，而丢了不报错，图只是空白）；
  4. 分区跳转不能用 `window.App` 判空（`App` 是 const，不在 window 上 ⇒ 点了没反应且无报错）；
  5. 结构变了 `_SEC_IDX_VER` 必须 +1（否则老用户浏览器里残留僵尸索引项）。
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

TPL = Path(__file__).resolve().parent.parent / "web_template.html"

# 拆卡后必须仍在的容器（少一个就是静默空白图）
CHART_IDS = [
    "chart-f-soc", "chart-f-inv", "chart-f-basis", "chart-f-basispf", "chart-f-term",
    "chart-f-vd", "chart-f-matrix", "chart-f-sector", "f-strategy-body", "f-strategy-score",
    "f-cycle-body", "q-signal-body", "f-votes-body", "f-sector-panel",
    "wt-c1", "wt-c2", "wt-c3", "wt-c4", "wt-h-prod", "wt-h-wh",
]


def _tpl() -> str:
    return io.open(TPL, "r", encoding="utf-8", newline="").read()


def _detail() -> str:
    s = _tpl()
    i = s.index('<div id="f-detail-view"')
    # 到下一个顶层视图（overview 在其前面，这里取到 tab 结束即可）
    j = s.index('<div id="tab-warmup"', i) if '<div id="tab-warmup"' in s[i:] else s.index("</div>\r\n\r\n<!--", i)
    return s[i:j]


@pytest.mark.unit
def test_two_zones_exist_and_ordered():
    """A 区在 B 区之前，且两区都带说明文案（用户要的「数据/策略分开」）。"""
    d = _detail()
    assert 'id="f-zone-a"' in d and 'id="f-zone-b"' in d
    assert d.index('id="f-zone-a"') < d.index('id="f-zone-b"'), "B 区跑到 A 区前面了"
    assert "A · 数据底座" in d and "B · 信号与研判" in d


@pytest.mark.unit
def test_numbering_is_single_and_ordered():
    """★ 只允许一套编号：A1–A8 / B1–B4 / A6.1–6.3。

    【回退判据】仓单三卡若又写回 ①②③，页面上就同时存在两个「①」（本次重构要消灭的正是这个）。
    """
    s = _tpl()
    # 静态 h3：<h3>A1 …
    for n in ("A1 ", "A2 ", "A3 ", "A4 ", "A5 ", "A6.1 ", "A6.2 ", "A6.3 ", "A8 ",
              "B1 ", "B2 ", "B3 ", "B4 "):
        assert "<h3>%s" % n in s, "缺编号 %s" % n.strip()
    # A7 的卡是 JS 渲染的、A9 的 h3 带 style（flex 布局）⇒ 都按内容断言
    assert ">A7 研报基本面指标" in s, "缺编号 A7"
    assert "<span>A9 数据计算面板</span>" in s, "缺编号 A9"
    assert "<h3>① 仓单总览" not in s, "仓单区又用回第二套 ①（与三指标系列撞号）"
    assert "<h3>② 品种仓单细则" not in s
    assert "<h3>③ 仓单历史走势" not in s
    assert "<h3>① 三指标策略系统" not in s, "旧编号残留"


@pytest.mark.unit
def test_all_chart_containers_survive_split():
    """拆卡最常见的静默故障：容器 id 没搬过去 ⇒ 画布空白、控制台无报错。"""
    s = _tpl()
    for i in CHART_IDS:
        assert 'id="%s"' % i in s, "拆卡后丢了容器 %s" % i


@pytest.mark.unit
def test_jump_uses_typeof_not_window_app():
    """★ `App` 是 `const`，不在 `window` 上 ⇒ `window.App` 恒 undefined，点了没反应且不报错。

    这是本次真实踩到的坑（第一版就是这么写的，跳转静默失效）。
    """
    s = _tpl()
    assert "fJumpZone(z) {" in s, "缺分区跳转方法"
    assert "if (window.App && App.fJumpZone)" not in s, "又用 window.App 判空了（会静默失效）"
    assert "typeof App !== 'undefined' && App.fJumpZone" in s, "缺 typeof 形式的判空"


@pytest.mark.unit
def test_jump_targets_exist():
    """面包屑/吸顶条/分区条上的每个 data-zone 都要有对应分区。"""
    s = _tpl()
    zones = set(re.findall(r'data-zone="([ab])"', s))
    assert zones == {"a", "b"}, "跳转目标异常: %r" % zones
    for z in zones:
        assert 'id="f-zone-%s"' % z in s


@pytest.mark.unit
def test_sec_index_version_bumped():
    s = _tpl()
    assert "_SEC_IDX_VER: '33'," in s, "结构变了但索引版本没 +1（老浏览器会留僵尸索引项）"
    assert "_SEC_IDX_VER: '32'," not in s


@pytest.mark.unit
def test_breadcrumb_is_clickable_now():
    """旧的面包屑是一整串不可点文本，且漏了研报指标/板块相关。"""
    d = _detail()
    assert "单品种明细 · 三指标策略 / 估值驱动四象" not in d, "旧面包屑文本还在"
    assert 'class="f-jump" data-zone="a"' in d and 'class="f-jump" data-zone="b"' in d


@pytest.mark.unit
def test_zone_bands_are_not_cards():
    """★ 分区条不能是 `.card` —— 否则会被「小模块筛选」当成一个可折叠模块。

    【判据】`_secScan` 只把 `.card` 当模块；分区条一旦带 card 类，索引里会多出
    「A · 数据底座」这种点开什么都没有的条目。
    """
    s = _tpl()
    for z in ("f-zone-a", "f-zone-b"):
        m = re.search(r'<div([^>]*id="%s"[^>]*)>' % z, s)
        assert m, "找不到分区条 %s" % z
        assert "card" not in m.group(1).split("class=")[-1][:40] or 'class="f-zone' in m.group(1), \
            "分区条带了 card 类（会被当成小模块）"


@pytest.mark.unit
def test_zone_a_is_foldable():
    """效果图里的「整区折叠」：A 区所有块带 f-a-block，且有收起按钮与方法。"""
    s = _tpl()
    d = _detail()
    assert d.count("f-a-block") >= 12, "A 区块标记不足: %d" % d.count("f-a-block")
    assert 'id="f-fold-a"' in d, "缺收起按钮"
    assert "fFoldA(force) {" in s, "缺折叠方法"
    # A 区 8 张卡（A1–A5 + A6.1–6.3 + A8）与研报容器都要在折叠范围内
    for anchor in ("<h3>A1 ", "<h3>A5 ", "<h3>A6.1 ", "<h3>A6.3 ", "<h3>A8 "):
        i = d.index(anchor)
        head = d[d.rindex("<div", 0, i):i]
        assert "f-a-block" in head, "%s 未纳入折叠" % anchor


@pytest.mark.unit
def test_fold_resizes_after_expand():
    """★ 展开后必须 resize —— 隐藏期间容器宽高为 0，ECharts 不会自己复位。

    【判据】只看"有没有 resize 调用"，不看文案；这是本项目反复踩到的同款坑
    （2026-10-21 半宽改造：隐藏视图里初始化的图停在 100px）。
    """
    s = _tpl()
    i = s.index("fFoldA(force) {")
    body = s[i:i + 900]
    assert "_resizeAllCharts" in body, "展开后没 resize（图会空白/尺寸错）"
    assert "if (!fold)" in body, "只在展开时 resize，收起时不必"


@pytest.mark.unit
def test_a1_charts_are_half_width_in_grid():
    """★ A1 拆成独立卡后一度拉满 1572px（用户反馈「图表过大」）。

    正确做法：两张图放进 `grid-2` 各占一列（≈778px），**不能再挂 chart-half**
    —— 那会在 50% 的格子里再砍一半（`.chart-half` 是 max-width:50%）。
    """
    d = _detail()
    i = d.index('id="chart-f-soc"')
    head = d[max(0, i - 400):i]
    assert 'class="grid grid-2"' in head, "A1 的两张图没放进两列网格（会拉满整卡宽）"
    assert "chart-half" not in d[d.rindex("<div", 0, i):i + 120], "chart-f-soc 不该挂 chart-half（在 grid 里）"
    j = d.index('id="chart-f-inv"')
    assert "chart-half" not in d[d.rindex("<div", 0, j):j + 120], "chart-f-inv 不该挂 chart-half（在 grid 里）"


@pytest.mark.unit
def test_chart_axis_labels_have_room():
    """★ 旋转轴标签 + 底部图例需要留白，否则文字被画布裁掉（用户反馈「字体被遮挡」）。

    【判据】期限结构 / 加工利润 / 基差两图 / 仓单图 的 grid.bottom 不得小于 48。
    """
    s = _tpl()
    checks = {
        "期限结构": "grid: { left: 58, right: 46, top: 14, bottom: 52 }",
        "加工利润": "grid: { left: 65, right: 26, top: 16, bottom: 64 }",
        "基差走势": "grid: { left: 52, right: 52, top: 14, bottom: 54 }",
        "基差与现货期货": "grid: { left: 58, right: 18, top: 14, bottom: 54 }",
        "仓单走势": "grid: { left: 62, right: 52, top: 14, bottom: 54 }",
    }
    for name, needle in checks.items():
        assert needle in s, "%s 的 grid 没留出轴标签空间: %s" % (name, needle)


@pytest.mark.unit
def test_profit_chart_colors_are_literal():
    """★ ECharts 画在 canvas 上**不认 CSS 变量**：写 `var(--amber)` 会回落成浅灰。

    用户报障原话「A4 中图表的曲线颜色太浅」—— 表格里的圆点是 HTML 能解析，
    于是出现「表格有色、曲线是灰的」这种极难自查的现象。
    """
    s = _tpl()
    i = s.index("const colors = [")
    seg = s[i:s.index(";", i)]   # 只看这一行数组；后面的 gColor 用 var() 是给 HTML 用的，合法
    assert "var(--" not in seg, "配色里还有 CSS 变量（canvas 解析不了，会变灰）: %s" % seg.split("\n")[0]
    # 【2026-10-21】第一色由 #FF9F0A 换成 #EA580C：前者白底对比度只有 2.06（<3），
    #   亮色主题下曲线很淡。这里守"有可读的强调色"，不写死某一个十六进制值。
    assert "#EA580C" in seg or "#FF9F0A" in seg, "第一个口径的强调色丢了: %s" % seg.split("\n")[0]


@pytest.mark.unit
def test_a9_compute_panel_lives_in_fund_not_quant():
    """数据计算面板（含相关系数矩阵）2026-10-21 从技术面 ⑫ 迁到基本面 A9。

    【为什么要守】它是被 block 级搬走的，两边都可能"回退"：
      · 技术面若有人复原 ⑫，页面会有两张同 id 的卡（id 冲突 → getElementById 拿到错的那张）；
      · 基本面若有人把 A9 挪出 A 区，折叠时它不会跟着收起，还留在数据区外面。
    """
    s = _tpl()
    # tab-quant 区间内不许再出现这张卡
    q0 = s.index('id="tab-quant"')
    q1 = s.index('id="tab-fund"')
    assert 'id="q-compute"' not in s[q0:q1], "技术面页还有数据计算面板（应只在基本面 A9）"
    # tab-fund 区间内必须有，且带 A 区块标记（跟着整区折叠）
    f0 = s.index('id="tab-fund"')
    assert 'id="q-compute"' in s[f0:], "基本面页找不到 A9"
    assert '<div class="card f-a-block" id="q-compute">' in s, "A9 没挂 f-a-block（整区折叠会漏掉它）"


@pytest.mark.unit
def test_a9_follows_a8_in_dom_order():
    """A9 必须紧跟在 A8 之后 —— 两者都是"相关性核验"，主题相邻。"""
    d = _detail()
    assert d.index("<h3>A8 ") < d.index("A9 数据计算面板"), "A9 跑到 A8 前面了"
    assert d.index("A9 数据计算面板") < d.index('id="f-zone-b"'), "A9 掉到 B 区去了"


@pytest.mark.unit
def test_a9_reads_fund_variety_and_loads_with_fund():
    """品种源必须是 #f-variety，加载时机在 loadFund —— 否则在基本面页算的是技术面的品种。"""
    s = _tpl()
    assert "_computeSym() {" in s, "缺 _computeSym"
    i = s.index("_computeSym() {")
    body = s[i:i + 400]
    assert "f-variety" in body, "_computeSym 没优先读 #f-variety"
    assert "q-variety" in body, "缺 #q-variety 兜底（直接调 loadQuantCompute 时要用）"

    # loadQuantCompute 内部必须用 _computeSym
    j = s.index("async loadQuantCompute(force, reset) {")
    assert "this._computeSym()" in s[j:j + 400], "loadQuantCompute 还在用 _quantSym"
    # 基本面加载链里要触发它
    k = s.index("async loadFund() {")
    assert "this.loadQuantCompute();" in s[k:k + 2000], "loadFund 没触发 A9 加载"


@pytest.mark.unit
def test_ab_bubbles_toggle_visibility():
    """★ 2026-10-22：A/B 从「跳转锚点」改成「气泡切换」（用户要研报页那种胶囊）。

    守三件事，缺一条就退化成"两边同时显示"或"点了没反应"：
      1. B 区四张卡必须带 f-b-block（否则隐藏 A 时 B 区不会跟着出来）；
      2. 默认状态是 'a'（用户选的：进页面先看数据）；
      3. fJumpZone 必须是**切换**而不是滚动 —— 再点当前气泡要收起。
    """
    s = _tpl()
    d = _detail()
    assert d.count("f-b-block") == 4, "B 区应有 4 张卡带 f-b-block，实际 %d" % d.count("f-b-block")
    assert "_fZone: 'a'," in s, "缺默认状态 _fZone='a'"
    # 切换语义：点当前已激活的 → ''
    i = s.index("fJumpZone(z) {")
    body = s[i:i + 260]
    assert "this._fZone === z" in body and "''" in body, "fJumpZone 不是切换语义（点第二次应收起）"
    assert "_applyFZone" in body, "fJumpZone 没调用 _applyFZone"


@pytest.mark.unit
def test_apply_fzone_keeps_empty_semantics():
    """★ 空串在 _fZone 里**有语义**（= 两区都收起），不许被 `|| 'a'` 兜底成默认值。

    这是本项目反复踩的同一类坑（「空串有语义」的下拉禁补默认值）。
    第一版写成 `const z = this._fZone || 'a'`，点第二下会"收起又弹回 A"，
    而且因为没报错，只有手点时才发现。
    """
    s = _tpl()
    i = s.index("_applyFZone() {")
    body = s[i:i + 1500]           # 方法体约 1.2k，取 900 会截掉尾部的 hint 段
    assert "this._fZone || 'a'" not in body, "空串被兜底成 'a'（点两次收不起来）"
    assert "undefined" in body and "null" in body, "缺 undefined/null 的初值判断"
    # 两区都收起时要有引导语，否则整页只剩两枚气泡
    assert 'id="f-zone-hint"' in s, "缺两区收起时的引导提示"
    assert "f-zone-hint" in body, "_applyFZone 没同步引导提示的显隐"


@pytest.mark.unit
def test_ab_bubble_is_pill_with_inverted_active():
    """气泡视觉对齐研报页：胶囊 + 选中反色（不是原来的浅灰小开关）。"""
    s = _tpl()
    seg = s[s.index(".f-subnav{"):s.index(".f-subnav{") + 900]
    assert "border-radius:999px" in seg, "气泡不是胶囊形"
    assert ".f-subnav button.on{background:var(--text);color:var(--bg)" in seg.replace("\r\n", ""), \
        "选中态没有反色（与研报页不一致）"


@pytest.mark.unit
def test_fold_and_bubble_do_not_fight():
    """切到 A 时要解除整区折叠 —— 否则气泡选中 A、里面却是被折叠的空壳。"""
    s = _tpl()
    i = s.index("_applyFZone() {")
    body = s[i:i + 900]
    assert "fFoldA(false)" in body, "_applyFZone 没解除 A 区折叠"
