# -*- coding: utf-8 -*-
"""用**真实数据**跑完整链路，定位观点到底在哪一层被裁。

【为什么必须这样做】上一版验证用**手工构造**的 items 直接喂 _build_daily_views_table，
  绕过了 `_collect_daily_report_items`（那里还有 max_len / text_cap 等**上游**截断）
  ⇒ 测出"下游不截断"就以为是修好了，而真实链路仍可能被上游裁。
  ⇒ 真实链路的四层（可疑）截断点：
      ① _extract_key_opinion(max_len=150)      单条观点结构性裁剪
      ② _merge_same_source_items(text_cap=200) 同源合并后拼接裁剪
      ③ _build_daily_views_table 的 op[:40]/view[:80]   （已改）
      ④ trade 侧同型
"""
import importlib
import os
import sys
from pathlib import Path

os.environ.setdefault("AGENTSENSE_AUTH_DISABLE", "1")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
wa = importlib.import_module("web_app")
get_db = wa.get_db   # db 是 web_app 里的模块级单例（database 模块本身没有这些查询函数）

DATE = "2026-10-11"      # 该周含 10-14（用户报的"10.14 周度"）
RTYPE = "周报"

print("=" * 84)
print("  真实链路 · 观点长度逐层观测（date=%s rtype=%s）" % (DATE, RTYPE))
print("=" * 84)

rows = get_db().list_research_reports(limit=800)
print("  DB 行数: %d" % len(rows))

items = wa._collect_daily_report_items(rows, DATE, rtype=RTYPE)
print("  items: %d 条" % len(items))

lu = [it for it in items if str(it.get("variety") or "").startswith("LU")]
print()
print("── ① _collect_daily_report_items 产出（上游截断后的形态）")
for it in lu:
    op = str(it.get("opinion") or "")
    tr = str(it.get("trade") or "")
    print("   来源=%s" % it.get("source"))
    print("     opinion 长度=%d  trade 长度=%d  merged=%s" % (len(op), len(tr), it.get("merged_count")))
    print("     opinion 末尾: …%s" % op[-40:])
    print("     含换行: opinion=%s trade=%s" % ("\n" in op, "\n" in tr))

# ② 直接看原始 conclusion（不走 items，看原文到底多长）
print()
print("── ② 原始 conclusion 里 LU 段的长度（判断上游裁了多少）")
import json  # noqa: E402
ext = Path.home() / ".tradingagents/external_data"
f = ext / "LU_research.json"
if f.exists():
    # ⚠ 聚合文件是 dict{variety, updated, reports} —— `reports` 才是列表
    #   （第一版按 list 读 ⇒ 切片报 KeyError: slice(None,3,None)）
    _j = json.loads(f.read_text(encoding="utf-8"))
    recs = _j.get("reports") if isinstance(_j, dict) else _j
    for r in (recs or [])[:3]:
        c = str(r.get("conclusion") or "")
        seg = c[c.find("## 供需格局"):] if "## 供需格局" in c else c
        seg = seg[:seg.find("## 数据支撑")] if "## 数据支撑" in seg else seg
        print("   报 id=%s source=%s" % (r.get("id"), str(r.get("source"))[:16]))
        print("     该品种段全文 %d 字；_extract_key_opinion 默认 max_len=360 时的产出长度:"
              % len(seg))
        for ml in (150, 360, 100000):
            got = wa._extract_key_opinion(seg, max_len=ml, include_trade=False)
            print("        max_len=%-7s → %d 字" % (ml, len(got)))
else:
    print("   （无 LU_research.json）")

# ③ 最终总表行
print()
print("── ③ 最终总表 LU 行")
tbl = wa._build_daily_views_table(DATE, items, RTYPE, llm_views={}, llm_trades={}, all_rows=rows)
row = next((ln for ln in tbl.split("\n") if ln.startswith("| LU")), "")
print("   行长度 %d 字符" % len(row))
print("   含省略号: %s" % ("…" in row))
cells = [c.strip() for c in row.strip("|").split("|")]
if len(cells) >= 7:
    print("   观点列长度: %d" % len(cells[5]))
    print("   观点列末尾: …%s" % cells[5][-50:])
    print("   头寸列长度: %d" % len(cells[6]))
print()
print("=" * 84)
print("  ⇒ 若 ③ 的观点列已明显长于 ② 的 max_len=150 产出 ⇒ 说明下游已放开、上游是瓶颈")
print("=" * 84)
