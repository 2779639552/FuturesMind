# -*- coding: utf-8 -*-
"""P1: 把各 tab 的"页级控制条"整块搬到页签最顶部(索引条之上), 包一层 .page-tools。

为什么用字节替换而不是 Edit 工具:
  本文件 12820 行**全 CRLF**; Edit/open(w) 会把 CRLF 转成 LF,
  造成"整文件变更"的假 diff(项目铁律 #8)。
本脚本只做二进制切片插入, 不碰任何一行的行尾符。

搬迁后元素顺序变了, 但 DOM id 不动 ⇒ 所有 getElementById 调用不受影响。
"""
import re
import shutil
import sys
from pathlib import Path

P = Path(r"D:\AgentSense\AgentSense\web_template.html")

# (tab, 控制块起始锚点, 控制块之后的下一个锚点, 块内必须出现的 id, 说明)
BLOCKS = [
    ("run", "  <!-- Config Row -->", "  <!-- 分析输入数据小看板", "sym", "分析配置栅格"),
    ("sentiment", "  <!-- Lookback control -->", "  <!-- Sentiment-Price Overlay Chart -->", "lookback-days", "回看天数条"),
    ("quant", "  <!-- \u2500\u2500 \u9876\u90e8: \u54c1\u79cd\u4e0e\u533a\u95f4\u63a7\u5236 + \u6570\u636e\u8fb9\u754c \u2500\u2500 -->",
     "  <!-- \u2500\u2500 \u2460 K \u7ebf\u56fe(\u4e3b\u56fe, \u7531\u770b\u677f\u8fc1\u5165\u5e76\u589e\u5f3a) \u2500\u2500 -->", "q-variety", "品种/K线控制条"),
    ("fund", "  <!-- \u2500\u2500 \u5de5\u5177\u6761: \u54c1\u79cd + \u64cd\u4f5c \u2500\u2500 -->",
     "  <!-- \u2500\u2500 \u2460 \u4e09\u6307\u6807\u7b56\u7565\u7cfb\u7edf \u2500\u2500 -->", "f-variety", "基本面工具条"),
    ("warrant", "  <!-- \u6570\u636e\u72b6\u6001\u6761 -->",
     "  <!-- ===================== \u7b2c\u4e00\u680f: \u4ed3\u5355\u603b\u89c8 ===================== -->", "wt-date", "数据状态条(图不当)"),
    ("warrant", "    <div style=\"display:flex;gap:12px;align-items:end;flex-wrap:wrap;margin-bottom:14px\">\r\n      <div style=\"flex:1;min-width:220px\">",
     "    <div class=\"wt-kpi\" id=\"wt-kpi\"></div>", "wt-product", "品种仓单控制格(只搬格, 图表留下)"),
    ("research", "  <!-- \u7814\u62a5\u95ee\u7b54\u5361\u7247(", "  <!-- \u7814\u62a5\u6bcf\u65e5\u603b\u7ed3\u5361\u7247(", "res-ask-question", "研报问答控制条"),
    ("gtja", "  <!-- \u2460 \u67e5\u8be2\u63a7\u5236 + \u8986\u76d6\u77e9\u9635 -->", "  <!-- \u2461 \u56db\u6e90\u72b6\u6001", "gt-variety", "查询控制条"),
    ("variety", "  <!-- \u63a7\u5236\u533a: \u6a21\u5757\u4e0b\u62c9 + \u65e5\u671f\u8303\u56f4 + \u5feb\u6377\u6863 + \u5e8f\u5217\u591a\u9009 -->",
     "  <!-- \u56fe\u8868 -->", "bm-variety", "数据模块控制区"),
]

data = P.read_bytes()
orig = bytes(data)
crlf_before = data.count(b"\r\n")
lf_before = data.count(b"\n")
size_before = len(data)

report = []
# 收集每块的切片, 最后统一插入
collected = {}   # tab -> [bytes, ...]
for tab, start_m, end_m, need_id, note in BLOCKS:
    sb = start_m.encode("utf-8")
    eb = end_m.encode("utf-8")
    a = data.find(sb)
    if a < 0:
        report.append("FAIL 起始锚点未找到: %s / %s" % (tab, note))
        continue
    if data.find(sb, a + 1) >= 0:
        report.append("FAIL 起始锚点不唯一: %s / %s" % (tab, note))
        continue
    b = data.find(eb, a)
    if b < 0:
        report.append("FAIL 结束锚点未找到: %s / %s" % (tab, note))
        continue
    slice_ = data[a:b]
    if need_id.encode("utf-8") not in slice_:
        report.append("FAIL 切片内未见预期 id %s (%s / %s)" % (need_id, tab, note))
        continue
    # 去掉切片
    data = data[:a] + data[b:]
    collected.setdefault(tab, []).append(slice_)
    report.append("OK   取出 %-10s %-28s %6d bytes" % (tab, note, len(slice_)))

if any(r.startswith("FAIL") for r in report):
    print("\n".join(report))
    print("!! 有失败项, 未写入任何内容")
    sys.exit(1)

# 逐 tab 在页签首行之后插入 .page-tools 包裹
for tab, slices in collected.items():
    anchor = ('<div id="tab-%s" class="tab' % tab).encode("utf-8")
    i = data.find(anchor)
    if i < 0:
        print("!! 未找到页签首行: %s" % tab)
        sys.exit(1)
    j = data.find(b"\r\n", i)
    if j < 0:
        print("!! 页签首行无行尾: %s" % tab)
        sys.exit(1)
    ins = j + 2
    block = b'  <div id="tab-%s-tools" class="page-tools">\r\n' % tab.encode()
    for s in slices:
        block += s
    block += b'  </div>\r\n'
    data = data[:ins] + block + data[ins:]
    report.append("OK   插入 %-10s .page-tools (%d 块)" % (tab, len(slices)))

# 行尾符自检: 必须一个都没变
crlf_after = data.count(b"\r\n")
lf_after = data.count(b"\n")
if lf_after - crlf_after != 0:
    print("\n".join(report))
    print("!! 行尾符被破坏: bare LF %d -> %d" % (lf_before - crlf_before, lf_after - crlf_after))
    sys.exit(1)

shutil.copy2(P, str(P) + ".bak_p1_20260930")
P.write_bytes(data)

print("\n".join(report))
print("-" * 60)
print("size  %d -> %d (%+d)" % (size_before, len(data), len(data) - size_before))
print("CRLF  %d -> %d   bareLF=%d" % (crlf_before, crlf_after, lf_after - crlf_after))
print("backup: %s.bak_p1_20260930" % P)
