# -*- coding: utf-8 -*-
"""上线收尾: 1) 验证公网隧道已带新 tab; 2) 清理测试任务只留一份示例。"""
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
out = []

# ── 1. 公网隧道 ──
url = (ROOT / "public_url.txt").read_text(encoding="utf-8").strip()
try:
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    r = op.open(url + "/", timeout=30)
    html = r.read().decode("utf-8", "replace")
    out.append("公网 GET / -> %s len=%d" % (r.status, len(html)))
    for needle in ('data-tab="foreign"', 'id="tab-foreign"', "startForeign"):
        out.append("   contains %r : %s" % (needle, needle in html))
except Exception as e:
    out.append("公网检查失败: %s: %s" % (type(e).__name__, str(e)[:200]))

# ── 2. 清理测试任务(保留最新一份作为可预览示例) ──
sys_path = str(ROOT)
import sys  # noqa: E402
sys.path.insert(0, sys_path)
import os  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
import foreign_report  # noqa: E402

jobs = foreign_report.list_jobs(100)
out.append("现有任务 %d 个" % len(jobs))
keep = next((j for j in jobs if j.get("status") == "done"), None)
if keep:
    out.append("保留示例: %s (%s)" % (keep.get("id"), keep.get("out_name")))
for j in jobs:
    if keep and j.get("id") == keep.get("id"):
        continue
    foreign_report.delete_job(j["id"])
    out.append("已删除: %s (%s)" % (j.get("id"), j.get("filename")))
out.append("剩余任务 %d 个" % len(foreign_report.list_jobs(100)))

(ROOT / "probe" / "_finalize.txt").write_text("\n".join(out), encoding="utf-8")
print("\n".join(out))
