"""数据源可靠性取证：从 agentsense.db 统计各源近 14 天的采集成败与产出。"""
import sqlite3
from collections import defaultdict

DB = r"C:\Users\lenovo\.tradingagents\agentsense.db"
c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row


def cols(t):
    return [r[1] for r in c.execute(f"PRAGMA table_info({t})")]


print("=== collection_log 字段 ===")
print(cols("collection_log"))
print()
print("=== alerts 字段 ===")
print(cols("alerts"))
print()

print("=== 近 14 天 collection_log 按 source/platform + status ===")
rows = list(c.execute(
    "select * from collection_log where started_at >= date('now','-14 day') "
    "order by started_at desc"
))
print(f"总条数: {len(rows)}")
if rows:
    print("样例:", dict(rows[0]))
print()

agg = defaultdict(lambda: defaultdict(int))
notes = defaultdict(int)
for r in rows:
    d = dict(r)
    key = d.get("source") or d.get("platform") or d.get("name") or "?"
    agg[key][d.get("status") or "?"] += 1
    n = d.get("notes_count") or d.get("count") or d.get("items") or 0
    notes[key] += int(n or 0)

for k in sorted(agg):
    st = ", ".join(f"{s}={n}" for s, n in sorted(agg[k].items()))
    print(f"  {k:20} {st:45} 累计条数={notes[k]}")

print()
print("=== 近 14 天 alerts 按类型 ===")
al = list(c.execute(
    "select alert_type, severity, count(*) n, max(created_at) last "
    "from alerts where created_at >= date('now','-14 day') "
    "group by alert_type, severity order by n desc"
))
for r in al:
    print(f"  {r['alert_type']:36} {r['severity']:8} x{r['n']:<4} 最近={r['last']}")
if not al:
    print("  (无)")

print()
print("=== 各内容表最新数据时间与量 ===")
for t in ["posts", "research_reports", "news", "sentiment_daily", "trade_signals"]:
    try:
        n = c.execute(f"select count(*) from {t}").fetchone()[0]
        cs = cols(t)
        tcol = next((x for x in ("created_at", "collected_at", "date", "publish_time",
                                 "pub_time", "ts", "dt") if x in cs), None)
        last = c.execute(f"select max({tcol}) from {t}").fetchone()[0] if tcol else "?"
        print(f"  {t:20} {n:>8} 条   最新({tcol})={last}")
    except Exception as e:
        print(f"  {t:20} 读取失败: {e}")

print()
print("=== posts 按平台（近 14 天采集量）===")
try:
    cs = cols("posts")
    pcol = "platform" if "platform" in cs else None
    tcol = next((x for x in ("created_at", "collected_at", "publish_time", "pub_time", "ts") if x in cs), None)
    if pcol:
        q = f"select {pcol} p, count(*) n"
        if tcol:
            q += f", max({tcol}) last"
        q += f" from posts group by {pcol} order by n desc"
        for r in c.execute(q):
            print(f"  {r['p']:18} {r['n']:>7} 条" + (f"  最新={r['last']}" if tcol else ""))
except Exception as e:
    print("  失败:", e)
