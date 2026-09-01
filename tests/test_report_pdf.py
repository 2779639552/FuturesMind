"""报告渲染守卫:网页版 HTML 与 PDF 生成(2026-09-01 新增)。

背景:原 PDF 用 fpdf2 把 markdown 去符号后 dump 纯文本,外观与网页前端相差甚远。
本次改为:先 _report_to_html 把 markdown 渲染成"内嵌前端深色主题 CSS + 内嵌 marked.min.js"
的自包含 HTML 页面,再让 headless Chromium(Playwright)打印成 PDF,与网页端同引擎同外观;
Playwright 不可用时回退 fpdf2。本测试不联网、不起真实浏览器,只守卫纯函数与路由装配。
"""

import pytest

import web_app
from web_app import _generate_pdf, _generate_pdf_fpdf, _parse_rating, _report_to_html

_SAMPLE = """# Commodity Futures Analysis: RB

**Date**: 2026-09-01
**Generated**: 2026-09-01 10:00:00

RATING: 强多 | CONFIDENCE: 0.82 | SCORE: 7

## Technical Analysis

| 指标 | 数值 | 结论 |
|------|------|------|
| MACD | 金叉 | 偏多 |
| RSI  | 58.2 | 中性 |

## Synthesis & Recommendation

**Rating**: Buy — 供需偏紧。
"""


# ---------------------------------------------------------------------------
# 1) _parse_rating:提取评级头 / 未命中返回 None
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_parse_rating_hit_and_miss():
    r = _parse_rating(_SAMPLE)
    assert r == {"rating": "强多", "confidence": "0.82", "score": 7}
    assert _parse_rating("没有评级头的正文\n") is None
    assert _parse_rating("RATING: 平 | CONFIDENCE: 0.5 | SCORE: 5").get("score") == 5


# ---------------------------------------------------------------------------
# 2) _report_to_html:主题/横幅/脚本转义
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_report_html_contains_theme_and_banner():
    html = _report_to_html(_SAMPLE, "commodity_RB_20260901_120000.md")
    # 深色主题 + 前端同款 report-content 样式
    assert "--brand: #ff5a1f" in html
    assert 'class="report-content"' in html
    # RATING 横幅自动从正文提取并渲染(元素本体出现,而非仅 CSS 规则)
    assert '<div class="signal-banner">' in html
    assert "强多" in html
    # 页头:从文件名解析品种
    assert "Commodity Futures Analysis — RB" in html
    # 注入正文用了 JSON 转义,引号不截断脚本
    assert "marked.parse(" in html


@pytest.mark.unit
def test_report_html_escapes_script_end_tag_in_content():
    # 正文里若含 "</script>",必须转义为 "<\/script>",否则会提前截断内嵌脚本
    evil = "# Title\n\n```\n</script>\n```\n" + "正文含 </script> 与双引号 \" 与换行"
    html = _report_to_html(evil, "commodity_AO_20260901_120000.md")
    assert "<\\/script>" in html  # 转义后的形式出现在注入 JSON 里
    # 页面自身的收尾 script 标签不丢失
    assert html.count("</script>") == 2  # marked 脚本 + 注入脚本,各一个收尾


@pytest.mark.unit
def test_report_html_no_rating_still_ok():
    html = _report_to_html("# 只有标题\n\n无评级头正文\n", "commodity_RB_20260901_120000.md")
    assert '<div class="signal-banner">' not in html  # 无评级头 → 不渲染横幅元素
    assert 'class="report-content"' in html


# ---------------------------------------------------------------------------
# 3) HTML 路由:/api/report/<file>/html 返回独立网页版
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_report_html_route_serves_page(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "REPORT_DIR", tmp_path)
    f = tmp_path / "commodity_RB_20260901_120000.md"
    f.write_text(_SAMPLE, encoding="utf-8")

    c = web_app.app.test_client()
    r = c.get("/api/report/commodity_RB_20260901_120000.md/html")
    assert r.status_code == 200
    assert r.content_type.startswith("text/html")
    body = r.get_data(as_text=True)
    assert "signal-banner" in body
    assert "Technical Analysis" in body  # 正文渲染进页面


@pytest.mark.unit
def test_report_html_route_missing_404(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "REPORT_DIR", tmp_path)
    r = web_app.app.test_client().get("/api/report/nope.md/html")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 4) PDF:Playwright 不可用时回退 fpdf2(输出以 %PDF 开头)
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_generate_pdf_fpdf_fallback_bytes():
    data = _generate_pdf_fpdf("# Plain ASCII report\n\nBody text here.\n", "commodity_RB_x.md")
    assert data.startswith(b"%PDF")


@pytest.mark.unit
def test_generate_pdf_falls_back_when_html_render_fails(monkeypatch):
    # 模拟渲染层故障(如 Playwright 异常)→ 仍返回可用 PDF,不 500
    def _boom(*_a, **_k):
        raise RuntimeError("renderer down")

    monkeypatch.setattr(web_app, "_report_to_html", _boom)
    data = _generate_pdf("Plain ASCII report body.\n", "commodity_RB_x.md")
    assert data.startswith(b"%PDF")
