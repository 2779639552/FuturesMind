# -*- coding: utf-8 -*-
"""总表「发行方/方向/置信度」纵向逐机构对齐 + 观点口径放宽（2026-10-19）。

【本轮改动的四类"会静默出错"点，逐条钉住】
  ① **对齐靠固定行高**：三列是三个独立单元格，无法共享 grid 行；只有
     「每项 20px + 同 gap + td 顶部对齐」才能让第 k 项严格同行。
     任一处漏 height/漏 box-sizing/td 用默认 middle ⇒ **整列错位且不报错**。
  ② **家数以发行方为准**：方向/置信度少拆出一项时补**不可见占位**（visibility:hidden），
     不能用 display:none（高度塌陷会让后续项上移，反而更乱）。
  ③ **列宽规则必须限定总表**：`nth-child` 是全局的，直接改会把第二节
     「各家观点与逻辑」等**列序不同**的表一起压窄。
  ④ **解析只能看第一节**：第二节也是表格、首列也是品种代码；混进来会让
     「分歧根源」（如「数据口径不同」）**冒充观点并覆盖**第一节，中位数掉到 6 字。
"""
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _h() -> str:
    return (ROOT / "web_template.html").read_bytes().decode("utf-8").replace("\r\n", "\n")


def _app() -> str:
    return (ROOT / "web_app.py").read_bytes().decode("utf-8").replace("\r\n", "\n")


def _rule(src: str, sel: str) -> str:
    """取某条 CSS 声明块。用**全局定位**而非区间切片 —— 切片终点极易踩空（本轮踩了两次）。"""
    m = re.search(re.escape(sel) + r"\s*\{([^}]*)\}", src)
    assert m, "缺少 CSS 规则 %s" % sel
    return m.group(1).replace("\n", " ")


def _fn_body(src: str, sig: str, end_sig: str) -> str:
    """按**下一个已知锚点**精确切片（不用固定长度窗口 —— 那会跨进下一个方法）。"""
    i = src.index(sig)
    j = src.index(end_sig, i)
    return src[i:j]


# ==================================================== A. 三列纵向对齐（结构机制）
@pytest.mark.unit
def test_source_cell_exists_and_count_flows_to_other_columns():
    """发行方列必须走 _vizSourceCell，且**返回的家数**传给方向/置信度。"""
    h = _h()
    assert "_vizSourceCell(td) {" in h, "没有发行方列的纵向渲染方法"
    body = _fn_body(h, "_decorateViewTable(root) {", "// 【功能】长文本单元格")
    assert "_vizSourceCell(tds[iSrc])" in body, "总表循环里没调用发行方渲染"
    assert "this._vizDirCell(tds[iDir], n)" in body, "方向列没接收发行方家数"
    assert "this._vizConfCell(tds[iConf], n)" in body, "置信度列没接收发行方家数"


@pytest.mark.unit
def test_three_columns_share_same_row_height_contract():
    """★★ 对齐的**机制**：三项同类元素必须 20px + border-box，且三列 gap 相同。

    【为什么这条最重要】三个单元格各自独立排版，唯一能让第 k 项同行的办法就是
      **每项等高 + 同 gap**。改任一处（如 20px→auto、漏 box-sizing）都会让
      多机构行逐项累积偏移 —— 页面不报错，只是"看起来没对齐"。
    """
    h = _h()
    gaps = []
    for sel in (".rv-stack", ".dir-chips", ".conf-bars"):
        m = re.search(r"gap:(\d+)px", _rule(h, sel))
        assert m, "%s 没有 gap（三项之间没有统一间距，对齐无从谈起）" % sel
        gaps.append(m.group(1))
    assert len(set(gaps)) == 1, "三列 gap 不一致 ⇒ 第 k 项会逐项错开：%s" % gaps

    for sel in (".src-item", ".dir-chips > .dir-chip", ".conf-bars > .conf-bar"):
        block = _rule(h, sel)
        assert "height:20px" in block, "%s 没有固定 20px（高度随内容变 ⇒ 对齐失效）" % sel
        assert "box-sizing:border-box" in block, \
            "%s 缺 box-sizing ⇒ padding 把 20px 撑大、逐项累积偏移" % sel

    assert "vertical-align:top" in _rule(h, "td.rv-vstack"), \
        "三列 td 没设 vertical-align:top（默认 middle 会让内容不等的格中心对齐）"


