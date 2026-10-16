# -*- coding: utf-8 -*-
r"""外文研报 · 横版两列版式 + 提速改造 回归护栏（2026-10-14）。

================================ 背景 ================================
用户要求三件事：
  ① 版式/配色/字体参考给定汇报图；
  ② 改成**横版 PPT(16:9)** 尺寸；
  ③ 页面**两列**；封面**不要整页**，只在第一页顶部放一个色块。
外加：外文研报要**提速**（领导反馈双向交互下等太久）。

================================ 本文件守什么 ================================
A. 尺寸：必须是 16:9 横版（338.67×190.5mm），**不能**再是 `format="A4"`。
B. 两列：多列必须落在 **body** 上；`.pg` **不能**自己开多列（它是 auto 高 +
   `column-fill:auto` ⇒ 退化成"内容全堆第一列、右列全空"，实测过）。
C. 色块：`.cover` 是顶部色块（`column-span:all` + 高度远小于一页）；
   `h2.sec` **不能**带 `column-span:all`（会让页内右列整列空白，实测过）。
D. 留白自检：扫描范围必须**按页高比例**算 —— 写死像素会在换尺寸后静默失效。
E. 提速：全局并发闸存在且**两个调用点都过闸**；翻译在归纳**之前**启动（并行）；
   渲染回退**不许重渲**（旧逻辑会渲第 3 次）。

⚠️ 断言纪律（本项目踩过多次）：
  · 凡在源码里找一句话，先确认它**唯一**，否则要限定上下文 —— 否则断言会命中
    **注释/docstring** 而假绿（本文件里 E 组专门用了 `_code_only` 剥注释）。
  · 坏的必须能红：配套 `probe/_mutation_foreign_restyle.py` 做变异自检。
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import foreign_report as fr  # noqa: E402

PY = ROOT / "foreign_report.py"
CSS = ROOT / "assets" / "foreign_report_pdf.css"


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _code_only(src: str) -> str:
    """剥掉整行注释，避免残留检查命中**自己写的注释**（本项目的高频假绿来源）。"""
    out = []
    for line in src.splitlines():
        s = line.strip()
        if s.startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


def _css_code_only(css: str) -> str:
    """剥掉 CSS 的 `/* … */` 注释。

    ⚠️ 必须剥 —— 本文件的样式区里写了大量**解释性注释**，其中会提到 `.pg`、`795`、
    `column-span:all` 这些"被检查对象"的名字。不剥注释就会把注释当成规则/残留，
    造出假红或假绿（这套护栏第一版就栽在这里：`.pg` 正则匹配到了注释里的 `.pg{...}`）。
    """
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _def_body(src: str, func: str, end_marker: str) -> str:
    """取某个函数定义的**正文（不含 docstring）**，到 end_marker 为止。"""
    blk = src[src.index(func):]
    blk = blk[:blk.index(end_marker)]
    # 砍掉 docstring：第一个三引号对之间
    i = blk.find('"""')
    if i >= 0:
        j = blk.find('"""', i + 3)
        if j > i:
            blk = blk[: i] + blk[j + 3:]
    return blk


@pytest.fixture(scope="module")
def py() -> str:
    return _src(PY)


@pytest.fixture(scope="module")
def css() -> str:
    return _src(CSS)


# ════════════════════════════════════════════════════════════
# A. 横版 PPT 尺寸
# ════════════════════════════════════════════════════════════
class TestHorizontalPptSize:
    def test_size_constants_are_16_9(self):
        assert fr.PPT_W_MM == "338.67mm", "PPT 宽应为 13.333in = 338.67mm"
        assert fr.PPT_H_MM == "190.5mm", "PPT 高应为 7.5in = 190.5mm"
        w = float(fr.PPT_W_MM.rstrip("m"))
        h = float(fr.PPT_H_MM.rstrip("m"))
        assert abs(w / h - 16 / 9) < 0.01, f"宽高比 {w / h:.3f} 不是 16:9"

    def test_render_pdf_uses_ppt_size_not_a4(self, py):
        blk = py[py.index("def _render_pdf("):]
        blk = blk[:blk.index("def _blank_page_audit(")]
        assert "width=PPT_W_MM" in blk, "page.pdf 未用横版宽"
        assert "height=PPT_H_MM" in blk, "page.pdf 未用横版高"
        assert 'format="A4"' not in _code_only(blk), "仍在用 A4 格式"


