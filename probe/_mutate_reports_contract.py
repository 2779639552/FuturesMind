# -*- coding: utf-8 -*-
"""真实文件级变异验证：把错误赋值注入 web_template.html，跑护栏，必须变红；然后还原。

⚠️ web_template.html 是 CRLF；新注入行也必须用 \\r\\n。
"""
import shutil
import subprocess
import sys

TPL = r"D:\AgentSense\AgentSense\web_template.html"
BAK = r"D:\AgentSense\AgentSense\probe\_bak_reports_contract.html"
PY = r"D:\AgentSense\AgentSense\venv\Scripts\python.exe"

ANCHOR = b"v.generated_at = d.generated_at || '';"
INJECT = b"\r\n          v.report_rows = d.reports || 0;"

orig = open(TPL, "rb").read()
assert orig.count(ANCHOR) == 1, "锚点不唯一: %d" % orig.count(ANCHOR)
shutil.copy2(TPL, BAK)

mutated = orig.replace(ANCHOR, ANCHOR + INJECT, 1)
open(TPL, "wb").write(mutated)
print("[注入] bytes %d -> %d, 含错误赋值: %s"
      % (len(orig), len(mutated),
         b"v.report_rows = d.reports" in mutated))

try:
    r = subprocess.run(
        [PY, "-m", "pytest",
         "tests/test_research_variety_summary.py::test_template_never_assigns_scalar_reports_to_report_rows",
         "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=r"D:\AgentSense\AgentSense", capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    tail = (r.stdout or "").strip().splitlines()[-6:]
    print("[变异体运行] rc=%s" % r.returncode)
    for ln in tail:
        print("   ", ln)
    killed = r.returncode != 0
finally:
    shutil.copy2(BAK, TPL)
    restored = open(TPL, "rb").read()
    print("[还原] 字节一致: %s | bareLF=%d"
          % (restored == orig, restored.count(b"\n") - restored.count(b"\r\n")))

print("\n结论: 变异体 %s —— 护栏%s"
      % ("变红(FAIL)" if killed else "仍绿(PASS)", "有效, 抓得住该回退" if killed else "【失效】"))
sys.exit(0 if killed else 1)
