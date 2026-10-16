# -*- coding: utf-8 -*-
"""验收：tab 结构重组（2026-10-14 国君数据→数据仓库 / 仓单分析→基本面量化 / 情绪数据换位）

【为什么单写这个脚本】「整块并入」的静默失效点分散在 10 处以上（nav / 容器 / switchTab /
 Ctrl+N / 平铺名单 / 重复 id / 孤儿调用 / div 配平…），靠一两行 grep 查不全。
 本脚本与**改动前备份**逐项对比，专门判"是不是我引入的"。

★ 关键方法论（本次实测）：**div 配平与残留检查都必须先剥注释** ——
   注释里会写 `此处 <div id="tab-gtja">` 这类字面量，
   不剥注释会把"结构完全正确"误报成"少了 </div> / 容器没删干净"（本次首查即中招）。

用法: python probe/_verify_tab_restructure.py [备份文件]
"""
from __future__ import annotations

import glob
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "web_template.html"

PASS = 0
FAIL = 0


def ck(ok: bool, msg: str) -> None:
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    print("  %s %s" % ("PASS" if ok else "FAIL", msg))


def strip_comments(s: str) -> str:
    return re.sub(r"<!--.*?-->", "", s, flags=re.S)


def container_end(h: str, i: int):
    d, k = 0, i
    pat = re.compile(r"<div\b|</div>|<!--.*?-->", re.S)
    while k < len(h):
        m = pat.search(h, k)
        if not m:
            return None
        tok = m.group(0)
        if tok.startswith("<!--"):
            pass
        elif tok == "<div":
            d += 1
        else:
            d -= 1
            if d == 0:
                return m.end()
        k = m.end()
    return None


def span(h: str, tab: str):
    m = re.search(r'<div id="tab-%s" class="tab(?: active)?">' % tab, h)
    if not m:
        return None
    e = container_end(h, m.start())
    return (m.start(), e) if e else None


def analyse(h: str):
    js = h[h.index("<script>"):]
    defined = set(re.findall(r'id="([a-zA-Z0-9_-]+)"', h))
    used = set(re.findall(r"getElementById\(['\"]([a-zA-Z0-9_-]+)['\"]\)", js))
    used |= set(re.findall(r"_initChart\(['\"]([a-zA-Z0-9_-]+)['\"]\)", js))
    calls = set(re.findall(r"this\.((?:_render|load)[A-Z]\w*)\(", js))
    defs = set(re.findall(r"^ (?:async )?((?:_render|load)[A-Z]\w*)\(", js, re.M))
    # ★ 重复 id 必须**剥注释后**统计：迁移/说明注释里会照抄 `<div id="tab-gtja-tools">`
    #   这类字面量，不剥注释会把"结构完全正确"误报成"出现重复 id"（本次实测踩过）。
    ids = re.findall(r'id="([a-zA-Z0-9_-]+)"', strip_comments(h))
    dup = {i for i in ids if ids.count(i) > 1}
    return (used - defined), (calls - defs), dup


