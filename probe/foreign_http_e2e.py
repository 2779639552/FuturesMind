# -*- coding: utf-8 -*-
"""通过 Flask test_client 走一遍「外文研报」HTTP 层端到端(含鉴权关闭)。

验证点:
1) 5 条新路由全部注册(而非被全局守卫 401 掩盖);
2) 上传 → 轮询 → 下载(附件/内联)全链路可用;
3) 非法 id / 缺文件 / 错误扩展名被正确拒绝。
结果写 probe/_foreign_http_e2e.txt
"""
import os
import sys
import time
from pathlib import Path

os.environ["AGENTSENSE_AUTH_DISABLE"] = "1"  # 仅本进程生效, 不影响线上服务

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import web_app  # noqa: E402  【重】导入完整 Flask 应用

SAMPLE = ROOT / "probe" / "_sample_foreign_report.pdf"
out: list[str] = []
c = web_app.app.test_client()

# ── 1. 路由注册检查 ──
for path, method in (("/api/foreign/jobs", "GET"), ("/api/foreign/job/abc123", "GET"),
                     ("/api/foreign/job/abc123", "DELETE")):
    r = c.open(path, method=method)
    out.append("%-6s %-32s -> %s %s" % (method, path, r.status_code,
                                        (r.get_data(as_text=True) or "")[:120].replace("\n", " ")))

# ── 2. 参数校验 ──
r = c.post("/api/foreign/upload", data={})
out.append("POST /api/foreign/upload (无文件) -> %s %s" % (r.status_code, r.get_data(as_text=True)[:120]))

r = c.post("/api/foreign/upload", data={"file": (b"hello", "a.txt")},
           content_type="multipart/form-data")
out.append("POST /api/foreign/upload (.txt)  -> %s %s" % (r.status_code, r.get_data(as_text=True)[:120]))

r = c.get("/api/foreign/job/../../etc")
out.append("GET  /api/foreign/job/../../etc -> %s" % r.status_code)

# ── 3. 完整链路 ──
t0 = time.time()
with SAMPLE.open("rb") as fh:
    r = c.post("/api/foreign/upload", data={
        "file": (fh, "sample_foreign_report.pdf"),
        "include_images": "1", "include_insight": "1",
    }, content_type="multipart/form-data")
out.append("POST /api/foreign/upload (PDF)   -> %s %s" % (r.status_code, r.get_data(as_text=True)[:160]))
jid = (r.get_json() or {}).get("id")
out.append("job_id=%s" % jid)

if jid:
    while time.time() - t0 < 900:
        time.sleep(8)
        j = c.get(f"/api/foreign/job/{jid}").get_json() or {}
        out.append("  [%4ds] %3s%% %-9s %s" % (time.time() - t0, j.get("progress"),
                                               j.get("stage"), j.get("message")))
        if j.get("status") in ("done", "error"):
            break
    j = c.get(f"/api/foreign/job/{jid}").get_json() or {}
    out.append("final status=%s pages=%s out_name=%s has_pdf=%s" % (
        j.get("status"), j.get("pages"), j.get("out_name"), j.get("has_pdf")))
    if j.get("has_pdf"):
        r1 = c.get(f"/api/foreign/download/{jid}")
        r2 = c.get(f"/api/foreign/download/{jid}?inline=1")
        out.append("download attachment -> %s  len=%s  ct=%s" % (
            r1.status_code, len(r1.get_data()), r1.headers.get("Content-Disposition", "")[:90]))
        out.append("download inline     -> %s  len=%s  ct=%s" % (
            r2.status_code, len(r2.get_data()), r2.headers.get("Content-Type", "")))
    jobs = c.get("/api/foreign/jobs").get_json() or {}
    out.append("jobs list count=%d first_id=%s" % (
        len(jobs.get("jobs") or []), (jobs.get("jobs") or [{}])[0].get("id")))
    # 不删, 留着供页面历史列表展示

out_path = ROOT / "probe" / "_foreign_http_e2e.txt"
out_path.write_text("\n".join(out), encoding="utf-8")
print("\n".join(out))