@pytest.mark.unit
def test_missing_items_are_padded_with_invisible_ghosts():
    """★ 家数不齐（发行方 3、方向只拆出 2）时必须补**不可见**占位，且不是 display:none。"""
    h = _h()
    body = _fn_body(h, "_vizDirCell(td, n) {", "_vizConfCell(td, n) {")
    assert "Math.max(n || 0, chips.length)" in body, "方向列没有按发行方家数补齐"
    assert "ghost" in body, "补齐项没有 ghost 标记"

    conf = _fn_body(h, "_vizConfCell(td, n) {", "// 【功能】把容器内")
    assert "Math.max(n || 0, bars.length)" in conf, "置信度列没有按发行方家数补齐"

    ghost = _rule(h, ".src-item.ghost, .dir-chip.ghost, .conf-bar.ghost")
    assert "visibility:hidden" in ghost, "占位项不是不可见"
    assert "display:none" not in ghost, \
        "★ 用了 display:none ⇒ 高度塌陷、后续项上移，比不补齐还乱"


@pytest.mark.unit
def test_column_width_overrides_are_scoped_to_views_table():
    """★ 列宽重排只作用于总表：全局 nth-child 改宽会压坏第二节等列序不同的表。"""
    h = _h()
    for w in ("96px", "100px", "68px", "90px", "60px", "64px"):
        assert re.search(r"\.rv-views-tbl[^{]*\{[^}]*width:\s*" + w, h), \
            "总表专属列宽 %s 缺失" % w

    i = h.index("_decorateViewTable(root) {")
    seg = h[i:i + 1600]
    assert "classList.add('rv-views-tbl')" in seg, "没有给总表打 .rv-views-tbl 标记"
    assert seg.index("classList.add('rv-views-tbl')") < seg.index("_clampLongCell("), \
        "★ 打标在 clamp 之后 ⇒ 测量时列宽还没变，\"是否被裁\"判断会错"


# ==================================================== B. 解析：只能读第一节
@pytest.mark.unit
def test_parser_stops_at_section_two():
    """★★ 必须只解析第一节 —— 否则第二节的「分歧根源」会冒充并覆盖观点。"""
    s = _app()
    body = _fn_body(s, "def _parse_llm_views_table(", "def _parse_llm_conflict_views(")
    assert "二[、.．]" in body, "没有「二、」分节截断 ⇒ 第二节表格会被一起解析"
    assert "seg = body[:" in body, "没有把解析范围收敛到第一节"


