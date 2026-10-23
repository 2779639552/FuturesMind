# -*- coding: utf-8 -*-
"""「观点被截断」修复的护栏（2026-10-16 用户报障）。

【用户报障】部分观点被截断（如 10.14 周度中的 LU 的一句话核心观点）。

【实测链路】截断在**四层**，而且**串联**（改一层不够）：
      ① `_extract_key_opinion(seg, max_len=150)`   把函数默认的 360 收紧成 150
      ② `_merge_same_source_items(text_cap=200)`   合并层软上限
      ③ `_build_daily_views_table` 的 `op[:40]` + `view[:80]`   两层叠加
      ④ trade 侧同型（`trade[:60]` / `[:80]` / 延续文案 `[:45]`）
  实测（周报 LU，原文段落 398~418 字）：max_len=150 → 80~145 字；360 → 277~312 字。

【修法原则】**数据层完整 + 显示层收敛**：
  · 数据层不再丢信息（否则落盘 md / 导出 PDF 也丢）；
  · 显示层默认收 4 行 + 「展开全文」（保证可达、不把表格撑爆）。

【★★ 两个必须保留的约束（放开截断时最容易一起删掉）】
  1. 清洗：`\\n` → 空格（否则 markdown 表格被劈成两行）、`|` → `/`（否则后面列全错位）；
  2. **prompt 预算**：opinion 会进 LLM prompt，prompt 侧有 28000 限制
     ⇒ 放开显示**必须**配套"材料精简 + 按预算装载"，否则后面的品种材料被静默切掉。
     本项目原注释"21 品种 × 4 机构 × 200 字仍在 payload 28000 以内"就是这个约束的由来。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "web_app.py"
TPL = ROOT / "web_template.html"


def _app() -> str:
    return APP.read_text(encoding="utf-8")


def _h() -> str:
    return TPL.read_bytes().decode("utf-8")


# ============================================================ A. 数据层：不该再有硬截断
@pytest.mark.unit
def test_no_hard_truncation_in_opinion_pipeline():
    """★★ 观点链路上**不该再有**字数硬截断（这是用户报障的直接原因）。

    【为什么逐条钉】这些截断分散在 4 个函数里、而且是**串联**的
      （只改一层的话症状减轻但不消失，很容易误判为"已修好"）。
      实测字符串（都是本项目真实存在过的写法）：
        · `view[:60] + "…"`       LLM 观点
        · `op[:40]`               回退拼接（每家机构）
        · `view[:80] + "…"`       回退拼接（总）
        · `payload[:28000]`       ★ prompt 材料**静默硬切**
      放开后必须验证它们**都不在**了。
    """
    s = _app()
    # ★★ 用 **AST** 判定，不做文本匹配。
    #   【为什么】文本匹配（`pat not in s`）会被**注释与 docstring** 命中 ——
    #   而我在代码里写明了"此处原先 view[:60]…"这类说明（生产代码与测试都会引用旧写法）
    #   ⇒ 断言恒红。**注释/docstring 不是违规代码**，判据必须只看真实语法结构。
    #   （这是"判据要盯行为、不要盯文本"的一个具体形态；本项目在 mutation skill 里也有同型教训。）
    import ast
    caps = []          # (行号, 目标表达式, 截断长度)
    tree = ast.parse(s)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        sl = node.slice
        # ★★ 两种形态都要认：
        #     `x[N]`    → slice 是 ast.Constant(int)
        #     `x[:N]`   → slice 是 **ast.Slice(upper=Constant(int))**
        #   ⚠ 只认 Constant 会**漏掉 `x[:N]` 这种最常见写法**（变异自检实测：
        #     把 `view[:60]` 加回去，护栏照样全绿 —— M1/M2 存活就是这个原因）。
        n_val = None
        if isinstance(sl, ast.Constant) and isinstance(sl.value, int):
            n_val = sl.value
        elif isinstance(sl, ast.Slice):
            up = sl.upper
            if isinstance(up, ast.Constant) and isinstance(up.value, int):
                n_val = up.value
        if n_val is None:
            continue
        tgt = ast.unparse(node.value) if hasattr(ast, "unparse") else ""
        low = tgt.lower()
        if any(k in low for k in ("view", "opinion", "payload", "trade", "op")):
            caps.append((node.lineno, tgt, n_val))
    bad = [c for c in caps if c[2] in (40, 45, 60, 80, 28000)]
    assert not bad, (
        "★ 仍存在观点链路的字数硬截断（注释里的引用不算）：%s"
        % ["第 %d 行 %s[:%d]" % (ln, t, v) for ln, t, v in bad]
    )


@pytest.mark.unit
def test_opinion_extraction_uses_default_length():
    """★ `_extract_key_opinion` 的调用不该再收紧到 150。

    【实测依据】max_len=150 → 产出 80~145 字；360 → 277~312 字（默认即"不裁"）。
    ★ 另一条链路（逐品种观点总览）用的是默认值 ⇒ 收紧会导致**同一份研报在两个页签长度不同**。
    """
    s = _app()
    # ⚠ 别用 `"max_len=150" not in s` —— **我自己写的说明注释里就含这串**
    #   （"此处原为 max_len=150"），会让断言恒红（自指陷阱）。
    #   ⇒ 只查**调用行**：不含注释、必须带 `= _extract_key_opinion(` 与收尾括号。
    calls = [ln.strip() for ln in s.splitlines()
             if "_extract_key_opinion(" in ln and not ln.strip().startswith("#")]
    assert not [ln for ln in calls if "max_len=150" in ln], \
        "★ 仍有人为收紧到 150（观点被砍一半）：%s" % [ln for ln in calls if "max_len=150" in ln]
    assert any("opinion = _extract_key_opinion(seg, max_len=360)" in ln for ln in calls), \
        "观点提取没回到 360（= 函数默认 = 不额外收紧）"


@pytest.mark.unit
def test_merge_cap_decoupled_from_display():
    """★★ 合并层的 cap 必须**只服务于 prompt 预算**，不能同时截显示。

    【为什么】`_merge_same_source_items` 的产物 `items[i]['opinion']` **两用**：
      ① 总表「一句话核心观点」列（显示）② 进 LLM prompt（预算）。
      若 cap 卡在 200，显示就被迫截（周报 21 行里 14 行带 `…`）；
      若完全不卡，prompt 又可能超 28000。
      ⇒ 正解：`text_cap=0`（items 存**完整**，供显示）+ **prompt 组装处单独精简**。
    """
    s = _app()
    m = re.search(r"_merge_same_source_items\(recs, flatten=True, text_cap=(\d+), trade_cap=(\d+)\)", s)
    assert m, "找不到合并调用（签名变了？）"
    assert m.group(1) == "0", "★ text_cap 非 0 ⇒ 显示端仍被截（会重新出现「…」）"
    assert m.group(2) == "0", "★ trade_cap 非 0（trade 不进 prompt，放开零预算影响）"
    # prompt 侧必须另有精简
    i = s.index("_MAT_CAP = 320")
    seg = s[i:i + 700]
    assert "_MAT_CAP" in seg and "要点: {i['opinion'][:_MAT_CAP]}" in seg, \
        "★ prompt 材料没做精简 ⇒ 预算会被撑爆（放开显示的同时把预算丢了）"


@pytest.mark.unit
def test_cleaning_still_present():
    """★★ 放开截断时**绝不能连带删掉清洗** —— 这是"能删/不能删"的分界。

    · `\\n` 不清 → markdown 表格单元格里换行会把**整表劈成两行**；
    · `|` 不清 → 竖线是单元格分隔符，会把**后面的列全部挤歪**。
    两者都是"不报错、只是表坏了"的类型。
    """
    s = _app()
    i = s.index("def _build_daily_views_table(")
    j = s.index("\ndef ", i + 10)
    body = s[i:j]
    assert 'replace("\\n", " ")' in body, "★ 观点/头寸的换行清洗被删了（表会被劈开）"
    assert 'replace("|", "/")' in body, "★ 竖线清洗被删了（后续列会错位）"
    # ★★ 判据用**不变量**而不是计数：**凡是清 `|` 的地方必须同时清 `\n`**。
    #   【为什么不用"计数 >= N"】处数会随字段增删而变（本项目实测有 4 处，我一开始按 3 写
    #     ⇒ 删掉其中一处仍 >= 3 ⇒ 护栏放行，变异存活）。不变量判据与处数无关，
    #     还能抓住"以后新增字段又只清竖线"这种同型回归。
    offenders = [
        ln.strip()[:110] for ln in body.splitlines()
        if 'replace("|", "/")' in ln and 'replace("\\n", " ")' not in ln
    ]
    assert not offenders, (
        "★ 有字段只清竖线、不清换行（markdown 表会被劈成两行）：%s" % offenders
    )


# ============================================================ B. prompt 预算
@pytest.mark.unit
def test_fit_prompt_payload_is_module_level():
    """★ 预算装载逻辑必须是**模块级纯函数**（内嵌闭包无法从测试注入 ⇒ 护栏咬不住）。"""
    s = _app()
    assert "\ndef _fit_prompt_payload(" in s, "装载逻辑不是模块级函数（测试无法注入）"
    assert "_payload_txt = _fit_prompt_payload(payload.split(" in s, "调用点没改成模块级函数"


@pytest.mark.unit
def test_fit_prompt_payload_semantics():
    """★★ 装载语义三连：装得下全装 / 装不下**跳过而非停止** / 略过必须**显式标注**。

    【为什么"跳过"而不是"停止"】遇到一个长条目就停，会把**它后面所有短条目**一起挡掉
      （白丢大量本可放进去的材料）。
    【为什么要标注】静默截断会让 LLM 以为"这些品种今天没研报"⇒ 可能漏写甚至编造。
    """
    import importlib
    import os
    os.environ.setdefault("AGENTSENSE_AUTH_DISABLE", "1")
    wa = importlib.import_module("web_app")
    f = wa._fit_prompt_payload

    a = "【SC】A 方向:看多\n要点: " + "x" * 50
    b = "【TA】B 方向:看空\n要点: " + "y" * 50
    # ① 预算充足 ⇒ 全装、无标注
    r = f([a, b], budget=10000)
    assert a in r and b in r and "未列入" not in r
    # ② 预算不够 ⇒ 装得下的装、略过的标注
    r2 = f([a, b], budget=len(a) + 3)
    assert a in r2, "装得下的没装进去"
    assert "未列入" in r2, "★ 略过条目却没有标注（LLM 会以为那些品种没研报）"
    assert "TA" in r2.split("未列入")[-1], "标注里没写明是哪个品种"
    # ③ ★ 长条目**不该挡住**后面的短条目
    r3 = f(["【SC】" + "L" * 500, "【TA】短"], budget=len("【TA】短") + 8)
    assert "【TA】短" in r3, "★ 长条目把后面的短条目挡住了（应「跳过」而不是「停止」）"
    # ④ 边界：空输入 / 预算极小 都不能抛
    assert f([], budget=0) == ""
    assert bool(f(["【SC】" + "L" * 50], budget=5)), "全装不下时应返回标注而不是空/异常"


@pytest.mark.unit
def test_prompt_material_cap_keeps_budget_safe():
    """★ 材料精简的 cap 必须 **≥ 单篇产出上限(306)** 且**足够小不超预算**。

    【取值依据】放开后单篇 opinion 上限 306 字（max_len=360 实测）
      ⇒ cap < 306 会重新出现"单篇被砍"；
      ⇒ 且需 21 品种 × 4 机构 × cap < 28000。
    """
    s = _app()
    m = re.search(r"_MAT_CAP = (\d+)", s)
    assert m, "找不到材料 cap"
    cap = int(m.group(1))
    assert cap >= 306, "cap=%d < 单篇上限 306 ⇒ 单篇仍会被砍" % cap
    assert 21 * 4 * cap < 28000, "cap=%d ⇒ 21×4 条会超 28000 预算" % cap


# ============================================================ C. 显示层收敛
@pytest.mark.unit
def test_clamp_judges_by_actual_clipping_not_char_count():
    """★★ 长文本列判据 = **是否真被裁**（几何），而不是字符数阈值。

    【2026-10-19 改判据的理由】原判据是 `txt.length <= 120`，与列宽无关：
      宽列（观点 480px、每行约 30 字）4 行放得下也照样长按钮；
      窄列（头寸）放不下反而没有按钮 —— 判据和"是否真被裁"根本不是一回事。
    【几何判据的风险与兜底】折叠容器内 `clientHeight=0`，`scrollHeight>clientHeight`
      **恒真** ⇒ 会全量长按钮（这是原先改用字符数的原因）。现在取**失败方向安全**：
      测不出高度（clientHeight===0）时**一律给按钮** —— 最坏是多一个按钮，
      而反过来（该给却没给）才是"全文不可达"、正是用户抱怨的那种"又被截断"。
    """
    h = _h()
    i = h.index("_clampLongCell(td) {")
    j = h.index("_groupSectorMenus(container) {", i)      # 精确切片，别用固定窗口跨到下一个方法
    body = h[i:j]
    assert "scrollHeight" in body and "clientHeight" in body, "没有按「是否真被裁」判断"
    assert "box.clientHeight === 0" in body, "折叠容器内测不出高度时必须有兜底分支"
    assert "? true" in body, "★ 测不出高度时应**一律给按钮**（失败方向必须是内容可达）"
    # ⚠ 必须**排除注释行**：本测试的 docstring/注释里就写着旧判据
    #   `txt.length <= 120`（用来说明"为什么改"），整段 `in body` 匹配会恒红
    #   （自指陷阱，本轮第三次踩）。
    code = "\n".join(ln for ln in body.splitlines() if not ln.strip().startswith("//"))
    assert "txt.length <= 120" not in code, "仍按字符数判断 ⇒ 与列宽脱钩（宽列白加按钮）"


@pytest.mark.unit
def test_clamp_has_expand_entry_and_print_expand():
    """★★ 只做 clamp 而不给入口 = 用户仍以为被截断；打印不放开 = PDF 里还是丢数据。"""
    h = _h()
    assert "展开全文 ▾" in h, "★ 没有「展开全文」入口（本次修复的目的正是让完整内容可达）"
    i = h.index("_clampLongCell(td) {")
    body = h[i:i + 1600]
    assert "classList.toggle('open')" in body, "没有切换展开态"
    assert "clamp-toggle" in body, "没建按钮"
    # ★ 必须**真的注册 click 监听** —— 只建按钮不绑事件 = 点了没反应
    #   （变异自检实测：把 'click' 改成 'noop' 时，上面几条断言照样绿 ⇒ 补这一条）
    assert "addEventListener('click'" in body, "★ 按钮没注册 click 监听（点了不会展开）"
    # 打印时放开
    assert "@media print" in h, "缺打印媒体查询"
    j = h.index("@media print")
    seg = h[j:j + 420]
    assert "clamp3" in seg and "display:block !important" in seg, "打印时没放开 clamp（PDF 里仍被截）"
    assert "clamp-toggle" in seg and "display:none !important" in seg, "打印时没隐藏展开按钮"


@pytest.mark.unit
def test_clamp_only_touches_named_columns():
    """★ 只有「一句话核心观点」「头寸建议」两列收敛（按**表头文本**定位，不写死序号）。"""
    h = _h()
    i = h.index("_decorateViewTable(root) {")
    body = h[i:i + 1500]
    assert "'一句话核心观点'" in body and "'头寸建议'" in body, "没按表头文本定位长文本列"
    assert "iLong" in body and ".forEach(k =>" in body, "长文本列没接线到单元格处理"


@pytest.mark.unit
def test_clamp_is_idempotent():
    """★ 幂等：切日期/口径会重渲染，重复装饰不能叠加包裹。"""
    h = _h()
    i = h.index("_clampLongCell(td) {")
    body = h[i:i + 1600]
    assert "dataset.clamped === '1'" in body, "没有幂等标记"
    assert "if (!td || td.dataset.clamped === '1') return;" in body, "幂等标记没用于提前返回"


@pytest.mark.unit
def test_missing_value_not_clamped():
    """★ 空值/「—」不能被包裹（否则空单元格也会长出个按钮）。"""
    h = _h()
    i = h.index("_clampLongCell(td) {")
    body = h[i:i + 1600]
    assert "if (!txt || txt === '—') return;" in body, "没有对空值/「—」早退"
