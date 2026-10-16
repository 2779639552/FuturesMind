# -*- coding: utf-8 -*-
r"""页级控制条 + 统一品种状态层 的回归护栏（2026-09-30）。

背景：用户反馈「品种选择被困在某个小模块里，其他小模块依赖它取值，无法单独完成」。
修复分三层：
  P1 控制条常驻吸顶：各 tab 首个层级放 .page-tools，靠 CSS sticky 吸顶，
     并由 _secScan / _applySecFilter 明确排除（不折叠、不被筛选隐藏）。
  P2 统一品种状态层：新增 App._curVariety(scope)，替换 7 处直接读 #sym 的写法，
     去掉「overlay 回退去借 #sym」的跨页依赖。
  P3 清理：移除幽灵 id trade-variety 的活引用；isHeader 不再误放行控制条/不可见元素。

本文件只做**静态文本断言**（不起浏览器），浏览器级验证见
probe/_e2e_page_tools.mjs 与 tests/js/section_index_harness.mjs。

三条写断言的经验（都踩过）：
  1) 别做「整份源码子串断言」—— 注释里会合法地写「此前是 getElementById('sym').value」
     这类说明，会把注释误判成残留。统一走 _live_occurrences()（按注释判定过滤）。
  2) 别用全局正则剥注释 —— 代码里有形如 https://... 的字符串，`//` 到行尾一剥就
     把真实代码一起吃掉，断言于是静默永真。改为逐行判定行首是否为注释。
  3) 变异锚点必须精确到唯一位置 —— 文件里有多处 position: sticky / is-open，
     只按通用子串替换会打到别的元素上，变异「看似生效实则没打中」。
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "web_template.html"

# 8 个有控制条的页签（foreign 等无操作控件的页不在其列）
# 【2026-10-14 结构重组】warrant 已并入 fund、gtja 已并入 warehouse(改名「卖方数据」)
# ⇒ 不再是独立 tab, 从本名单移除(它们的控制条也已降为**页内卡**, 不再受"tab 控制条"约束)。
TABS_WITH_TOOLS = ["run", "sentiment", "quant", "fund", "research", "variety"]

# 各页签控制条里必须持有的「入口控件」：多数页是品种下拉，情绪页是回溯区间。
TOOLS_PRIMARY_CONTROL = {
    "run": "sym",
    "sentiment": "lookback-days",
    "quant": "q-variety",
    "fund": "f-variety",
    "research": "res-ask-variety",
    "variety": "bm-variety",
}


def _src() -> str:
    return TEMPLATE.read_text(encoding="utf-8", errors="replace")


def _line_of(src: str, pos: int) -> str:
    return src[src.rfind("\n", 0, pos) + 1 : src.find("\n", pos)]


def _is_comment_pos(src: str, pos: int) -> bool:
    """判断 pos 处是否处于注释内。

    只用两种**无歧义**的判据，故意不做「最近未闭合 /* 推断到哪」的区间运算：
    模板里有一段 `/* JSON 接口 ...` 的长块注释与 JS 字符串混排，若按「最近的 /* 未
    闭合 ⇒ 此后都算注释」推断，会把 1500+ 字节之后的整段活代码误判成注释，
    于是断言静默永真（实测踩到：变异明明生效却报「未击杀」）。
    对本文件的断言集（两个 needle 都写在整行 `//` 注释里）行级判定零误判。
    """
    st = _line_of(src, pos).strip()
    if st.startswith("//") or st.startswith("*") or st.startswith("/*"):
        return True
    # HTML 注释区间：`<!--` 不会出现在 JS 字符串里，故这条区间判断是安全的。
    return src.rfind("<!--", 0, pos) > src.rfind("-->", 0, pos)


def _live_occurrences(src: str, needle: str):
    """返回 needle 的全部**活代码**出现位置（注释里的不算）。"""
    out = []
    start = 0
    while True:
        i = src.find(needle, start)
        if i < 0:
            break
        if not _is_comment_pos(src, i):
            out.append(i)
        start = i + 1
    return out


def _strip_block_comments(css: str) -> str:
    """只用于 CSS 小片段：去掉 /* ... */ 注释。"""
    return re.sub(r"/\*.*?\*/", " ", css, flags=re.S)


def _method_body(src: str, name: str) -> str:
    """取 App 对象里某个方法的源码体（括号配平到方法结束）。"""
    m = re.search(r"\b" + name + r"[ \t]*\([^)]*\)[ \t]*\{", src)
    assert m, "未找到方法 " + name
    i = m.end() - 1
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i : j + 1]
    pytest.fail(name + " 花括号不配平")


def _enclosing_body(src: str, pos: int) -> str:
    """取包住 pos 的那个顶层方法（App 里两空格缩进的方法）的体，找不到返回空串。"""
    best = None
    # 允许 async / static 前缀：runMultiCompare 就是 `async runMultiCompare() {`
    for m in re.finditer(
        r"\n  (?:async[ \t]+)?([A-Za-z_$][\w$]*)[ \t]*\([^)]*\)[ \t]*\{", src
    ):
        if m.start() < pos:
            best = m.end() - 1
    if best is None:
        return ""
    depth = 0
    for j in range(best, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[best : j + 1]
    return ""


def _div_span(src: str, start: int):
    """从 start 处的 <div 开标签起做 div 配平，返回 (段文本, 结束下标)。"""
    depth, j = 0, start
    for m in re.finditer(r"<div[ >]|</div>", src[start:]):
        depth += -1 if m.group(0).startswith("</") else 1
        if depth == 0:
            j = start + m.end()
            break
    return src[start:j], j


def _tools_segment(src: str, tab: str) -> str:
    i = src.find('<div id="tab-' + tab + '-tools"')
    assert i > 0, "#tab-" + tab + " 内未找到控制条"
    seg, _ = _div_span(src, i)
    return seg


# ══════════════════════════════════════════════════════════════════
# 1) 控制条落位
# ══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("tab", TABS_WITH_TOOLS)
def test_page_tools_container_exists(tab):
    """每个有操作控件的页签都必须有一个 .page-tools 控制条容器。"""
    assert ('<div id="tab-' + tab + '-tools" class="page-tools">') in _src(), (
        "#tab-" + tab + " 缺少页级控制条容器 —— 品种/区间控件若留在内容卡里，"
        "折叠或筛选后用户就找不到入口（本次修复的根因）"
    )


@pytest.mark.parametrize("tab", TABS_WITH_TOOLS)
def test_page_tools_is_top_level_not_in_block(tab):
    """控制条必须是页签的直接子元素，且没有被折叠进 .sec-block。"""
    src = _src()
    m = re.search(r'<div id="tab-' + tab + r'"[^>]*>', src)
    assert m, "未找到 #tab-" + tab
    i = m.start()
    j = src.find('<div id="tab-' + tab + '-tools"', i)
    assert j > 0, "#tab-" + tab + " 内未找到控制条"
    head = src[i:j]
    assert "sec-block" not in head, "#tab-" + tab + " 控制条被折叠外壳包住"
    assert "sec-index" not in head, "#tab-" + tab + " 控制条在小模块索引之下（应在其上）"


@pytest.mark.parametrize("tab", TABS_WITH_TOOLS)
def test_page_tools_holds_primary_control(tab):
    """控制条必须持有该页的入口控件（品种下拉 / 区间），不能留在内容卡里。"""
    seg = _tools_segment(_src(), tab)
    want = TOOLS_PRIMARY_CONTROL[tab]
    assert ('id="' + want + '"') in seg, (
        "#tab-" + tab + " 的控制条里找不到入口控件 " + want
        + " —— 入口又回到内容卡里，折叠/筛选后用户就找不到了（本次修复的根因）"
    )


@pytest.mark.parametrize("tab", TABS_WITH_TOOLS)
def test_page_tools_lives_below_tab_open_tag(tab):
    """控制条必须紧跟页签开标签（作为该页首个内容块），不能散落在内容中间。

    【2026-10-14 例外：quant 改为「总览 → 详情」双视图】
    该页新增了 `#q-overview-view`（全品种总览）+ `#q-detail-view`（外壳，内含原有模块），
    两者**互斥显示**（`display:none` 切换，见 App.qOpenDetail / qBackOverview）。
    于是控制条不再字面紧跟页签开标签，但**不变量仍然成立**：
    在**同一视图内**没有任何可见内容块顶在控制条之上。
    故对 quant 改为断言「控制条是 `#q-detail-view` 内的第一个 `.page-tools`」，
    其余 tab 维持原断言。

    ⚠️ 本条同时记录一个已消失的前提：原断言理由写的是"吸顶时顺序颠倒"，
    而 `.page-tools` 的 `position:sticky` 已于 2026-10-14 由用户要求**改为 static**
    （见模板内该规则注释）⇒ 吸顶顺序风险本身已不存在，但"控制条要在视图顶部"
    这条产品要求仍要守（否则用户要在长内容里翻找入口）。
    """
    src = _src()
    m = re.search(r'<div id="tab-' + tab + r'"[^>]*>', src)
    assert m
    if tab in ("quant", "fund"):
        # 【2026-10-16 追加 fund】基本面页也做了同款「全品种总览 → 单品种明细」双视图
        #   (#f-overview-view / #f-detail-view) ⇒ 控制条不再字面紧跟页签开标签。
        #   **不变量仍成立且照旧被断言**：控制条是该视图内的第一个 .page-tools。
        # ⚠️ 不能用 `src[i:]`（会切到**文件末尾**，把其它 tab 的 .page-tools 也算进来 →
        #    断言假红）。必须用 **div 深度配平**精确框出该容器。
        def _span(anchor: str) -> str:
            # ★ 必须回退到真正的开标签 `<div`：anchor 是 `id="..."`，它在标签**内部**，
            #   从那里起算会漏掉本元素自身的开标签 ⇒ 配平提前归零、span 少一个外层
            #   （实测：`q-detail-view` 会算成 426 字符，而真值 12473）。
            start = src.rindex("<div", 0, src.index(anchor))
            depth, i = 0, start
            pat = re.compile(r"<div\b|</div>")
            while i < len(src):
                mm = pat.search(src, i)
                assert mm, f"{anchor} 未配平"
                depth += 1 if mm.group(0) == "<div" else -1
                if depth == 0:
                    return src[start:mm.end()]
                i = mm.end()
            raise AssertionError(f"{anchor} 未配平")

        # ⚠ 容器 id 的前缀与 tab 名**不同**(tab=quant → 前缀 q; tab=fund → 前缀 f),
        #   直接拿 tab 拼 id 会 `id="quant-detail-view"` 找不到(实测踩到)。
        _pfx = {"quant": "q", "fund": "f"}[tab]
        detail = _span('id="%s-detail-view"' % _pfx)
        ov = _span('id="%s-overview-view"' % _pfx)
        assert ('id="tab-%s-tools"' % tab) in detail, (
            "#tab-%s 的控制条不在 #%s-detail-view 内 —— 详情视图会没有入口控件"
            % (tab, tab)
        )
        assert detail.count('class="page-tools"') == 1, (
            "#%s-detail-view 内 .page-tools 恰好应有 1 个" % tab
        )
        assert 'class="page-tools"' not in ov, (
            "%s 的**总览视图**里放了 .page-tools —— 双视图会各有一个控制条、切换时互相干扰"
            % tab
        )
        return
    after = src[m.end() : m.end() + 400]
    assert ('<div id="tab-' + tab + '-tools"') in after, (
        "#tab-" + tab + " 的控制条未紧跟页签开标签 —— "
        "页签内首个内容块会顶在控制条之上，吸顶时顺序颠倒"
    )


# ══════════════════════════════════════════════════════════════════
# 2) 折叠体系排除控制条
# ══════════════════════════════════════════════════════════════════

def test_sec_scan_excludes_page_tools():
    """_secScan 必须排除 .page-tools —— 否则控制条会被当小模块再折叠一次。"""
    body = _method_body(_src(), "_secScan")
    assert "contains('page-tools')" in body, (
        "_secScan 未排除 .page-tools —— 控制条会被包进折叠外壳，"
        "默认只展开第一个有内容的模块，控制条（高度约 30px）落在收起态，入口再次消失"
    )


def test_apply_sec_filter_spares_page_tools():
    """_applySecFilter 清场时不得隐藏 .page-tools。"""
    body = _method_body(_src(), "_applySecFilter")
    assert "classList.contains('page-tools')" in body, (
        "_applySecFilter 未放行 .page-tools —— 勾选任一小模块后品种入口会一起消失"
    )


def test_apply_sec_filter_resyncs_chip_open_state():
    """清空筛选后必须同步标签 is-open（否则残留「已展开」态，收起点两次才动）。"""
    body = _method_body(_src(), "_applySecFilter")
    assert "classList.toggle('is-open'" in body, (
        "_applySecFilter 清空筛选时未同步标签 is-open —— "
        "取消勾选后标签仍高亮，与实际折叠态不符（实测『再点收起』用例失败即此）"
    )


def test_is_header_rejects_page_tools():
    """段头放行判据不得把控制条当段头。"""
    body = _method_body(_src(), "_applySecFilter")
    assert "classList.contains('page-tools')) return false" in body, (
        "isHeader 未排除 .page-tools —— 筛选时控制条会被当作段头整块放行"
    )


def test_is_header_rejects_invisible_elements():
    """不可见元素（display:none / opacity:0）不得被当成段头放行。"""
    body = _method_body(_src(), "_applySecFilter")
    assert "display === 'none'" in body and "opacity" in body, (
        "isHeader 未排除不可见元素 —— opacity:0 的元素 offsetHeight=0，"
        "会被『矮且有字』的判据误判成段头而放行，筛选后页面留下看不见却占位的空壳"
    )


# ══════════════════════════════════════════════════════════════════
# 3) 统一品种状态层
# ══════════════════════════════════════════════════════════════════

def test_cur_variety_layer_exists():
    """统一状态层 _curVariety 必须存在，且以控件 id 为 scope。"""
    body = _method_body(_src(), "_curVariety")
    assert "getElementById(scope)" in body, "_curVariety 未按 scope 读控件"
    assert "this._tabVariety[scope]" in body, (
        "_curVariety 缺少「元素不存在 -> 回退最后已知值」的兜底"
    )


def test_no_live_direct_sym_reads_remain():
    """活代码里不得再有直接读 #sym 的写法（一律走状态层）。"""
    live = _live_occurrences(_src(), "getElementById('sym').value")
    assert not live, (
        "活代码里仍有 %d 处直接读 #sym.value —— 品种取值绕过状态层后，"
        "控件被折叠/未渲染时会取到池内第一项（等于静默取错品种）" % len(live)
    )


