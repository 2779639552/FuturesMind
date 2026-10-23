"""报告正文版式护栏（2026-10-23）。

【事故】运行分析页的技术报告出现"标题挤到右侧一列、正文留在左侧"的错乱，
顶部三段文字还叠在一起。**不报错、控制台零输出**，只是排版坏了。

【根因】`web_template.html` 里给「卡片标题行三段式」写的那条：
    .card > div:has(> h3):not(.card):not(.grid):not(.sec-block) {
        display: flex; flex-wrap: wrap; align-items: center;
    }
判据 `:has(> h3)` 太宽 —— 任何"卡片里包含 h3 的 div"都会中招。
而报告正文 `.report-content` 里只要出现一个 `### xxx` 小标题（LC 报告就有
`### Technical Bias`），整个正文就被变成 flex 容器 ⇒ 段落与 h2 按内容宽度横排。

**为什么是"偶发"**：没写 h3 的报告完全正常。所以这个坑会随机出现，
且只在含三级标题的报告上出现。

【本文件守两条】
  1. 静态：`.report-content` 必须显式 `display:block`，且那条 `:has(> h3)`
     规则必须排除 `.report-content`（两道防线，任一道在都能拦住）；
  2. 动态：真的用浏览器渲染一个含 h3 的正文，断言它不是 flex、
     且多个 h2 的左边界一致、纵向递增（这是"单列"的可观测定义）。
     没有 playwright 时跳过，不让 CI 因缺浏览器而红。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TPL = ROOT / "web_template.html"


def _css() -> str:
    return "\n".join(re.findall(r"<style[^>]*>(.*?)</style>",
                                TPL.read_text(encoding="utf-8"), re.S))


# ---------------------------------------------------------------------------
# 静态
# ---------------------------------------------------------------------------
def test_report_content_pins_block_display():
    css = _css()
    m = re.search(r"\.report-content\s*\{([^}]*)\}", css)
    assert m, "找不到 .report-content 规则"
    body = m.group(1)
    assert re.search(r"display:\s*block", body), (
        ".report-content 没有显式 display:block —— 一旦有别的 :has() 规则命中它，"
        "正文会被拆成多列且不报错"
    )


def test_card_title_row_rule_excludes_report_content():
    """直接定位「把某个 div 设成 display:flex」的那条 `:has(> h3)` 规则。

    ⚠ 只盯会产生 flex 的那条：`... :has(> h3) > div:nth-child(2) > select` 这类
      后代选择器作用于标题行里的小控件，不会把正文变成 flex，不必要求它排除。
      判据过宽会把护栏变成噪音（本项目踩过"恒绿/恒红"的坑）。
    """
    css = _css()
    checked = 0
    for sel, body in re.findall(r"([^{}]*:has\(>\s*h3\)[^{}]*)\{([^{}]*)\}", css, re.S):
        if "display:flex" not in body.replace(" ", "").replace("\n", ""):
            continue
        core = re.sub(r"/\*.*?\*/", "", sel, flags=re.S).strip()
        # 作用对象必须就是那个 div 本身（:has 之后没有后代选择器 `>`），
        # 否则是标题行内部的控件组（`> div:nth-child(2)`），与正文无关。
        # ⚠ 用 rsplit 取**最后**一次出现：注释里也可能引用这个选择器片段，
        #   从头切会切到注释中间（本文件第一版就栽在这）。
        after = core.rsplit(":has(> h3)", 1)[1] if ":has(> h3)" in core else ""
        if ">" in after:
            continue
        checked += 1
        assert ".report-content" in core, (
            f"这条规则会把含 h3 的正文拆成 flex 横排（LC 报告因此错乱）: {core[:90]}"
        )
    assert checked >= 1, "没检查到任何「卡片标题行」规则，正则或选择器写法变了"


# ---------------------------------------------------------------------------
# 动态（有浏览器才跑）
# ---------------------------------------------------------------------------
def _render_report_like_html(tmp_path: Path) -> Path:
    """构一个最小可复现页：卡片 + 正文（含 h3 与多个 h2）。"""
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<style>{_css()}</style></head><body style="padding:24px">
<div class="card"><h3>Technical Analysis</h3>
<div class="report-content">
<p>BIAS: 看空</p>
<h2>一、价格趋势</h2><p>正文一</p>
<h2>二、均线系统</h2><p>正文二</p>
<h3>Technical Bias: 看空</h3>
<table><tr><td>技术面</td><td>看空</td></tr></table>
</div></div></body></html>"""
    p = tmp_path / "report_like.html"
    p.write_text(html, encoding="utf-8")
    return p


def test_rendered_report_stays_single_column(tmp_path):
    pytest.importorskip("playwright.sync_api", reason="无 playwright，跳过渲染级断言")
    from playwright.sync_api import sync_playwright

    page_path = _render_report_like_html(tmp_path)
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001 - 无浏览器可执行文件时跳过
            pytest.skip(f"无法启动 chromium: {exc}")
        pg = browser.new_page(viewport={"width": 1200, "height": 800})
        pg.goto(page_path.as_uri())
        info = pg.evaluate("""() => {
          const rc = document.querySelector('.report-content');
          const cs = getComputedStyle(rc);
          const hs = [...rc.querySelectorAll('h2')].map(h => {
            const r = h.getBoundingClientRect();
            return {left: Math.round(r.left), top: Math.round(r.top), w: Math.round(r.width)};
          });
          return {display: cs.display, hs};
        }""")
        browser.close()

    assert info["display"] == "block", (
        f".report-content 被渲染成 {info['display']} —— 正文会被按内容宽度横排"
    )
    lefts = {h["left"] for h in info["hs"]}
    assert len(lefts) == 1, f"多个 h2 左边界不一致（说明被分栏）: {info['hs']}"
    tops = [h["top"] for h in info["hs"]]
    assert tops == sorted(tops), f"h2 没有自上而下纵向排列: {info['hs']}"
