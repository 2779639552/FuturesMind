# -*- coding: utf-8 -*-
"""质量门徽标的界面护栏（2026-10-23）。

【为什么需要】检查器算完的结果只进了 `/api/analysis_results`，而 `web_template.html`
里搜 `research_review` **零命中** —— 界面上看不见。看不见的质量门等于没有。

【本文件守什么】
  静态（文本级）
    1. 三个方法存在，且**有调用点**（方法写了没人调 = 白改）
    2. 三种状态分别用 badge-bear / badge-neutral / badge-bull（复用既有主题色类，不新造颜色）
    3. 明细默认 `display:none` —— 通过时不该占位
    4. 明细文本必须过 `_esc`（研报/规则文本里可能有 `<`）
    5. 点徽标要 `stopPropagation`（否则会触发卡片折叠）
    6. 没有 `quality_gate` 字段的历史结果 → 不渲染，保持旧观感
  动态（playwright，缺浏览器则跳过）
    7. 注入「未通过」数据 → 徽标出现、默认收起、点击后展开且包含问题明细
    8. 注入「通过」数据 → 绿徽标、没有明细容器
"""
from __future__ import annotations

import io
import json
import re
from contextlib import contextmanager
from contextlib import contextmanager
from pathlib import Path

import pytest

TPL = Path(__file__).resolve().parents[1] / "web_template.html"


def _tpl() -> str:
    return io.open(TPL, encoding="utf-8").read()


def _seg(name: str, end_marker: str) -> str:
    src = _tpl()
    i = src.find(name)
    assert i >= 0, f"模板里找不到 {name}"
    j = src.find(end_marker, i)
    assert j > i, f"找不到 {name} 的结束标记 {end_marker}"
    return src[i:j]


# ---------------------------------------------------------------------------
# 静态
# ---------------------------------------------------------------------------
class TestStatic:
    def test_methods_exist(self):
        src = _tpl()
        for m in ("_renderQualityGateBadge(", "_renderQualityGateDetail(", "_toggleQG("):
            assert m in src, f"缺少方法 {m}"

    def test_badge_is_actually_wired_into_synthesis_card(self):
        """方法写了没人调 = 白改 —— 这是本项目踩过的坑。"""
        seg = _seg("const qgBadge", "document.getElementById('results-area')")
        assert "key === 'synthesis'" in seg, "徽标没绑定到综合研判卡"
        assert "${qgBadge}" in seg, "徽标没拼进 h3"
        assert "${qgBody}" in seg, "明细没拼进卡片"

    def test_three_states_use_existing_theme_classes(self):
        seg = _seg("_renderQualityGateBadge(d)", "_renderQualityGateDetail")
        assert "badge-bear" in seg, "未通过状态缺红徽标"
        assert "badge-neutral" in seg, "事前拦截状态缺琥珀徽标"
        assert "badge-bull" in seg, "通过状态缺绿徽标"
        # 暗色契约：只许用主题变量/既有 class，不许写死颜色
        for bad in ("#fff", "#ffffff", "white", "#000", "black"):
            assert bad not in seg.lower(), f"徽标里出现硬编码颜色 {bad}，暗色主题会瞎"

    def test_detail_hidden_by_default(self):
        seg = _seg("_renderQualityGateDetail(d)", "_toggleQG")
        assert "display:none" in seg, "明细默认没收起，通过时也会占位"

    def test_detail_text_is_escaped(self):
        seg = _seg("_renderQualityGateDetail(d)", "_toggleQG")
        assert "_esc(it.detail)" in seg, "问题明细没转义，规则文本里的 < 会破坏 DOM"

    def test_toggle_stops_propagation(self):
        seg = _seg("_toggleQG(ev)", "// 【后端】GET /api/analysis_results")
        assert "stopPropagation" in seg, "点徽标会冒泡到卡片，可能触发折叠"

    def test_backward_compatible_without_field(self):
        """老的分析结果没有 quality_gate，必须静默不渲染。"""
        seg = _seg("_renderQualityGateBadge(d)", "_renderQualityGateDetail")
        assert "if (!qg) return ''" in seg, "缺字段时应直接返回空串"

    def test_reads_both_pre_and_post(self):
        src = _tpl()
        seg = _seg("_renderQualityGateDetail(d)", "_toggleQG")
        assert "research_review_pre" in seg, "明细里没展示事前那一遍"
        assert "research_review" in seg, "明细里没展示事后那一遍"
        assert "research_review_pre" in src


# ---------------------------------------------------------------------------
# 动态（真实渲染）
# ---------------------------------------------------------------------------
_FAIL = {
    "ready": True,
    "reports": {"synthesis": "RATING: 中性 | CONFIDENCE: 中 | SCORE: 5\n\n## 综合研判\n正文"},
    "rating": {"rating": "中性", "confidence": "中", "score": 5},
    "research_review": {
        "ok": True, "pass": False, "score": 72,
        "issues": [{"code": "R5_no_invalidation", "severity": "high",
                    "detail": "给出了方向性判断，但全文没有失效/作废条件", "evidence": ""}],
    },
    "research_review_pre": {
        "ok": True, "pass": False, "score": 75,
        "issues": [{"code": "R3_caliber_unstated", "severity": "high",
                    "detail": "基差率 8.56% 未注明分母", "evidence": ""}],
    },
    "quality_gate": {"pre_failed": True, "post_failed": True, "section": "…"},
}

