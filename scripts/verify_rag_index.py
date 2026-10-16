# -*- coding: utf-8 -*-
"""重建后验收:① 抽样自召回率 ② 用户原话检索实测 ③ 库一致性。

验收标准(缺一不可):
  1) health_check 抽样自召回率 == 1.0(抽 60 篇)
  2) "PTA近日情况" 的 top 结果里出现 09-16 的两篇(579 华泰 / 564 国君)
  3) store.count() 与 DB done 篇数口径对得上
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradingagents.rag import service, store  # noqa: E402

DB = Path.home() / ".tradingagents" / "agentsense.db"

print("=" * 78)
print("[1] 索引规模")
print("=" * 78)
st = service.index_status()
print("  index_status:", st)
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
cur = con.cursor()
cur.execute("SELECT COUNT(*) FROM research_reports WHERE status='done'")
n_done = cur.fetchone()[0]
print(f"  DB done 篇数 = {n_done}")

print()
print("=" * 78)
print("[2] 抽样自召回体检(抽 60 篇)")
print("=" * 78)
hc = service.health_check(sample=60)
print(" ", hc)

print()
print("=" * 78)
print("[3] 用户原话检索实测")
print("=" * 78)
for kw in ("PTA近日情况", "PTA 近日情况", "PTA近期走势怎么样"):
    print(f"\n--- {kw!r} ---")
    for var in (None, "TA"):
        try:
            hits = service.retrieve_hits(kw, top_k=6, variety=var)
        except Exception as e:  # noqa: BLE001
            print(f"   variety={var}: ERR {e}")
            continue
        print(f"   [variety={var}]")
        for h in hits:
            m = h["metadata"] or {}
            print(
                f"      {h['score']:.4f} rid={m.get('report_id')} {m.get('publish_date')} "
                f"kind={m.get('kind')} | {h['text'][:48].replace(chr(10), ' ')}"
            )

print()
print("=" * 78)
print("[4] 09-16 两篇是否已入索引 + 可否自召回")
print("=" * 78)
from tradingagents.rag import embedding  # noqa: E402

for rid in (579, 564, 470, 485, 71, 150, 250, 350, 437):
    cur.execute("SELECT id,title,publish_date,status FROM research_reports WHERE id=?", (rid,))
    r = cur.fetchone()
    if not r:
        print(f"  rid={rid}: DB 无")
        continue
    try:
        one = store.get_by_report(rid, include=["documents"])
        docs = one.get("documents")
        docs = [] if docs is None else list(docs)
        ok = "-"
        if docs:
            v = embedding.encode([str(docs[0])])[0]
            res = store.query(list(v), top_k=1)
            ok = "✅" if res and str(res[0]["id"]).split(":", 1)[0] == str(rid) else f"❌(top1={res[0]['id'] if res else '无'})"
    except Exception as e:  # noqa: BLE001
        docs, ok = [], f"ERR {type(e).__name__}"
    print(f"  rid={rid:<5} {r[2]} {r[3]:<8} 块={len(docs):<4} 自召回={ok}")
con.close()
