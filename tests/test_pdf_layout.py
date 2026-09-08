"""Tests for `tradingagents.dataflows.pdf_layout`(2026-09-08 方案三).

版面感知 PDF 提取:纯几何函数用 tuple 输入直接测;端到端用 pymupdf 现场生成
带矩形"图表"的 PDF 做 roundtrip(pymupdf 已是研报链路既有依赖,缺失则 skip)。
"""

import pytest

from tradingagents.dataflows.pdf_layout import (
    cluster_regions,
    extract_layout_text,
    extract_plain_text,
    group_table_lines,
    is_cjk_char,
    page_layout_text,
    words_to_lines,
)


def _w(x0, y0, x1, y1, text):
    return (x0, y0, x1, y1, text)


@pytest.mark.unit
def test_is_cjk_char():
    assert is_cjk_char("铜")
    assert is_cjk_char("。")
    assert not is_cjk_char("")
    assert not is_cjk_char("A")


@pytest.mark.unit
def test_words_to_lines_merges_same_row_and_keeps_gap():
    words = [
        _w(0, 10, 20, 20, "图1"),
        _w(24, 10, 60, 20, "铜价"),
        _w(200, 10, 220, 20, "5000"),  # 同 y 但横向远 → 双空格分列
        _w(0, 40, 30, 50, "正文行"),
    ]
    lines = words_to_lines(words)
    assert len(lines) == 2
    assert lines[0][4] == "图1 铜价  5000"
    assert lines[1][4] == "正文行"


@pytest.mark.unit
def test_cluster_regions_drops_decoration_and_full_page():
    # 4 个相交矩形 = 一个图表簇;单条横线 = 装饰;占满页面的大框 = 整页装饰
    page_area = 1000 * 1400
    rects = [
        (100, 100, 200, 110), (100, 110, 200, 120),  # 相邻横线
        (100, 120, 200, 130), (100, 130, 200, 140),
        (0, 0, 1000, 4),  # 页眉横线(1 个元素)
        (10, 10, 990, 1390),  # 整页边框(会被覆盖过滤,但注意它先与页眉线聚簇)
    ]
    regions = cluster_regions(rects, page_area)
    assert len(regions) == 1
    assert regions[0] == (100.0, 100.0, 200.0, 140.0)


@pytest.mark.unit
def test_cluster_regions_empty():
    assert cluster_regions([], 1000.0) == []


@pytest.mark.unit
def test_page_layout_text_groups_figure_words_and_title():
    # 图表簇(4 条密排网格线,gap≤10 可聚成一簇)内轴数字/图例;簇上方图题、下方资料来源
    rects = [(100, 200, 400, 208), (100, 216, 400, 224), (100, 232, 400, 240), (100, 248, 400, 256)]
    words = [
        _w(90, 150, 160, 160, "图1：铜价走势"),
        _w(120, 210, 200, 220, "5000"),  # 轴数字(落在簇内)
        _w(120, 235, 240, 245, "2026-01"),
        _w(90, 300, 250, 310, "资料来源：Wind"),
        _w(90, 400, 300, 410, "正文段落。"),
    ]
    out = page_layout_text(words_to_lines(words), rects, 1000 * 1400)
    assert "【图】图1：铜价走势" in out
    assert "5000" in out
    assert "资料来源：Wind" in out
    assert "正文段落。" in out
    # 图题与资料来源收进【图】块后,不再作为普通行重复输出
    assert out.count("图1：铜价走势") == 1
    assert out.count("资料来源：Wind") == 1


@pytest.mark.unit
def test_page_layout_text_no_regions_passthrough():
    words = [_w(0, 0, 10, 10, "a"), _w(0, 20, 10, 30, "b")]
    assert page_layout_text(words_to_lines(words), [], 1000.0) == "a\nb"


@pytest.mark.unit
def test_group_table_lines_aggregates_digit_rows():
    lines = [
        "表1：库存变动",
        "2026-01  100  200",
        "2026-02  110  190",
        "资料来源：Wind",
        "以上为普通正文,不含表格特征。",
    ]
    out = group_table_lines(lines)
    assert any(o.startswith("【表】表1：库存变动") for o in out)
    joined = [o for o in out if o.startswith("【表】")][0]
    assert "2026-01  100  200" in joined
    assert "资料来源：Wind" in joined
    body = [o for o in out if "普通正文" in o][0]
    assert not body.startswith("【表】")  # 正文行不被吞进表格块


