# -*- coding: utf-8 -*-
"""把生成的 PDF 逐页渲染成 PNG，供肉眼检查排版（技能包要求的自检步骤：不要只看留白数字）。

用法：python probe/render_pdf_pages.py <pdf> [out_dir] [dpi]
"""
import sys
from pathlib import Path

import pymupdf

pdf = Path(sys.argv[1])
out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parent / "_pages"
dpi = int(sys.argv[3]) if len(sys.argv) > 3 else 96
out_dir.mkdir(parents=True, exist_ok=True)

doc = pymupdf.open(str(pdf))
for i, pg in enumerate(doc, 1):
    pm = pg.get_pixmap(dpi=dpi)
    pm.save(str(out_dir / f"p{i:02d}.png"))
doc.close()
print("rendered", len(doc), "pages ->", out_dir)
