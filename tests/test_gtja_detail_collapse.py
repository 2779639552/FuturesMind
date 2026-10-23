"""数据仓库「明细数据」折叠栏护栏(2026-10-19)。

【需求】用户: "将数据仓库中的明细数据栏折叠, 只在点击查看后下拉显示"。

【本文件守什么】
  1. ★ 默认必须**收起** —— 进页面会自动渲染第一个指标(showGtjaIndicator(0)),
     若自动渲染也展开, 等于"默认展开"(115 行明细照样占满首屏), 与诉求相反。
  2. ★ 只有**主动点「查看」**才展开(按钮传 expandDetail=true)。
  3. 表头可点开/收(与按钮共用 toggleGtjaDetail 状态机, 各写一份必不同步)。
  4. #gt-table 必须留在 DOM 里(折叠只靠 .expander 的 display:none) ——
     _renderGtjaTable 与多个测试都按 id 取它, 删了就白屏。
  5. 不得把 display:none 直接写死在 #gt-table 上(那样"展开"要逐个改内联样式)。
"""

from __future__ import annotations

import io
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
TPL = ROOT / "web_template.html"


def _s() -> str:
    return io.open(TPL, encoding="utf-8").read()


def test_expander_defaults_collapsed():
    """★ 静态 HTML 不能带 open 类(带了就是默认展开)。"""
    s = _s()
    m = re.search(r'<div class="([^"]*)" id="gt-detail-expander"', s)
    assert m, "找不到 #gt-detail-expander(折叠栏容器)"
    classes = m.group(1).split()
    assert "expander" in classes, "必须复用全站 .expander 组件"
    assert "open" not in classes, "默认不能带 open 类 ⇒ 一进页面就展开"


def test_detail_header_is_clickable():
    s = _s()
    seg = s[s.find('id="gt-detail-expander"'):s.find('id="gt-detail-expander"') + 900]
    assert 'class="expander-header"' in seg
    assert "App.toggleGtjaDetail()" in seg, "表头没有绑定开关 ⇒ 展开后收不回去"
    assert 'class="arrow"' in seg, "缺箭头, 用户看不出可折叠"


def test_gt_table_stays_in_dom_inside_expander_body():
    """★ 折叠靠 .expander-body 的 display:none, 不能删 #gt-table 或给它写死 display:none。"""
    s = _s()
    i_body = s.find('id="gt-detail-expander"')
    seg = s[i_body:i_body + 1400]
    assert '<div class="expander-body"' in seg
    assert 'id="gt-table"' in seg, "#gt-table 不在折叠栏里(或已被删)"
    m = re.search(r'<div id="gt-table"([^>]*)>', s)
    assert m and "display:none" not in m.group(1), "#gt-table 不要写死 display:none"


def test_only_explicit_view_click_expands():
    """★ 「查看」按钮传 true; 进页自动渲染不带 true。"""
    s = _s()
    assert "App.showGtjaIndicator(${i}, true)" in s, "查看按钮没传 expandDetail ⇒ 点了不展开"
    # 自动渲染那行必须保持无参
    m = re.search(r"if \(\(d\.indicators \|\| \[\]\)\.length\) (this\.showGtjaIndicator\([^)]*\))", s)
    assert m, "找不到进页自动渲染第一个指标的调用"
    assert m.group(1) == "this.showGtjaIndicator(0)", \
        "自动渲染不得传 expandDetail ⇒ 否则一进页面明细就是展开的"


def test_toggle_method_uses_single_state_machine():
    s = _s()
    i = s.find(" toggleGtjaDetail(force) {")
    assert i > 0
    body = s[i:i + 700]
    assert "classList.contains('open')" in body
    assert "classList.toggle('open', open)" in body
    assert "force === undefined" in body, "省略参数应表示「切换」, 否则表头点了没反应"


def test_show_gtja_indicator_guards_expand():
    s = _s()
    i = s.find(" showGtjaIndicator(i, expandDetail) {")
    assert i > 0, "showGtjaIndicator 必须接收 expandDetail"
    body = s[i:i + 1200]
    assert "if (expandDetail) this.toggleGtjaDetail(true);" in body, \
        "条件展开缺失 ⇒ 要么永远不展开, 要么自动渲染也展开"


def test_warehouse_tab_div_balance():
    """★ 结构类改动必须核对 div 配平(删/改卡片曾经把后续卡挤出 tab)。

    【为什么不数全文件】整份模板的 `<div` 比 `</div>` 多 3 —— 这是**脚本里拼 HTML
      的模板字符串**造成的(改动前的备份同样是 3, 与本轮无关)。真正要守的是
      「数据仓库这一页的 DOM 层级自洽」: 从 `#tab-warehouse` 起按 depth 走,
      必须在本文档内回到 0 且中途不为负(负 = 多写了 `</div>`, 会把后续卡踢出 tab)。
    """
    s = re.sub(r"<!--.*?-->", "", _s(), flags=re.S)
    s = re.sub(r"<script\b[^>]*>.*?</script>", "", s, flags=re.S)
    i = s.find('id="tab-warehouse"')
    assert i > 0, "找不到 #tab-warehouse"
    depth, j = 0, s.rfind("<div", 0, i)
    while j < len(s):
        if s.startswith("</div>", j):
            depth -= 1
            assert depth >= 0, "多写了 </div> @%d: %r" % (j, s[j - 60:j + 10])
            j += 6
            if depth == 0:
                # ★ 只"回到 0"不够: 多写一个 `</div>` 会让它**提前**闭合(内容被挤出 tab)。
                # 判据: 闭合点与「下一个 tab / </main>」之间只能是空白。
                # 下一个「tab 容器」或 </main>; 不能只找 `<div id="tab-` ——
                # 页内有 `id="tab-gtja-tools"` 这类**控制条**(不是 tab) 会误判成边界。
                m = re.search(r'</main>|<div id="tab-[a-z]+" class="tab"', s[j:])
                nxt = j + (m.start() if m else len(s))
                assert not s[j:nxt].strip(), \
                    "#tab-warehouse 提前闭合(与下一个 tab 之间还有内容) ⇒ 卡片被挤出 tab"
                return
            continue
        if s.startswith("<div", j):
            depth += 1
        j += 1
    raise AssertionError("tab-warehouse 的 div 在文档结束前没有配平(缺 </div>)")
