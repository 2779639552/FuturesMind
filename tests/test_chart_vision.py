"""Tests for `tradingagents.dataflows.chart_vision`(2026-09-08 位图图表视觉重述)。

视觉模型不进单测:monkeypatch pdf_layout 的区域定位/渲染与 describe_png,
只测组装逻辑(追加重述节/幂等标记/图表数上限/钩子兜底)。
"""

import pytest

import tradingagents.dataflows.chart_vision as cv
import tradingagents.dataflows.pdf_layout as pdf_layout


@pytest.fixture()
def _fake_pdf_region(monkeypatch):
    """桩掉几何层:1 个位图区域 + 渲染出假 PNG,不碰真 PDF。"""
    monkeypatch.setattr(
        pdf_layout, "bitmap_chart_regions",
        lambda path: [{"page": 0, "bbox": (27.0, 44.0, 627.0, 324.0)}],
    )
    monkeypatch.setattr(pdf_layout, "render_region_png", lambda path, page, bbox, dpi=150: b"png")
    monkeypatch.setattr(
        pdf_layout, "extract_layout_text", lambda path: "正文行。\n【图】图1：铜价走势"
    )


@pytest.fixture()
def _vision_ok(monkeypatch):
    monkeypatch.setattr(cv, "describe_png", lambda png: "铜价震荡偏强,约5000元/吨。")


@pytest.mark.unit
def test_build_layout_appends_vision_section(_fake_pdf_region, _vision_ok):
    out = cv.build_layout_with_vision("fake.pdf")
    assert out is not None
    assert "【图】图1：铜价走势" in out  # 原版面提取保留
    assert "【图】铜价震荡偏强,约5000元/吨。" in out  # 重述条目带【图】前缀
    assert f"(以上1条为{cv.SECTION_MARK}" in out  # 尾注(幂等标记+免责)


@pytest.mark.unit
def test_build_layout_no_regions_returns_none(_fake_pdf_region, monkeypatch):
    # 无位图:一律 None(脚本记 no_bitmap,不落占位;与脚本原行为一致)
    monkeypatch.setattr(pdf_layout, "bitmap_chart_regions", lambda path: [])
    assert cv.build_layout_with_vision("fake.pdf", empty_marker=False) is None
    assert cv.build_layout_with_vision("fake.pdf", empty_marker=True) is None


@pytest.mark.unit
def test_build_layout_all_describe_fail(_fake_pdf_region, monkeypatch):
    monkeypatch.setattr(cv, "describe_png", lambda png: (_ for _ in ()).throw(RuntimeError("down")))
    # 脚本口径(empty_marker=True):落"无可用图表"占位 → 幂等不重跑
    out = cv.build_layout_with_vision("fake.pdf", empty_marker=True)
    assert f"({cv.SECTION_MARK}:无可用图表)" in out
    # 钩子口径(False):失败留白,下次可用脚本补 → None
    assert cv.build_layout_with_vision("fake.pdf", empty_marker=False) is None


@pytest.mark.unit
def test_build_layout_max_charts_caps(monkeypatch):
    monkeypatch.setattr(
        pdf_layout, "bitmap_chart_regions",
        lambda path: [{"page": i, "bbox": (0, 0, 100, 100)} for i in range(20)],
    )
    monkeypatch.setattr(pdf_layout, "render_region_png", lambda *a, **k: b"png")
    seen: list[int] = []
    monkeypatch.setattr(cv, "describe_png", lambda png: (seen.append(1), f"图{len(seen)}")[1])
    out = cv.build_layout_with_vision("fake.pdf", max_charts=3)
    assert len(seen) == 3
    assert f"(以上3条为{cv.SECTION_MARK}" in out


@pytest.mark.unit
def test_describe_for_hook_skips_when_ollama_down(monkeypatch):
    monkeypatch.setattr(cv, "ollama_available", lambda timeout=2.0: False)
    assert cv.describe_for_hook("fake.pdf") == ""


@pytest.mark.unit
def test_describe_for_hook_returns_empty_when_no_vision_layout(monkeypatch):
    monkeypatch.setattr(cv, "ollama_available", lambda timeout=2.0: True)
    monkeypatch.setattr(cv, "build_layout_with_vision", lambda *a, **k: None)
    assert cv.describe_for_hook("fake.pdf") == ""


@pytest.mark.unit
def test_describe_for_hook_never_raises(monkeypatch):
    monkeypatch.setattr(cv, "ollama_available", lambda timeout=2.0: True)
    monkeypatch.setattr(cv, "build_layout_with_vision", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert cv.describe_for_hook("fake.pdf") == ""


# ── web_app 薄适配层 _vision_describe_safely ──

web_app = pytest.importorskip("web_app")


@pytest.mark.unit
def test_web_app_vision_hook_passthrough_when_marked(monkeypatch):
    called: list[str] = []
    monkeypatch.setattr(cv, "describe_for_hook", lambda fp: called.append(fp) or "")
    layout = "正文\n(以上1条为图表视觉重述,由视觉模型基于图表位图生成)"
    out = web_app._vision_describe_safely(1, "fake.pdf", layout)
    assert out == layout
    assert called == []  # 幂等:已有重述节不再调视觉模型


@pytest.mark.unit
def test_web_app_vision_hook_applies_and_falls_back(monkeypatch):
    monkeypatch.setattr(cv, "describe_for_hook", lambda fp: "重述版layout")
    assert web_app._vision_describe_safely(1, "fake.pdf", "旧layout") == "重述版layout"
    monkeypatch.setattr(cv, "describe_for_hook", lambda fp: "")  # 本次没做 → 原样保留
    assert web_app._vision_describe_safely(1, "fake.pdf", "旧layout") == "旧layout"
