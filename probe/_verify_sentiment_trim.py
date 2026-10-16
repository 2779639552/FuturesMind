# -*- coding: utf-8 -*-
"""静态校验：情绪页精简后的结构与语法。"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMPL = ROOT / "web_template.html"
raw = TMPL.read_bytes()
src = raw.decode("utf-8")
fails = []


def ck(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


print("[1] 行尾 / 基本形态")
ck(raw.count(b"\r\n") > 0 and (raw.count(b"\n") - raw.count(b"\r\n")) == 0,
   f"CRLF 完整 (CRLF={raw.count(chr(13).encode()+chr(10).encode())}, bareLF={raw.count(b'\n') - raw.count(b'\r\n')})")

print("\n[2] tab-sentiment 容器 div 配平")
i = src.index('<div id="tab-sentiment"')
j = src.index(">", i)
depth = 0
k = i
end = None
while k < len(src):
    if src.startswith("<div", k):
        depth += 1
        k += 4
    elif src.startswith("</div>", k):
        depth -= 1
        k += 6
        if depth == 0:
            end = k
            break
    else:
        k += 1
ck(end is not None, f"tab-sentiment 闭合成功 (结束偏移={end})")
seg = src[i:end] if end else ""
ck(seg.count("<!--") == seg.count("-->"), f"区间注释配平 --={seg.count('<!--')} 结束={seg.count('-->')}")

print("\n[3] 已删模块的 HTML 归零（属性形式）")
for pat in ('id="asub-authors"', 'id="asub-framework"', 'id="asub-agentval"',
            'id="asub-agentbt"', 'id="asub-graph"',
            'id="authors-table"', 'id="chart-graph"', 'id="chart-sankey"',
            'id="btn-val-start"', 'id="btn-bt-start"', 'id="val-detail"', 'id="bt-detail"',
            'id="chart-val-dir"', 'id="chart-val-score"', 'id="chart-bt-compare"',
            'data-asub="authors"', 'data-asub="framework"', 'data-asub="agentval"',
            'data-asub="agentbt"', 'data-asub="graph"'):
    ck(pat not in src, f"无残留 {pat}")

print("\n[3b] 孤儿函数：定义保留(便于恢复)，但必须无调用入口")
for call in ("this.loadAuthors(", "this.loadGraphChart(", "this.loadSankeyChart(",
             "App.startAgentValidation(", "App.startBatchBacktest("):
    n = src.count(call)
    ck(n == 0, f"无调用点 {call} → 命中 {n}")
for d in ("async loadAuthors() {", "async loadGraphChart() {", "async loadSankeyChart() {",
          "async startAgentValidation() {", "async startBatchBacktest() {"):
    ck(d in src, f"定义仍保留: {d}")

print("\n[4] 保留模块完好")
for pat in ('id="asub-comprehensive"', 'id="asub-events"', 'id="analysis-subnav"',
            'data-asub="comprehensive"', 'data-asub="events"',
            'id="update-log"', 'id="btn-update"', 'id="update-perkw"',
            'id="backtest-stats"'):
    ck(pat in src, f"仍是 {pat}")

print("\n[5] 子导航按钮数 = 2")
nav = src[src.index('id="analysis-subnav"'):]
nav = nav[:nav.index("</div>")]
ck(nav.count("data-asub=") == 2, f"analysis-subnav 内按钮数={nav.count('data-asub=')} (应为 2)")
ck('id="asub-authors"' not in seg and 'id="asub-graph"' not in seg, "已删面板不在 tab 内")

print("\n[6] 版本号与开关一致性")
ck("_SEC_IDX_VER: '24'," in src, "_SEC_IDX_VER = '24'")
ck("_SEC_IDX_VER: '23'" not in src, "旧版本号 '23' 已退场")
ck("_SEC_FLAT_TABS" in src and "'sentiment'" not in src.split("_SEC_FLAT_TABS")[1][:200],
   "sentiment 未加入平铺名单(仍参与折叠 ⇒ 需重建索引)")

print("\n[7] 死分支清理")
ck("if (sub === 'authors')" not in src, "authors 死分支已删")
ck("if (sub === 'graph')" not in src, "graph 死分支已删")
ck("if (sub === 'comprehensive') this.loadComprehensive();" in src, "comprehensive 分支在")
ck("if (sub === 'events') this.loadEvents();" in src, "events 分支在")

print("\n[8] 内联 JS 语法 (node --check)")
parts = []
pos = 0
while True:
    a = src.find("<script>", pos)
    if a == -1:
        break
    b = src.find("</script>", a)
    parts.append(src[a + 8:b])
    pos = b + 9
tmp = ROOT / "probe" / "_tmp_inline_check.js"
tmp.write_text("\n;\n".join(parts), encoding="utf-8")
proc = subprocess.run(["node", "--check", str(tmp)], capture_output=True, text=True)
ck(proc.returncode == 0, f"node --check 通过 (returncode={proc.returncode}) {proc.stderr[:200]}")
tmp.unlink(missing_ok=True)

print("\n结果:", "全部通过 ✔" if not fails else f"{len(fails)} 项失败 ✘")
sys.exit(1 if fails else 0)
