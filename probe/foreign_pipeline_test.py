# -*- coding: utf-8 -*-
"""不启 Flask，直接跑 foreign_report 的完整管线（验收用）。

用法：python probe/foreign_pipeline_test.py [样本PDF路径]
结果：把任务终态 + 产物校验结论写到 probe/_foreign_test_report.txt
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import foreign_report as fr  # noqa: E402

SAMPLE = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "probe" / "._sample_foreign_report.pdf"
if not SAMPLE.exists():
    SAMPLE = ROOT / "probe" / "_sample_foreign_report.pdf"

fd = fr.UPLOAD_DIR
fd.mkdir(parents=True, exist_ok=True)
up = fd / "test_pipeline_sample.pdf"
up.write_bytes(SAMPLE.read_bytes())

t0 = time.time()
jid = fr.start_job(up, SAMPLE.name, {"include_images": True, "include_insight": True})
print("job:", jid)

last = ""
while True:
    time.sleep(10)
    job = fr.get_job(jid) or {}
    if job.get("message") != last:
        last = job.get("message") or ""
        print("[%4ds] %3s%% %-10s %s" % (time.time() - t0, job.get("progress"), job.get("stage"), last))
    if job.get("status") in ("done", "error"):
        break
    if time.time() - t0 > 1800:
        print("TIMEOUT")
        break

job = fr.get_job(jid) or {}
lines = []
lines.append("job_id=%s" % jid)
lines.append("status=%s stage=%s progress=%s" % (job.get("status"), job.get("stage"), job.get("progress")))
lines.append("elapsed=%.1fs" % (time.time() - t0))
lines.append("title=%s" % job.get("title"))
lines.append("org=%s" % job.get("org"))
lines.append("src_pages=%s chars=%s chunks=%s" % (job.get("n_pages"), job.get("chars"), job.get("chunks")))
lines.append("out_pdf=%s" % job.get("out_pdf"))
lines.append("out_name=%s" % job.get("out_name"))
lines.append("pages=%s" % job.get("pages"))
lines.append("error=%s" % job.get("error"))
for w in job.get("warnings") or []:
    lines.append("WARN: %s" % w)
lines.append("--- log ---")
lines.extend(job.get("log") or [])

# ── 产物自检（按技能包口径）──
out = job.get("out_pdf")
if out and Path(out).exists():
    import pymupdf

    doc = pymupdf.open(out)
    lines.append("--- verify ---")
    lines.append("pdf_pages=%d size=%.2fMB" % (len(doc), Path(out).stat().st_size / 1048576))
    lines.append("page_size=%s" % (doc[0].rect,))
    fonts = set()
    for pg in doc:
        for f in pg.get_fonts(full=True):
            fonts.add((f[3], f[4]))
    lines.append("fonts=%s" % sorted(fonts))
    toc = doc.get_toc()
    lines.append("toc=%d" % len(toc))
    for t in toc:
        lines.append("   %s%s -> p%d" % ("  " * (t[0] - 1), t[1], t[2]))
    # 底部留白（排除页眉页脚带）
    for i, pg in enumerate(doc, 1):
        pm = pg.get_pixmap(dpi=72)
        h, w, n, s = pm.height, pm.width, pm.n, pm.samples
        last_y = 0
        for y in range(34, min(795, h)):
            if any(s[y * w * n + k] < 246 for k in range(0, w * n, n * 5)):
                last_y = y
        pct = round((min(795, h) - last_y) * 100 / (min(795, h) - 34)) if last_y else 100
        lines.append("   p%-3d bottom_blank=%d%%" % (i, pct))
    # 异常字符扫描
    txt = "\n".join(pg.get_text() for pg in doc)
    for bad in ("**", "<b>", "</b>", "<li>", "&amp;lt;", "None", "nan"):
        if bad in txt:
            lines.append("SUSPECT_TOKEN: %r" % bad)
    lines.append("total_text_chars=%d" % len(txt))
    doc.close()
else:
    lines.append("--- verify: NO PDF ---")

(ROOT / "probe" / "_foreign_test_report.txt").write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines[:20]))