def test_comp_variety_reads_via_state_layer():
    """分析工具子模块的品种也必须走状态层（此前它被困在 asub-comprehensive 卡里）。"""
    src = _src()
    live = _live_occurrences(src, "getElementById('comp-variety')")
    assert not live, "活代码里仍有 %d 处直接读 #comp-variety（应走 _curVariety）" % len(live)
    assert "_curVariety('comp-variety')" in src


def test_populate_variety_selects_sets_default():
    """填充下拉后必须补默认选中，否则单独打开某模块时下拉停在空白首项。"""
    body = _method_body(_src(), "_populateVarietySelects")
    assert "sel.value = this._curVariety(id)" in body, (
        "_populateVarietySelects 只填不选 —— 与状态层记录不一致，"
        "该模块看起来像没选品种"
    )


def test_default_code_documented_as_fallback_only():
    """_defaultCode 定义处必须注明只作兜底（它返回池内第一项，不是「当前品种」）。"""
    src = _src()
    i = src.find("_defaultCode() {")
    assert i > 0, "未找到 _defaultCode 定义"
    assert "_varieties[0]" in src[i : i + 200], "_defaultCode 未返回池内第一项"
    assert "兜底" in src[max(0, i - 420) : i + 120], (
        "_defaultCode 未注明只作兜底 —— 它会被误当成当前品种使用，"
        "于是模块单独打开时拿的是池内第一项"
    )


# ══════════════════════════════════════════════════════════════════
# 3b) 空值语义豁免 —— 「全部品种」下拉不得被补成池首
# （2026-09-30 用户报障：问 PTA 只召回橡胶板块）
# ══════════════════════════════════════════════════════════════════
# 【症状】研报问答问「PTA近日情况」答"现有片段不足以判断"，当日国泰君安
#   「PX PTA MEG 基本面数据」(rid=1463) 与华泰「聚酯日报」(rid=1440) 一条都搜不到。
# 【根因】_populateVarietySelects 填充后无条件补默认选中，而 _curVariety 的兜底是
#   _defaultCode() = 池内第一项(BR)。#res-ask-variety 的首项本是
#   <option value="">全部品种</option>，空串是**合法语义**，被静默改成 BR
#   ⇒ 前端把 variety="BR" 发给后端 ⇒ 检索被品种过滤到橡胶板块。
#   实证①(qa_messages)：09-30 那次问句 filters.variety="BR"，而 09-29/09-28 同一问句为 "TA"。
#   实证②(检索层 A/B)：variety="BR" 召回 6 篇全是 RU/NR/BR；
#          variety="TA" 召回含 rid=1463/1440。
# 【修法】该 select 内存在 option[value=""] ⇒ 空串有语义 ⇒ 跳过补选。
EMPTY_SEMANTIC_SELECTS = (
    "res-ask-variety",       # 研报问答：空串=全部品种(问句带品种名时由后端 _infer_variety_from_question 识别)
    "res-filter-variety",    # 研报列表筛选：空串=全部品种
    "event-variety",         # 情绪事件：空串=全部品种
    "res-upload-variety",    # 本地研报上传：空串=自动识别(可含多品种)
    "ud-upload-variety",     # 自传数据上传：空串=自动识别
    # dwh-ds-variety 已于 2026-10-08 随「国君数据集」模块一并删除
)

