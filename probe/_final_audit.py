# -*- coding: utf-8 -*-
"""最终核对：本轮所有改动过的文件的行尾符与关键结构。"""
import re
from pathlib import Path

FILES = [
    (r"D:\AgentSense\AgentSense\web_template.html", "CRLF"),
    (r"D:\AgentSense\AgentSense\tests\test_page_tools_variety_state.py", "LF"),
    (r"D:\AgentSense\AgentSense\tests\test_research_variety_summary.py", "LF"),
    (r"D:\AgentSense\AgentSense\tests\test_upload_dropzone.py", "LF"),
    (r"C:\Users\lenovo\.workbuddy\skills\agentsense-add-tab\SKILL.md", "LF"),
    (r"C:\Users\lenovo\WorkBuddy\2026-09-07-16-38-57\.workbuddy\memory\MEMORY.md", "CRLF"),
]

print("=== 行尾符核对 ===")
for p, want in FILES:
    d = Path(p).read_bytes()
    crlf, lf = d.count(b"\r\n"), d.count(b"\n") - d.count(b"\r\n")
    got = "CRLF" if lf == 0 and crlf > 0 else ("LF" if crlf == 0 else "MIXED!")
    ok = "OK " if got == want else "!! "
    print("  [%s] %-62s %s (crlf=%d bareLF=%d)" % (ok, Path(p).name, got, crlf, lf))

t = Path(r"D:\AgentSense\AgentSense\web_template.html").read_text(encoding="utf-8", errors="replace")
print("\n=== web_template.html 关键结构 ===")
checks = [
    ("控制条容器唯一", t.count('<div id="tab-gtja-tools" class="page-tools">') == 1),
    ("覆盖矩阵卡已移出控制条", '<h3>覆盖矩阵' in t and t.count('<h3>覆盖矩阵') == 1),
    ("_SEC_IDX_VER >= 16", int(re.search(r"_SEC_IDX_VER:\s*'(\d+)'", t).group(1)) >= 16),
    ("badge 挂在覆盖矩阵标题下", '覆盖矩阵' in Path(r"D:\AgentSense\AgentSense\web_template.html")
        .read_text(encoding="utf-8", errors="replace").split('<h3>覆盖矩阵')[1][:60]),
    ("已删除 report_rows 错误赋值", "v.report_rows = d.reports" not in t),
    ("消费端仍按数组兜底", "const reps = v.report_rows || [];" in t),
]
for name, ok in checks:
    print("  [%s] %s" % ("OK " if ok else "!! ", name))