_PASS = {
    "ready": True,
    "reports": {"synthesis": "RATING: 中性 | CONFIDENCE: 中 | SCORE: 5\n\n## 综合研判\n正文"},
    "rating": {"rating": "中性", "confidence": "中", "score": 5},
    "research_review": {"ok": True, "pass": True, "score": 100, "issues": []},
    "research_review_pre": {"ok": True, "pass": True, "score": 100, "issues": []},
    "quality_gate": {"pre_failed": False, "post_failed": False, "section": ""},
}

_INIT = """
window.__QG_CASE__ = %s;
window.fetch = function (url) {
  var isResults = String(url).indexOf('/api/analysis_results') >= 0;
  var body = isResults ? window.__QG_CASE__ : {ok: false, ready: false, rows: [], stages: [], cards: []};
  return Promise.resolve({ok: true, json: function () { return Promise.resolve(body); }});
};
"""


@contextmanager
def _render(tmp_path: Path, case: dict):
    pytest.importorskip("playwright.sync_api", reason="无 playwright，跳过渲染级断言")
    from playwright.sync_api import sync_playwright

    # 模板用 `/static/xxx.js` 绝对路径，file:// 下会 404 ⇒ 脚本在 `const App` 之前就抛
    # `marked is not defined`，整段不执行（实测踩到）。改成相对路径并把两个依赖复制进来。
    root = TPL.parent
    html = tmp_path / "qg.html"
    src = _tpl().replace('src="/static/', 'src="static/')
    io.open(html, "w", encoding="utf-8", newline="").write(src)
    static_dir = tmp_path / "static"
    static_dir.mkdir(exist_ok=True)
    import shutil

    for name in ("marked.min.js", "echarts.min.js"):
        dep = root / "static" / name
        if dep.exists():
            shutil.copy2(dep, static_dir / name)

    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"无法启动 chromium: {exc}")
        pg = b.new_page(viewport={"width": 1100, "height": 900})
        pg.add_init_script(_INIT % json.dumps(case, ensure_ascii=False))
        pg.goto(html.as_uri(), wait_until="load")
        pg.wait_for_timeout(1200)
        # 脚本能跑到底的判据：App 这个全局词法绑定可见
        assert pg.evaluate("() => typeof App === 'object'"), (
            "模板脚本没执行完（多半是 static 依赖没加载上），App 未定义"
        )
        pg.evaluate("""() => {
          if (!document.getElementById('results-area')) {
            const d = document.createElement('div');
            d.id = 'results-area';
            document.body.appendChild(d);
          }
        }""")
        pg.evaluate("() => App.loadAnalysisResults('TA', '2026-10-23')")
        pg.wait_for_timeout(500)
        try:
            yield pg
        finally:
            b.close()


class TestRendering:
    def test_failed_gate_shows_badge_and_expands(self, tmp_path):
        with _render(tmp_path, _FAIL) as pg:
            badge = pg.query_selector("#results-area .badge-bear")
            assert badge, "未通过时没有红色徽标"
            assert "质检未通过" in badge.inner_text(), badge.inner_text()

            detail = pg.query_selector("#qg-detail")
            assert detail, "缺少明细容器"
            assert detail.is_visible() is False, "默认应收起"

            badge.click()
            pg.wait_for_timeout(250)
            assert detail.is_visible() is True, "点击后没有展开"
            text = detail.inner_text()
            assert "事前" in text and "事后" in text, "明细没区分事前/事后"
            assert "基差率 8.56% 未注明分母" in text, "事前那条问题没显示"
            assert "没有失效" in text, "事后那条问题没显示"

    def test_passing_gate_shows_green_and_no_detail(self, tmp_path):
        with _render(tmp_path, _PASS) as pg:
            badge = pg.query_selector("#results-area .badge-bull")
            assert badge, "通过时没有绿色徽标"
            assert "质检通过" in badge.inner_text()
            assert pg.query_selector("#qg-detail") is None, "通过时不该有明细容器"

    def test_precaught_then_passed_shows_amber(self, tmp_path):
        """事前拦下、事后通过 —— 这是"质量门真的起了作用"的状态，最该看得见。"""
        case = dict(_PASS)
        case["research_review_pre"] = _FAIL["research_review_pre"]
        case["quality_gate"] = {"pre_failed": True, "post_failed": False, "section": ""}
        with _render(tmp_path, case) as pg:
            badge = pg.query_selector("#results-area .badge-neutral")
            assert badge, "事前拦截时没有琥珀徽标"
            assert "事前拦截" in badge.inner_text(), badge.inner_text()
            assert "1 项" in badge.inner_text(), "没显示拦截条数"

            detail = pg.query_selector("#qg-detail")
            assert detail and detail.is_visible() is False, "明细应存在但默认收起"
            badge.click()
            pg.wait_for_timeout(250)
            text = detail.inner_text()
            assert "基差率 8.56% 未注明分母" in text, "事前那条问题没显示"
            assert "事后" not in text, "事后已通过，不该有事后分组"
