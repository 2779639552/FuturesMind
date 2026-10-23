# -*- coding: utf-8 -*-
"""第二节「观点对比与冲突」：分歧状态列收窄 + 每家发行方独占一行（2026-10-19）。

【本轮钉住的三类"会静默出错"】
  ① **列宽是"相对份额"，不是绝对值**：`table-layout:fixed + width:100%` 下，
     各列定宽之和 < 表宽时浏览器**按比例摊开**。实测 62/120/110 被放大成 227/439/402（×3.66）。
     ⇒ 只调绝对值、不调份额，页面上看不出变化。
     ⇒ 而「分歧状态」原 120px 是全表**第二宽**的定宽，放大后成了 439px 的最宽列。
  ② **`<br>` 会被 marked 转义**：落盘 md 里是正常的 `<br>`（后端未转义），但 GFM 表格
     单元格里的 inline HTML 被 marked 当纯文本 ⇒ DOM 里是字面 `&lt;br>` ⇒ 多家机构挤在一段。
  ③ **单家行不该拆**：拆出单层 `.cf-row` 是无意义包裹（还会让"段数≠家数"的校验失真）。
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _h() -> str:
    return (ROOT / "web_template.html").read_bytes().decode("utf-8").replace("\r\n", "\n")


def _rule(src: str, sel: str) -> str:
    """取某条 CSS 声明块（全局定位，不做区间切片）。"""
    m = re.search(re.escape(sel) + r"\s*\{([^}]*)\}", src)
    assert m, "缺少 CSS 规则 %s" % sel
    return m.group(1).replace("\n", " ")


def _method(src: str, sig: str, end_sig: str) -> str:
    i = src.index(sig)
    j = src.index(end_sig, i)
    return src[i:j]


def _px(src: str, sel: str) -> int:
    m = re.search(r"width:\s*(\d+)px", _rule(src, sel))
    assert m, "%s 没有定宽" % sel
    return int(m.group(1))


# ============================================ A. 列宽：绝对 px + 唯一 auto 列
SEL_STATE = (".report-content table.rv-conflict-tbl th:nth-child(2),\n"
             ".report-content table.rv-conflict-tbl td:nth-child(2)")
SEL_LOGIC = (".report-content table.rv-conflict-tbl th:nth-child(3),\n"
             ".report-content table.rv-conflict-tbl td:nth-child(3)")


@pytest.mark.unit
def test_conflict_state_column_is_absolute_and_logic_column_absorbs_rest():
    """★★ 分歧状态必须是**绝对 px**，且观点列必须是 **auto** —— 两者缺一不可。

    【★ 为什么"只写小 px"不管用（2026-10-19 用户二次反馈）】
      `table-layout:fixed + width:100%` 下，若**所有列都有定宽**且总和 < 表宽，
      浏览器会把定宽**等比例放大**填满表宽。实测第一版把分歧状态设成 76px，
      页面实际仍是 **141px**（×1.86）—— 用户看到的"留白过大"依旧。
      ⇒ 必须有**恰好一列 width:auto** 吃掉剩余空间，其余列才会保持绝对 px。
      实测修好后：分歧状态 **76px**、观点与逻辑 **853px**。
    """
    h = _h()
    w_state = _px(h, SEL_STATE)
    assert w_state <= 90, "分歧状态定宽 %dpx 太大（4~5 个字够用即止）" % w_state
    # 观点列必须 auto —— 这是"其余列不被等比放大"的前提
    assert "width: auto" in _rule(h, SEL_LOGIC), \
        "★ 观点列不是 width:auto ⇒ 所有列都有定宽，浏览器会等比放大，分歧状态收窄无效"


@pytest.mark.unit
def test_conflict_table_has_exactly_one_auto_column():
    """★ 机制不变量：第二节**必须**有且仅有 1 个 auto 列。

    【为什么钉"恰好一个"】0 个 ⇒ 全部定宽 ⇒ 等比放大（用户反馈的问题原地复现）；
      多个 ⇒ 剩余空间被平分，观点列拿不到最大份额。
    """
    h = _h()
    block = h[h.index("/* ── 第二节「观点对比与冲突」列宽重排"):h.index("/* 每家机构独占一段")]
    # ⚠ ① 先剔除注释：说明文字里也有 `width:100%`，不剔会把它当成一条列宽声明。
    #   ② 用 `[ ]*` 而不是 `\s*`：避免转义层级把 `\s` 写坏（本轮实测被写成 `//s` ⇒ 恒不匹配）。
    code = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
    widths = re.findall(r"width:[ ]*([^;]+);", code)
    autos = [w for w in widths if w.strip() == "auto"]
    assert len(autos) == 1, "第二节的 auto 列应恰好 1 个，实际 %d 个（widths=%s）" % (len(autos), widths)
    # 其余列要么是 px 要么是百分比；不能有第二个 auto
    assert len(widths) >= 4, "列宽声明过少（应覆盖 4 个定宽列）"


@pytest.mark.unit
def test_conflict_width_rules_are_scoped_to_conflict_table():
    """★ 列宽规则必须限定第二节：`nth-child` 是全局的，裸改会压坏总表与其它表。"""
    h = _h()
    rules = re.findall(r"[^\n{}]*rv-conflict-tbl[^\n{}]*\{[^}]*width:\s*\d+px", h)
    assert rules, "没有第二节专属列宽规则"
    for r in rules:
        assert "nth-child" in r, "份额规则必须定位到具体列：%s" % r[:60]
    # 总表那套 .rv-views-tbl 不能被改掉
    assert ".rv-views-tbl" in h and "rv-conflict-tbl" in h, "两套作用域必须共存"


# ============================================ B. 每家一段（拆行）
@pytest.mark.unit
def test_wired_after_views_table():
    """必须接线，且放在总表后处理之后（两者用不同表头定位，互不干扰）。"""
    h = _h()
    assert "_decorateConflictTable(root) {" in h, "没有第二节后处理方法"
    body = _method(h, "const md = (d.content || '')", "} catch (e) { el.innerHTML")
    assert "this._decorateViewTable(el);" in body, "总表后处理不见了"
    assert "this._decorateConflictTable(el);" in body, "第二节后处理没有接线"
    assert body.index("_decorateViewTable(el)") < body.index("_decorateConflictTable(el)"), \
        "接线顺序应在总表之后"


@pytest.mark.unit
def test_locates_columns_by_header_text_not_index():
    """★ 按**表头文本**定位列，不写死序号（列顺序变了也不会切错列）。"""
    h = _h()
    body = _method(h, "_decorateConflictTable(root) {", "_splitSourceRows(td) {")
    assert "'分歧状态'" in body, "没有按「分歧状态」表头定位"
    assert "'各家观点与逻辑'" in body, "没有按「各家观点与逻辑」表头定位"
    assert "if (iState < 0 || iLogic < 0) return;" in body, \
        "★ 缺「两列表头都命中才处理」的守卫 ⇒ 会误伤其它表格"


@pytest.mark.unit
def test_unescapes_marked_and_splits_per_source():
    """★★ 必须同时处理**真 `<br>`** 与 **marked 转义后的 `&lt;br>`**。"""
    h = _h()
    body = _method(h, "_splitSourceRows(td) {", "_decorateViewTable(root) {")
    # ⚠ 必须只查**定义分隔符的那一行**：本方法注释里也写着 `&lt;br>`（用来说明为什么），
    #   整段 `in body` 匹配会恒真 ⇒ 变异"只认未转义 br"存活（本轮变异自检抓到的洞）。
    m = re.search(r"const BR = (.+);", body)
    assert m, "没有定义换行分隔符正则 BR"
    br = m.group(1)
    assert "&lt;" in br, "★ 分隔符没认 marked 转义后的 `&lt;br>` ⇒ 真实页面上切不开"
    # ⚠ 别断言 `<br` 字面量：正则写成 `(?:&lt;|<)br` 时 `<` 是**独立分支**（`<br` 不出现）。
    #   只需断言两种前缀都在：转义的 `&lt;` 与裸 `<`。
    assert "<" in br, "分隔符没认未转义的 `<br>`（缺裸 `<` 分支）"
    assert "split(BR)" in body, "没有按分隔符切分"
    # 只认 br 这一种标记（不要整段反转义，避免注入面）
    assert "innerHTML" in body, "没有把结果写回"
    assert re.search(r"cf-row", body), "没有生成 .cf-row 段"


@pytest.mark.unit
def test_single_source_row_is_not_split():
    """★★ 判据必须是「**真的有多家**」，不能是「有几个 <br>」。

    【2026-10-19 用户反馈】只有国泰君安一家时，名称/内容被拆成**三行**且段间有虚线 ——
    因为一家机构内部用 `<br>` 分段（理由①/理由②/风险）也会凑出 ≥2 段，
    原先的 `if (parts.length < 2) return;` 就把它当"多家"拆了 ⇒ **把一家伪装成三家**。
    改判据：至少 2 段以「机构名 + 方向」开头，才算多家。
    """
    h = _h()
    body = _method(h, "_splitSourceRows(td) {", "_decorateViewTable(root) {")
    # ★ 必须按"段首是不是机构名+方向"来数家数
    assert "const HEAD =" in body, "没有定义「段首机构名+方向」的识别正则"
    assert "heads = parts.filter" in body, "没有统计「段首像机构」的段数"
    assert "if (heads < 2)" in body, "★ 判据不是按家数（会按 <br> 段数误拆单家）"
    # ★ 单家分支：不拆段、不加分隔线，但要还原真换行
    # ⚠ 代码里有**两处** `if (heads < 2)`（① 兜底切分 ② 单家分支）⇒ 必须取**最后一处**，
    #   用 search 会命中兜底那段（那里本来就没有 join('<br>')，断言必然误红）。
    ms = re.findall(r"if \(heads < 2\) \{(.*?)\n\}", body, re.S)
    assert len(ms) >= 1, "缺少单家分支 `if (heads < 2)`（实际 %d 处）" % len(ms)
    single = ms[-1]
    assert "parts.join('<br>')" in single, \
        "★ 单家分支没有把转义的 <br> 还原成真换行（页面上会显示字面 <br>）"
    assert "cf-row" not in single, "★ 单家分支仍在生成 .cf-row（会把一家拆成多段）"
    assert "cf-cell" not in single, "★ 单家分支仍打 .cf-cell（会套上段间虚线）"
    assert "cfSplit === '1'" in body, "没有单元格级幂等标记"


@pytest.mark.unit
def test_conflict_decorate_is_idempotent():
    """重复调用不得叠加（切日期/类型会重渲染）。"""
    h = _h()
    body = _method(h, "_decorateConflictTable(root) {", "_splitSourceRows(td) {")
    assert "cfDone" in body, "没有表级幂等标记"
    assert "cfDone === '1'" in body and "return" in body, "幂等标记没有真的用于提前返回"


@pytest.mark.unit
def test_each_row_gets_a_separator_style():
    """段与段之间要有视觉分隔，否则「每家一行」读起来仍是一整块。"""
    h = _h()
    rule = _rule(h, ".report-content table td.cf-cell .cf-row + .cf-row")
    assert "border-top" in rule, ".cf-row 之间没有分隔线"
    assert "margin-top" in rule or "padding-top" in rule, ".cf-row 之间没有间距"


@pytest.mark.unit
def test_each_segment_is_sub_split_unconditionally():
    """★★ 必须在**每一段内部**再切一次，且不受 `heads` 条件限制。

    【2026-10-19 用户四次反馈】LLM 会漏写 `<br>`，把第三家粘在第二家尾部：
      「…己内酰胺累库华泰 看多(68%)—库存未回升…」
    旧实现的兜底切分只在 `if (heads < 2)` 里 —— 而"前两家有 `<br>`、第二三家粘连"时
    heads 已经是 2 ⇒ **永远不进兜底** ⇒ 华泰一直挂在国君尾部。
    """
    h = _h()
    body = _method(h, "_splitSourceRows(td) {", "_decorateViewTable(root) {")
    assert "flatMap" in body, "★ 没有对每段内部二次切分（粘连永远修不到）"
    # 二次切分必须在 heads 判断**之前**、且在顶层（不在任何 if 花括号内）
    i_split = body.index("flatMap")
    i_heads = body.index("const heads =")
    assert i_split < i_heads, "★ 二次切分在数家数之后 ⇒ 家数仍是错的"
    # 从方法开头到 flatMap 之间，花括号必须配平（= flatMap 不在条件分支里）
    seg = body[:i_split]
    # 方法签名本身带一个 `{` ⇒ 配平判据要 +1，否则恒红（本条第一版就写错了）
    assert seg.count("{") == seg.count("}") + 1, \
        "★ 二次切分被包在 if 分支里（只在特定条件下执行）"


@pytest.mark.unit
def test_split_anchor_uses_source_name_list_not_fuzzy_wildcard():
    """★★ 切分锚点必须是**明确的机构名词表**，不能用「2~8 个中文字符」这类模糊通配。

    【为什么】贪婪通配让「国泰君安 看空(70%)」在「国」「泰」「君安」三处都成立
      ⇒ 把机构名切成三段（用户反馈过"名称被拆成三行"）。
    """
    h = _h()
    body = _method(h, "_splitSourceRows(td) {", "_decorateViewTable(root) {")
    assert "const SRC =" in body, "没有机构名词表"
    assert "国泰君安" in body and "紫金天风" in body, "词表缺少长名（长名必须在前，避免子串抢先命中）"
    # SRC 里「国泰君安」必须排在「国泰」之前（否则「国泰」会先匹配，留下「君安…」残片）
    assert body.index("国泰君安") < body.index("|国泰|"), \
        "★ 词表顺序错：短名「国泰」排在长名「国泰君安」之前 ⇒ 会切出「君安…」残片"
    # 切分/判首都用词表构造，而不是模糊通配
    assert "new RegExp('(?=(?:' + SRC" in body, "切分锚点没用词表构造"
    # 不得再出现「2~10 个中文字符 + 方向」这种模糊锚点
    assert not re.search(r"\{[2-9],1?0?\}\s*\\s\*\(\?:看多", body), \
        "★ 仍存在模糊通配锚点（会切碎机构名）"


@pytest.mark.unit
def test_prefix_only_segments_are_merged_into_next():
    """★ 带序号的多家（「① 华泰 看多」）会被切出独立的「①」段 ⇒ 必须并入下一段。"""
    h = _h()
    body = _method(h, "_splitSourceRows(td) {", "_decorateViewTable(root) {")
    assert "prefixOnly" in body, "没有前缀归并（序号会单独成段，段数虚高）"
    assert "carry" in body, "没有把纯前缀段累积到下一段"
    # ⚠ 只查"名字存在"不够：把它改成 `if (false)` 时上面两条仍绿（本轮变异自检抓到的洞）
    assert "if (prefixOnly.test(seg))" in body, \
        "★ 前缀归并的条件没有真正用于分支判断（写死 if(false) 也会通过）"
