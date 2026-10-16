"""把源码注释/文档中过时的「20 品种」口径同步为「21 品种」(扩池后文档漂移清理)。

纯注释/文档字符串替换, **不改任何运行时行为**。安全措施:
  1. 只处理白名单文件(项目根 + 指定子模块), 不碰 probe/ worklog/ backup/ venv/;
  2. 逐文件备份到 backup/palm21_doc_<ts>/;
  3. 只替换精确 token: "20 品种"→"21 品种" / "20品种"→"21品种" / "20个品种"→"21个品种"
     以及 ppi_basis 的 "本池 14/20"→"本池 15/21";
  4. 打印逐条改动, 便于人工复核。

运行: ./venv/Scripts/python.exe probe/_sync_pool_doc_20to21.py [--apply]
不带 --apply 为 dry-run(只打印)。
"""
from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

WHITELIST = [
    "fund_module.py",
    "quant_module.py",
    "web_app.py",
    "price_fetcher.py",
    "ppi_basis.py",
    "news_collector_bloomberg.py",
    "research_collector_dongzheng.py",
    "research_collector_gtja.py",
    "scheduler.py",
    "signal_analyzer.py",
    "web_template.html",
    "tradingagents/dataflows/commodity_futures.py",
    "data_collection/validate/batch_collect.py",
]

REPL = [
    ("20 品种", "21 品种"),
    ("20品种", "21品种"),
    ("20个品种", "21个品种"),
    ("本池 14/20", "本池 15/21"),
]

# 排除项: 描述**历史事实**的句子(当时确实是 20 池), 改成 21 会篡改记录。
# 键 = (相对路径, 行内容中必须出现的片段); 命中即整行跳过。
EXCLUDE = [
    ("tradingagents/dataflows/commodity_futures.py", "用户指定 20 品种池"),
]

apply = "--apply" in sys.argv
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
bak = os.path.join("backup", f"palm21_doc_{ts}")
if apply:
    os.makedirs(bak, exist_ok=True)

total = 0
for rel in WHITELIST:
    if not os.path.isfile(rel):
        print(f"[skip] 不存在: {rel}")
        continue
    with open(rel, encoding="utf-8") as f:
        lines = f.read().split("\n")

    ex_frags = [frag for r, frag in EXCLUDE if r == rel]
    hits = []
    out_lines = []
    for i, line in enumerate(lines, 1):
        if any(frag in line for frag in ex_frags):
            out_lines.append(line)
            continue
        new = line
        for old, rep in REPL:
            if old in new:
                hits.append((i, old, line.strip()[:110]))
                new = new.replace(old, rep)
        out_lines.append(new)

    if hits:
        if apply:
            dst = os.path.join(bak, rel.replace("/", "__"))
            shutil.copy2(rel, dst)
            with open(rel, "w", encoding="utf-8") as f:
                f.write("\n".join(out_lines))
        print(f"\n### {rel}  ({len(hits)} 处)")
        for ln, old, text in hits:
            print(f"  L{ln}: [{old}→21] {text}")
        total += len(hits)
    else:
        print(f"[ok] 无残留: {rel}")

print(f"\n{'已应用' if apply else 'DRY-RUN'}; 命中合计 {total} 处; 备份目录={bak if apply else '(未写)'}")