def main() -> int:
    cands = sorted(glob.glob(str(ROOT / "backup" / "__restructure_original_*.html")))
    if len(sys.argv) > 1:
        bak = Path(sys.argv[1])
    elif cands:
        bak = Path(cands[-1])
    else:
        print("✘ 找不到改动前备份 __restructure_original_*.html")
        return 2
    print("备份基线: %s\n" % bak.name)

    before = bak.read_text(encoding="utf-8")
    h = TPL.read_text(encoding="utf-8")
    clean = strip_comments(h)          # ★ 剥注释后的活代码

    print("=== A. nav ===")
    nav = h[h.index('<nav id="nav">', 90000): h.index('</nav>', h.index('<nav id="nav">', 90000))]
    order = re.findall(r'data-tab="([a-z]+)"', nav)
    want = ["run", "research", "quant", "fund", "sentiment", "foreign", "warehouse", "variety"]
    ck(order == want, "导航顺序 = %s（期望 %s）" % (order, want))
    ck('data-tab="gtja"' not in clean, "nav 无 gtja（剥注释）")
    ck('data-tab="warrant"' not in clean, "nav 无 warrant（剥注释）")
    # 情绪数据在基本面量化后、外文研报前
    i_f, i_s, i_fo = order.index("fund"), order.index("sentiment"), order.index("foreign")
    ck(i_f < i_s < i_fo, "情绪数据位于「基本面量化」后、「外文研报」前")

    print("\n=== B. 容器 ===")
    ck('<div id="tab-gtja" class="tab">' not in clean, "tab-gtja 容器已删（剥注释）")
    ck('<div id="tab-warrant" class="tab">' not in clean, "tab-warrant 容器已删（剥注释）")
    tabs = re.findall(r'<div id="tab-([a-z]+)" class="tab(?: active)?">', clean)
    ck(sorted(tabs) == sorted(["run", "research", "quant", "fund", "sentiment", "foreign",
                               "warehouse", "variety"]), "留下的 tab 容器 = %s" % tabs)

    print("\n=== C. 迁移落点 ===")
    wh = span(h, "warehouse")
    fu = span(h, "fund")
    ck(wh is not None, "warehouse 容器存在")
    ck(fu is not None, "fund 容器存在")
    # 卖方数据是 warehouse 第一块
    ck(h[wh[0]:wh[0] + 200].find('tab-gtja-tools') != -1,
       "卖方数据块位于 warehouse 容器首位")
    # 仓单是 fund 最后一块 —— 判据：wt 块的首尾容器都在 fund 内，且末容器贴近容器尾
    fund_seg = h[fu[0]:fu[1]]
    ck("wt-product" in fund_seg, "仓单块已并入 fund（控制条 wt-product 在容器内）")
    ck("wt-h-wh" in fund_seg, "仓单历史块已并入 fund（wt-h-wh 在容器内）")
    # wt-h-wh 是原 warrant 页最后一个容器；它之后应只剩闭合标签
    tail = fund_seg[fund_seg.index('id="wt-h-wh"'):]
    ck(len(tail) < 400, "仓单块位于 fund **末尾**（wt-h-wh 之后仅剩 %d 字符）" % len(tail))
    ck("卖方数据" in h, "「卖方数据」文案在位")
    ck(h.count("卖方数据(国君数据") >= 1, "标题已改名为「卖方数据」(保留来源说明)")

    print("\n=== D. 控制条（铁律：一个 tab 恰好 1 个 .page-tools）===")
    for tb in ("warehouse", "fund"):
        sp = span(h, tb)
        # 用剥注释后的同区间统计
        seg = strip_comments(h[sp[0]:sp[1]])
        n = seg.count('class="page-tools"')
        ck(n <= 1, "%s 的 .page-tools 数 = %d（应 ≤1）" % (tb, n))
    ck('class="page-tools"' not in strip_comments(h[span(h, "warehouse")[0]:span(h, "warehouse")[1]]),
       "并入方的控制条已降为页内卡（warehouse 无 page-tools）")

    print("\n=== E. id 与方法保留（harness 依赖）===")
    for k in ("gt-variety", "gt-indicators", "gt-chart", "gt-table", "gt-sources",
              "wt-product", "wt-scope", "wt-date", "tab-gtja-tools", "tab-warrant-tools"):
        ck(k in clean, "保留 %s" % k)
    for m in ("loadGtja", "loadGtjaOverview", "_renderGtjaSources", "_renderGtjaIndicators",
              "loadWarrant", "_renderWarrantRanks", "_renderWarrantHist"):
        ck(m in h, "保留方法 %s" % m)

    print("\n=== F. 接线 ===")
    ck("this.loadWarehouse(); this.loadGtja();" in h, "warehouse 分支已接 loadGtja")
    ck("this.loadFund(); this.loadWarrant();" in h, "fund 分支已接 loadWarrant")
    ck("if (t === 'gtja')" not in h, "旧 gtja 分支已删")
    ck("if (t === 'warrant')" not in h, "旧 warrant 分支已删")
    m = re.search(r"const tabs = \[([^\]]+)\]", h)
    arr = re.findall(r"'([a-z]+)'", m.group(1))
    ck(arr == want[:7], "Ctrl+N 数组 = %s（7 项）" % arr)
    ck("e.key <= '7'" in h, "Ctrl+N 上界 = '7'")

    print("\n=== G. 结构完整性（剥注释后 div 配平）===")
    mi, me = h.index("<main>"), h.index("</main>")
    c = strip_comments(h[mi:me])
    net = len(re.findall(r"<div\b", c)) - len(re.findall(r"</div>", c))
    ck(net == 0, "<main> 剥注释后 div 净 = %+d（应 0）" % net)
    for tb in ("warehouse", "fund", "research", "quant", "sentiment", "foreign", "run", "variety"):
        sp = span(h, tb)
        seg = strip_comments(h[sp[0]:sp[1]])
        n = len(re.findall(r"<div\b", seg)) - len(re.findall(r"</div>", seg))
        ck(n == 0, "tab-%s 剥注释后 div 净 = %+d" % (tb, n))

    print("\n=== H. 与改动前对比：不引入新缺陷 ===")
    b_miss, b_calls, b_dup = analyse(before)
    n_miss, n_calls, n_dup = analyse(h)
    ck(not (n_miss - b_miss), "未新增「缺失 id 引用」（新增 %s）" % (sorted(n_miss - b_miss) or "无"))
    ck(not (n_calls - b_calls), "未新增「无定义的 _/load 调用」（新增 %s）" % (sorted(n_calls - b_calls) or "无"))
    ck(not (n_dup - b_dup), "未新增「重复 id」（新增 %s）" % (sorted(n_dup - b_dup) or "无"))
    # 【信息项，不作判据】`wt-c2b` 是 JS 里两个**互斥分支**(if/else if)各自模板串里的 id，
    #   运行时只会命中一个 ⇒ 不是真重复。静态正则会把它算进来，属已知假阳性。
    print("  INFO 当前静态重复 id = %s（wt-c2b 属 JS 互斥分支的假阳性, 改动前即存在）"
          % (sorted(n_dup) or "无"))

    print("\n=== I. 迁移说明注释在位（可回退）===")
    ck("原「国君数据」页已整块并入" in h, "gtja 原位置留了迁移说明")
    ck("原「仓单分析」页已整块并入" in h, "warrant 原位置留了迁移说明")
    ck("备份" in h or "backup/" in h, "说明里写了备份位置")

    print("\n=== %d PASS / %d FAIL ===" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