@pytest.mark.unit
def test_parser_handles_multi_source_and_direction_synonyms():
    """★ 方向列校验：多机构（「中性/中性/中性/中性」）与「中性偏震荡」都必须通过。

    【两次踩坑记录】校验写 `len(x)<=6` ⇒ 多机构行全被挡（解析全空、静默回退 700+ 字原文）；
      改成逐项 `len(p)<=4` ⇒ 「中性偏震荡」(5 字) 又被挡。故最终用**句读**区分结构，
      长度只做宽松上限（8）。这里用**真函数**做行为验证，因为结构断言拦不住
      "校验过严 ⇒ 整行被跳过 ⇒ 静默回退"这类错误。
    """
    s = _app()
    body = _fn_body(s, "def _parse_llm_views_table(", "def _parse_llm_conflict_views(")
    assert "，。；,;" in body, "方向列校验没有排除句读（无法与第二节长文本区分）"
    assert "len(p) > 8" in body, "方向词长度上限不是 8（会挡掉「中性偏震荡」）"

    os.environ.setdefault("AGENTSENSE_AUTH_DISABLE", "1")
    sys.path.insert(0, str(ROOT))
    import web_app

    md = (
        "## 一、当日观点总表\n"
        "| 品种 | 发行方 | 方向 | 置信度 | 一句话核心观点 | 头寸建议 |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| SC | 华泰/永安/东证/国君 | 中性/中性/中性/中性 | 48%/52%/76%/68% | 低库存托底但供应回归，反弹后偏空 | 逢高布空 |\n"
        "| P | 华泰/永安 | 中性偏震荡/中性 | 55%/52% | 高产与弱出口压制，震荡寻底 | 观望 |\n"
        "\n## 二、观点对比与冲突\n"
        "| 品种 | 分歧状态 | 各家观点与逻辑 | 分歧焦点 | 分歧根源 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| SC | 多空对立 | 华泰 看多(72%)—供应收缩 | 投产节奏 | 数据口径不同 |\n"
        "| P | 单一观点 | 华泰 中性(55%)—库存 | — | 关注权重不同 |\n"
    )
    views, trades = web_app._parse_llm_views_table(md)
    assert "SC" in views and views["SC"].startswith("低库存托底"), \
        "多机构方向列（中性/中性/中性/中性）被误挡 ⇒ 该品种静默回退"
    assert "P" in views and views["P"].startswith("高产与弱出口"), \
        "「中性偏震荡」被误挡 ⇒ 该品种静默回退"
    for bad in ("数据口径不同", "关注权重不同"):
        assert bad not in views.values(), \
            "★ 第二节的「分歧根源」冒充了观点（分节截断失效）"


@pytest.mark.unit
def test_parser_reads_opinion_by_fixed_column_order():
    """★ 观点/头寸按**固定列序**取，不靠 -2/-1 猜列数（列数一变就错位）。"""
    s = _app()
    body = _fn_body(s, "def _parse_llm_views_table(", "def _parse_llm_conflict_views(")
    assert "cells[4]" in body, "观点没有按固定列序 cells[4] 取"
    assert "cells[5]" in body, "头寸没有按固定列序 cells[5] 取"


# ==================================================== C. 观点口径（prompt）
@pytest.mark.unit
def test_opinion_prompt_widened_but_still_condensed():
    """观点口径放宽到 60~100 字，但仍要求"合成一段"，并列出必覆盖品种。"""
    s = _app()
    assert "60~100 字" in s, "观点字数口径没放宽到 60~100"
    assert "合成一段" in s, "没要求合成一段（会退化成逐家罗列）"
    assert "不是**要求你照着分行输出" in s or "不是要求你照着分行输出" in s, \
        "没说明「素材按机构分行 ≠ 输出分行」"
    # ⚠ 不能只查 `"_variety_codes" in s` —— 变量**定义处**也含这串，
    #   删掉 prompt 里的插值仍会绿（本轮变异自检抓到的洞）。必须查插值表达式。
    assert "{len(_variety_codes)}" in s, \
        "没有把本次材料品种清单**插值**进 prompt（LLM 会漏品种 ⇒ 回退 700 字原文）"


@pytest.mark.unit
def test_prompt_has_no_negative_template_that_llm_copies():
    """★★ 禁止类示例会被 LLM 当成模板照抄 —— 观点段落里不得出现「方向(数字%)」示例。

    【实测】第一版为说明"不要逐家罗列"给了一个反例
      「永安 看多(58%)—出口配额紧；国泰君安 中性(58%)—柴油支撑」
      ⇒ 21/21 行**全部**照抄该格式。删掉反例、只留正面示例后才归零。
    """
    s = _app()
    i = s.index("【重点1】")
    seg = s[i:i + 1400]
    # 只查【重点1】这一段：第二节本来就需要「机构 方向(置信度)—逻辑」格式，不能误伤
    assert not re.search(r"看[多空]\s*[(（]\s*\d+\s*%", seg), \
        "观点口径段落里出现了「方向(数字%)」示例 ⇒ LLM 会照抄成逐家罗列"
    assert "不出现任何机构名称" in seg, "没有明确禁止机构名（会写成逐家清单）"
