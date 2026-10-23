"""点击模块名回总览的静态契约护栏(2026-10-21)。

【需求】用户原话: "修改技术量化与基本面量化的展示页部分: 在单品种明细页面下时,
点击大模块名部分(如: 基本面量化)直接返回到全品种总览页"。

【本文件守什么(行为细节由 tests/js/nav_back_overview_harness.mjs 真执行)】
  1. ★ 只有 **nav 点击**才回退: Ctrl+1~7 与程序化 switchTab 不传 opts ⇒ 保留既定的
     "切走再切回不丢上下文"(否则从别的页跳回某品种会被强制弹回总览)。
  2. ★ 回退必须在 lazy-load 分支**之前**执行, 否则 switchTab 先按"明细态"跑
     loadQuant/loadFund 拉一轮明细(有的一次要等几十秒), 再切视图 ⇒ 白算一轮。
  3. ★ 回退只切 display, **不许**在里面调 loadQuantOverview/loadFundRanking:
     switchTab 紧接着就会按新视图状态调它们(后者顺带重排定时器), 这里再调 = 重复请求。
  4. 基本面明细页那个「基本面量化」大字标题也要可点(用户看到并想点的就是它)。
"""

from __future__ import annotations

import io
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
TPL = ROOT / "web_template.html"


def _s() -> str:
    return io.open(TPL, encoding="utf-8").read()


def _method() -> str:
    s = _s()
    m = re.search(r"\n\s*_navBackToOverview\(t\)\s*\{", s)
    assert m, "找不到 _navBackToOverview 方法定义"
    i = s.index("{", m.start())
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[m.start():j + 1]
    raise AssertionError("_navBackToOverview 花括号未闭合")


def test_nav_click_passes_from_nav_flag():
    s = _s()
    assert "this.switchTab(btn.dataset.tab, { fromNav: true })" in s, "nav 点击没带来源标记"
    # 快捷键必须保持不传 opts（保留上下文语义）
    assert re.search(r"this\.switchTab\(tabs\[parseInt\(e\.key\) - 1\]\);", s), "Ctrl+数字不该带 fromNav"


def test_switch_tab_accepts_opts_and_calls_early():
    s = _s()
    assert "switchTab(t, opts) {" in s, "switchTab 未接收 opts"
    call = "if (opts && opts.fromNav) this._navBackToOverview(t);"
    assert s.count(call) == 1, "回退调用应恰好 1 处"
    # 必须在 two-view 的 lazy-load 分支之前
    i_call = s.index(call)
    i_fund = s.index("if (t === 'fund') {", i_call)
    i_quant = s.index("if (t === 'quant') {", i_call)
    assert i_call < i_fund and i_call < i_quant, "回退必须在 lazy-load 之前，否则先白拉一轮明细"


def test_back_helper_only_toggles_display():
    m = _method()
    for bad in ("loadQuantOverview", "loadFundRanking", "loadQuant(", "loadFund("):
        assert bad not in m, "回退里不该调 %s（switchTab 紧接着会调，重复请求）" % bad
    assert "dv.style.display = 'none'" in m
    # 总览必须还原成空串（交还样式表），不能设 'block'
    assert re.search(r"ov\.style\.display = ''", m), "总览应还原为空串"
    assert "display = 'block'" not in m


def test_back_helper_covers_both_modules():
    m = _method()
    assert "quant: ['q-detail-view', 'q-overview-view']" in m
    assert "fund: ['f-detail-view', 'f-overview-view']" in m
    assert "return false" in m and "return true" in m


def test_back_helper_is_safe_without_scroll_to():
    m = _method()
    assert "typeof window !== 'undefined' && window.scrollTo" in m, "缺 window.scrollTo 兜底"


def test_fund_module_heading_is_clickable():
    s = _s()
    m = re.search(r'<span onclick="App\.fBackOverview\(\)"[^>]*>基本面量化</span>', s)
    assert m, "基本面明细页的「基本面量化」标题应可点返回总览"
    assert "cursor:pointer" in m.group(0), "可点标题要有手型光标"


def test_template_stays_crlf():
    raw = io.open(TPL, "rb").read()
    assert raw.count(b"\n") == raw.count(b"\r\n"), "模板出现裸 LF(本项目铁律：CRLF)"