POPULATE_ALLOWS_EMPTY = (
    "const _allowsEmpty = !!sel.querySelector('option[value=\"\"]');\n"
    "      if (!sel.value && !_allowsEmpty) sel.value = this._curVariety(id);"
)


def _select_has_empty_option(src: str, sid: str) -> bool:
    """该 <select id=sid> 内是否存在 <option value="">。"""
    m = re.search(r"<select[^>]*\bid=\"" + re.escape(sid) + r"\"[^>]*>", src)
    if not m:
        return False
    end = src.find("</select>", m.end())
    if end < 0:
        return False
    return 'value=""' in src[m.end() : end]


def _populate_assertions_ok(src: str) -> bool:
    """_populateVarietySelects 必须既补默认选中、又豁免空值有语义的下拉。"""
    body = _method_body(src, "_populateVarietySelects")
    if "sel.value = this._curVariety(id)" not in body:
        return False
    if POPULATE_ALLOWS_EMPTY not in body:
        return False
    return True


def test_populate_exempts_empty_semantic_selects():
    """_populateVarietySelects 必须豁免「空串有语义」的下拉，不得一律补成池首。"""
    assert _populate_assertions_ok(_src()), (
        "_populateVarietySelects 缺少空值语义豁免 —— 会把「全部品种」静默补成池内首项"
        "(实测: 研报问答品种被设成 BR, 问 PTA 时检索被过滤到橡胶板块, 当日 PTA 研报全部搜不到)"
    )
    live = _live_occurrences(_src(), 'option[value=""]')
    assert len(live) == 1, (
        "空值豁免的判据必须**恰好 1 处**在活代码里(实测 %d 处) —— 判据落在注释里等于没生效"
        % len(live)
    )


@pytest.mark.parametrize("sid", EMPTY_SEMANTIC_SELECTS)
def test_empty_semantic_select_html_still_has_empty_option(sid):
    """这些下拉的 HTML 首项必须保留 value=""（空串是它们的正常语义）。"""
    assert _select_has_empty_option(_src(), sid), (
        "#%s 的 <option value=\"\"> 被删了 —— 空串不再可选, 该模块将被迫落到某个具体品种" % sid
    )


def test_pool_first_is_not_all_varieties():
    """判据自检: 池内第一项确实不是「全部」——证明补成池首会改变语义。"""
    src = _src()
    i = src.find("_defaultCode() {")
    assert i > 0
    assert "_varieties[0]" in src[i : i + 200]
    assert _src().count("全部品种") >= 3, "「全部品种」文案变少 —— 请核对空值语义下拉是否被改动"


MUT_POPULATE = [
    (
        "删掉空值豁免(回到无条件补池首)",
        lambda s: s.replace(
            "!sel.value && !_allowsEmpty) sel.value", "!sel.value) sel.value", 1
        ),
    ),
    (
        "删掉补默认选中本身",
        lambda s: s.replace(
            "if (!sel.value && !_allowsEmpty) sel.value = this._curVariety(id);", "", 1
        ),
    ),
    (
        "res-ask-variety 首项改成非空(空串不再有语义)",
        lambda s: s.replace(
            '<select id="res-ask-variety"><option value="">全部品种</option></select>',
            '<select id="res-ask-variety"></select>',
            1,
        ),
    ),
]


@pytest.mark.parametrize("desc,mutate", MUT_POPULATE, ids=[d for d, _ in MUT_POPULATE])
def test_populate_guard_kills_mutation(desc, mutate):
    """逐个破坏「空值语义豁免」，确认护栏会红。"""
    orig = _src()
    mutated = mutate(orig)
    assert mutated != orig, "变异未生效: " + desc
    killed = not _populate_assertions_ok(mutated) or not all(
        _select_has_empty_option(mutated, sid) for sid in EMPTY_SEMANTIC_SELECTS
    )
    assert killed, "护栏被变异骗过(未击杀): " + desc


def test_populate_mutation_anchors_are_unique():
    """新增变异的锚点必须唯一（否则会打到别处造成假击杀）。"""
    orig = _src()
    for a in [
        "!sel.value && !_allowsEmpty) sel.value",
        "if (!sel.value && !_allowsEmpty) sel.value = this._curVariety(id);",
        '<select id="res-ask-variety"><option value="">全部品种</option></select>',
    ]:
        assert orig.count(a) == 1, "锚点不唯一(%d 处): %r" % (orig.count(a), a[:70])


# ══════════════════════════════════════════════════════════════════
# 4) 清理与样式
# ══════════════════════════════════════════════════════════════════

def test_ghost_trade_variety_not_in_populate_list():
    """_populateVarietySelects 的填充名单不得再带幽灵 id trade-variety。"""
    body = _method_body(_src(), "_populateVarietySelects")
    assert "trade-variety" not in body, (
        "_populateVarietySelects 的填充名单里还有 trade-variety —— "
        "该 id 在 DOM 中不存在（交易回测页已下线），属死引用，每进一次页面白填一次"
    )


def test_ghost_trade_variety_live_read_is_guarded():
    """活代码里对 trade-variety 的读取必须落在自带缺件守卫的下线函数内。

    多策略对比已按用户要求保留为「可调用的代码资产、不接任何 UI 入口」，
    所以它内部仍会读到 trade-variety；关键是它必须先做缺件检查再退出，
    绝不能裸奔到赋值语句（否则页面会抛错白屏）。
    """
    src = _src()
    occ = _live_occurrences(src, "trade-variety")
    for pos in occ:
        enclosing = _enclosing_body(src, pos)
        assert "trade-variety" in enclosing, "定位不到 trade-variety 所在函数"
        assert "_need" in enclosing, (
            "trade-variety 的活引用不在「先查缺件再退出」的守卫函数内 —— "
            "裸读不存在的控件会抛错白屏"
        )


def test_page_tools_css_is_sticky_below_header():
    """控制条必须吸顶，且 top 避开顶部导航栏（header 高 56px）。"""
    m = re.search(r"\.page-tools[ \t]*\{([^}]*)\}", _src())
    assert m, "缺少 .page-tools 样式"
    css = m.group(1)
    assert "position: sticky" in css, ".page-tools 未吸顶"
    assert "top: 56px" in css, ".page-tools 的 top 未避开 header(56px)，会盖住导航栏"
    assert "z-index:" in css, ".page-tools 缺 z-index"


def test_page_tools_has_opaque_background():
    """吸顶条必须有实色底，否则下方内容透上来糊成一片。"""
    m = re.search(r"\.page-tools[ \t]*\{([^}]*)\}", _src())
    assert m
    css = _strip_block_comments(m.group(1))
    assert "background:" in css, ".page-tools 无背景色，吸顶时内容会透出来"
    assert "--bg-glass" not in css, (
        ".page-tools 用了半透明的 --bg-glass 做底色 —— 吸顶条必须实色"
    )


def test_analysis_subnav_sticky():
    """分析工具子导航条应吸顶（否则切到作者/事件后要滚回页顶才能换页）。"""
    m = re.search(r"#analysis-subnav[ \t]*\{([^}]*)\}", _src())
    assert m, "缺少 #analysis-subnav 吸顶样式"
    assert "position: sticky" in m.group(1)


# ══════════════════════════════════════════════════════════════════
# 5) 护栏自检（变异验证）—— 防止护栏写成永真断言
#
# 每个变异都精确锚定到唯一位置（不靠通用子串），再由下面的 WATCHED
# 联合检查判定「是否被击杀」：只要有一条监视器变红，就算击杀成功。
# ══════════════════════════════════════════════════════════════════

ISOPEN_RESYNC = (
    "if (r.btn) r.btn.classList.toggle('is-open',\n"
    "            (r.blocks || []).every(b => !b.classList.contains('is-collapsed')));"
)

MUTATIONS = [
    (
        "_secScan 不再排除 page-tools",
        lambda s: s.replace(
            "if (cls.contains('page-tools') || cls.contains('sec-index')",
            "if (cls.contains('sec-index')",
            1,
        ),
    ),
    (
        "控制条丢掉了品种下拉",
        lambda s: s.replace('<select id="sym"', '<select data-x="sym"', 1),
    ),
    (
        "恢复直接读 #sym.value",
        lambda s: s.replace(
            "const sym = this._curVariety('sym');",
            "const sym = document.getElementById('sym').value;",
            1,
        ),
    ),
    (
        "丢弃 is-open 同步",
        lambda s: s.replace(ISOPEN_RESYNC, "", 1),
    ),
    (
        "_populateVarietySelects 只填不选",
        lambda s: s.replace(
            "if (!sel.value && !_allowsEmpty) sel.value = this._curVariety(id);", "", 1
        ),
    ),
    (
        "丢掉空值语义豁免(全部品种被补成池首)",
        lambda s: s.replace(
            "!sel.value && !_allowsEmpty) sel.value", "!sel.value) sel.value", 1
        ),
    ),
    (
        "控制条不再吸顶",
        lambda s: s.replace(
            ".page-tools {\n  position: sticky;", ".page-tools {\n  position: relative;", 1
        ),
    ),
    (
        "isHeader 不再排除 page-tools",
        lambda s: s.replace(
            "if (el.classList.contains('page-tools')) return false;", "", 1
        ),
    ),
]


