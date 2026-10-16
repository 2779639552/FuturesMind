# -*- coding: utf-8 -*-
"""更可靠的编造检测: 结论「数据支撑」里的具体数值(带单位) 在原文里是否出现。
日期格式差异不算; 结论里 >50% 的数值原文完全没有 → 疑似编造/串味。"""
import sqlite3, re

db = r"C:\Users\lenovo\.tradingagents\agentsense.db"
c = sqlite3.connect(db)
c.row_factory = sqlite3.Row
rows = c.execute("""SELECT id, title, varieties, extracted_text, conclusion_md
  FROM research_reports WHERE status IN ('done','summarized')""").fetchall()

# 提取带单位的数值: 数字+单位(万吨/元/吨/美元/桶/亿/手/%等)
NUM_PAT = re.compile(r"\d+(?:\.\d+)?(?:\.\d+)?\s*(?:万吨|万手|亿元|亿美元|元/吨|美元/吨|美元/桶|元/桶|%|点|手|万吨|亿桶|天|周)")
# 原文数字集合(纯数字也收集, 便于宽松匹配)
def numbers_in(text):
    return set(re.findall(r"\d+(?:\.\d+)?", text))

suspicious = []
checked = 0
for r in rows:
    cm = r["conclusion_md"] or ""
    txt = r["extracted_text"] or ""
    if not cm or not txt:
        continue
    m = re.search(r"## 数据支撑(.*?)(## |\Z)", cm, re.S)
    support = m.group(1) if m else ""
    if not support:
        continue
    # 结论里的数值
    concl_nums = set(re.findall(r"\d+(?:\.\d+)?", support))
    # 去除常见日期(YYYY/MM/DD 混合)与年份
    real_nums = {n for n in concl_nums if not re.match(r"^(19|20)\d{2}$", n) and len(n) >= 2}
    if len(real_nums) < 3:
        continue
    txt_nums = numbers_in(txt)
    missing = [n for n in real_nums if n not in txt_nums]
    if len(missing) >= 3 and len(missing) / len(real_nums) >= 0.6:
        checked += 1
        suspicious.append({
            "id": r["id"], "title": (r["title"] or "")[:34],
            "varieties": r["varieties"], "nums": len(real_nums), "missing": len(missing),
            "missing_ex": missing[:8],
        })

print("检测到结论>=60%数值原文没有的行数:", len(suspicious), "(共检测", checked, "篇有>=3数值)")
for s in suspicious[:20]:
    print(f"  #{s['id']} {s['varieties']:14s} {s['title']}  数值{s['nums']}缺{s['missing']} {s['missing_ex']}")
c.close()
