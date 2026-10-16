# -*- coding: utf-8 -*-
"""横版两列版式预览：拿已有任务的 _compile.html，换上新 CSS 重新渲染 + 出图。

【为什么可以这样做】`_assemble_html` 把 CSS **内联**在 HTML 里，所以旧任务的
  `_compile.html` 带的是旧样式。这里只把 `<style>…</style>` 换成新的
  assets/foreign_report_pdf.css，并补上 run-in 类，就能在**不重跑 LLM** 的前提下
  预览新版的真实排版效果（省掉几分钟到十几分钟的任务耗时）。

用法：
    python probe/_preview_style.py                     # 取最近一个任务
    python probe/_preview_style.py 97f6245ebb00        # 指定任务
    python probe/_preview_style.py 97f6245ebb00 --shots 1,2,3
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import foreign_report as fr  # noqa: E402


def newest_job() -> str:
    outs = [p for p in (ROOT / "data" / "foreign" / "out").iterdir() if p.is_dir()]
    outs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for d in outs:
        if (d / "_compile.html").exists():
            return d.name
    raise SystemExit("找不到任何带 _compile.html 的任务")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    jid = args[0] if args else newest_job()
    shots = "1,2,3"
    for a in sys.argv[1:]:
        if a.startswith("--shots"):
            shots = a.split("=", 1)[1] if "=" in a else shots

    out_dir = ROOT / "data" / "foreign" / "out" / jid
    src_html = out_dir / "_compile.html"
    if not src_html.exists():
        raise SystemExit(f"没有 {src_html}")

    html = src_html.read_text(encoding="utf-8")
    css = (ROOT / "assets" / "foreign_report_pdf.css").read_text(encoding="utf-8")

    # ① 换 CSS（内联样式整块替换）
    n_style = len(re.findall(r"<style>.*?</style>", html, re.S))
    html = re.sub(r"<style>.*?</style>", lambda _m: f"<style>{css}</style>", html,
                  count=1, flags=re.S)
    print(f"[1] 内联样式替换：原 {n_style} 块 → 新 CSS {len(css)} 字符")

    # ② 补 run-in（与原 _assemble_html 同逻辑）
    i = html.find('class="cover"')
    marker = '<section class="pg">'
    j = html.find(marker, i)
    if i >= 0 and j >= 0:
        html = html[:j] + '<section class="pg run-in">' + html[j + len(marker):]
        print("[2] 已给色块后第一个 .pg 打上 run-in")
    else:
        print(f"[2] ⚠ 未找到 cover({i}) 或后续 .pg({j})")

    # ③ 落盘"换好新样式"的 HTML —— 供 probe/_measure_columns.mjs 测量真实几何。
    #   ⚠️ 不能直接测原来的 _compile.html：它内联的是**旧** CSS（改版前的样式），
    #      测出来的色块高度/列宽都是旧值（本会话踩过：色块测出 390px 其实是旧样式）。
    styled = out_dir / "_preview_styled.html"
    styled.write_text(html, encoding="utf-8")
    print(f"[3] 已落盘样式化 HTML：{styled.name}（供几何测量）")

    # ④ 渲染
    out_pdf = out_dir / "_preview_horizontal.pdf"
    marks: list[tuple] = []
    info = fr._render_pdf(html, out_pdf, "版式预览 · 横版两列", marks)
    print(f"[4] 渲染完成：{info['pages']} 页 → {out_pdf.name}")

    # ④ 尺寸 + 留白自检
    import pymupdf
    doc = pymupdf.open(str(out_pdf))
    p0 = doc[0]
    w_mm, h_mm = p0.rect.width / 72 * 25.4, p0.rect.height / 72 * 25.4
    print(f"[4] 页面尺寸：{w_mm:.1f} × {h_mm:.1f} mm  "
          f"（目标 338.7 × 190.5；宽高比 {w_mm / h_mm:.3f}，目标 1.778）")
    bad = fr._blank_page_audit(out_pdf)
    print(f"[5] 留白自检：异常空白页 {len(bad)} 页 {bad[:8]}")

    # ⑤ 出图（看真实观感）
    want = [int(x) for x in shots.split(",") if x.strip().isdigit()]
    for pno in want:
        if pno > len(doc):
            continue
        pm = doc[pno - 1].get_pixmap(dpi=110)
        png = out_dir / f"_preview_p{pno}.png"
        pm.save(str(png))
        print(f"[6] 出图：{png.relative_to(ROOT)}  ({pm.width}×{pm.height})")
    doc.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
