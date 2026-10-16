# -*- coding: utf-8 -*-
"""三项结构性归位的**只读**验收（可反复重跑，不修改任何文件）。

A. 量化信号研判整卡 → 基本面量化；体系方向票的多空逻辑系 + 5 个扩展块 → 基本面量化
B. 多品种截面排行 → 总览页折叠模块（默认收起、展开才拉数）
C. 两处分数：技术面详情顶部（技术面口径）+ ① 三指标策略卡（基本面口径）

用法：./venv/Scripts/python.exe probe/_verify_relocate.py
"""
from __future__ import annotations

import glob
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "web_template.html"

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


def strip_c(s: str) -> str:
    return re.sub(r"<!--.*?-->", "", s, flags=re.S)


def span_of(clean: str, tab: str) -> str:
    """取 tab 容器自身范围（div 深度配平；用下一个 tab 容器不行 —— 注释会干扰）。"""
    a = clean.index('<div id="tab-%s" class="tab">' % tab)
    d = 0
    pat = re.compile(r"<div\b|</div>")
    k = a
    while k < len(clean):
        m = pat.search(clean, k)
        d += 1 if m.group(0) == "<div" else -1
        if d == 0:
            return clean[a:m.end()]
        k = m.end()
    raise AssertionError("未配平 tab-" + tab)