# ════════════════════════════════════════════════════════════
# B./C. 两列版式与顶部色块（CSS 契约）
# ════════════════════════════════════════════════════════════
class TestTwoColumnLayout:
    def test_columns_live_on_body(self, css):
        """★ 多列必须设在 body 上：`.pg` 高度是 auto，column-fill:auto 在那会退化。"""
        m = re.search(r"\nbody\{[^}]*\}", css)
        assert m, "找不到 body 规则"
        assert "column-count:2" in m.group(0), (
            "body 未开两列 —— 若把多列放在 .pg 上会退化成'内容全堆第一列'（实测过）"
        )

    def test_pg_does_not_open_its_own_columns(self, css):
        """★ `.pg` 不许再自己开多列（那正是'右列全空'的成因）。

        ⚠️ 必须先剥 CSS 注释：样式区里有解释性注释会提到 `.pg{…column-count:2}`，
        不剥就会把它当成一条真规则（本护栏第一版即如此假红）。
        """
        code = _css_code_only(css)
        for m in re.finditer(r"\.pg[^{]*\{[^}]*\}", code):
            assert "column-count" not in m.group(0), (
                f".pg 规则里又出现了 column-count：{m.group(0)[:80]}"
            )

    def test_cover_is_a_top_band_spanning_columns(self, css):
        """色块横跨两列、且**不占满一页**（用户明确要求封面不要整页）。"""
        m = re.search(r"\.cover\{[^}]*\}", css)
        assert m, "找不到 .cover 规则"
        rule = m.group(0)
        assert "column-span:all" in rule, "色块应横跨两列"
        assert "break-inside:avoid" in rule, "色块不应被拆到两页"
        # 不能出现"整页高"的写法（旧版封面是 height:270mm）
        assert "height:270mm" not in rule, "色块又变成整页高了"
        assert not re.search(r"height:\s*(19[0-9]|2[0-9]{2})mm", rule), "色块高度接近整页"

    def test_section_heading_has_no_column_span(self, css):
        """★ h2.sec 不能带 column-span:all（会造成页内右列整列空白，实测过）。"""
        m = re.search(r"h2\.sec\{[^}]*\}", css)
        assert m, "找不到 h2.sec 规则"
        assert "column-span" not in m.group(0), (
            "h2.sec 又加了 column-span:all —— 会让页内右列整列空白"
        )

    def test_grid_columns_use_minmax_zero(self, css):
        """grid 列必须 `minmax(0,1fr)`：写 `1fr` 时最小内容宽度会撑爆容器（实测三栏卡溢出）。"""
        for m in re.finditer(r"grid-template-columns:\s*repeat\([^;]+;", css):
            seg = m.group(0)
            assert "minmax(0," in seg, f"发现未用 minmax(0,…) 的网格：{seg[:70]}"

    def test_run_in_class_applied_on_first_section(self, py):
        """色块后的第一个 .pg 要打 run-in（否则第一页只剩色块）。"""
        blk = py[py.index("def _assemble_html("):]
        assert 'class="pg run-in"' in blk
        assert 'body.find(marker, i)' in blk or "find(marker, i)" in blk


