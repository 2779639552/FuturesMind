# -*- coding: utf-8 -*-
"""精确验证上传扩展名白名单(.txt 应被 400 拒绝)。"""
import os
import sys
from pathlib import Path

os.environ["AGENTSENSE_AUTH_DISABLE"] = "1"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
import web_app  # noqa: E402

c = web_app.app.test_client()
out = []
for name, body in (("a.txt", b"hello"), ("b.exe", b"MZ"), ("c.PDF", b"%PDF-1.4\n")):
    r = c.post("/api/foreign/upload", data={"file": (open("rb" and __import__("io").BytesIO(body), "rb") if False else __import__("io").BytesIO(body), name)},
               content_type="multipart/form-data")
    out.append("%-6s -> %s %s" % (name, r.status_code, r.get_data(as_text=True)[:110].replace("\n", " ")))
(ROOT / "probe" / "_foreign_ext_check.txt").write_text("\n".join(out), encoding="utf-8")
print("\n".join(out))