def main() -> int:
    h_raw = TPL.read_bytes().decode("utf-8")
    assert h_raw.count("\r\n") > 0, "web_template.html 应为 CRLF"
    h = h_raw.replace("\r\n", "\n")          # ★ 后续 index/切片按 LF 思维写
    js = h[h.index("<script>"):]                # JS 区（模板的 JS 在所有 tab 之后）
    clean = strip_c(h)
    q = span_of(clean, "quant")
    f = span_of(clean, "fund")

    # ── A. 迁移 ──────────────────────────────────────────────────
    print("=== A. 《期货多空逻辑》口径迁移 ===")
    ck("量化信号研判" not in q, "① 技术面页已无「量化信号研判」")
    ck('id="q-signal-body"' in f and 'id="q-signal-badge"' in f,
       "① 信号卡已在基本面页（id 未改）")
    ck('id="f-votes-body"' in f and 'id="f-verdict-card"' in f, "② 新增「多空逻辑体系研判」卡")
    ck("各体系方向票" in js, "② 技术面仍保留「各体系方向票」表（只是少了多空逻辑系行）")
    ck("多空逻辑系方向票" in js, "② 基本面 _renderFundVotes 有多空逻辑系方向票表")
    ck('id="f-strategy-score"' in f, "① 三指标策略卡已加信号计分行")
    # 技术面 _renderVerdict 不再有多空逻辑扩展块
    # 技术面视图 = _renderVerdict 方法体；基本面视图 = _renderFundVotes 方法体
    def method_body(region: str, name: str) -> str:
        i = region.index(" " + name + "(")
        d, st, k = 0, False, region.index("{", i)
        while k < len(region):
            ch = region[k]
            if ch == "{":
                d += 1
                st = True
            elif ch == "}":
                d -= 1
                if st and d == 0:
                    return region[i:k + 1]
            k += 1
        raise AssertionError("未配平 " + name)
    tech_v = method_body(js, "_renderVerdict")
    fund_v = method_body(js, "_renderFundVotes")
    for key in ("ex.basis_calculation", "ex.calendar_spread", "ex.cross_commodity_spread",
                "ex.warrant_basis", "ex.reversal_signals"):
        ck(key not in tech_v, "① 技术面 _renderVerdict 已无 %s" % key)
        ck(key in fund_v, "② 基本面 _renderFundVotes 已含 %s" % key)
    ck("startsWith('多空逻辑')" in tech_v, "① 技术面 votes 已按 book 前缀排除多空逻辑系")
    ck("startsWith('多空逻辑')" in fund_v, "② 基本面 votes 只取多空逻辑系")
    # 技术面仍保留墨菲/Carver 五块
    for key in ("ex.ma_crossover", "ex.volume_open_interest", "ex.trend_structure",
                "ex.shrinkage_ladder", "ex.estimate_reliability"):
        ck(key in h, "① 技术面保留扩展块 %s" % key)
    ck("_movedVotes" in js, "① votes 表用 _movedVotes 记录被迁走的票（用于提示）")
    ck(js.count("startsWith('多空逻辑')") == 3,
       "过滤条件共 3 处（技术面 _movedVotes + 技术面排除 + 基本面包含）",
       "实测 %d" % js.count("startsWith('多空逻辑')"))
    ck("已迁至「基本面量化 → 多空逻辑体系研判」" in h, "② 技术面留了迁出指向说明")
    print()

    # ── B. 截面排行折叠 ──────────────────────────────────────────
    print("=== B. 截面排行 → 总览折叠模块 ===")
    ov = clean[clean.index('<div id="q-overview-view">'):]
    ov = ov[:ov.index('<!-- ══════')] if "<!-- ══════" in ov[:12000] else ov[:12000]
    ck('id="q-rank-table"' in ov, "排行表已在总览页内")
    # ⚠ 不能查整个 tab-quant：排行卡的新家 #q-overview-view 也在 tab-quant 内。
    #    必须限定在**单品种详情视图** #q-detail-view 的范围内。
    dv = clean[clean.index('<div id="q-detail-view"'):]
    dv = dv[:dv.index('<!-- ══════')] if "<!-- ══════" in dv[:20000] else dv[:20000]
    ck("多品种量化截面排行" not in dv, "单品种详情视图内已无排行表",
       "（迁移注释在详情视图内说明去哪了，标题本身不该再出现）")
    ck('id="q-rank-table"' not in dv, "详情视图内已无 #q-rank-table")
    ck('id="q-rank-body" data-open="0"' in h, "折叠容器 data-open=0（默认收起）")
    ck('id="q-rank-body"' in h and 'max-height:0;overflow:hidden' in h, "折叠用 max-height")
    ck('id="q-rank-head"' in h and 'id="q-rank-caret"' in h, "折叠头 + 指示器已生成")
    ck(" toggleQovRank() {" in h, "toggleQovRank 已定义")
    i = h.index(" toggleQovRank() {")
    seg = h[i:i + 2600]
    ck("if (!this._qovRankLoaded)" in seg and "this.loadQuantRanking(0)" in seg,
       "★ 展开时才拉数（惰性加载）")
    ck("display:none" not in re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", seg, flags=re.S)),
       "折叠区代码未用 display:none")
    ck("this._qovRankOpen =" in h, "折叠状态有独立状态键")
    for cid in ("q-rank-metric", "q-rank-order", "q-rank-note", "q-rank-badge", "q-rank-table"):
        ck('id="%s"' % cid in h, "排行原 id 保留: %s" % cid)
    print()

    # ── C. 两处分数 ──────────────────────────────────────────────
    print("=== C. 两处分数 ===")
    ck('id="q-detail-score"' in h, "技术面详情顶部有计分徽标容器")
    ck(" _updateDetailScore(sym) {" in h, "_updateDetailScore 已定义")
    ck("this._updateDetailScore(sym);" in h, "loadQuant 里调用（切品种时刷新）")
    ck("this._updateDetailScore(code);" in h, "qOpenDetail 里调用（点行即显）")
    ck("与总览「截面排行」同源" in h, "标签注明与排行同口径（防误读成基本面）")
    ck("_qovData" in h[h.index(" _updateDetailScore(sym) {"):][:900],
       "分数取自已加载的总览数据（零额外请求）")
    ck("基本面信号计分" in h, "① 卡有基本面口径计分（措辞已区分）")
    ck("f-strategy-score" in h[h.index("async loadQuantSignals() {"):][:6000],
       "★ 信号计分由 loadQuantSignals 同一次请求写入（不额外拉）")
    print()

    # ── D. 接线 ──────────────────────────────────────────────────
    print("=== D. 接线 ===")
    ck("const sym = this._fundSym();" in h[h.index("async loadQuantSignals() {"):][:300],
       "★ loadQuantSignals 品种源 = #f-variety（跨页迁移最易漏的一处）")
    lq = h[h.index(" async loadQuant(force) {"):]
    lq = lq[:lq.index("\n },")]
    ck("this.loadQuantSignals();" not in re.sub(r"//[^\n]*", "", lq),
       "loadQuant 已摘掉 signals 调用（否则重复拉）")
    ck("this.loadQuantRanking(" not in re.sub(r"//[^\n]*", "", lq),
       "loadQuant 已摘掉 ranking 调用（否则给隐藏表格白拉）")
    ck("this._updateDetailScore(sym);" in lq, "loadQuant 已接分数刷新")
    lf = h[h.index(" async loadFund() {"):]
    lf = lf[:lf.index("\n },")]
    ck("this.loadQuantSignals();" in lf, "loadFund 已接 signals")
    ck("this.loadFundVotes();" in lf, "loadFund 已接 loadFundVotes")
    ck("_renderFundVotes(d)" in h, "_renderFundVotes 已定义")
    ck("async loadFundVotes() {" in h, "loadFundVotes 已定义")
    ck("/api/quant/verdict" in h, "复用同一 verdict 接口（后端零改动）")
    ck("_SEC_IDX_VER: '27'," in h, "_SEC_IDX_VER 26→27（卡片增删铁律）")
    ck("_SEC_IDX_VER: '26'," not in h, "旧版本号已无")
    print()

    # ── E. 结构配平 / 语法 / id 完整性 ───────────────────────────
    print("=== E. 结构与完整性 ===")
    mi, me = h.index("<main>"), h.index("</main>")
    seg = strip_c(h[mi:me])
    no, nc = len(re.findall(r"<div\b", seg)), len(re.findall(r"</div>", seg))
    ck(no == nc, "<main> 内 div 配平", "%d/%d 净=%+d" % (no, nc, no - nc))
    for tb in ("quant", "fund"):
        s2 = span_of(clean, tb)
        n1, n2 = len(re.findall(r"<div\b", s2)), len(re.findall(r"</div>", s2))
        ck(n1 == n2, "#tab-%s 内 div 配平" % tb, "%d/%d 净=%+d" % (n1, n2, n1 - n2))
    ms = list(re.finditer(r"<script>(.*?)</script>", h, re.S))
    js = max(ms, key=lambda m: len(m.group(1))).group(1)
    tmp = ROOT / "probe" / "_inline_relocate.js"
    tmp.write_text(js, encoding="utf-8")
    r = subprocess.run(["node", "--check", str(tmp)], capture_output=True, text=True)
    tmp.unlink(missing_ok=True)
    ck(r.returncode == 0, "内联 JS 语法通过 node --check", (r.stderr or "")[:200])

    baks = sorted(glob.glob(str(ROOT / "backup" / "web_template_pre_relocate_*.html")))
    if baks:
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
        ck(not (n_miss - b_miss), "未新增「引用但不存在」的 id", str(sorted(n_miss - b_miss)))
        ck(not (n_calls - b_calls), "未新增孤儿方法调用", str(sorted(n_calls - b_calls)))
        print("     (改动前缺失 id %d / 孤儿调用 %d —— 均为既有)" % (len(b_miss), len(b_calls)))
    print()

    print("=" * 62)
    print("结果: %d PASS / %d FAIL" % (PASS, FAIL))
    if FAILS:
        print("失败项:")
        for x in FAILS:
            print("   -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