# ════════════════════════════════════════════════════════════
# D. 留白自检：必须按页高比例（换尺寸后不许静默失效）
# ════════════════════════════════════════════════════════════
class TestBlankPageAuditScalesWithPageHeight:
    def test_uses_ratio_not_hardcoded_pixels(self, py):
        """★ 扫描范围必须按页高比例（写死 A4 的像素会在换尺寸后静默失效）。

        ⚠️ 必须排除 docstring —— 该函数用 docstring **解释**了旧实现里的 `y∈[34,795]`，
        直接搜 `"795"` 会命中这段说明文字（本护栏第一版即如此假红）。
        """
        body = _def_body(py, "def _blank_page_audit(",
                         "# ═══════════════════════════ 任务状态机")
        code = _code_only(body)
        assert "795" not in code, "仍在写死 A4 的 795 —— 横版下会判定失真"
        assert re.search(r"h \* 0\.\d+", code), "未按页高比例计算扫描范围"
        assert "range(34" not in code, "仍从写死的 34 起扫"

    def test_detects_blank_bottom_on_horizontal_page(self, tmp_path):
        """★ 功能性：横版页上"底部大面积留白"必须能被判出（正是我修的那个失效点）。"""
        import pymupdf
        mmp = 72 / 25.4
        w, h = fr.PPT_W_MM, fr.PPT_H_MM
        wpt = float(w.rstrip("m")) * mmp
        hpt = float(h.rstrip("m")) * mmp

        # ① 只有顶部一小段文字 ⇒ 底部大留白 ⇒ 应被判为异常
        doc = pymupdf.open()
        pg = doc.new_page(width=wpt, height=hpt)
        pg.insert_text((40, 60), "只有顶部一行内容", fontsize=11)
        p_blank = tmp_path / "blank.pdf"
        doc.save(str(p_blank))
        doc.close()

        # ② 整页铺满文字 ⇒ 不该被判为异常
        doc = pymupdf.open()
        pg = doc.new_page(width=wpt, height=hpt)
        y = 40
        while y < hpt - 20:
            pg.insert_text((40, y), "内容行 " * 8, fontsize=10)
            y += 16
        p_full = tmp_path / "full.pdf"
        doc.save(str(p_full))
        doc.close()

        blank_bad = fr._blank_page_audit(p_blank)
        full_bad = fr._blank_page_audit(p_full)
        assert blank_bad == [1], f"横版页的底部大留白未被识别（bad={blank_bad}）"
        assert full_bad == [], f"铺满内容的页被误判为空白（bad={full_bad}）"

    def test_does_not_crash_on_tiny_page(self, tmp_path):
        """极端小页不应崩（旧实现在 h<34 时区间为空、恒判"不空白"）。"""
        import pymupdf
        doc = pymupdf.open()
        doc.new_page(width=100, height=20)
        p = tmp_path / "tiny.pdf"
        doc.save(str(p))
        doc.close()
        assert isinstance(fr._blank_page_audit(p), list)