def _watch_secscan(s):
    return "contains('page-tools')" in _method_body(s, "_secScan")


def _watch_primary_control(s):
    i = s.find('<div id="tab-run-tools"')
    if i < 0:
        return False
    seg, _ = _div_span(s, i)
    return '<select id="sym"' in seg


def _watch_no_live_sym(s):
    return not _live_occurrences(s, "getElementById('sym').value")


def _watch_is_open(s):
    return "classList.toggle('is-open'" in _method_body(s, "_applySecFilter")


def _watch_populate(s):
    return _populate_assertions_ok(s)


def _watch_sticky(s):
    m = re.search(r"\.page-tools[ \t]*\{([^}]*)\}", s)
    return bool(m) and "position: sticky" in m.group(1) and "top: 56px" in m.group(1)


def _watch_isheader(s):
    return "classList.contains('page-tools')) return false" in _method_body(s, "_applySecFilter")


WATCHED = [
    _watch_secscan,
    _watch_primary_control,
    _watch_no_live_sym,
    _watch_is_open,
    _watch_populate,
    _watch_sticky,
    _watch_isheader,
]


@pytest.mark.parametrize("desc,mutate", MUTATIONS, ids=[d for d, _ in MUTATIONS])
def test_guard_kills_mutation(desc, mutate):
    """把关键约束逐个破坏，确认上面的护栏真的会红。"""
    orig = _src()
    mutated = mutate(orig)
    assert mutated != orig, "变异未生效: " + desc
    killed = not all(fn(mutated) for fn in WATCHED)
    assert killed, "护栏被变异骗过（未击杀）: " + desc


def test_mutation_anchors_are_safe():
    """变异的锚点必须安全：要么唯一，要么每一处都落在活代码里。

    必要性：ISOPEN / position:sticky 这类文本在模板里有多份（别的元素也用），
    若按通用子串替换会打到别处 —— 变异「号称生效」却没改到受监视的代码，
    自检于是报「未击杀」，看着像护栏失灵，实则是变异没打中（实测踩到 3 例）。
    """
    orig = _src()
    # ① 必须唯一的锚点（打偏就会造成假击杀）
    for a in [
        "if (cls.contains('page-tools') || cls.contains('sec-index')",
        '<select id="sym"',
        ISOPEN_RESYNC,
        "const _allowsEmpty = !!sel.querySelector('option[value=\"\"]');",
        "if (!sel.value && !_allowsEmpty) sel.value = this._curVariety(id);",
        ".page-tools {\n  position: sticky;",
        "if (el.classList.contains('page-tools')) return false;",
    ]:
        assert orig.count(a) == 1, "锚点不唯一（%d 处）: %r" % (orig.count(a), a[:60])
    # ② 允许重复的锚点：每一处都必须是活代码（打到任意一处都算有效变异）
    sym_anchor = "const sym = this._curVariety('sym');"
    assert orig.count(sym_anchor) >= 1
    assert len(_live_occurrences(orig, sym_anchor)) == orig.count(sym_anchor), (
        "有 _curVariety('sym') 的调用落在注释里 —— 变异可能打到注释上"
    )

# ══════════════════════════════════════════════════════════════
# 覆盖矩阵：必须在小模块里 + 不得吸顶（2026-09-30 用户报障）
# ══════════════════════════════════════════════════════════════
# 【症状】国君数据页「覆盖矩阵」不在小模块中, 且遮挡其他小模块内容。
# 【根因】该卡片被放在 <div id="tab-gtja-tools" class="page-tools"> 内部：
#   ① `.page-tools` 是 position:sticky + 实色底, 只适合装矮控制条；塞进 21 行大表格后
#      整块被钉在顶部, 实色底**盖住下方模块**；
#   ② `_secScan` 对 `.page-tools` 是**整块跳过**(控制条永不参与折叠) ⇒ 它进不了
#      小模块索引、也收不起来。
# 【修法】把覆盖矩阵卡移出 .page-tools, 落成 tab 顶层普通 .card。

# 【2026-10-14】该容器随「国君数据」并入 warehouse 时, 按用户口径**去掉了
#   `class="page-tools"`(降为页内卡) —— 一个 tab 只许一个 .page-tools,
#   而 warehouse 的顶部控制条不存在(各卡自带上控件)。id 保留(harness 依赖)。
GTJA_TOOLS_OPEN = '<div id="tab-gtja-tools">'


def _gtja_tools_span(src):
    """返回 (起, 止) —— 用 div 深度配对找出 .page-tools 的真实闭合位置。

    ⚠️ 不能靠"找下一个 </div>" —— .page-tools 内外都有大量 </div>,
    这样会得到错误区间, 使断言读错范围(实测踩到: 只改注释没结构也能"通过")。
    """
    i = src.find(GTJA_TOOLS_OPEN)
    if i == -1:
        return None
    depth = 0
    j = i
    while j < len(src):
        nxt_open = src.find('<div', j)
        nxt_close = src.find('</div>', j)
        if nxt_close == -1:
            return None
        if nxt_open != -1 and nxt_open < nxt_close:
            depth += 1
            j = nxt_open + 4
        else:
            depth -= 1
            j = nxt_close + 6
            if depth == 0:
                return (i, j)
    return None


def test_gtja_matrix_card_removed():
    """⚠️⚠️ 覆盖矩阵必须**整体不存在**（2026-10-08 用户要求删除, 防回加）。"""
    src = _src()
    assert '<h3>覆盖矩阵' not in src, "覆盖矩阵卡又回来了 —— 用户已要求删除该模块"
    assert 'id="gt-matrix"' not in src, "#gt-matrix 容器又回来了"
    assert 'loadGtjaCoverage' not in src, "loadGtjaCoverage 方法又回来了"
    # 控制条仍只许 1 张矮卡（查询控制）
    span = _gtja_tools_span(src)
    assert span, "未找到 #tab-gtja-tools 的闭合区间（结构异常）"
    inside = src[span[0]:span[1]]
    assert inside.count('class="card"') == 1, (
        "#tab-gtja-tools 内含 %d 张卡片（应为 1）—— 控制条只适合装矮卡" % inside.count('class="card"')
    )





def test_gtja_page_tools_holds_only_one_card():
    """控制条内只能有 1 张矮卡（查询控制）—— 多塞一张就会重新变成遮挡源。"""
    src = _src()
    span = _gtja_tools_span(src)
    assert span, "未找到 #tab-gtja-tools 的闭合区间"
    inside = src[span[0]:span[1]]
    n = inside.count('class="card"') + inside.count("class='card'")
    assert n == 1, (
        "#tab-gtja-tools 内含 %d 张卡片（应为 1, 即「① 查询控制」）—— "
        "控制条只适合装矮卡, 多塞内容会吸顶遮挡" % n
    )


def test_sec_index_version_bumped_for_matrix_move():
    """结构变了必须 +1 版本号, 否则旧索引不重建, 用户看到的还是旧布局。

    ⚠️ 这里断言**精确值**而不是 >= N —— >= 是"单调不回头"的写法,
       任何后续版本都恒真, 结构性增删时忘了 +1 就抓不住(实测过的假绿形态)。
       每次改 tab 内卡片结构, 请同步 +1 并更新本断言的期望值。
    """
    src = _src()
    m = re.search(r"_SEC_IDX_VER:\s*'(\d+)'", src)
    assert m, "未找到 _SEC_IDX_VER"
    assert m.group(1) == '24', (
        "_SEC_IDX_VER=%s (期望 '24')。若你刚改了 tab 内卡片结构/初始展开形态: 把它 +1 "
        "并同步本断言; "
        "若不慎回退: 用户浏览器里会残留已删除的旧索引项(僵尸项)" % m.group(1)
    )


def test_no_zombie_index_labels_for_merged_cards():
    """★ 索引里不得出现指向已删除卡片的僵尸项（④走势图/⑤明细数据）。"""
    src = _src()
    for t in ('<h3>④ 走势图', '<h3>⑤ 明细数据'):
        assert t not in src, (
            "%s 仍存在 ⇒ 若索引未重建, 会出现点了没反应的僵尸索引项" % t
        )
    assert GTJA_MERGED_TITLE in src, "合并卡标题缺失"




# ── 本缺陷的变异自检 ────────────────────────────────────────────


