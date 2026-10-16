"""数据源取证 2：研报三源产出、仓单新鲜度、社媒落盘位置、新闻/信号表是否在用。"""
import sqlite3
import os
import glob
import json
from datetime import datetime
from collections import Counter

DB = r"C:\Users\lenovo\.tradingagents\agentsense.db"
c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row

print("=== 1) research_reports 按来源（source/collector 字段）===")
cols = [r[1] for r in c.execute("PRAGMA table_info(research_reports)")]
print("字段:", cols)
scol = next((x for x in ("source", "collector", "origin", "channel", "provider") if x in cols), None)
if scol:
    for r in c.execute(f"select {scol} s, count(*) n, max(created_at) last "
                       f"from research_reports group by {scol} order by n desc"):
        print(f"  {str(r['s']):22} {r['n']:>5} 篇   最近={r['last']}")
else:
    print("  无来源字段，改用文件名/标签推断")
print()

print("=== 2) research_reports 近 7 天每日新增 ===")
for r in c.execute("select substr(created_at,1,10) d, count(*) n from research_reports "
                   "where created_at >= date('now','-7 day') group by d order by d"):
    print(f"  {r['d']}  {r['n']:>4} 篇")
print()

print("=== 3) 仓单数据新鲜度 ===")
wd = r"D:\AgentSense\AgentSense\data\warrant\web"
for f in ("overview.json", "detail.json"):
    p = os.path.join(wd, f)
    if os.path.exists(p):
        st = os.stat(p)
        print(f"  {f:14} 修改时间={datetime.fromtimestamp(st.st_mtime):%Y-%m-%d %H:%M}  大小={st.st_size/1024:.0f}KB")
    else:
        print(f"  {f:14} 不存在")
ov = os.path.join(wd, "overview.json")
if os.path.exists(ov):
    try:
        d = json.load(open(ov, encoding="utf-8"))
        print("  overview 顶层键:", list(d)[:12])
        for k in ("as_of", "date", "collected_at", "ex_latest", "generated_at"):
            if isinstance(d, dict) and k in d:
                print(f"    {k} = {d[k]}")
    except Exception as e:
        print("  读取 overview 失败:", e)
print()

print("=== 4) 社媒帖子落盘位置（THINK2_DIR / output）===")
cands = [
    r"C:\Users\lenovo\.tradingagents\think2\output",
    r"C:\Users\lenovo\.tradingagents\output",
    r"D:\AgentSense\AgentSense\data\output",
]
for d in cands:
    print(f"  {d} -> {'存在' if os.path.isdir(d) else '不存在'}")
    if os.path.isdir(d):
        fs = sorted(glob.glob(os.path.join(d, "*")), key=os.path.getmtime, reverse=True)[:5]
        for f in fs:
            print(f"      {os.path.basename(f)}  {datetime.fromtimestamp(os.path.getmtime(f)):%Y-%m-%d %H:%M}")
print()

print("=== 5) news / sentiment_daily / trade_signals 是否在用 ===")
for t in ("news", "sentiment_daily", "trade_signals", "posts"):
    try:
        n = c.execute(f"select count(*) from {t}").fetchone()[0]
        print(f"  {t:16} {n} 行")
    except Exception as e:
        print(f"  {t:16} 错误 {e}")
