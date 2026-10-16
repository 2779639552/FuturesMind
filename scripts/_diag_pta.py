"""诊断:国君 PTA 研报的方向归属(第一步提取/聚合/结论段)。"""
import json
import os
import sqlite3

c = sqlite3.connect(r"D:\AgentSense\AgentSense\_d.db")
c.row_factory = sqlite3.Row
if not os.path.exists(r"D:\AgentSense\AgentSense\_d.db"):
    raise SystemExit("需先复制 DB")

rows = c.execute(
    "SELECT id, title, varieties, direction, structured_data, conclusion_md FROM research_reports "
    "WHERE title LIKE '%多PX空PTA%' ORDER BY id"
).fetchall()
for r in rows:
    print(f"===== id={r['id']} | {r['title'][:60]} =====")
    print("  DB varieties:", r["varieties"], "| DB direction:", r["direction"])
    try:
        sd = json.loads(r["structured_data"] or "{}")
    except Exception:
        sd = {}
    for v in sd.get("varieties") or []:
        print(f"  第一步提取: {v.get('variety')} direction={v.get('direction')!r} confidence={v.get('confidence')}")
    cm = r["conclusion_md"] or ""
    # 各品种段的交易要素方向
    import re
    for m in re.finditer(r"##\s*([A-Z0-9]+)\s*结论\n(.*?)(?=\n##\s*[A-Za-z0-9]+\s*结论\b|\Z)", cm, re.S):
        code, seg = m.group(1), m.group(2)
        tm = re.search(r"##\s*交易要素[^\n]*\n([^\n]+)", seg)
        print(f"  结论段 {code}: 交易要素行={tm.group(1).strip()[:80] if tm else '(无)'}")
c.close()