def _matrix_assertions_ok(src):
    """返回 True 表示「覆盖矩阵已删除」这批断言在 src 上全过。"""
    if '<h3>覆盖矩阵' in src or 'id="gt-matrix"' in src:
        return False
    if 'loadGtjaCoverage' in src:
        return False
    span = _gtja_tools_span(src)
    if not span:
        return False
    inside = src[span[0]:span[1]]
    if inside.count('class="card"') != 1:
        return False
    m = re.search(r"_SEC_IDX_VER:\s*'(\d+)'", src)
    if not m or m.group(1) != '24':
        return False
    return True



def test_matrix_baseline_passes():
    """先证明未变异时全过, 否则后面「击杀」是假象。"""
    assert _matrix_assertions_ok(_src()), "基线未通过 —— 断言本身写错了"


MATRIX_CARD_BLOCK = (
    '<div class="card" style="margin-bottom:16px">\n'
    '    <h3>覆盖矩阵 <span class="badge badge-info" id="gt-matrix-badge"></span></h3>\n'
    '    <div id="gt-matrix" style="overflow-x:auto;margin-top:10px">\n'
    '      <span style="color:var(--text-muted)">覆盖矩阵加载中…</span>\n'
    '    </div>\n'
    '  </div>'
)


def test_matrix_guard_kills_readd():
    """【变异·真结构】把覆盖矩阵卡**重新加回** tab 顶层，护栏必须变红。"""
    src = _src()
    span = _gtja_tools_span(src)
    assert span, "未找到 #tab-gtja-tools 的闭合区间"
    insert_at = span[1]
    mutated = src[:insert_at] + "\n  " + MATRIX_CARD_BLOCK + "\n" + src[insert_at:]
    assert mutated != src, "变异未生效"
    assert '<h3>覆盖矩阵' in mutated, "变异后矩阵没出现 —— 变异构造失败，击杀无意义"
    assert not _matrix_assertions_ok(mutated), (
        "把覆盖矩阵加回来后护栏没变红 —— 说明护栏抓不住这个回退"
    )





def test_matrix_guard_kills_extra_card_in_tools():
    """【变异·真结构】往控制条里再塞一张卡，护栏必须变红。

    ⚠️ 必须是真变异：早先写成 `src.replace(x, x)`（自替换空操作），变异根本没发生，
    "击杀"是假的。现在直接改写 span 区间。
    """
    src = _src()
    span = _gtja_tools_span(src)
    assert span, "未找到 #tab-gtja-tools 的闭合区间"
    inside = src[span[0]:span[1]]
    assert inside.count('class="card"') == 1
    end = inside.rfind("</div>")
    assert end != -1, "控制条内找不到闭合 div"
    extra = '<div class="card"><h3>伪卡</h3></div>\n'
    mut_inside = inside[:end] + extra + inside[end:]
    mutated = src[:span[0]] + mut_inside + src[span[1]:]
    assert mutated != src, "变异未生效"
    assert not _matrix_assertions_ok(mutated), (
        "往控制条里多塞一张卡后护栏没变红 —— 说明卡片数判据没起作用"
    )

def test_gtja_tab_div_balance():
    """★ 删除/增删国君页卡片后, #tab-gtja 的 div 配对必须平衡。

    【背景 2026-10-08】删除覆盖矩阵卡时漏删了一层闭合 </div>(矩阵卡外层闭合),
    #tab-gtja 提前闭合 ⇒ ② 四源状态 / ③ 指标总览 被挤出 tab 掉到 <main> 下,
    用户看到"这两个模块平铺到了所有大模块下面"。静态文本断言看不见这种结构错位,
    只有 div 深度配平能抓。
    """
    import re as _re
    src = _src()
    # 【2026-10-14 结构重组】锚点由 #tab-gtja 改为 **#tab-warehouse** —— 卖方数据块已并入该页,
    #   且"卡片被挤出容器"的失效模式在并入后同样成立(而且影响面更大: 会挤到 warehouse 的
    #   上传研报/自传数据/研报库三块之后)。覆盖面顺带扩大为整页。
    i = src.find('<div id="tab-warehouse" class="tab">')
    j = src.find('</main>', i)
    assert i != -1 and j > i, "找不到 #tab-warehouse 区间"
    seg = _re.sub(r'<!--.*?-->', '', src[i:j], flags=_re.S)
    depth = 0
    k = 0
    while k < len(seg):
        no = seg.find('<div', k)
        nc = seg.find('</div>', k)
        if no == -1 and nc == -1:
            break
        if no != -1 and (nc == -1 or no < nc):
            depth += 1
            k = no + 4
        else:
            depth -= 1
            k = nc + 6
            if depth < 0:
                break
    assert depth == 0, (
        "#tab-gtja 内 div 配对不平衡(深度 %d) —— 删/加卡片时漏了闭合 </div>, "
        "卡片会被挤出 tab 容器" % depth
    )
    # 卖方数据块现仅剩「② 指标总览」一张卡（「② 四源取数状态」已按用户要求删除, 2026-10-14），
    # 它必须仍在 warehouse 容器内（未被挤到 </main> 或其它 tab 下）。
    assert GTJA_MERGED_TITLE in src[i:j], (
        "%s 不在 #tab-warehouse 区间内 —— 卡片被挤出容器了" % GTJA_MERGED_TITLE)


def test_gtja_tab_div_balance_mutation():
    """【变异】给 #tab-gtja 多塞一个悬空 </div>, 护栏必须红。"""
    import re as _re
    src = _src()
    # 【2026-10-14】锚点同步改为 warehouse（见上条用例说明）
    i = src.find('<div id="tab-warehouse" class="tab">')
    j = src.find('</main>', i)
    seg = _re.sub(r'<!--.*?-->', '', src[i:j], flags=_re.S)
    # 在首张卡前插一个悬空 </div> (模拟"删卡漏删闭合")
    idx = seg.find('<div class="card"')
    assert idx != -1
    mutated = src[:i+idx] + '</div>' + src[i+idx:]
    assert mutated != src, "变异未生效"
    # 手动验证变异后的 tab 区间不平衡
    i2 = mutated.find('<div id="tab-warehouse" class="tab">')
    j2 = mutated.find('</main>', i2)
    seg2 = _re.sub(r'<!--.*?-->', '', mutated[i2:j2], flags=_re.S)
    depth = 0
    k = 0
    bad = False
    while k < len(seg2):
        no = seg2.find('<div', k)
        nc = seg2.find('</div>', k)
        if no == -1 and nc == -1:
            break
        if no != -1 and (nc == -1 or no < nc):
            depth += 1
            k = no + 4
        else:
            depth -= 1
            k = nc + 6
            if depth < 0:
                bad = True
                break
    assert bad or depth != 0, "变异未造成不平衡 —— 变异构造失败"





# ── 国君页三卡合并（2026-09-30 用户要求）─────────────────────────
# 【症状】④ 走势图 / ⑤ 明细数据 都只由 ③ 指标总览的「查看」按钮驱动, 却各自成卡:
#   ① 点「查看」得靠 _gtEnsureVisible 去展开下面两块, 否则屏幕上什么都不动(像按钮坏了);
#   ② 三块各占一条小模块索引项, 「选指标→看走势→看明细」被拆成三段。
# 【修法】并入 ③ 一张卡, 用虚线分隔的 <div> 子标题(不用 h3, 免得串索引名)。
# 【关键约束】三个容器 id 与三个 badge 一律不改 —— tests/js/gtja_module_harness.mjs
#   按 id 断言渲染行为(50 例)。

GTJA_MERGED_TITLE = '<h3>② 指标总览 / 走势图 / 明细'   # 【2026-10-14】原③ —— ② 四源取数状态卡删除后升为②


def _merged_card_span(src):
    """返回合并卡的 (起, 止)。以标题所在 div 起做 div 深度配平。

    ⚠️ 与 _gtja_tools_span 同源: 不能用"找下一个 </div>" —— 卡内外都有大量 </div>。
    """
    i = src.find(GTJA_MERGED_TITLE)
    if i == -1:
        return None
    start = src.rfind('<div', 0, i)
    if start == -1:
        return None
    depth = 0
    j = start
    while j < len(src):
        nxt_open = src.find('<div', j)
        nxt_close = src.find('</div>', j)
        if nxt_close == -1:
            return None
        if nxt_open != -1 and nxt_open < nxt_close:
            depth += 1
            j = nxt_open + 4
        else:
            depth -= 1
            j = nxt_close + 6
            if depth == 0:
                return (start, j)
    return None


def test_gtja_three_containers_share_one_card():
    """★ 总览表 / 走势图 / 明细 三个容器必须在同一张卡内。"""
    src = _src()
    span = _merged_card_span(src)
    assert span, "未找到合并卡（标题「③ 指标总览 / 走势图 / 明细」不见了？）"
    inside = src[span[0]:span[1]]
    for i in ('id="gt-indicators"', 'id="gt-chart"', 'id="gt-table"'):
        assert i in inside, (
            "%s 不在合并卡内 —— 三块被拆开了。合并的意义是「点查看→就地出图出表」,"
            "拆开后又要靠 _gtEnsureVisible 去展开兄弟块, 且各占一条索引项" % i
        )
    # 三个 badge 也要在卡内（徽标渲染的是本卡数据, 留在卡外会语义错位）
    for b in ('id="gt-ind-badge"', 'id="gt-chart-badge"', 'id="gt-tbl-badge"'):
        assert b in inside, "%s 不在合并卡内（徽标与它渲染的数据分离了）" % b


