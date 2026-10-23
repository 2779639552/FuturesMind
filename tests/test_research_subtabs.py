"""研报页二级页签护栏(2026-10-19)。

【需求】用户: "将研报页的改动应用到原项目中" —— 来源是实验副本里验证过的
  「问答 / 每日总结 / 单品种 / 观点总览」四视图 + 页面头。

【本文件守什么】
  1. ★ 四个视图必须齐、默认必须是「每日总结」(用户点进研报页最常看的是当日总结)。
  2. ★ 研报的问答卡装在不显眼的容器里: #tab-research-tools(.page-tools)
     —— 必须随「问答」联动显隐, 否则问答会与其它视图同屏(副本里实测踩过)。
  3. ★ 只切换顶层卡片的 display: 不许改 DOM 归属、不许 display:none 掉整个容器,
     否则卡片里的异步渲染/图表会受影响, 也很难回退。
  4. 认不出归属的卡片必须保留(宁可多显示, 不要凭空藏内容)。
  5. 页面头只能注入一次(来回切页不得重复插), 且只在研报页注入。
  6. 不许把实验副本的只读开关(window.__LAB_NO_LLM)带进生产。
"""

from __future__ import annotations

import io
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
TPL = ROOT / "web_template.html"


def _s() -> str:
    return io.open(TPL, encoding="utf-8").read()


MARK = "// ══ 【2026-10-19】研报页二级页签"


def _script() -> str:
    """取注入块本体(已并入主脚本, 用注释标记 + IIFE 结尾定位)。"""
    s = _s()
    i = s.find(MARK)
    assert i > 0, "找不到研报页签代码块(注释标记)"
    j = s.find("})();", i)
    assert j > i
    return s[i:j + 5]


def test_blocks_exist():
    s = _s()
    assert '<style id="rsh-subtabs">' in s
    assert MARK in s


def test_inline_script_count_stays_one():
    """★ 页签脚本必须**并在主脚本里**, 不能新开 <script> 块。

    tests/js/overlay_variety_picker_harness.mjs 断言"恰好 1 个内联脚本块",
    单独成块会让它变红(实测: 期望恰好 1 个, 实际 2)。
    """
    s = _s()
    inline = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", s, re.S)
    assert len(inline) == 1, f"内联脚本块必须恰好 1 个, 实际 {len(inline)}"
    assert '<script id="rsh-subtabs">' not in s, "不要再单开 script 块"


def test_four_views_and_default():
    js = _script()
    for label in ("问答", "每日总结", "单品种", "观点总览"):
        assert f"n: '{label}'" in js, f"缺少页签 {label}"
    assert "def: 'daily'" in js, "默认视图必须是每日总结"
    assert "'每日总结'" in js and "研报每日总结" not in js  # 匹配词走 m: ['每日总结']


def test_qa_card_container_follows_tab():
    """★ 问答卡在 .page-tools 里, 必须联动显隐。"""
    js = _script()
    assert "toolsIn: 'qa'" in js, "缺少 toolsIn 配置"
    assert "tab.querySelector(':scope > .page-tools')" in js, "没有取 .page-tools"
    assert "tools.style.display = (k === CONF.toolsIn) ? '' : 'none'" in js, \
        "问答容器没有被联动显隐 ⇒ 问答会与其它视图同屏"


def test_only_toggles_top_level_cards():
    js = _script()
    assert "tab.children" in js and "classList.contains('card')" in js, \
        "必须只遍历 tab 的直接子卡片, 不能递归"
    assert re.search(r"c\.style\.display = \(!hit \|\| hit\.k === k\) \? '' : 'none'", js), \
        "认不出归属的卡片应保留(条件里要有 !hit)"
    assert "removeChild" not in js, "不许删节点(只切换 display)"
    assert "innerHTML =" in js  # 只允许用于页面头自己的节点


def test_head_injected_once_and_scoped():
    js = _script()
    assert "TAB = 'research'" in js, "必须以研报页为作用域"
    assert "heads" not in js  # 用 querySelector 幂等, 不是数个数
    assert "tab.querySelector(':scope > .rsh-head')" in js, "页面头必须幂等(已存在就复用)"
    assert "META = ['RESEARCH', '研报'" in js


def test_switch_hook_is_wrapped_not_replaced():
    js = _script()
    assert "App.switchTab = function (t) {" in js
    assert "orig.apply(this, arguments)" in js, "必须调用原 switchTab, 否则整站切页失效"
    assert "t === TAB" in js, "只在进入研报页时挂载"


def test_no_lab_only_flags_leak():
    s = _s()
    assert "__LAB_NO_LLM" not in s, "实验副本的只读开关不能被带进生产"
    assert "AGENTSENSE_NO_SCHEDULER" not in s


def test_research_tab_div_balance():
    """★ 结构类改动必须核对 div 配平(JS 里拼的 HTML 不参与, 先剥 script/注释)。"""
    s = re.sub(r"<!--.*?-->", "", _s(), flags=re.S)
    s = re.sub(r"<script\b[^>]*>.*?</script>", "", s, flags=re.S)
    i = s.find('id="tab-research"')
    assert i > 0, "找不到 #tab-research"
    depth, j = 0, s.rfind("<div", 0, i)
    while j < len(s):
        if s.startswith("</div>", j):
            depth -= 1
            assert depth >= 0, "多写了 </div> @%d" % j
            j += 6
            if depth == 0:
                m = re.search(r'</main>|<div id="tab-[a-z]+" class="tab"', s[j:])
                nxt = j + (m.start() if m else len(s))
                assert not s[j:nxt].strip(), "#tab-research 提前闭合(与下一个 tab 之间有残留内容)"
                return
            continue
        if s.startswith("<div", j):
            depth += 1
        j += 1
    raise AssertionError("tab-research 的 div 没有配平(缺 </div>)")
