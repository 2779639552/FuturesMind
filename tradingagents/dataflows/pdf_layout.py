"""研报 PDF 版面感知提取:图表区域聚类 + 表格块聚合(2026-09-08 方案三)。

【为什么】线性文本倾倒会把图表撕成轴数字块(如 "0 500 1000 1500…"),嵌入分高、
零观点,还被 RAG 目录/表格过滤规则整块丢弃 —— 图表主题(印尼镍矿产量、BU-SC
价差…)随之丢失。本模块按页把 **矢量图形区域**(get_drawings 聚类)内的文字
(轴/图例)与最近的图题行、资料来源行聚成一个【图】块;表N 标题后的表格行聚成
【表】块 —— 图表信息因此可被检索,数字由后续 LLM 在上下文中自行解读。

【分层】web_app 的 PDF 词→行版面还原逻辑整体下沉到本模块(避免 dataflows→web_app
循环导入);extract_plain_text 保持旧行为(extracted_text 口径不变),extract_layout_text
为图表感知的新口径(写 research_reports.layout_text,仅供 RAG 消费)。

【为什么纯几何、不用 LLM】图表题注仅 99 处(76 份 PDF,永安系图无题注)且 GTJA
双栏图块序≠阅读序,文本顺序分组不可靠;get_drawings 矢量元素(网格线/柱体/边框)
几何上必然包围图表,是唯一全覆盖的分组依据。图表块本身含图题/图例文字,可被
检索命中;数字解读交给问答 LLM 在上下文中完成(Phase A),LLM 重述留 Phase B。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 词→行版面还原参数(自 web_app 下沉,语义不变)──
# 同一行 y 坐标容差(磅);行内相邻词横向间隔超过该值时插双空格(保留分栏/表格列结构)
_PDF_LINE_Y_TOL = 3.0
_PDF_WORD_GAP = 12.0
# CJK 文字与常见全角标点:这些字符两两相邻时拼接不加空格
_CJK_CHARS = "。，、；：（）【】“”‘’％%℃"

# ── 图表聚类参数 ──
_REGION_GAP = 10.0        # 矢量元素矩形互相扩张该距离后相交即并入同簇(图表内部网格线间距)
_REGION_MARGIN = 22.0     # 词归属:图表簇向外扩该距离(轴数字常悬在图框外)
_MIN_STROKES = 4          # 少于该矢量元素数的簇视为装饰线(页眉横线等),不算图表
_PAGE_COVER = 0.85        # 簇面积占页面比例超过该值视为整页装饰(边框/底纹),丢弃
_TITLE_GAP = 90.0         # 图题/资料来源行距图表簇的最大垂直距离(磅)
_FIG_MARK_RE = re.compile(r"^\s*(?:图表|图|表)\s*\d+\s*[：:]")
_SOURCE_RE = re.compile(r"资料来源|数据来源|Source[:：]")
# 表格行判定:数字占比达标,或含双空格列间隙(版面还原保留的列结构)
_TABLE_LINE_DIGIT_RATIO = 0.2
_MAX_TABLE_LINES = 60


def is_cjk_char(ch: str) -> bool:
    """单字符是否为 CJK 文字/全角标点(CJK 相邻拼接时不插空格)。"""
    if not ch:
        return False
    return "\u4e00" <= ch <= "\u9fff" or ch in _CJK_CHARS


def _join_sep(left: str, right: str) -> str:
    """相邻两词的连接符:CJK 相邻不插空格,其余插一个空格。"""
    return "" if (is_cjk_char(left[-1:]) and is_cjk_char(right[:1])) else " "


def words_to_lines(words: list[tuple]) -> list[tuple]:
    """把词条按 y 容差聚成行、行内按 x 排序,返回带 bbox 的文本行。

    【参数】words: [(x0, y0, x1, y1, text), …](PyMuPDF get_text("words") 的五元组投影)。
    【返回】[(x0, y0, x1, y1, text), …] 行内拼接完成,但**不跨 block 排序**——
           调用方先按 block 分组传入(PDF 双栏靠 block 划分天然隔离,纯 y 排序会把
           双栏目录串行,实测 "01 要点综述 / 目录 / 02 价格表现" 混成一行)。

    【关键逻辑】设计软件导出的研报 PDF 各文本块独立坐标,默认 get_text() 会碎成
               一行一 token(实测永安周报平均行长 5.4 字);按 y 容差归行还原版面,
               行内横向间隔超阈值(分栏/表格列)插双空格保留列结构。
    """
    ordered = sorted(words, key=lambda w: (w[1], w[0]))
    buckets: list[list] = []
    cur: list = []
    cur_y = None
    for w in ordered:
        x0, y0, x1, y1, txt = w[0], w[1], w[2], w[3], w[4]
        if cur_y is None or abs(y0 - cur_y) <= _PDF_LINE_Y_TOL:
            cur.append((x0, x1, txt, y0, y1))
            if cur_y is None:
                cur_y = y0
        else:
            buckets.append(cur)
            cur = [(x0, x1, txt, y0, y1)]
            cur_y = y0
    if cur:
        buckets.append(cur)

    lines: list[tuple] = []
    for bucket in buckets:
        bucket.sort(key=lambda t: t[0])
        text = bucket[0][2]
        for prev, nxt in zip(bucket, bucket[1:], strict=False):
            sep = "  " if (nxt[0] - prev[1]) > _PDF_WORD_GAP else _join_sep(text, nxt[2])
            text += sep + nxt[2]
        lines.append((
            min(t[0] for t in bucket), min(t[3] for t in bucket),
            max(t[1] for t in bucket), max(t[4] for t in bucket), text,
        ))
    return lines


def _rects_intersect(a: tuple, b: tuple, gap: float) -> bool:
    """两矩形各向外扩 gap 后是否相交(用于矢量元素聚簇)。"""
    return not (a[2] + gap < b[0] or b[2] + gap < a[0] or a[3] + gap < b[1] or b[3] + gap < a[1])


def _rect_area(r: tuple) -> float:
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


def cluster_regions(rects: list[tuple], page_area: float) -> list[tuple]:
    """把矢量元素矩形聚成图表区域簇,返回各簇并集 bbox(x0,y0,x1,y1)。

    【关键逻辑】① 先按面积预过滤:占页面比例超 _PAGE_COVER 的矩形是整页边框/
              底纹(先丢,否则它会把页面上所有图表吸进同一个大簇);
              ② 相邻(扩张 _REGION_GAP 后相交)矩形逐步合并;
              ③ 少于 _MIN_STROKES 个元素的簇是装饰线(页眉横线等),不算图表。
              返回按面积降序。
    """
    if not rects:
        return []
    usable = [
        r for r in rects
        if not (page_area > 0 and _rect_area(r) / page_area > _PAGE_COVER)
    ]
    if not usable:
        return []
    clusters: list[dict] = []
    for r in usable:
        merged = list(r)
        hit: dict | None = None
        for c in clusters:
            if _rects_intersect(tuple(merged), c["bbox"], _REGION_GAP):
                c["bbox"] = [
                    min(c["bbox"][0], merged[0]), min(c["bbox"][1], merged[1]),
                    max(c["bbox"][2], merged[2]), max(c["bbox"][3], merged[3]),
                ]
                c["n"] += 1
                merged = c["bbox"]
                hit = c
        if hit is None:
            clusters.append({"bbox": list(merged), "n": 1})
    out = [
        tuple(c["bbox"])
        for c in clusters
        # 【双保险】小矩形链条可合并出面积超页面的巨型簇(实测国君封面页侧边饰条,
        # area% 1.05+),后置面积过滤兜底丢弃
        if c["n"] >= _MIN_STROKES and not (page_area > 0 and _rect_area(c["bbox"]) / page_area > _PAGE_COVER)
    ]
    out.sort(key=_rect_area, reverse=True)
    return out


def _point_in_rect(x: float, y: float, r: tuple, margin: float) -> bool:
    return (r[0] - margin) <= x <= (r[2] + margin) and (r[1] - margin) <= y <= (r[3] + margin)


def page_layout_text(lines: list[tuple], draw_rects: list[tuple], page_area: float) -> str:
    """单页版面输出:矢量簇内文字聚成【图】块,其余行按阅读序输出(纯函数,可测)。

    【参数】lines: words_to_lines 输出的带 bbox 行 [(x0,y0,x1,y1,text)];
           draw_rects: get_drawings 展开的矩形列表;page_area: 页面面积
           (0 表示未知,跳过整页覆盖过滤)。
    【返回】该页文本(行间 \\n)。图表块格式(图题/资料来源行收进块内,不重复输出):
           【图】{图题行}
           {簇内行}
           {资料来源行}
    """
    regions = cluster_regions(draw_rects, page_area)
    if not regions:
        return "\n".join(t[4] for t in lines)

    # 行归属:行中心落在(扩张后的)某图表簇内 → 归入该簇
    fig_lines: dict[int, list[tuple]] = {}
    normal: list[tuple] = []
    for ln in lines:
        cx, cy = (ln[0] + ln[2]) / 2, (ln[1] + ln[3]) / 2
        hit = next((i for i, r in enumerate(regions) if _point_in_rect(cx, cy, r, _REGION_MARGIN)), None)
        if hit is None:
            normal.append(ln)
        else:
            fig_lines.setdefault(hit, []).append(ln)

    # 图题:距簇最近的 图N/图表N/表N 标记行(**上方或下方** —— 实测两类排版并存:
    # 国君周报题注在图上,碳酸锂专题在图下);资料来源:簇下方最近的来源行。
    # 被收进【图】块的行从普通行里剔除(避免同一段文字输出两遍);但含多个
    # 图N 标记的行(GTJA 双栏图 "图1：… 图2：…" 同一行)不消费,让同排两个簇都能挂上。
    items: list[tuple] = []  # (y, text);【图】块以簇顶 y 参与阅读序排序
    consumed: set[int] = set()
    for i, r in enumerate(regions):
        title_idx = source_idx = -1
        best_gap = _TITLE_GAP
        for j, ln in enumerate(normal):
            if j in consumed or not _FIG_MARK_RE.match(ln[4]):
                continue
            cands = [
                g for g in (r[1] - ln[3] if ln[3] <= r[1] else None, ln[1] - r[3] if ln[1] >= r[3] else None)
                if g is not None
            ]
            # 题注行与簇纵向重叠(既不在上也不在下)不作题注
            if not cands:
                continue
            gap = min(cands)
            if gap < best_gap:
                title_idx, best_gap = j, gap
        best_gap = _TITLE_GAP
        for j, ln in enumerate(normal):
            if j in consumed:
                continue
            if _SOURCE_RE.search(ln[4]) and ln[1] >= r[3] and (ln[1] - r[3]) < best_gap:
                source_idx, best_gap = j, ln[1] - r[3]
        block = ["【图】" + (normal[title_idx][4].strip() if title_idx >= 0 else "")]
        block.extend(t[4] for t in sorted(fig_lines.get(i, []), key=lambda t: (t[1], t[0])))
        if source_idx >= 0:
            block.append(normal[source_idx][4].strip())
        if title_idx >= 0 and len(_FIG_MARK_RE.findall(normal[title_idx][4])) == 1:
            consumed.add(title_idx)
        if source_idx >= 0:
            consumed.add(source_idx)
        items.append((r[1], "\n".join(block)))

    for j, ln in enumerate(normal):
        if j not in consumed:
            items.append((ln[1], ln[4]))
    items.sort(key=lambda it: it[0])
    return "\n".join(it[1] for it in items)


def group_table_lines(lines: list[str]) -> list[str]:
    """表N 标题后的表格行聚合:数字行挂回标题,输出【表】块(纯函数,可测)。

    【关键逻辑】命中 表N:/图表N: 标记行后,连续吞并表格样行(数字占比达标或带
              列间隙)与资料来源行,至多 _MAX_TABLE_LINES;遇普通正文/下一个标记行
              即停。无表格可聚合时原样返回。
    """
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        if not (_FIG_MARK_RE.match(ln) and ln.strip().startswith(("表", "图表"))):
            out.append(ln)
            i += 1
            continue
        buf = [f"【表】{ln.strip()}"]
        j = i + 1

        def _table_like(s: str) -> bool:
            if not s.strip():
                return False
            if _SOURCE_RE.search(s):
                return True
            digits = sum(ch.isdigit() for ch in s)
            return digits / max(len(s), 1) >= _TABLE_LINE_DIGIT_RATIO or "  " in s

        taken = 0
        while j < n and taken < _MAX_TABLE_LINES and _table_like(lines[j]):
            buf.append(lines[j].strip())
            taken += 1
            j += 1
            if _SOURCE_RE.search(lines[j - 1]):
                break  # 资料来源即表格收尾
        out.append("\n".join(buf))
        i = j
    return out


def _drawing_rects(page) -> list[tuple]:
    """get_drawings 的矩形展开(d.rect 即各矢量元素的包围盒)。"""
    rects: list[tuple] = []
    try:
        drawings = page.get_drawings()
    except Exception:  # noqa: BLE001  个别页 drawings 解析失败不拖垮整篇
        return rects
    for d in drawings:
        r = d.get("rect")
        if r is None:
            continue
        rects.append((r.x0, r.y0, r.x1, r.y1))
    return rects


def _page_lines(page) -> list[tuple]:
    """单页 → 带 bbox 文本行(按 block 分组聚行,双栏天然隔离;无词条返回空)。"""
    words = page.get_text("words")
    if not words:
        return []
    blocks: dict[int, list] = {}
    for w in words:
        blocks.setdefault(w[5], []).append((w[0], w[1], w[2], w[3], w[4]))
    lines: list[tuple] = []
    for bno in sorted(blocks):
        lines.extend(words_to_lines(blocks[bno]))
    return lines


def _import_pymupdf():
    """PyMuPDF 导入(≥1.24 推荐入口 pymupdf,旧版兼容名 fitz);缺失返回 None。"""
    try:
        import pymupdf
        return pymupdf
    except ImportError:
        try:
            import fitz as pymupdf  # type: ignore[no-redef]
            return pymupdf
        except ImportError:
            return None


def extract_layout_text(path: Path) -> str:
    """PDF → 版面感知全文(图表聚成【图】块、表格聚成【表】块,无截断)。

    【返回】str;PyMuPDF 缺失/读取失败返回空串(调用方回退 extracted_text)。
    """
    pymupdf = _import_pymupdf()
    if pymupdf is None:
        return ""
    try:
        doc = pymupdf.open(str(path))
        parts: list[str] = []
        for page in doc:
            lines = _page_lines(page)
            if not lines:
                parts.append(page.get_text())  # 纯图页退回默认提取
                continue
            pr = page.rect
            page_text = page_layout_text(
                lines, _drawing_rects(page), pr.width * pr.height if pr else 0.0
            )
            parts.append("\n".join(group_table_lines(page_text.split("\n"))))
        doc.close()
        return "\n".join(parts)
    except Exception:
        logger.warning("Failed layout extraction for %s", path, exc_info=True)
        return ""


def extract_plain_text(path: Path) -> str:
    """PDF → 线性全文(旧行为:block 分组词→行版面还原,无图表聚合;extracted_text 口径)。"""
    pymupdf = _import_pymupdf()
    if pymupdf is None:
        return ""
    try:
        doc = pymupdf.open(str(path))
        parts = []
        for page in doc:
            lines = _page_lines(page)
            if not lines:
                parts.append(page.get_text())
                continue
            parts.append("\n".join(t[4] for t in lines))
        doc.close()
        return "\n".join(parts)
    except Exception:
        logger.warning("Failed to read PDF text %s", path, exc_info=True)
        return ""


# ── 位图图表区域(视觉重述用,2026-09-08 方案三补充)──
# 32/76 份研报的图表是整块位图(get_drawings 无矢量可聚),轴数字在像素里,
# 文本提取只能拿到图题。本节把位图 bbox 定位 + 渲染成 PNG,交给视觉模型重述。
_BITMAP_MIN_W = 180.0     # 小于此宽度的位图视为 logo/图标(磅)
_BITMAP_MIN_H = 90.0
_BITMAP_UNION_GAP = 6.0   # 相邻位图(图与轴/图例拆成两张图)合并的最大间距


def _merge_rects(rects: list[tuple], gap: float) -> list[tuple]:
    """并查式矩形合并(扩张 gap 后相交即并集),迭代到稳定;无最小元素数限制
    (位图图表可能只拆成 2 张图,与 cluster_regions 的 _MIN_STROKES 语义不同)。"""
    boxes = [list(r) for r in rects]
    changed = True
    while changed:
        changed = False
        out: list[list] = []
        for b in boxes:
            hit = None
            for o in out:
                if _rects_intersect(tuple(b), tuple(o), gap):
                    o[:] = [min(o[0], b[0]), min(o[1], b[1]), max(o[2], b[2]), max(o[3], b[3])]
                    hit = o
                    break
            if hit is None:
                out.append(list(b))
            else:
                changed = True  # 并集可能吞进新邻居,再扫一轮
        boxes = out
    return [tuple(b) for b in boxes]


def bitmap_chart_regions(path: Path) -> list[dict]:
    """定位 PDF 内的位图图表区域,返回 [{"page": 页码, "bbox": (x0,y0,x1,y1)}, …]。

    【关键逻辑】① 按宽高过滤 logo/图标;② 过滤整页背景装饰图与包含它的容器图
              (实测永安周报每页一张全页底图 + 一张内容图);③ 相邻(扩张
              _BITMAP_UNION_GAP 后相交)位图合并(研报导出常把柱体与坐标轴
              拆成两张紧贴的图);④ 失败返回空(调用方跳过该报告)。
    """
    pymupdf = _import_pymupdf()
    if pymupdf is None:
        return []
    try:
        doc = pymupdf.open(str(path))
        out: list[dict] = []
        for pno, page in enumerate(doc):
            pr = page.rect
            page_area = pr.width * pr.height if pr else 0.0
            rects = []
            for info in page.get_image_info():
                b = info.get("bbox")
                if not b or (b[2] - b[0]) < _BITMAP_MIN_W or (b[3] - b[1]) < _BITMAP_MIN_H:
                    continue
                # 【过滤】整页背景装饰图(实测永安周报每页一张 0,0,717,403 全页图叠内容图)
                if page_area > 0 and _rect_area(b) / page_area > _PAGE_COVER:
                    continue
                rects.append((b[0], b[1], b[2], b[3]))
            # 【过滤】包含关系:容器图(背景)包含内容图时只留内容图
            rects = [
                r for r in rects
                if not any(
                    other is not r
                    and other[0] <= r[0] and other[1] <= r[1]
                    and other[2] >= r[2] and other[3] >= r[3]
                    and _rect_area(other) > _rect_area(r)
                    for other in rects
                )
            ]
            if not rects:
                continue
            for b in _merge_rects(rects, _BITMAP_UNION_GAP):
                out.append({"page": pno, "bbox": b})
        doc.close()
        return out
    except Exception:
        logger.warning("Failed to locate bitmap charts in %s", path, exc_info=True)
        return []


def render_region_png(path: Path, page_no: int, bbox: tuple, dpi: int = 150) -> bytes:
    """把 PDF 指定页指定区域渲染成 PNG 字节(视觉模型输入);失败返回 b""。"""
    pymupdf = _import_pymupdf()
    if pymupdf is None:
        return b""
    try:
        doc = pymupdf.open(str(path))
        page = doc[page_no]
        pix = page.get_pixmap(clip=pymupdf.Rect(*bbox), dpi=dpi)
        data = pix.tobytes("png")
        doc.close()
        return data
    except Exception:
        logger.warning("Failed to render region %s p%s", path, page_no, exc_info=True)
        return b""