# ════════════════════════════════════════════════════════════
# E. 提速改造
# ════════════════════════════════════════════════════════════
class TestSpeedup:
    def test_global_llm_semaphore_defined(self, py):
        assert "LLM_CONCURRENCY = 5" in py
        assert "threading.BoundedSemaphore(LLM_CONCURRENCY)" in py

    def test_both_invoke_sites_pass_the_gate(self, py):
        """★ 两个真实调用点（_ask_json / _ask_text）都必须过闸 —— 漏一个并发就失控。"""
        code = _code_only(py)
        n = len(re.findall(r"with _LLM_SEM:", code))
        assert n == 2, f"过闸点应为 2 处（_ask_json + _ask_text），实际 {n} 处"
        # 每个 invoke 都必须紧跟在 with 块内
        for m in re.finditer(r"with _LLM_SEM:\s*\n\s*r = llm\.invoke\(", code):
            assert m, "有 invoke 未包在 _LLM_SEM 里"
        assert code.count("llm.invoke(") == 2, "llm.invoke 调用点数量变了，需复核是否都过闸"

    def test_translate_starts_before_analysis(self, py):
        """★ 翻译必须在归纳**之前**提交（否则等于没并行）。"""
        code = _code_only(py)
        i_submit = code.index("_tr_pool.submit(_do_translate)")
        i_analyze = code.index("_build_analysis_input(", i_submit)
        assert i_submit < i_analyze, "翻译提交晚于归纳 ⇒ 仍是串行"
        assert "def _do_translate() -> None:" in code
        assert "_tr_fut.result()" in code, "缺少等待翻译完成的 join"

    def test_no_third_render_on_rollback(self, py):
        """★ 回退必须还原字节，不许再渲一遍（旧逻辑会渲第 3 次，单次 6~21s）。

        调用点共 4 处是**预期**的：①第 1 版 ②续排版 ③④两处"字节没拿到"的极端兜底。
        关键是：**运行期**最多 2 次 —— 两个兜底必须都在 `if _first_pdf_bytes:` 的 else 分支里。
        """
        code = _code_only(py)
        assert "_first_pdf_bytes = out_pdf.read_bytes()" in code, "未保存第 1 版字节"

        # ★ 关键：必须断言"还原字节"真的**落在 `if _first_pdf_bytes:` 分支里**。
        #   只数 `write_bytes(_first_pdf_bytes)` 出现几次是不够的 —— 变异测试证明：
        #   把条件改成 `if False:`（还原永不执行、退化成重渲）时，计数断言照样绿。
        n_restore = len(re.findall(
            r"if _first_pdf_bytes:\s*\n\s*out_pdf\.write_bytes\(_first_pdf_bytes\)", code))
        assert n_restore == 2, (
            f"应有 2 处「在 `if _first_pdf_bytes:` 内还原字节」的写法，实际 {n_restore} 处"
            "—— 若条件被改成恒假，回退就退化成了重渲"
        )
        # 两处兜底重渲都必须紧跟 `else:`（即被 _first_pdf_bytes 的判空守卫住）
        n_guarded = len(re.findall(
            r"else:\s*(?:#[^\n]*\n)?\s*info\d* = _render_pdf\(", code))
        assert n_guarded == 2, (
            f"应有 2 处兜底重渲落在 else 分支里（实际 {n_guarded} 处）"
            "—— 若落到主流程就说明回退又去重渲了"
        )
        n_render = len(re.findall(r"info\d* = _render_pdf\(", code))
        assert n_render == 4, (
            f"_render_pdf 调用点应为 4 处（1 正常 + 1 续排 + 2 兜底），实际 {n_render} 处"
        )

    def test_progress_is_monotonic(self, py):
        """并行后两个来源都写进度，必须单调不减否则进度条来回跳。"""
        blk = py[py.index("def _log("):]
        blk = blk[:blk.index("def get_job(")]
        assert 'max(int(job.get("progress") or 0)' in blk

    def test_translate_worker_pool_still_bounded(self, py):
        """翻译仍是有界并发（别为了快就 unlimited）。"""
        assert "TRANSLATE_WORKERS = 3" in py
        blk = py[py.index("def _do_translate() -> None:"):]
        blk = blk[:blk.index("_tr_pool = ThreadPoolExecutor")]
        assert "ThreadPoolExecutor(max_workers=TRANSLATE_WORKERS)" in blk

    def test_semaphore_actually_caps_concurrency(self):
        """★ 功能性：真开 20 个线程抢闸，同时在跑的不得超过 LLM_CONCURRENCY。"""
        import threading
        import time as _t

        cur = 0
        peak = 0
        lk = threading.Lock()

        def worker():
            nonlocal cur, peak
            with fr._LLM_SEM:
                with lk:
                    cur += 1
                    peak = max(peak, cur)
                _t.sleep(0.05)
                with lk:
                    cur -= 1

        ts = [threading.Thread(target=worker) for _ in range(20)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert peak <= fr.LLM_CONCURRENCY, f"峰值并发 {peak} 超过上限 {fr.LLM_CONCURRENCY}"
        assert peak >= 2, f"峰值并发只有 {peak} ⇒ 闸门把并发压死了，并行收益归零"


# ════════════════════════════════════════════════════════════
# F. 顺带修的 <br> 与 EOL
# ════════════════════════════════════════════════════════════
class TestSummaryCardBreakRendering:
    def test_kk_allows_br(self, py):
        """摘要卡三栏的三个字段都要 allow_br —— 否则模型写的 `<br>` 会显示成字面量。"""
        blk = py[py.index("def _summary_html("):]
        blk = blk[:blk.index("def _appendix_html(")]
        for field in ("kk", "kv", "kd"):
            assert f'_rich(c.get("{field}") or "", allow_br=True)' in blk, \
                f"摘要卡字段 {field} 未开 allow_br"

    def test_br_becomes_real_newline(self):
        html = fr._summary_html({
            "lead": "x",
            "cards": [{"kk": "A<br>B", "kv": "1", "kd": "d"}] * 3,
            "conclusions": [],
        })
        kks = re.findall(r'<div class="kk">(.*?)</div>', html)
        assert kks and "<br>" in kks[0], f"kk 未还原真换行：{kks[:1]}"
        assert not any("&lt;br&gt;" in x for x in kks), "kk 里仍是字面 br"


class TestEol:
    @pytest.mark.parametrize("rel,expect_crlf", [
        ("foreign_report.py", False),
        ("assets/foreign_report_pdf.css", True),
    ])
    def test_eol(self, rel, expect_crlf):
        b = (ROOT / rel).read_bytes()
        crlf = b.count(b"\r\n")
        bare = b.count(b"\n") - crlf
        if expect_crlf:
            assert bare == 0, f"{rel} 出现 {bare} 个裸 LF（应为纯 CRLF）"
        else:
            assert crlf == 0, f"{rel} 出现 {crlf} 个 CRLF（应为纯 LF）"
