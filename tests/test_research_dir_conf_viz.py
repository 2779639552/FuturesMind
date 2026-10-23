# -*- coding: utf-8 -*-
"""研报「方向 / 置信度」可视化的护栏（2026-10-16 用户要求）。

【用户要求原话】
  研报总结部分：观点总表中方向/置信度部分可视性优化（颜色）如置信度部分使用类似进度条的方法
  提升可视化程度，方向可以分看多（红），与看空（绿）。

【用户确认的两点】
  ① 范围 = **两处一起统一**（「当日观点总表」+「逐品种观点总览」）—— 同一份研报在两处必须同色；
  ② 置信度进度条颜色 = **强度色（高=红）**。

【本文件守什么】颜色语义、定位方式、幂等、以及三个"会静默出错"的点：
  · 方向配色**反了**（看多绿/看空红）—— 本项目原来就是这么写的，且**两处不一致**；
  · 后端 markdown 表格的后处理会**所有表都插一遍**（列序号写死 ⇒ 列序一变就错位）；
  · 缺失值被画成 0% 空进度条 ⇒ 把"研报没给置信度"读成"给了个 0 分"（编造数据）。
    ⚠ 行为级验证（真 ECharts/DOM 计算样式）在 probe/_e2e_dir_conf_viz.mjs。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "web_template.html"


def _h() -> str:
    return TPL.read_bytes().decode("utf-8")


# ============================================================ A. 颜色语义（核心诉求）
@pytest.mark.unit
def test_dir_chip_css_follows_china_convention():
    """★★ 方向 chip 三态配色必须是 看多=红 / 看空=绿 / 中性=琥珀（中国市场惯例）。

    【为什么单列一条】这是用户这次改动的**原始诉求**，而且项目原来的写法是**反的**
      （.badge-bull 用绿、.badge-bear 用红）⇒ 任何人"顺手照抄旧代码"就会把语义改回去，
      而这类错误**不会报错**、界面上也不明显（用户只会觉得"看着还是不对"）。
    """
    h = _h()
    i = h.index(".dir-chip.up")
    seg = h[i:i + 360]
    assert ".dir-chip.up { background:var(--red-bg); color:var(--red); }" in seg, "看多不是红色"
    assert ".dir-chip.down { background:var(--green-bg); color:var(--green); }" in seg, "看空不是绿色"
    assert ".dir-chip.flat { background:var(--amber-bg); color:var(--amber); }" in seg, "中性不是琥珀色"


@pytest.mark.unit
def test_overview_chips_flipped_to_china_convention():
    """★★ 「逐品种观点总览」的 chip 配色也要跟着反色（用户确认"两处一起统一"）。

    【为什么】同一个品种在「总表」与「总览」两处都显示方向；只要一处没改，
      用户就会看到**同一份研报两种颜色**，反而比不改更困惑。
    """
    h = _h()
    assert "x === '看多' ? 'var(--red-bg)'" in h, "总览的看多底色不是红"
    assert "x === '看空' ? 'var(--green-bg)'" in h, "总览的看空底色不是绿"
    # 旧的反色写法必须**全部消失**（否则是"两套并存"，谁生效取决于出现位置）
    assert "x === '看多' ? 'var(--green-bg)'" not in h, "★ 仍有旧的反色写法残留"
    assert "x === '看多' ? 'var(--green)'" not in h, "★ 仍有旧的反色写法残留（文字色）"


@pytest.mark.unit
def test_conf_cell_uses_intensity_scale():
    """★ 置信度进度条用**强度色**（用户确认）：高=红 / 中=琥珀 / 低=绿。

    【为什么钉阈值】阈值改了会静默改变所有行的观感；且本项目另有「逐品种总览」的 confCell
      也有一份同样的分档 —— 两处必须一致，否则同一份研报在两处"颜色档位不同"。
    """
    h = _h()
    assert "const col = c >= 0.7 ? 'var(--red)' : c >= 0.4 ? 'var(--amber)' : 'var(--green)';" in h, \
        "逐品种总览的置信度分档不是「高红/中琥珀/低绿」"
    # 总表后处理里的分档（百分数口径）要与之等价：70 / 40
    i = h.index("_vizConfCell(td, n) {")
    body = h[i:i + 1400]
    assert "v >= 70 ? 'var(--red)'" in body and "v >= 40 ? 'var(--amber)'" in body, \
        "总表的置信度分档与总览不一致（70/40 百分数档）"


@pytest.mark.unit
def test_conf_tooltip_says_not_win_rate():
    """★ 置信度必须有"这不是行情胜率"的说明 —— 否则用户会把"高置信"读成"看涨"。

    【为什么】表头原本就有这句说明（`不是行情胜率`），改成进度条后**信息不能丢**：
      数字本身不解释语义，进度条更长只会强化"这是强度/胜率"的误读。
    """
    h = _h()
    assert "不是行情胜率" in h, "置信度的 tooltip 丢了「不是行情胜率」"
    # 两处都要有（总表的后处理 + 总览的 confCell）
    i = h.index("_vizConfCell(td, n) {")
    assert "不是行情胜率" in h[i:i + 1400], "总表的进度条没有语义说明"
    j = h.index("const confCell = c => {")
    assert "不是行情胜率" in h[j:j + 900], "总览的进度条没有语义说明"


# ============================================================ B. 后处理：定位与边界
@pytest.mark.unit
def test_decorate_locates_columns_by_header_text():
    """★★ 必须按**表头文本**定位列，不能写死列序号。

    【为什么】观点总表的列是后端拼的（品种/名称/发行方/方向/置信度/一句话/头寸/研报数/有新研报），
      以后加一列（本项目 2026-09-11 就加过「头寸建议」）序号就整体平移 ⇒
      写死 index 会把 chip 画到"发行方"列上，**不报错、只是错位**。
    """
    h = _h()
    i = h.index("_decorateViewTable(root) {")
    body = h[i:i + 1200]
    assert "th.textContent" in body, "没有按表头文本找列"
    assert "'方向'" in body and "'置信度'" in body, "没按「方向」「置信度」两个表头文本定位"
    assert "findIndex(" in body, "没用 findIndex 定位（可能写死了列序号）"


@pytest.mark.unit
def test_decorate_only_touches_view_table():
    """★ 只处理**同时**含「方向」与「置信度」表头的表 ⇒ 不误伤二~四节的其它表格。

    【为什么】总结正文里还有别的表（覆盖表、关键数据表…）。若只要求"含方向"或
      "含置信度"任一命中，就会把不相干的表也改掉；两列同时要求是最小充分判据。
    """
    h = _h()
    i = h.index("_decorateViewTable(root) {")
    body = h[i:i + 1200]
    assert "iDir < 0 || iConf < 0" in body, "没有「两列表头都命中才处理」的守卫"


@pytest.mark.unit
def test_decorate_is_idempotent():
    """★ 幂等：重复调用不能叠加样式。

    【为什么】切换日期/周报→日报会**重渲染**同一容器；若每次调用都再插一层 chip，
      会出现"chip 里套 chip"、进度条叠进度条（而且每切一次就厚一层）。
    """
    h = _h()
    i = h.index("_decorateViewTable(root) {")
    body = h[i:i + 1200]
    assert "dataset.vizDone" in body, "没有幂等标记"
    assert "vizDone === '1'" in body and "return" in body, "幂等标记没有真的用于提前返回"
    # 单元格级也要防重复包裹（同一格被处理两次）
    # ⚠ 别用固定窗口 h[k:k+1500] 取方法体：本轮 _vizDirCell 变长后会**跨进 _vizConfCell**，
    #   那样"在 A 方法里查 B 方法才有的语句"照样通过（假绿）。
    #   改用**下一个方法签名**精确切片。
    for m, nxt in (("_vizDirCell(td, n) {", "_vizConfCell(td, n) {"),
                   ("_vizConfCell(td, n) {", "// 【功能】把容器内")):
        seg = h[h.index(m):h.index(nxt, h.index(m))]
        assert "innerHTML =" in seg, "%s 没有整体覆盖内容（可能追加式插入 ⇒ 叠加）" % m


@pytest.mark.unit
def test_missing_confidence_is_not_faked_as_zero():
    """★★ 缺失值必须原样保留，**不能**画成 0% 的空进度条。

    【为什么】研报常不给置信度（本项目约定显示「—(未给置信度)」，**不拿 0.5 冒充**）。
      一旦把「—」也渲染成进度条，0% 的空条会被读成"分析给了个 0 分" ⇒ **编造数据**。
    """
    h = _h()
    i = h.index("_vizConfCell(td, n) {")
    body = h[i:i + 1500]
    assert "raw === '—'" in body, "没有对「—」做早退"
    # 数值解析失败时应**原样文本返回**，而不是当 0 处理
    assert "return '<span style=\"color:var(--text-muted)\">' + this._escHtml(p) + '</span>';" in body, \
        "解析不出百分数时没原样保留（可能被当成 0）"


def _method_body(src: str, name: str, until: str) -> str:
    """取两个标记之间的方法体（含起点、不含终点）。

    【★ 为什么不能用固定长度窗口】`_vizDirCell` 与 `_vizConfCell` 只隔 ~617 字符，
      用 `src[i:i+1500]` 会**跨进下一个方法** ⇒ 断言在"A 方法里查 B 方法才有的语句"时
      **照样通过**（变异自检实测：把 `_vizDirCell` 的拆分成整格单值后仍然绿）。
      这类"窗口跨方法"的洞很难看出来，因为代码本身完全正确。
    """
    i = src.index(name)
    j = src.index(until, i)
    return src[i:j]


@pytest.mark.unit
def test_multi_source_split_so_divergence_visible():
    """★ 一行多家机构（`华泰 / 国泰君安`）要**逐个**渲染，分歧才看得见。

    【为什么】总表把同一品种的多家机构合在一行，方向用 ` / ` 分隔（如「看空 / 看多」）。
      若整格只渲染一个 chip，就会把"分歧"抹成一个颜色 —— 而分歧恰恰是这张表最该看的信息。
    """
    h = _h()
    dir_body = _method_body(h, "_vizDirCell(td, n) {", "_vizConfCell(td, n) {")
    conf_body = _method_body(h, "_vizConfCell(td, n) {", "// 【功能】把容器内")
    for body, tag in ((dir_body, "方向列"), (conf_body, "置信度列")):
        assert "split(/\\s*\\/\\s*/)" in body, "%s 没有按「 / 」拆开多家机构" % tag
        assert ".map(" in body, "%s 没有逐项渲染" % tag


@pytest.mark.unit
def test_decorate_wired_after_sector_menus():
    """★ 接线：必须在渲染完 md 之后调用，且放在「板块折叠菜单」之后。

    【为什么单列一条】定义了方法却**没有调用**（或调用点挂在别的流程里）是**静默失效**：
      页面照常渲染、只是方向/置信度没有可视化（本项目踩过同型：方法定义了、接线挂错 tab）。
    """
    h = _h()
    assert "this._decorateViewTable(el);" in h, "后处理没有被调用（方法写了但没用）"
    i = h.index("this._groupSectorMenus(el);")
    seg = h[i:i + 300]
    assert "this._decorateViewTable(el);" in seg, "没有接在「总结渲染完」这条链上"
    assert seg.index("_groupSectorMenus") < seg.index("_decorateViewTable"), \
        "调用顺序应在折折叠菜单之后（此时表格已在最终 DOM 里）"


@pytest.mark.unit
def test_bar_width_follows_value():
    """★ 进度条宽度必须由数值决定（长度是这里唯一表达强弱的量）。

    【为什么要紧】宽度若写成固定值/随机，进度条就退化成装饰 —— 看不出"谁更确信"。
    """
    h = _h()
    i = h.index("_vizConfCell(td, n) {")
    body = h[i:i + 1500]
    # ⚠ 字面量别多写结尾引号：实际代码是 `'width:' + v + '%;background:' + col`
    #   ⇒ 写成 `'width:' + v + '%'` 会命中 0（第一版就这么写的，属判据写错、产品没问题）
    assert "width:' + v + '%" in body, "总表进度条宽度没有绑定数值"
    j = h.index("const confCell = c => {")
    assert "width:${pct}%" in h[j:j + 900], "总览进度条宽度没有绑定数值"