def test_gtja_merged_card_has_single_h3():
    """⚠️ 合并卡内只能有 1 个 h3 —— _secTitleOf 取第一个 h3 作索引名。"""
    src = _src()
    span = _merged_card_span(src)
    assert span, "未找到合并卡"
    inside = src[span[0]:span[1]]
    n = inside.count('<h3>')
    assert n == 1, (
        "合并卡内含 %d 个 h3（应为 1）。_secTitleOf 只取**卡内第一个** h3, "
        "多塞会把「走势图」「明细数据」当索引名, 卡内子标题请用 <div>" % n
    )


def test_gtja_old_standalone_cards_gone():
    """④⑤ 不得再作为独立 h3 卡片存在（合并必须真发生, 不是复制一份）。"""
    src = _src()
    for t in ('<h3>④ 走势图', '<h3>⑤ 明细数据'):
        assert t not in src, "%s 仍作为独立卡片存在 —— 合并没做干净（会重复渲染同一容器）" % t


def test_showgtjaindicator_no_longer_ensures_visible():
    """★ 点「查看」后结果就地可见, 不得再调 _gtEnsureVisible 去滚动/展开。

    ⚠️ _gtEnsureVisible **方法本体保留**（harness ④ 段守护它, 且是通用工具）,
       这里只禁 showGtjaIndicator 继续依赖它。
    """
    src = _src()
    body = _method_body(src, "showGtjaIndicator")
    assert "_gtEnsureVisible(" not in body, (
        "showGtjaIndicator 仍在调 _gtEnsureVisible —— 三卡已合并, 结果就地渲染在同一张卡里, "
        "再展开兄弟块只会把当前视线滚走"
    )
    # 反向确认渲染调用还在（防止"直接删空函数体"式假绿）
    assert "_renderGtjaChart(" in body and "_renderGtjaTable(" in body, (
        "showGtjaIndicator 不再渲染图表/明细 —— 合并后它仍必须驱动这两块"
    )


def _merge_assertions_ok(src):
    """这批断言在 src 上是否全过。"""
    span = _merged_card_span(src)
    if not span:
        return False
    inside = src[span[0]:span[1]]
    if not all(i in inside for i in
               ('id="gt-indicators"', 'id="gt-chart"', 'id="gt-table"')):
        return False
    if inside.count('<h3>') != 1:
        return False
    if '<h3>④ 走势图' in src or '<h3>⑤ 明细数据' in src:
        return False
    try:
        body = _method_body(src, "showGtjaIndicator")
    except Exception:
        return False
    if "_gtEnsureVisible(" in body:
        return False
    return "_renderGtjaChart(" in body and "_renderGtjaTable(" in body


def test_gtja_merge_baseline_passes():
    """先证未变异时全过, 否则「击杀」是假象。"""
    assert _merge_assertions_ok(_src()), "基线未通过 —— 断言本身写错了"


def test_gtja_merge_guard_kills_split_back():
    """【变异·真结构】把 #gt-table 那块**真的搬出**合并卡, 护栏必须变红。"""
    src = _src()
    span = _merged_card_span(src)
    assert span, "未找到合并卡"
    inside = src[span[0]:span[1]]
    tbl = inside.find('<!-- 明细数据')
    assert tbl != -1, "卡内找不到明细数据块"
    dup, end = _div_span(src, span[0] + tbl + inside[tbl:].find('<div'))
    assert dup.startswith("<div"), "明细块配对异常"
    start_abs = span[0] + tbl + inside[tbl:].find('<div')
    mutated = src[:start_abs] + src[end:] + "\n" + dup
    assert mutated != src, "变异未生效"
    assert not _merge_assertions_ok(mutated), (
        "把明细数据搬出合并卡后护栏没变红 —— 抓不住这次拆分"
    )


def test_gtja_merge_guard_kills_extra_h3():
    """【变异】给合并卡塞第二个 h3, 护栏必须变红（防索引串名）。"""
    src = _src()
    anchor = GTJA_MERGED_TITLE
    assert src.count(anchor) == 1
    mutated = src.replace(anchor, anchor + '\n    <h3>走势图</h3>', 1)
    assert mutated != src, "变异未生效"
    assert not _merge_assertions_ok(mutated), (
        "卡内多塞一个 h3 后护栏没变红 —— 抓不住「索引名被串掉」"
    )

# ── 国君页默认展开（2026-09-30 v18 → 2026-10-08 v20：覆盖矩阵已删, 剩余两卡平铺）──
#
# 【根因】原默认展开是「只展开第一个高度 ≥200px 的块」。问题在于**高度是渲染期
#   不可信的临时量**：索引在 loadGtja() 之后(0ms)构建时各卡还是「查询后显示。」空壳
#   (实测当时覆盖矩阵 110px、四源状态 45px)，真高(429px / 895px)要等接口回填才有。
#   于是「四源取数状态有内容却被默认收起」—— 这就是用户报的「仍然折叠」。
# 【修法】新增 _SEC_OPEN_BY_TAB 显式配置默认展开的下标集合，不再让几何量替用户决定
#   「哪些模块该被看见」。

# 【2026-10-14】页面迁入 warehouse ⇒ 配置键同步改(否则配置永远命中不到,
#   变成静默死配置)。
# 【2026-10-14 同日】「② 四源取数状态」卡按用户要求删除后, 下标含义刷新为:
#   0 = ① 查询控制, 1 = ② 指标总览(原③) —— 恰好仍是「卖方数据块的两张卡」, 故 [0,1] 不变。
#   ⚠ 判据见下方 test_gtja_default_open_indices_match_card_order（按 .card 真实序列核下标）。
GTJA_DEFAULT_OPEN_CFG = "'warehouse': [0, 1],"

# _resetSecFilter 末尾那行的**多行上下文**锚点（单行 `openRows.has(i)` 在
# _renderSectionIndex 里也有一份，不唯一定位就会改错目标）。
RESET_CHIP_SYNC_ANCHOR = (
    "      if (r.btn) r.btn.classList.toggle('is-open', openRows.has(i));\n"
    "    });\n"
    "    this._applySecFilter(idx, tabId);"
)


def _default_open_assertions_ok(src: str) -> bool:
    """这批断言在 src 上是否全过（供基线与变异自检共用）。"""
    # ① 显式配置存在，且国君页配了前两块
    if src.count(GTJA_DEFAULT_OPEN_CFG) != 1:
        return False
    if "_SEC_OPEN_BY_TAB: {" not in src:
        return False
    # ② 派发函数：未配置返回 null（=走旧逻辑），配置了返回 Set
    helper = _method_body(src, "_secDefaultOpen")
    if "return null" not in helper or "new Set(o)" not in helper:
        return False
    # ③ 建索引时用配置集合裁决默认展开
    render = _method_body(src, "_renderSectionIndex")
    if "this._secDefaultOpen(tabId)" not in render:
        return False
    if "openRows.has(i)" not in render:
        return False
    # ④ ★ 清空筛选后必须用**同一套**配置还原
    #    （漏了它，点一次「共 N 项」就会把刚看到的模块又收回去）
    reset = _method_body(src, "_resetSecFilter")
    if "this._secDefaultOpen(tabId)" not in reset:
        return False
    if "openRows.has(i)" not in reset:
        return False
    # ⑤ 两个方法体内不得残留悬空 openIdx 引用
    #    （改名漏改一处 ⇒ 运行时 ReferenceError：块的开合已改完、标签态同步被跳过，
    #      E2E 实测症状就是「清空后 ③ 仍撑着不收起」）
    for body in (render, reset):
        if "i === openIdx" in body:
            return False
    return True


def test_gtja_default_open_config_exists():
    """国君页必须显式配置剩余两卡（四源状态 + 指标总览）默认展开（平铺）。"""
    assert _default_open_assertions_ok(_src()), (
        "默认展开配置缺失或被回退 —— 用户一进国君页又会看到模块折叠"
    )


