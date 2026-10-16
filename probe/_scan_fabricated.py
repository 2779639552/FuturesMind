# -*- coding: utf-8 -*-
"""扫全库 done 研报: 结论「数据支撑」里的日期-数值 vs 原文文本交叉验证。
识别"结论引用了原文没有的数据"(RAG 串味/编造)的疑似行。"""
import sqlite3, re

db = r"C:\Users\lenovo\.tradingagents\agentsense.db"
c = sqlite3.connect(db)
c.row_factory = sqlite3.Row
rows = c.execute("""SELECT id, title, varieties, extracted_text, conclusion_md
  FROM research_reports WHERE status IN ('done','summarized')""").fetchall()
print("done/summarized 总数:", len(rows))

# 从结论「数据支撑」节抓 YYYY-MM-DD 日期, 看该日期是否在原文出现
suspicious = []
for r in rows:
    cm = r["conclusion_md"] or ""
    txt = r["extracted_text"] or ""
    if not cm or not txt:
        continue
    # 只取「数据支撑」节
    m = re.search(r"## 数据支撑(.*?)(## |\Z)", cm, re.S)
    support = m.group(1) if m else cm
    dates = set(re.findall(r"\d{4}-\d{2}-\d{2}", support))
    if not dates:
        continue
    missing = [d for d in dates if d not in txt]
    # 结论里 40%+ 的日期在原文找不到 → 疑似串味/编造
    if len(missing) >= 2 and len(missing) / len(dates) >= 0.5:
        suspicious.append({
            "id": r["id"], "title": (r["title"] or "")[:36],
            "varieties": r["varieties"], "dates": len(dates), "missing": len(missing),
            "missing_dates": missing[:6],
        })
print("疑似串味/编造(结论>=50%日期原文没有):", len(suspicious))
for s in suspicious[:25]:
    print(f"  #{s['id']} {s['varieties']:12s} {s['title']}  日期{s['dates']}缺{s['missing']} {s['missing_dates']}")
c.close()
