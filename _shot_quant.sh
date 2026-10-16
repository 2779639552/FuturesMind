#!/usr/bin/env bash
# 量化页端到端截图验证(临时脚本, 验证完即删)
# 【为什么要链式】agent-browser 的 daemon 在每次独立 CLI 调用之间会重置页面(变 about:blank),
#   所以 打开→点击→等待→截图 必须在同一条命令里连续执行。
set -u
export PATH="/d/npm_storage/node_global:/usr/bin:$PATH"
export NO_PROXY="127.0.0.1,localhost"
export no_proxy="127.0.0.1,localhost"
cd /d/AgentSense/AgentSense || exit 1

URL="http://127.0.0.1:5055/"
OUT="shots"
mkdir -p "$OUT"

echo "== 1. 打开页面 =="
agent-browser open "$URL" 2>&1 | tail -3
sleep 3

echo "== 2. 点击「量化分析」 =="
agent-browser click 'nav button[data-tab="quant"]' 2>&1 | tail -3
sleep 12

echo "== 3. 页面状态 =="
agent-browser eval "JSON.stringify({title:document.title,tab:document.querySelector('.tab.active')?.id,navCount:document.querySelectorAll('nav button').length,bodyLen:document.body.innerHTML.length})" 2>&1 | tail -3

echo "== 4. 全页截图 =="
agent-browser screenshot "$OUT/quant_full.png" --full 2>&1 | tail -2
ls -l "$OUT/quant_full.png" 2>/dev/null | awk '{print "   size:", $5}'

echo "== 5. 视口截图 =="
agent-browser screenshot "$OUT/quant_view.png" 2>&1 | tail -2
ls -l "$OUT/quant_view.png" 2>/dev/null | awk '{print "   size:", $5}'

echo "== done =="