def test_gtja_default_open_indices_match_card_order():
    """配置的下标 [0, 1] 必须**真的**指向「① 查询控制 / ② 指标总览」这两张卡。

    【2026-10-14 重写】原用例断言「四源卡在指标总览之前」。四源卡已按用户要求删除 ⇒
    改为**按 .card 真实序列**核对下标：warehouse 页第 0 张 `.card` 是 ① 查询控制、
    第 1 张是 ② 指标总览（原③）。

    这比原实现更强：原来只比两个标题的先后，若将来在 card[0] 之前插入新卡，
    「序号整体后移」这件事原用例看不出来（两个标题的相对先后没变），
    而本用例会立刻红 —— 配置指错模块时页面照常渲染，肉眼极难发现。
    """
    import re as _re
    src = _src()
    i = src.find('<div id="tab-warehouse" class="tab">')
    assert i != -1, "找不到 #tab-warehouse"
    j = src.find("</main>", i)
    clean = _re.sub(r"<!--.*?-->", "", src[i:j], flags=_re.S)
    cards = [m.start() for m in _re.finditer(r'<div class="card"[^>]*>', clean)]
    assert len(cards) >= 2, (
        "warehouse 页 .card 少于 2 张（实测 %d）⇒ _SEC_OPEN_BY_TAB 的 [0,1] 已无意义，"
        "请同步修改配置" % len(cards)
    )
    seg0 = clean[cards[0]:cards[1]]
    seg1 = clean[cards[1]:cards[2] if len(cards) > 2 else len(clean)]
    assert "① 查询控制" in seg0, (
        "warehouse 页第 0 张 .card 不是「① 查询控制」⇒ 下标 0 指错了模块。实际内容: %s"
        % _re.sub(r"<[^>]+>", " ", seg0)[:80]
    )
    title = GTJA_MERGED_TITLE.replace("<h3>", "")   # 「② 指标总览 / 走势图 / 明细 」
    assert title in seg1, (
        "warehouse 页第 1 张 .card 不是「%s」⇒ _SEC_OPEN_BY_TAB['warehouse']=[0,1] 指错了模块。"
        "实际内容: %s" % (title, _re.sub(r"<[^>]+>", " ", seg1)[:80])
    )


def test_default_open_baseline_passes():
    """先证未变异时全过，否则后面「击杀」是假象。"""
    assert _default_open_assertions_ok(_src()), "基线未通过 —— 断言本身写错了"


def test_default_open_mutation_anchors_are_unique():
    """变异锚点必须唯一，否则会打到别处（本文件开头经验 3）。"""
    src = _src()
    for a in (
        GTJA_DEFAULT_OPEN_CFG,
        RESET_CHIP_SYNC_ANCHOR,
        "    return new Set(o);",
        "    const o = this._SEC_OPEN_BY_TAB[tabId];",
    ):
        assert src.count(a) == 1, "锚点不唯一（%d 处）: %r" % (src.count(a), a[:70])


def test_default_open_guard_kills_config_removed():
    """【变异·真结构】删掉国君页的默认展开配置，护栏必须变红。

    防的是「悄悄把配置去掉、退回成只展开首块」这种回退。
    """
    src = _src()
    mutated = src.replace(GTJA_DEFAULT_OPEN_CFG + "\n", "", 1)
    assert mutated != src, "变异未生效（没删掉配置行）"
    assert not _default_open_assertions_ok(mutated), (
        "去掉配置后护栏没变红 —— 抓不住「退回只展开首块」的回退"
    )


def test_default_open_guard_kills_reset_filter_regression():
    """【变异·真回归】把 _resetSecFilter 的 openRows.has(i) 改回悬空 openIdx。

    这正是本轮 E2E 抓到的真实缺陷（静态文本断言当时全绿）：
    清空筛选时抛 "openIdx is not defined"，块的开合已改完、标签态同步被跳过，
    表现为「刚展开的模块又被收回去」。
    """
    src = _src()
    regressed = RESET_CHIP_SYNC_ANCHOR.replace("openRows.has(i)", "i === openIdx")
    assert regressed != RESET_CHIP_SYNC_ANCHOR
    mutated = src.replace(RESET_CHIP_SYNC_ANCHOR, regressed, 1)
    assert mutated != src, "变异未生效"
    assert not _default_open_assertions_ok(mutated), (
        "把 _resetSecFilter 退回 openIdx 后护栏没变红 —— 抓不住这类改名漏改"
    )


def test_default_open_guard_kills_helper_neutralized():
    """【变异】让 _secDefaultOpen 恒返回 null（配置形同虚设），护栏必须变红。"""
    src = _src()
    mutated = src.replace("    return new Set(o);", "    return null;", 1)
    assert mutated != src, "变异未生效"
    assert not _default_open_assertions_ok(mutated), (
        "配置失效后护栏没变红 —— _secDefaultOpen 被架空也测不出来"
    )


# ── 一键修复「观点生成失败」占位(2026-10-08)─────────────────────────

def test_reconclude_stuck_button_exists():
    """数据仓库页必须有「一键修复」常驻条 + 占位计数徽标(2026-10-08 新增)。

    ⚠️ 按钮必须在 tab 顶层的**常驻区**(res-repair-bar, 非 .card 折叠块) ——
    放在研报库卡片内会被 _secScan 包进 sec-block 且默认收起(高度 0)裁掉,
    用户看不到也点不到(2026-10-08 踩过)。
    """
    src = _src()
    assert 'id="res-repair-bar"' in src, "一键修复常驻条 #res-repair-bar 缺失"
    assert 'id="res-repair-btn"' in src, "一键修复按钮 #res-repair-btn 缺失"
    assert 'id="res-stuck-count"' in src, "占位计数徽标 #res-stuck-count 缺失"
    assert 'App.reconcludeStuck()' in src, "一键修复按钮未接线 reconcludeStuck"
    assert 'refreshStuckCount' in src, "refreshStuckCount 方法缺失(进入数据仓库页刷新占位计数)"
    assert 'reconcludeStuck' in src, "reconcludeStuck 方法缺失(调 POST 启动修复)"
    # 按钮必须位于 res-repair-bar 内部(常驻不折叠), 不得在研报库卡片内
    i_bar = src.find('id="res-repair-bar"')
    i_btn = src.find('id="res-repair-btn"')
    assert i_bar != -1 and i_btn > i_bar, "一键修复按钮不在常驻条内部 —— 会被折叠裁掉"


def test_reconclude_stuck_button_mutation():
    """【变异】把一键修复按钮删掉,护栏必须红。"""
    src = _src()
    assert 'id="res-repair-btn"' in src, "变异前置:按钮不存在"
    mutated = src.replace('id="res-repair-btn" onclick="App.reconcludeStuck()">🔧 一键修复</button>', '<!-- 变异删除按钮 -->')
    assert mutated != src, "变异未生效"
    assert not _reconclude_stuck_assertions_ok(mutated), (
        "删掉一键修复按钮后护栏没变红 —— 说明护栏抓不住这个回退"
    )


def _reconclude_stuck_assertions_ok(src):
    """返回 True 表示一键修复按钮/徽标/方法在 src 上全过。"""
    if 'id="res-repair-bar"' not in src:
        return False
    if 'id="res-repair-btn"' not in src:
        return False
    if 'id="res-stuck-count"' not in src:
        return False
    if 'refreshStuckCount' not in src or 'reconcludeStuck' not in src:
        return False
    return True


# ── 走势图自动渲染(2026-10-08)──────────────────────────────────────

def test_gtja_auto_render_first_indicator():
    """进入国君页查询后必须自动渲染第一个指标走势图(2026-10-08 新增)。

    【背景】删覆盖矩阵后指标总览卡默认展开, 走势图区域(340px 空白容器)直接暴露,
    但走势图原本靠点「查看」才渲染 ⇒ 用户误以为"走势图不显示"。修复: loadGtjaOverview
    渲染指标后自动 showGtjaIndicator(0)。此护栏防将来有人把这行删回退。
    """
    src = _src()
    assert "this.showGtjaIndicator(0);" in src, (
        "loadGtjaOverview 里自动渲染第一个指标走势图的调用缺失 —— "
        "删覆盖矩阵后指标总览卡默认展开, 不自动渲染会导致走势图区域空白"
    )


def test_gtja_auto_render_mutation():
    """【变异】把自动渲染调用删掉, 护栏必须红。"""
    src = _src()
    assert "this.showGtjaIndicator(0);" in src, "变异前置:自动渲染行不存在"
    mutated = src.replace("this.showGtjaIndicator(0);", "// 变异删除自动渲染")
    assert mutated != src, "变异未生效"
    assert "this.showGtjaIndicator(0);" not in mutated, "变异未删掉自动渲染行"
    assert not _gtja_auto_render_ok(mutated), (
        "删掉自动渲染调用后护栏没变红 —— 说明护栏抓不住这个回退"
    )


def _gtja_auto_render_ok(src):
    """返回 True 表示自动渲染行在 src 上存在。"""
    return "this.showGtjaIndicator(0);" in src


# ══════════════════════════════════════════════════════════════
# 2026-10-14 结构重组：把决策钉死（防后人手滑改回）
# ══════════════════════════════════════════════════════════════
def _strip_c(s: str) -> str:
    """剥掉 HTML 注释后的活代码。

    ★ 本文件多处断言用裸字符串，而**迁移说明注释里会照抄被删掉的原写法**
      （如 `<div id="tab-gtja" class="tab">`）⇒ 不剥注释会把正确结构误报成"没删干净"。
    """
    import re as _re
    return _re.sub(r"<!--.*?-->", "", s, flags=_re.S)