@pytest.mark.unit
def test_group_table_lines_no_table_passthrough():
    lines = ["普通正文第一行", "第二行没有标记"]
    assert group_table_lines(lines) == lines


# ── 端到端:pymupdf 生成真 PDF roundtrip ──

pymupdf = pytest.importorskip("pymupdf", reason="pdf_layout 端到端需要 PyMuPDF")


@pytest.mark.unit
def test_extract_roundtrip_with_figure(tmp_path):
    import pymupdf as pm

    doc = pm.open()
    page = doc.new_page(width=595, height=842)
    # 【注意】insert_text 必须显式给内置中文字体 china-s:默认 helv 渲染不了 CJK(落成点符)
    page.insert_text((72, 100), "图1：铜价走势图", fontsize=12, fontname="china-s")
    # 画 5 条矩形(模拟图表网格线/柱体)→ 聚成图表簇
    for i in range(5):
        page.draw_rect(pm.Rect(72, 150 + i * 20, 300, 160 + i * 20))
    page.insert_text((80, 200), "5000", fontsize=8)
    page.insert_text((80, 270), "资料来源：Wind", fontsize=9, fontname="china-s")  # 距图表簇 <_TITLE_GAP(90)
    page.insert_text((72, 500), "正文:铜价震荡偏强。", fontsize=10, fontname="china-s")
    path = tmp_path / "t.pdf"
    doc.save(str(path))
    doc.close()

    layout = extract_layout_text(path)
    assert "【图】图1：铜价走势图" in layout
    assert "资料来源：Wind" in layout
    assert "铜价震荡偏强" in layout
    # 旧行为口径:无【图】标记,但正文/题注文字同样提取得到
    plain = extract_plain_text(path)
    assert "铜价震荡偏强" in plain
    assert "【图】" not in plain


@pytest.mark.unit
def test_extract_missing_file_returns_empty(tmp_path):
    assert extract_layout_text(tmp_path / "nope.pdf") == ""
    assert extract_plain_text(tmp_path / "nope.pdf") == ""


# ── 位图图表区域(2026-09-08 视觉重述补充)──

@pytest.mark.unit
def test_bitmap_chart_regions_filters_background_and_icons(tmp_path):
    """整页背景装饰图与 logo/图标应被过滤,内容图保留(回归:永安周报每页一张
    全页底图叠内容图,不过滤会把 170 页幻灯片全部算成图表)。"""
    import pymupdf as pm

    doc = pm.open()
    page = doc.new_page(width=720, height=405)
    # 整页背景图(IRect 必须 4 参:x0,y0,x1,y1)
    pix_bg = pm.Pixmap(pm.csRGB, pm.IRect(0, 0, 720, 405), 0)
    page.insert_image(pm.Rect(0, 0, 720, 405), stream=pix_bg.tobytes("png"))
    # 内容图(非整页)
    pix_ct = pm.Pixmap(pm.csRGB, pm.IRect(0, 0, 600, 280), 0)
    page.insert_image(pm.Rect(27, 44, 627, 324), stream=pix_ct.tobytes("png"))
    # 小图标(应被尺寸过滤)
    pix_ic = pm.Pixmap(pm.csRGB, pm.IRect(0, 0, 30, 30), 0)
    page.insert_image(pm.Rect(650, 370, 680, 400), stream=pix_ic.tobytes("png"))
    path = tmp_path / "t.pdf"
    doc.save(str(path))
    doc.close()

    from tradingagents.dataflows.pdf_layout import bitmap_chart_regions

    regions = bitmap_chart_regions(path)
    assert len(regions) == 1, f"背景/图标应被过滤,只留内容图: {regions}"
    b = regions[0]["bbox"]
    assert (b[0], b[1]) == (27.0, 44.0)


@pytest.mark.unit
def test_bitmap_chart_regions_missing_file(tmp_path):
    from tradingagents.dataflows.pdf_layout import bitmap_chart_regions

    assert bitmap_chart_regions(tmp_path / "nope.pdf") == []
