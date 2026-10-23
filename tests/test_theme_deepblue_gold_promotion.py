# -*- coding: utf-8 -*-
r"""深蓝金全站推广（2026-10-13）回归护栏。

背景：用户要求把此前只作用于 ``[data-theme="light"] #tab-quant`` 的
「冷白底 + 深蓝(navy) + 金(gold)」风格推广到所有页面与模块（**颜色仅亮色，暗色不动**；
**字体于当天追加"字体也统一一下"，拍板全站统一含暗色** —— 字族规则一律不带主题前缀，
亮/暗共用同一套字族，暗色只换字族、颜色仍一丝不动）。
落地分五块（对应任务 #144~#149）：
  #144 图表令牌：亮色 --chart-brand/-axis/-grid/-line 换深蓝金；_baseChartOptions 去 isDark 分支
  #145 CSS 令牌：#tab-quant 作用域块删除，约 40 条令牌重映射上移全局亮色块
  #146 字体规则：serif 标题 / num 表格 / sans 正文 —— 10-13 当天全站统一（含暗色字族，
       并补入 table th 与研报正文 h1-h3 两个漏出兵）
  #147 遗留收敛：signal-banner 亮色蓝渐变 → 冷白；审计注释同步
  #149 切主题重染：_initChart 记录 option 快照 + _repaintCharts 旧色→新色替换（不重取数）

写断言的三条经验（沿用 test_page_tools_variety_state 的教训）：
  1) 不做整份源码子串断言当唯一判据 —— 注释里会合法地出现 isDark / CHART.up 这类字样，
     统一先数一遍出现位置再决定判据；本文件的 needle 都是代码形态（带 ``const `` / 引号 /
     分号），注释里不会以同样形态出现。
  2) 亮色块用花括号配平抽取，不用正则 ``[^}]*`` —— 块内有 linear-gradient(...) 嵌套括号，
     正则会在第一个 ``}`` 处截断，断言于是静默永真。
  3) 变异自检必做：喂改坏的副本（THEME_TEMPLATE 环境变量指向副本），断言必须红；
     全绿的守卫可能是永真守卫（本项目 mutation-guard-verification 踩过）。
  4) 【2026-10-13 新增】CSS 注释里**禁出现字面量 ``*/``**：本次推广的注释写过令牌清单
     ``--btn-primary*/--thead-bg``，``*/`` 把注释提前闭合，残文被浏览器当成巨型选择器，
     正好把 ``[data-theme="light"] .card h3`` 规则吞掉 —— 文件里规则文本赫然在，浏览器
     里却完全不生效（h3 字体不换宋体），且无任何报错。守卫必须按浏览器方式（注释感知）
     抽规则，证明推广规则是"真规则"而不是注释碎片。
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = Path(os.environ.get("THEME_TEMPLATE", ROOT / "web_template.html"))


def _src() -> str:
    return TEMPLATE.read_text(encoding="utf-8", errors="replace")


def _css_block(src: str, selector: str) -> str:
    """按花括号配平抽取 selector 规则块的内容（支持嵌套括号的多行值）。"""
    idx = src.find(selector)
    assert idx != -1, f"选择器不存在: {selector}"
    i = src.index("{", idx)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i + 1 : j]
    raise AssertionError(f"花括号不配平: {selector}")


def _css_rules(css: str):
    """按**浏览器方式**切分 CSS：``/*`` 到第一个 ``*/`` 是注释（注释内禁再出现 ``*/``）；
    其余区域 ``选择器 { 体 }`` 为规则。返回 [(selector, body), ...]。

    为什么必须自己切而不用正则：浏览器对"注释里含 ``*/``"的处理是**提前闭合**，
    残文会并进后面规则的 selector —— 这正是 2026-10-13 吞掉 .card h3 规则的故障形态。
    只有复刻同样的切分，断言才能证明"规则真的能被浏览器解析"。
    """
    rules = []
    i, n = 0, len(css)
    sel_buf: list = []
    while i < n:
        if css.startswith("/*", i):
            j = css.find("*/", i + 2)
            if j == -1:
                raise AssertionError("CSS 注释未闭合")
            i = j + 2
            continue
        if css[i] == "{":
            depth, j = 1, i + 1
            while j < n and depth:
                if css.startswith("/*", j):
                    k = css.find("*/", j + 2)
                    j = (k + 2) if k != -1 else n
                    continue
                if css[j] == "{":
                    depth += 1
                elif css[j] == "}":
                    depth -= 1
                j += 1
            rules.append(("".join(sel_buf).strip(), css[i + 1 : j - 1]))
            sel_buf = []
            i = j
            continue
        sel_buf.append(css[i])
        i += 1
    return rules


def _style_block(src: str) -> str:
    return src[src.index("<style>") + 7 : src.index("</style>")]


@pytest.fixture(scope="module")
def src():
    assert TEMPLATE.exists(), f"模板不存在: {TEMPLATE}"
    return _src()


# ─────────────────────────────────────────────────────────────
# #145 CSS 令牌：深蓝金已上移为全局亮色
# ─────────────────────────────────────────────────────────────
class TestGlobalLightTokens:
    def test_brand_family_is_deepblue_gold(self, src):
        light = _css_block(src, '[data-theme="light"] {')
        assert "--brand: var(--gold-d);" in light, "亮色 --brand 应为 gold-d（浅底小字 5.01:1）"
        assert "--brand-hover: var(--navy);" in light
        assert "--brand-line: var(--gold);" in light, "--brand-line 只做边框，取 2.85:1 的 gold"
        assert "--brand: #5b5bd6;" not in light, "糖果紫品牌色必须退场"

    def test_button_and_surface_tokens(self, src):
        light = _css_block(src, '[data-theme="light"] {')
        assert "--btn-primary-bg: var(--navy);" in light, "主按钮 = 实心深蓝底"
        assert "--btn-primary-fg: var(--gold-w);" in light, "主按钮字 = gold-w（6.89:1 官方搭配）"
        assert "--bg: #f7f9fb;" in light, "页面底 = 冷白"
        assert "--bg-input: #ffffff;" in light
        assert "--border: var(--line);" in light
        assert "--text: var(--ink);" in light
        assert "--thead-bg: linear-gradient(180deg, rgba(14,42,71,0.055)" in light

    def test_quant_scope_block_deleted(self, src):
        assert '[data-theme="light"] #tab-quant {' not in src, \
            "#tab-quant 令牌作用域块应已删除（重映射全部上移全局亮色块）"

    def test_deepblue_palette_still_defined(self, src):
        light = _css_block(src, '[data-theme="light"] {')
        for tok in ("--navy:", "--navy2:", "--gold:", "--gold-d:", "--gold-l:",
                    "--gold-w:", "--ink:", "--muted:", "--line:", "--soft:", "--amber-d:"):
            assert tok in light, f"调色板令牌 {tok} 必须仍在亮色块定义"


# ─────────────────────────────────────────────────────────────
# #144 图表令牌 + isDark 分支清除
# ─────────────────────────────────────────────────────────────
class TestChartTokens:
    def test_light_chart_tokens_deepblue_gold(self, src):
        light = _css_block(src, '[data-theme="light"] {')
        assert "--chart-brand: #1b4571;" in light, "主序列线 = navy2"
        assert "--chart-brand-2: #b8935a;" in light, "次序列 = gold"
        assert "--chart-axis: #5a6472;" in light
        assert "--chart-grid: #dde3ea;" in light
        assert "--chart-line: #dde3ea;" in light
        assert "--chart-brand: #5b5bd6;" not in light, "糖果紫图表主色必须退场"

    def test_no_live_isdark(self, src):
        # 注释里允许出现 isDark 字样（说明历史），活代码不允许
        assert "const isDark" not in src, "不应再有 const isDark 声明"
        assert "isDark ?" not in src, "不应再有 isDark 三元分支（图表配色统一读 CHART getter）"

    def test_base_chart_options_reads_getters(self, src):
        m = re.search(r"_baseChartOptions\(title\) \{(.*?)\n  \},", src, re.S)
        assert m, "_baseChartOptions 未找到"
        body = m.group(1)
        assert "CHART.axis" in body and "CHART.grid" in body and "CHART.line" in body
        assert "#666" not in body and "#999" not in body and "#eee" not in body and "#ddd" not in body, \
            "_baseChartOptions 不应残留按主题硬编码的兜底色"


# ─────────────────────────────────────────────────────────────
# #146 字体规则全站统一（2026-10-13 当天追加：用户拍板"全站统一（含暗色）"）
# ─────────────────────────────────────────────────────────────
class TestGlobalFontRules:
    """字族规则一律**不带主题前缀** —— 亮/暗两套主题共用同一套字族；
    暗色只换字族，颜色令牌仍一丝不动（"暗色不动"契约只约束颜色，不约束字族）。"""

    def test_serif_titles_global(self, src):
        assert ".card h3,\n.expander-header," in src, "卡片/折叠条标题应进宋体栈（全局，无主题前缀）"
        assert ".report-content h1, .report-content h2, .report-content h3 { font-family: var(--serif); }" in src, \
            "研报正文标题应一并进宋体栈（本次统一补的漏出兵）"
        assert '[data-theme="light"] .card h3' not in src, "字体规则不应再带亮色前缀（已全站统一）"
        assert '[data-theme="light"] .expander-header' not in src

    def test_numeric_table_cells_and_headers(self, src):
        assert "table th, table td { font-family: var(--num); }" in src, \
            "表头+单元格应同族（Georgia）；只盖 td 会造出同一张表头脚字族打架"
        assert '[data-theme="light"] table td' not in src

    def test_body_sans_global(self, src):
        body_block = _css_block(src, "\nbody {")
        assert "font-family: var(--sans);" in body_block, "基座 body 应改吃 --sans 令牌栈（两套主题共用）"
        assert '[data-theme="light"] body {' not in src, "亮色 body 覆盖层应已删除（统一后不再需要）"
        assert "font-family: 'Inter'" not in src, "Inter 栈应已退场"

    def test_badge_info_navy(self, src):
        assert "[data-theme=\"light\"] .badge-info { background: rgba(27,69,113,0.10); color: var(--navy2); }" in src


# ─────────────────────────────────────────────────────────────
# #147 遗留亮色作用域规则收敛
# ─────────────────────────────────────────────────────────────
class TestLegacyConvergence:
    def test_signal_banner_cool_white(self, src):
        assert "#f0f4ff" not in src and "#e8f0fe" not in src, "信号横幅的糖果蓝渐变必须退场"
        assert "linear-gradient(135deg, #f5f7fa 0%, #eef2f7 100%)" in src, \
            "信号横幅亮色底应为冷白→极淡蓝灰"


# ─────────────────────────────────────────────────────────────
# #149 切主题图表重染机制
# ─────────────────────────────────────────────────────────────
def _method_body(src: str, name: str) -> str:
    """按**花括号配平**取一个方法的体。★ 本文件三条测试原先用的正则是坏的，见下。

    【★ 为什么必须换成配平】原判据用"名字 + 非贪婪 + 两空格缩进的 `},` 结尾"去截方法体
    —— 而模板里这些方法实际以**1 空格**缩进的 `},` 结尾 ⇒ 正则**永远匹配不到方法体本身**：
      · 方法之后**有**一个 2 空格缩进的 `},` 时 → 截出一段**跨数千行、约 266KB 的无关区间**
        （"断言碰巧通过"，其实什么都没测到）；
      · 之后**没有**这种串时 → `re.search` 返回 None ⇒ `assert m` 直接红，
        **看起来像产品缺陷，其实是判据写错**（实测：同一条测试会因**无关**的模板改动而红/绿翻转）。
    ⇒ 配平只依赖结构、与方法体怎么改无关。

    【★ 必须定位**定义**而不是首个匹配】`src.index("_initChart(")` 会命中**调用点**
    （`this._initChart('chart-xxx')`，实测早于定义 223 行）⇒ 配平截出的是一整块无关代码。
    故用"行首空白 + 可选 async + 名字 + 左括号"定位定义。
    """
    m = re.search(r"\n[ \t]*(?:async[ \t]+)?" + re.escape(name) + r"\s*\(", src)
    if not m:
        raise AssertionError("找不到方法定义: " + name)
    b = src.index("{", m.end())
    d, k = 0, b
    while k < len(src):
        ch = src[k]
        if ch == "{":
            d += 1
        elif ch == "}":
            d -= 1
            if d == 0:
                return src[b + 1:k]
        k += 1
    raise AssertionError("未配平: " + name)


class TestRepaintMechanism:
    def test_state_field(self, src):
        assert "_chartOpts: {}," in src, "需要 option 快照注册表状态字段"

    def test_init_chart_records_option(self, src):
        body = _method_body(src, "_initChart")
        assert "const rec = { option: null, notMerge: false };" in body
        assert "rec.option = option; rec.notMerge = !!notMerge;" in body
        assert "this._chartOpts[containerId] = rec;" in body
        assert "delete this._chartOpts[containerId];" in body, "dispose 旧实例时必须同步清快照"

    def test_snapshot_and_repaint_methods(self, src):
        assert "_snapshotChartColors() {" in src
        assert "_repaintCharts(before) {" in src
        repaint = _method_body(src, "_repaintCharts")
        assert "CHART_COLOR_KEYS" in src, "色键清单必须存在"
        assert "typeof v === 'function'" in repaint, "遍历必须跳过函数（格式化器/回调）"
        assert "seen.has(node)" in repaint, "遍历必须防环"
        assert "chart.setOption(rec.option, rec.notMerge);" in repaint

    def test_toggle_theme_hooks(self, src):
        body = _method_body(src, "toggleTheme")
        # 快照必须在 setAttribute 之前（切后旧色取不到）
        assert body.index("this._snapshotChartColors();") < body.index("setAttribute('data-theme', next)")
        assert "this._repaintCharts(before);" in body
        assert "_renderQuantKline(this._lastKlineData)" not in body, \
            "K 线特判已被通用重染取代，不应再留特判重渲染"

    def test_no_chart_string_literals(self, src):
        # #148（并行会话修复）的成果守卫：ECharts 吃不了 'CHART.up' 这种字符串字面量
        for lit in ("'CHART.up'", "'CHART.down'", "'CHART.brand'", "'CHART.axis'", "'CHART.grid'"):
            assert lit not in src, f"不应再有字符串字面量 {lit}（须用 getter 调用 CHART.xxx）"


# ─────────────────────────────────────────────────────────────
# CSS 注释卫生：注释里禁含 */（否则提前闭合吞掉后续规则）
# ─────────────────────────────────────────────────────────────
class TestCssCommentHygiene:
    """2026-10-13 事故的守卫：注释写 ``--btn-primary*/--thead-bg`` 提前闭合，
    把 ``[data-theme="light"] .card h3`` 规则吞进巨型选择器，浏览器静默不生效。"""

    def test_promoted_rules_are_real_rules(self, src):
        css = _style_block(src)
        by_sel = {}
        for sel, body in _css_rules(css):
            by_sel[re.sub(r"\s+", " ", sel).strip()] = body
        expect = {
            '[data-theme="light"]': "--brand: var(--gold-d);",
            # 字体规则已全站统一：选择器一律不带主题前缀（亮/暗共用）
            '.card h3, .expander-header, .report-content h1, .report-content h2, .report-content h3': "font-family: var(--serif);",
            'table th, table td': "font-family: var(--num);",
            'body': "font-family: var(--sans);",
            '[data-theme="light"] .badge-info': "color: var(--navy2);",
            '[data-theme="light"] .signal-banner': "#f5f7fa",
        }
        for sel, decl in expect.items():
            assert sel in by_sel, f"规则不存在或选择器被注释吞掉: {sel}"
            assert decl in by_sel[sel], f"{sel} 声明体不含 {decl!r}: {by_sel[sel][:80]!r}"

    def test_no_selector_contains_comment_fragment(self, src):
        css = _style_block(src)
        bad = [sel[:70] for sel, _ in _css_rules(css) if "*/" in sel or "/*" in sel]
        assert not bad, f"选择器混入注释碎片（注释被提前闭合的典型症状）: {bad}"

    def test_no_stray_comment_close(self, src):
        """注释区外不允许出现落单的 */（提前闭合后，原定结尾就会变成它）。"""
        css = _style_block(src)
        i, n, stray = 0, len(css), 0
        while i < n:
            if css.startswith("/*", i):
                j = css.find("*/", i + 2)
                i = (j + 2) if j != -1 else n
            elif css.startswith("*/", i):
                stray += 1
                i += 2
            else:
                i += 1
        assert stray == 0, f"样式表里有 {stray} 个落单的 */（注释被提前闭合）"


# ─────────────────────────────────────────────────────────────
# 守卫脚本联动：theme_token_audit 必须 exit 0
# ─────────────────────────────────────────────────────────────
def test_theme_token_audit_clean():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "theme_token_audit.py")],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "THEME_TEMPLATE": str(TEMPLATE)},
    )
    assert proc.returncode == 0, f"令牌审计未通过:\n{proc.stdout}\n{proc.stderr}"
    assert "漏定义 0 个" in proc.stdout
    assert "亮色待确认 0 个" in proc.stdout