def test_tab_restructure_invariants():
    """结构重组的三条产品决策必须同时成立。

    用户 2026-10-14 拍板：
      ① 数据仓库下并入 卖方数据(原国君数据改名) + 研报库 + 自传数据
      ② 基本面量化下并入 仓单分析
      ③ 情绪数据移到「基本面量化」后、「外文研报」前
    """
    src = _src()
    live = _strip_c(src)

    # ① nav 里不再有 gtja / warrant（并入 ⇒ 独立入口删除）
    assert 'data-tab="gtja"' not in live, "nav 仍有「国君数据」入口 —— 它已并入数据仓库"
    assert 'data-tab="warrant"' not in live, "nav 仍有「仓单分析」入口 —— 它已并入基本面量化"
    assert '<div id="tab-gtja" class="tab">' not in live, "#tab-gtja 容器未删"
    assert '<div id="tab-warrant" class="tab">' not in live, "#tab-warrant 容器未删"

    # ② nav 顺序：情绪数据 在 基本面量化 后、外文研报 前
    import re as _re
    nav = src[src.index('<nav id="nav">', 90000):]
    nav = nav[: nav.index("</nav>")]
    order = _re.findall(r'data-tab="([a-z]+)"', nav)
    assert order.index("fund") < order.index("sentiment") < order.index("foreign"), (
        "导航顺序不符：情绪数据必须位于「基本面量化」后、「外文研报」前（当前 %s）" % order
    )

    # ③ 控制条铁律：一个 tab 恰好 ≤1 个 .page-tools
    #    卖方数据块在 warehouse（该页无 page-tools）；仓单块在 fund（并入方控制条已降级）
    wh = live[live.index('<div id="tab-warehouse" class="tab">'):]
    wh = wh[: wh.index('<div id="tab-variety" class="tab">')] if '<div id="tab-variety"' in wh else wh
    assert '<div id="tab-gtja-tools">' in wh, "卖方数据块不在 warehouse 内"
    assert 'class="page-tools"' not in wh[:2000], (
        "并入 warehouse 的控制条未降为页内卡 —— 会让 warehouse 出现 page-tools"
    )

    # ④ 目标页接线：进 warehouse 要拉卖方数据、进 fund 要拉仓单
    assert "this.loadWarehouse(); this.loadGtja();" in src, "warehouse 未接 loadGtja"
    # 【2026-10-16】原断言写死 `this.loadFund(); this.loadWarrant();` 一行；fund 的两视图
    #   改造把它拆成了多行分支 ⇒ 改成**格式无关**的接线断言（守的是"进 fund 会拉仓单"
    #   这条行为，不是那行文本怎么排版）。
    assert re.search(r"if \(t === 'fund'\) \{[\s\S]{0,800}?this\.loadWarrant\(\)", src), (
        "fund 未接 loadWarrant（进基本面页不会加载仓单子模块）"
    )

    # ⑤ Ctrl+N 与 nav 对齐（7 项）且上界同步
    m = _re.search(r"const tabs = \[([^\]]+)\]", src)
    arr = _re.findall(r"'([a-z]+)'", m.group(1))
    assert arr == [t for t in order if t != "variety"], (
        "Ctrl+N 数组与导航不一致：%s vs %s" % (arr, order)
    )
    assert "e.key <= '7'" in src, "Ctrl+N 上界未随 7 页调整"


# ════════════════════════════════════════════════════════════════════
# 2026-10-14 三项内容精简的产品决策（用户拍板）—— 钉死，防止被无意识加回来
# ════════════════════════════════════════════════════════════════════
def test_trim3_content_decisions():
    """①删四源取数状态 ②删价格vs仓单图 ③研报指标整合为一个折叠块（默认收起）。

    【为什么值得钉】三条都是**产品决策**（"这块不看了"），不是 bug 修复 ——
    下次有人 refactor 时很容易顺手把卡加回来、或把折叠块拆回平铺，
    而页面照常渲染、肉眼难发现（尤其③：拆回平铺也"能看"）。
    """
    import re as _re
    src = _src()
    live = _strip_c(src)
    # ⚠ 判"页面文本"必须只看 **HTML 区**（`<script>` 之前）且**连 CSS 注释一起剥**：
    #   `_strip_c` 只剥 `<!-- -->`，而 CSS 的 `/* */` 与 JS 的 `//` 里提到旧模块名是
    #   **合理的**（记录"这里删过什么"）。不区分就会假红（本次实测踩到）。
    html_only = _re.sub(r"/\*.*?\*/", "", _strip_c(src[: src.index("<script>")]), flags=_re.S)

    # ① 数据仓库：四源取数状态卡已删，原③升为②
    assert "<h3>② 四源取数状态</h3>" not in live, "「四源取数状态」卡又出现了"
    assert 'id="gt-sources"' not in live, "#gt-sources 容器未删"
    assert "<h3>② 指标总览 / 走势图 / 明细" in live, "原③指标总览未升为②"
    assert "<h3>③ 指标总览" not in live, "旧的③编号残留"
    #  信息不丢：四源可用数仍在提示条里（loadGtja 写 #gt-note）
    assert "四源 ${c.available_sources" in src, (
        "四源取数状态卡删了、但 #gt-note 里的汇总也没了 ⇒ 信息真的丢了"
    )

    # ② 基本面量化：价格 vs 仓单库存 已删，关联分析保留
    assert "价格 vs 仓单库存" not in html_only, (
        "「价格 vs 仓单库存」图又出现了（只查 HTML 区, CSS/JS 注释里提及是允许的）"
    )
    assert 'id="chart-f-pi"' not in live, "#chart-f-pi 容器未删"
    assert 'id="f-pi-badge"' not in live, "#f-pi-badge 徽标未删"
    assert 'id="f-analysis-badge"' in live, "「价格-库存关联分析」被误删（只该删双轴图）"

    # ③ 研报基本面指标：一个折叠块（默认收起）
    assert " toggleResearchSection(pfx) {" in src, "toggleResearchSection 未定义"
    body = _method_body(src, "_renderResearchSection")
    # ⚠ 必须**先剥注释再查代码**：我在这段代码的注释里写了"不用 display:none"、
    #   提到 `<h3>` 等自述文字，不剥注释会把"解释"当成"代码"而假红（本次实测踩到）。
    body_code = _re.sub(r"/\*.*?\*/", "", body, flags=_re.S)
    body_code = _re.sub(r"//[^\n]*", "", body_code)
    assert "max-height:" in body_code, "折叠未用 max-height"
    assert "display:none" not in body_code, (
        "折叠用了 display:none ⇒ 里面的 ECharts 会 init 成 0×0 空白画布"
    )
    assert body_code.count("<h3") == 1, (
        "折叠块内出现 %d 个 <h3> —— 合并卡只许 1 个（多了会让 _secTitleOf 串名）"
        % body_code.count("<h3")
    )
    assert body_code.count('class="card"') == 1, (
        "折叠块外层出现 %d 个 .card（应为 1 —— 原来 7 类各一张）"
        % body_code.count('class="card"')
    )
    # 折叠状态跨渲染保持必须三处俱全（只查 "_rsOpen" 出现过太松 —— 变异自检实测：
    #   删掉初始化行后别处的引用仍会命中，等于没测）
    assert "this._rsOpen = this._rsOpen || {};" in src, (
        "折叠状态未初始化（toggle 与 render 不共用同一个对象 ⇒ 状态丢失）"
    )
    assert "this._rsOpen[P] =" in src, "toggle 未把折叠状态写回 _rsOpen"
    assert "this._rsOpen && this._rsOpen[P]" in body_code, (
        "render 未读回 _rsOpen ⇒ 切品种重渲染后会被强制收回，用户每次都要再点一遍"
    )

    # ④ 被删容器的取值点必须全部带守卫（防 TypeError）
    assert 'if (!el) return' in _method_body(src, "_renderGtjaSources"), (
        "#gt-sources 容器已删，_renderGtjaSources 缺 `if (!el) return` 守卫"
    )
    assert "if (piBadge)" in src, "#f-pi-badge 徽标已删，取值点缺 `if (piBadge)` 守卫"
    assert "_renderDashCharts(d, { pi:" not in src, (
        "_renderDashCharts 仍在传已删除的 pi 容器"
    )

    # ⑤ 版本号已 +1（铁律：tab 内卡片增删必须 +1，否则旧浏览器留僵尸索引项）
    # 【2026-10-14 后续】本轮删卡后为 26；同日「多空逻辑归位」（信号卡迁出 + 排行卡迁出）
    # 又增删了卡片 ⇒ 已升为 27。本断言只要求"**不是**初始的 25"，具体值由
    # tests/test_multi_logic_relocation.py::test_sec_index_version_bumped 钉住，
    # 避免每增删一次卡都要改两处。
    assert "_SEC_IDX_VER: '25'," not in src, "_SEC_IDX_VER 未随卡片增删 +1"
