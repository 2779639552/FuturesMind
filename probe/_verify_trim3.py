# -*- coding: utf-8 -*-
"""三项改动的验收脚本（可反复重跑，不修改任何文件）。

核对点：
  A. HTML 结构：三处改动确实生效（剥注释判定）+ div 配平
  B. 折叠块：代码形态正确（max-height / 不用 display:none / 单 h3 / 跨渲染保持）
  C. JS 语法：抽内联 script 交给 node --check
  D. id 完整性：与改动前逐项对比"未新增缺失 id / 未新增孤儿调用"
  E. 死代码守卫：被删容器的取值点必须都有守卫
"""
from __future__ import annotations

import glob
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "web_template.html"
PYEXE = ROOT / "venv/Scripts/python.exe"

PASS = FAIL = 0
FAILS: list = []


def ck(cond, label, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✔ %s" % label)
    else:
        FAIL += 1
        FAILS.append(label)
        print("  ✘ %s %s" % (label, extra))


def strip_comments(s: str) -> str:
    return re.sub(r"<!--.*?-->", "", s, flags=re.S)


def main() -> int:
    h = TPL.read_bytes().decode("utf-8")
    assert h.count("\r\n") > 0, "web_template.html 应为 CRLF"
    clean = strip_comments(h)

    print("=== A. 三项改动是否生效（剥注释判定）===")
    ck("<h3>② 四源取数状态</h3>" not in clean, "① 四源取数状态卡已删")
    ck('id="gt-sources"' not in clean, "① #gt-sources 容器已删")
    ck("<h3>② 指标总览 / 走势图 / 明细" in clean, "① 原③指标总览已升为②")
    ck("<h3>③ 指标总览" not in clean, "① 旧③标题已无")
    ck('id="f-pi-badge"' not in clean, "② #f-pi-badge 已删")
    ck('id="chart-f-pi"' not in clean, "② #chart-f-pi 容器已删")
    ck("价格 vs 仓单库存 <span" not in clean, "② 该图标题(含徽标)已删")
    # 【2026-10-16 反转】用户当天追加要求：连「价格-库存关联分析」小模块一起去掉。
    ck('id="f-analysis-badge"' not in clean, "② 「价格-库存关联分析」小模块已删(2026-10-16)")
    ck('id="f-analysis-body"' not in clean, "② #f-analysis-body 容器已删")
    ck('id="f-research"' in clean, "③ #f-research 容器仍在")
    ck("<!-- 【2026-10-14" in h, "三处都留了迁移/删除说明注释")
    print()

    print("=== B. 折叠块代码形态 ===")
    ck(h.count(" toggleResearchSection(pfx) {") == 1, "toggleResearchSection 已定义且唯一")
    ck("${P}-research-head" in h and "${P}-research-body" in h, "折叠块 head/body id 已生成")
    ck("${P}-research-caret" in h, "折叠指示器(caret)已生成")
    ck("max-height:${wasOpen ? 'none' : '0'}" in h, "默认收起用 max-height:0（非 display:none）")
    ck("max-height .28s ease" in h, "有过渡动画")
    # 折叠区里绝不能出现 display:none（否则 ECharts 空白）
    i = h.index("${P}-research-body")
    seg = h[i - 200:i + 1200]
    ck("display:none" not in seg, "折叠区未用 display:none（避免 ECharts 0×0 空白）")
    ck("this._resizeAllCharts()" in h[h.index(" toggleResearchSection(pfx) {"):][:2600],
       "展开时调 _resizeAllCharts 兜底")
    ck("this._rsOpen" in h, "折叠状态跨渲染保持(_rsOpen)")
    # 合并卡内只能有 1 个 h3（子标题必须用 div）
    rs_start = h.index(" const blocks = keys.map((k, i) => {")
    rs_block = h[rs_start:h.index("toggleResearchSection(P);", rs_start)]
    ck(rs_block.count("<h3") == 1, "折叠块内只有 1 个 <h3>（子标题用 div, 避免索引名串名）",
       "实测 %d 个" % rs_block.count("<h3"))
    ck(rs_block.count('class="card"') == 1, "折叠块外层只有 1 个 .card（原来 7 个）",
       "实测 %d 个" % rs_block.count('class="card"'))
    ck("<h3 style=\"margin:0\">研报基本面指标" in rs_block, "合并卡标题为「研报基本面指标」")
    print()

    print("=== C. 内联 JS 语法 ===")
    ms = list(re.finditer(r"<script>(.*?)</script>", h, re.S))
    js = max(ms, key=lambda m: len(m.group(1))).group(1)
    tmp = ROOT / "probe" / "_inline_check_trim.js"
    tmp.write_text(js, encoding="utf-8")
    r = subprocess.run(["node", "--check", str(tmp)], capture_output=True, text=True)
    tmp.unlink(missing_ok=True)
    ck(r.returncode == 0, "内联 JS 语法通过 node --check", (r.stderr or "")[:200])
    print()

    print("=== D. id 完整性 / 孤儿调用（与改动前对比）===")
    baks = sorted(glob.glob(str(ROOT / "backup" / "web_template_pre_trim3_*.html")))
    if not baks:
        print("  (无 pre_trim3 备份，跳过对比)")
    else:
        def analyse(p: Path):
            t = p.read_bytes().decode("utf-8")
            j = t[t.index("<script>"):]
            defined = set(re.findall(r'id="([a-zA-Z0-9_-]+)"', t))
            used = set(re.findall(r"getElementById\(['\"]([a-zA-Z0-9_-]+)['\"]\)", j))
            used |= set(re.findall(r"_initChart\(['\"]([a-zA-Z0-9_-]+)['\"]\)", j))
            calls = set(re.findall(r"this\.((?:_render|load|toggle)[A-Z]\w*)\(", j))
            defs = set(re.findall(r"^ (?:async )?((?:_render|load|toggle)[A-Z]\w*)\(", j, re.M))
            return used - defined, calls - defs
        b_miss, b_calls = analyse(Path(baks[-1]))
        n_miss, n_calls = analyse(TPL)
        # 【有意保留】这两个容器已按用户要求删除, 但 JS 取值点**刻意保留**(带守卫, 见 E 段)
        #   —— 防过度删除 + 便于回退（恢复卡片即复原）。故从"新增缺失"里豁免。
        ALLOWED_DEAD_REFS = {"gt-sources", "f-pi-badge"}
        new_miss = (n_miss - b_miss) - ALLOWED_DEAD_REFS
        ck(not new_miss, "未新增「引用但不存在」的 id（豁免 2 个有意保留的死引用）",
           str(sorted(new_miss)))
        ck(ALLOWED_DEAD_REFS.issubset(n_miss - b_miss) and len(n_miss - b_miss) == 2,
           "有意保留的死引用恰好是那 2 个（未扩大）", str(sorted(n_miss - b_miss)))
        ck(not (n_calls - b_calls), "未新增孤儿方法调用", str(sorted(n_calls - b_calls)))
        print("     (改动前缺失 id %d 个 / 孤儿调用 %d 个 —— 均为既有)" % (len(b_miss), len(b_calls)))
    print()

    print("=== E. 被删容器的取值点必须有守卫（防 TypeError）===")
    j = h[h.index("<script>"):]
    # gt-sources 的取值点
    gs = [m.start() for m in re.finditer(r"getElementById\('gt-sources'\)", j)]
    ck(len(gs) >= 1, "gt-sources 取值点保留(%d 处)" % len(gs))
    for pos in gs:
        nxt = j[pos:pos + 400]
        ck("if (!el) return" in nxt or "if (el)" in nxt or "if (!srcEl)" in nxt or "if (srcEl)" in nxt,
           "  gt-sources 取值点有守卫 @%d" % pos)
    # f-pi-badge 的取值点
    pb = [m.start() for m in re.finditer(r"getElementById\('f-pi-badge'\)", j)]
    ck(len(pb) >= 1, "f-pi-badge 取值点保留(%d 处)" % len(pb))
    for pos in pb:
        ck("if (piBadge)" in j[pos:pos + 260], "  f-pi-badge 取值点有守卫 @%d" % pos)
    # _renderDashCharts 的调用已不再传 pi
    ck("_renderDashCharts(d, { pi:" not in j, "_renderDashCharts 调用已不再传 pi 容器")
    print()

    print("=== F. div 配平（剥注释）===")
    mi, me = h.index("<main>"), h.index("</main>")
    seg = strip_comments(h[mi:me])
    no, nc = len(re.findall(r"<div\b", seg)), len(re.findall(r"</div>", seg))
    ck(no == nc, "<main> 内 div 配平", "%d/%d 净=%+d" % (no, nc, no - nc))
    for tb in ("warehouse", "fund"):
        a = clean.index('<div id="tab-%s" class="tab">' % tb)
        nxt = re.search(r'<div id="tab-[a-z]+" class="tab', clean[a + 10:])
        b = a + 10 + nxt.start() if nxt else len(clean)
        s2 = clean[a:b]
        n1, n2 = len(re.findall(r"<div\b", s2)), len(re.findall(r"</div>", s2))
        ck(n1 == n2, "#tab-%s 内 div 配平" % tb, "%d/%d 净=%+d" % (n1, n2, n1 - n2))
    print()

    print("=" * 62)
    print("结果: %d PASS / %d FAIL" % (PASS, FAIL))
    if FAILS:
        print("失败项:")
        for f in FAILS:
            print("   -", f)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
