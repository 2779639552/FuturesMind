# -*- coding: utf-8 -*-
"""自证两个合并护栏的「击杀」是真变异：先确认变异体确实被破坏，再确认护栏变红。"""
import sys

sys.path.insert(0, r"D:\AgentSense\AgentSense\tests")
from test_page_tools_variety_state import (   # noqa: E402
    _src, _merged_card_span, _merge_assertions_ok, GTJA_MERGED_TITLE, _div_span,
)

src = _src()

print("=== 基线 ===")
span = _merged_card_span(src)
inside = src[span[0]:span[1]]
print("合并卡区间", span, "| 卡内 h3 数", inside.count('<h3>'))
print("三容器同在卡内:", all(i in inside for i in
      ('id="gt-indicators"', 'id="gt-chart"', 'id="gt-table"')))
print("_merge_assertions_ok(基线) =", _merge_assertions_ok(src))

print("\n=== 变异①: 把明细块搬出合并卡 ===")
tbl = inside.find('<!-- 明细数据')
rel = inside[tbl:].find('<div')
start_abs = span[0] + tbl + rel
dup, end = _div_span(src, start_abs)
print("明细块首 40 字:", repr(dup[:40]))
print("明细块末 20 字:", repr(dup[-20:]))
mutated = src[:start_abs] + src[end:] + "\n" + dup
print("变异生效:", mutated != src)
mspan = _merged_card_span(mutated)
minside = mutated[mspan[0]:mspan[1]] if mspan else ""
moved_out = 'id="gt-table"' not in minside
print("★ #gt-table 确实不在合并卡内了:", moved_out)
print("_merge_assertions_ok(变异①) =", _merge_assertions_ok(mutated),
      "→", "已击杀 ✓" if not _merge_assertions_ok(mutated) else "未击杀 ✗")

print("\n=== 变异②: 给合并卡塞第二个 h3 ===")
m2 = src.replace(GTJA_MERGED_TITLE, GTJA_MERGED_TITLE + '\n    <h3>走势图</h3>', 1)
s2 = _merged_card_span(m2)
h3n = m2[s2[0]:s2[1]].count('<h3>') if s2 else -1
print("变异生效:", m2 != src, "| 卡内 h3 数 →", h3n)
print("_merge_assertions_ok(变异②) =", _merge_assertions_ok(m2),
      "→", "已击杀 ✓" if not _merge_assertions_ok(m2) else "未击杀 ✗")

print("\n=== 变异③: 恢复 _gtEnsureVisible 调用 ===")
m3 = src.replace(
    "    this._renderGtjaTable(ind);\n",
    "    this._renderGtjaTable(ind);\n"
    "    this._gtEnsureVisible(document.getElementById('gt-table'));\n", 1)
print("变异生效:", m3 != src)
print("_merge_assertions_ok(变异③) =", _merge_assertions_ok(m3),
      "→", "已击杀 ✓" if not _merge_assertions_ok(m3) else "未击杀 ✗")
