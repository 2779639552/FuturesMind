# -*- coding: utf-8 -*-
"""2026-10-14 三项结构性归位。**一次性补丁，禁止重跑。**

A. 《期货多空逻辑》口径从「技术面量化」归并到「基本面量化」：
   ① 「量化信号研判」整卡  ② 体系方向票里的**多空逻辑系**  ③ 5 个多空逻辑扩展块
B. 「多品种量化截面排行」：单品种详情 → 总览页**折叠模块**（默认收起、展开才拉数）
C. 两处分数：技术面详情顶部「多空计分」；① 三指标策略卡内「基本面信号计分」

【为什么要"降序 + 文本锚点"】
前几版我在改一处后按旧行号改下一处，导致索引漂移 + 写出一堆占位残渣。
本版：**先把全部行号只读收集好，再严格按行号从大到小执行**（高位增删不动低位锚点），
每步都断言命中，最后一次性拼回。要搬的片段用切片原样取走（不重打），
5 个扩展块用「锚点行 → 其后首个 stripped=='}' 的行」定界。

【设计要点】
· 后端零改动：`/api/quant/verdict` 是原子的（一次算完 5 本书），前端按 book 前缀 /
  扩展块键切片 ⇒ 不拆接口、不重复计算。
· 容器 id 一个不改（`tests/test_quant_overview.py` 与两个 JS harness 按 id 断言）。
· 折叠用 max-height（不用 display:none），并**镜像**而非重构 `toggleResearchSection`
  —— 后者的"初始化/写回/读回"三句已被护栏按具体语句形态断言，抽公共函数会让它变红。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "web_template.html"
MARK = 'id="f-verdict-card"'

# ══════════════════════════ 新增片段 ══════════════════════════
NOTE_SIGNAL = [
    " <!-- 【2026-10-14 迁出】此处原为「量化信号研判」卡(#q-signal-body/#q-signal-badge)。",
    " 迁出判据: 它的四个维度(库存+基差 / 反转资金 / 期限结构 / 利润)全部出自",
    " 《期货多空逻辑》(JerryMa) 三指标系统 ⇒ 基本面口径, 与「基本面量化」同源。",
    " 现位于 tab-fund(「① 三指标策略系统」之后); 容器 id 沿用 q- 前缀未改名。 -->",
]
NOTE_RANK = [
    " <!-- 【2026-10-14 迁出】此处原为「多品种量化截面排行」卡(#q-rank-table 等)。",
    " 迁出判据: 它是**跨品种**表(全池截面), 与「单品种详情」不同维度; 用户要求放到",
    " 总览页并折叠。现位于 #q-overview-view 末尾的折叠模块(默认收起, 展开才拉数)。",
    " 本品种自己的分数仍在**本页顶部**显示(#q-detail-score, 与排行表同口径)。 -->",
]
SIGNAL_CARD = [
    "",
    " <!-- ── ⑤ 量化信号研判 【2026-10-14 由「技术面量化」页迁入】 ──",
    " 迁入判据: 四个维度(库存+基差 / 反转资金 / 期限结构 / 利润)全部出自《期货多空逻辑》",
    " (JerryMa) —— 与上方「① 三指标策略系统」同源, 故与技术面分离。",
    " ⚠ 品种源必须取 #f-variety(loadQuantSignals 内已由 _quantSym 改为 _fundSym): 若仍读",
    "   #q-variety, 直接从基本面页进入时该下拉未初始化 ⇒ 首行 `if (!sym) return` ⇒ 整卡恒空",
    "   (跨页迁移最易漏的一处, 本项目 2026-10-13 迁库存族时踩过)。",
    " ⚠ 容器 id 沿用 q- 前缀**不重命名** —— tests/test_quant_overview.py 与",
    "   tests/js/quant_signals_profit_harness.mjs 按 id 断言。",
    " ⚠ 本卡与「① 三指标策略系统」的 #f-strategy-score 是**同一次请求的两个消费端**",
    "   (见 loadQuantSignals 末尾) ⇒ 两处数字必然一致, 不会各拉一次。 -->",
    " <div class=\"card\" >",
    " <h3>⑤ 量化信号研判 <span class=\"badge badge-info\" id=\"q-signal-badge\"></span></h3>",
    " <div id=\"q-signal-body\" style=\"font-size:0.8125rem;color:var(--text-secondary);line-height:1.9\">加载中...</div>",
    " </div>",
]
VERDICT_CARD = [
    "",
    " <!-- ── ⑥ 多空逻辑体系研判 【2026-10-14 由「技术面量化 → 方法论研判」迁入】 ──",
    " 迁入判据: 本卡内容全部出自《期货多空逻辑》(JerryMa) —— 该书的**方向票**",
    " (含 期限结构 / 库存周期 / 利润 三个变体) 与 5 个**扩展读数**(基差折盘面口径审计 /",
    " 跨期套利四逻辑 / 跨品种套利 / 虚实盘比与逼仓 / 行情反转三信号)。",
    " ⚠ 与技术面「方法论研判」是**同一接口的不同视图**(/api/quant/verdict): 接口原子",
    "   (一次算完 5 本书), 前端按 book 前缀与扩展块键切片 ⇒ 后端零改动、零重复计算。",
    " ⚠ 与 ④ 虚实盘比的关系: ④ 是 fund_module.py 的专卡(带经验档位 2.0/0.5); 本卡的",
    "   warrant_basis 是 quant_verdict 研判链路的读数 —— **同概念不同口径**, 并列供交叉验证。",
    " ⚠ 本卡只展示票与读数; 手数/止损等**仓位计算**仍在技术面「方法论研判」的固定读序里",
    "   (那部分依赖该卡的输入框, 不搬)。 -->",
    " <div class=\"card\" id=\"f-verdict-card\">",
    " <h3>⑥ 多空逻辑体系研判 <span class=\"badge badge-info\" id=\"f-votes-badge\"></span></h3>",
    " <div style=\"font-size:0.75rem;color:var(--text-muted);margin-bottom:10px;line-height:1.7\">",
    " 《期货多空逻辑》口径的方向票与读数（并列陈列，<b>不取平均、不混用</b>）。",
    " 技术面体系（鹿希武 / 墨菲 / 威科夫 / Carver）仍在「技术面量化 → 方法论研判」。</div>",
    " <div id=\"f-votes-body\" style=\"font-size:0.8125rem;color:var(--text-secondary);line-height:1.9\">加载中...</div>",
    " </div>",
]
SCORE_LINE = [
    " <div id=\"f-strategy-score\" style=\"font-size:0.8125rem;color:var(--text-secondary);margin-bottom:10px\">",
    " 基本面信号计分：—",
    " </div>",
]
DETAIL_SCORE = [
    "   <span id=\"q-detail-score\" style=\"font-size:0.8125rem;color:var(--text-secondary)\"></span>",
]
RANK_HEAD = [
    "  <!-- ── 多品种量化截面排行 【2026-10-14 由单品种详情迁入, 并折叠】 ──",
    "  迁入判据: 它是**跨品种**表(全池截面), 与「单品种详情」不同维度; 用户要求放总览页折叠。",
    "  ⚠ 折叠用 max-height(不用 display:none): 与「研报基本面指标」折叠块同一套机制, 避开",
    "    display:none 让子元素量不到尺寸的那类坑(本文件 ③ 段有教训记录)。",
    "  ⚠ 默认收起 + **展开时才拉数**(toggleQovRank 内惰性加载) —— 首拉十余秒。",
    "  ⚠ 容器 id(q-rank-*)一律不改: loadQuantRanking 按 id 取容器, 护栏也按 id 断言。 -->",
    "  <div class=\"card\" style=\"margin-top:12px\">",
    "   <div id=\"q-rank-head\" title=\"点击展开\" style=\"display:flex;align-items:center;justify-content:space-between;gap:8px;cursor:pointer;user-select:none\">",
    "    <h3 style=\"margin:0\">多品种量化截面排行 <span class=\"badge badge-info\" id=\"q-rank-badge\"></span></h3>",
    "    <span id=\"q-rank-caret\" style=\"font-size:0.8125rem;color:var(--text-muted);white-space:nowrap\">展开 ▾</span>",
    "   </div>",
    "   <div style=\"font-size:0.75rem;color:var(--text-muted);margin-top:4px;line-height:1.7\">",
    "    全池品种按所选维度的截面排序。默认「多空计分」为<b>技术面口径</b>（均线排列 / RSI / MACD / BOLL）。",
    "    单个品种自己的分数也在它的详情页顶部显示。</div>",
    "   <div id=\"q-rank-body\" data-open=\"0\" style=\"max-height:0;overflow:hidden;transition:max-height .28s ease\">",
]
RANK_TAIL = ["   </div>", "  </div>"]
EX_SLOT = "  //__EX_BLOCKS__"

NEW_JS = [
    "",
    " // 【功能】⑥ 多空逻辑体系研判: 只渲染《期货多空逻辑》口径的方向票 + 扩展读数",
    " // 【输入】品种取 #f-variety 【渲染】#f-votes-body / #f-votes-badge",
    " // 【后端】GET /api/quant/verdict?variety=(与技术面「方法论研判」**同一接口**)",
    " // 【为什么前端切片而不改后端】接口是原子的(一次算完 5 本书的全部读数), 拆接口会让",
    " //   同一份计算被执行两次; 前端按 book 前缀 / 扩展块键切片 ⇒ 零后端改动、零重复计算。",
    " async loadFundVotes() {",
    "  const sym = this._fundSym();",
    "  const body = document.getElementById('f-votes-body');",
    "  const badge = document.getElementById('f-votes-badge');",
    "  if (!body) return;",
    "  if (!sym) { body.innerHTML = this._emptyState('请先选择品种'); return; }",
    "  body.textContent = '加载中（需拉取 250 日线 + 基差 + 仓单，约 2~5 秒）...';",
    "  try {",
    "   const r = await fetch(`/api/quant/verdict?variety=${encodeURIComponent(sym)}`);",
    "   if (!r.ok) throw new Error(`HTTP ${r.status}`);",
    "   const d = await r.json();",
    "   if (!d.ok) { body.innerHTML = this._emptyState(`研判不可用：${d.error || '未知原因'}`); return; }",
    "   body.innerHTML = this._renderFundVotes(d);",
    "   if (badge) badge.textContent = `${d.variety} ${d.name || ''} · 多空逻辑口径`;",
    "  } catch (e) {",
    "   console.error('fund votes error:', e);",
    "   body.innerHTML = this._emptyState(`加载失败: ${e.message}`);",
    "  }",
    " },",
    "",
    " // 【功能】渲染多空逻辑系方向票 + 扩展读数(与技术面 _renderVerdict 共用同一 payload)",
    " // 【返回】HTML 串; 无内容时给**明确提示**(不留空白卡, 否则用户以为页面坏了)",
    " _renderFundVotes(d) {",
    "  const _stages = d.stages || {};",
    "  const md = s => this._mdLite(s);",
    "  const nz = v => (v === null || v === undefined || v === 'None' || v === '') ? '未识别' : v;",
    "  const parts = [];",
    "  // ① 方向票: 只取 book 以「多空逻辑」开头的行(含 期限结构/库存周期/利润 三个变体)",
    "  const votes = ((_stages.direction || {}).votes || [])",
    "   .filter(v => String(v.book || '').startsWith('多空逻辑'));",
    "  if (votes.length) {",
    "   const trs = votes.map(v => {",
    "    const b = v.bias || '';",
    "    const c = b.includes('做多') ? 'var(--red)' : (b.includes('做空') ? 'var(--green)' : 'var(--text-secondary)');",
    "    return `<tr style=\"border-bottom:1px solid var(--border)\">`",
    "     + `<td style=\"padding:5px 8px\"><b>${md(v.book)}</b></td>`",
    "     + `<td style=\"padding:5px 8px;color:${c};font-weight:600\">${md(b)}</td>`",
    "     + `<td style=\"padding:5px 8px;color:var(--text-muted);font-size:0.75rem\">${md(v.basis)}</td>`",
    "     + `<td style=\"padding:5px 8px\">${md(v.detail)}</td></tr>`;",
    "   }).join('');",
    "   parts.push(`<div style=\"margin-bottom:14px\">",
    "    <div style=\"font-weight:700;margin-bottom:4px\">多空逻辑系方向票（并列陈列，不取平均、不混用）</div>",
    "    <div class=\"table-scroll\" style=\"overflow-x:auto\"><table style=\"width:100%;border-collapse:collapse;font-size:0.8125rem\">",
    "    <thead><tr style=\"border-bottom:2px solid var(--border);color:var(--text-muted);font-size:0.75rem\">",
    "    <th style=\"padding:5px 8px;text-align:left\">体系</th><th style=\"padding:5px 8px;text-align:left\">方向票</th>",
    "    <th style=\"padding:5px 8px;text-align:left\">依据</th><th style=\"padding:5px 8px;text-align:left\">细节</th></tr></thead>",
    "    <tbody>${trs}</tbody></table></div>",
    "    <div style=\"font-size:0.75rem;color:var(--text-muted);margin-top:4px\">${md((_stages.direction || {}).conflict_note || '')}</div>",
    "   </div>`);",
    "  }",
    "  // ② 扩展读数: 5 个多空逻辑块(由技术面 _renderVerdict **整体搬迁**而来, 非重写)",
    "  const ex = _stages.extended || {};",
    "  const exBlocks = [];",
    EX_SLOT,
    "  if (exBlocks.length) {",
    "   parts.push(`<div>",
    "    <div style=\"font-weight:700;margin-bottom:4px\">扩展读数（<span style=\"color:var(--text-muted);font-weight:400\">不参与方向净票，只作读数与纪律提示</span>）</div>",
    "    <div style=\"font-size:0.8125rem;display:flex;flex-direction:column;gap:6px\">",
    "    ${exBlocks.map(b => `<div style=\"padding:6px 10px;border-left:3px solid var(--border);background:var(--bg-input);border-radius:6px\">${b}</div>`).join('')}",
    "    </div>",
    "   </div>`);",
    "  }",
    "  if (!parts.length) {",
    "   return '<div style=\"color:var(--text-muted)\">该品种暂无多空逻辑口径的方向票与扩展读数（库存 / 基差 / 利润 / 期限结构均无数据覆盖）。</div>';",
    "  }",
    "  return parts.join('');",
    " },",
    "",
    " // 【功能】总览页「多品种量化截面排行」折叠开关(默认收起; 首次展开才拉数)",
    " // 【为什么不重构 toggleResearchSection】后者「初始化 / 写回 / 读回」三句已被",
    " //   tests/test_page_tools_variety_state.py 按**具体语句形态**断言, 抽公共函数会把那三句",
    " //   挪走 ⇒ 护栏变红。此处镜像同一套 max-height 方案, 用独立状态键。",
    " toggleQovRank() {",
    "  const body = document.getElementById('q-rank-body');",
    "  const head = document.getElementById('q-rank-head');",
    "  const caret = document.getElementById('q-rank-caret');",
    "  if (!body) return;",
    "  if (body.dataset.open === '1') {",
    "   body.style.maxHeight = body.scrollHeight + 'px';",
    "   requestAnimationFrame(() => { body.style.maxHeight = '0px'; });",
    "   body.dataset.open = '0';",
    "   this._qovRankOpen = false;",
    "   if (caret) caret.textContent = '展开 ▾';",
    "   if (head) head.title = '点击展开';",
    "  } else {",
    "   body.style.maxHeight = body.scrollHeight + 'px';",
    "   body.dataset.open = '1';",
    "   this._qovRankOpen = true;",
    "   if (caret) caret.textContent = '收起 ▴';",
    "   if (head) head.title = '点击收起';",
    "   setTimeout(() => { if (body.dataset.open === '1') body.style.maxHeight = 'none'; }, 300);",
    "   if (!this._qovRankLoaded) { this._qovRankLoaded = true; this.loadQuantRanking(0); }",
    "  }",
    " },",
    "",
    " // 【功能】刷新单品种详情顶部的「多空计分」徽标(技术面口径, 与总览「截面排行」同源)",
    " // 【为什么读 _qovData 而不是再请求】总览每行已含 score(_overview_base 返回), 而详情是",
    " //   从总览点进来的 ⇒ 数据已在内存: 零额外请求, 且与总览表上的数字**必然一致**。",
    " //   名次按 score 降序(与排行表 score 维度的默认排序同口径)。",
    " // 【降级】总览尚未加载(_qovData 为 null)时留空, 不报错。",
    " _updateDetailScore(sym) {",
    "  const el = document.getElementById('q-detail-score');",
    "  if (!el) return;",
    "  const rows = ((this._qovData || {}).rows) || [];",
    "  const row = rows.find(r => r.variety === sym);",
    "  if (!row || row.score == null) { el.textContent = ''; return; }",
    "  const sorted = rows.filter(r => r.score != null).slice().sort((a, b) => b.score - a.score);",
    "  const rank = sorted.findIndex(r => r.variety === sym) + 1;",
    "  const c = row.score >= 2 ? 'var(--red)' : (row.score <= -2 ? 'var(--green)' : 'var(--text-secondary)');",
    "  el.innerHTML = `多空计分 <b style=\"color:${c}\">${row.score > 0 ? '+' : ''}${row.score}</b>`",
    "   + ` · ${this._escHtml(row.verdict || '—')}`",
    "   + (rank ? ` · 全池第 <b>${rank}</b>/${sorted.length}` : '')",
    "   + ` <span style=\"color:var(--text-muted)\">（技术面口径，与总览「截面排行」同源）</span>`;",
    " },",
]
FUND_CALLS = [
    " // 【2026-10-14 迁入】《期货多空逻辑》口径的两块: 量化信号研判 + 多空逻辑体系研判",
    " this.loadQuantSignals();",
    " this.loadFundVotes();",
]
SCORE_JS = [
    "  // 【2026-10-14】同一份 signals 也写入「① 三指标策略系统」卡的一行(基本面口径计分)。",
    "  //   一个 fetch 两个消费端 ⇒ 两处数字必然一致, 且不额外请求。",
    "  const _sc = document.getElementById('f-strategy-score');",
    "  if (_sc) {",
    "   const _cn = d.score >= 2 ? 'var(--red)' : (d.score <= -2 ? 'var(--green)' : 'var(--text-secondary)');",
    "   _sc.innerHTML = `基本面信号计分 <b style=\"color:${_cn};font-size:1rem\">${d.score > 0 ? '+' : ''}${d.score}</b>`",
    "    + ` · <span style=\"color:${_cn};font-weight:600\">${this._escHtml(d.bias || '—')}</span>`",
    "    + ` <span style=\"color:var(--text-muted)\">（库存+基差 / 反转 / 期限结构；利润为校验项不计分）</span>`;",
    "  }",
]


def main() -> int:
    raw = TPL.read_bytes()
    assert raw.count(b"\r\n") > 0, "web_template.html 应为 CRLF"
    txt = raw.decode("utf-8")
    if MARK in txt:
        print("★ 已应用过 —— 本脚本不幂等，**请勿重跑**。")
        print("  重做请先从 backup/web_template_pre_relocate_*.html 还原。")
        return 2
    print("md5(前) =", hashlib.md5(raw).hexdigest())
    L = txt.split("\r\n")
    LOG: list = []

    def find(key, nth=1, lo=0):
        hits = [i for i in range(lo, len(L)) if key in L[i]]
        assert len(hits) >= nth, "锚点 %r 命中 %d（需 %d, lo=%d）" % (key, len(hits), nth, lo)
        return hits[nth - 1]

    def ins(i, blk, tag):
        L[i:i] = blk
        LOG.append((tag, "+%d @%d" % (len(blk), i)))

    def rep(i, blk, tag):
        L[i:i + 1] = blk
        LOG.append((tag, "%d→%d @%d" % (1, len(blk), i)))

    def cut(a, b, tag):
        blk = L[a:b + 1]
        del L[a:b + 1]
        LOG.append((tag, "-%d @%d" % (len(blk), a)))
        return blk

    def block_end(i):
        return next(k for k in range(i + 1, len(L)) if L[k].strip() == "}")

    def method_end(defkey):
        s = find(defkey)
        d, st = 0, False
        for i in range(s, len(L)):
            for ch in L[i]:
                if ch == "{":
                    d += 1
                    st = True
                elif ch == "}":
                    d -= 1
            if st and d == 0:
                assert L[i].strip() in ("}", "},"), "配平可疑 %r" % L[i]
                return i
        raise AssertionError("未配平 " + defkey)

    def elem_close(openkey):
        i = find(openkey)
        d = 0
        for k in range(i, len(L)):
            d += len(re.findall(r"<div\b", L[k])) - len(re.findall(r"</div>", L[k]))
            if d == 0:
                return k
        raise AssertionError("未配平 " + openkey)

    # ═══ 先做"需要花括号配平定位"的收集（都在改之前）═══
    lf_end = method_end(" async loadFund() {")           # loadFund 结束行
    lqs_end = method_end(" async loadQuantSignals() {")   # loadQuantSignals 结束行
    lqs_sym = find("const sym = this._quantSym();", lo=find(" async loadQuantSignals() {"))
    ov_close = elem_close('id="q-overview-view"')
    lq_start = find(" async loadQuant(force) {")
    lq_sym = find("const sym = this._quantSym();", lo=lq_start)
    lq_sig = find(" this.loadQuantSignals();", lo=lq_start)
    lq_rank = find(" this.loadQuantRanking(force ? 1 : 0);", lo=lq_start)
    qod_title = find('q-detail-sym">单品种详情')
    r_start = find("<!-- ── ⑦ 多品种截面排行 ── -->")
    r_tbl = find('id="q-rank-table"')
    s_start = find("<!-- ── ⑤ 量化信号 ── -->")

    # ══════════ 按行号【降序】执行 ══════════
    ops = []

    ops.append((lf_end + 1, "NEW_JS", lambda i: ins(i, NEW_JS, "新增 4 个方法")))
    ops.append((lf_end, "FUND_CALLS", lambda i: ins(i, FUND_CALLS, "loadFund 增两处调用")))
    ops.append((find(" if (exBlocks.length) {"), "EX_NOTE",
                lambda i: ins(i, [" // 【2026-10-14 迁出】5 个多空逻辑系扩展读数(基差折盘面口径审计 / 跨期套利四逻辑 /",
                                  " // 跨品种套利 / 虚实盘比与逼仓 / 行情反转三信号)已整体迁至「基本面量化 →",
                                  " // 多空逻辑体系研判」。本页不再出现任何 (多空逻辑) 条目 —— 技术面只保留",
                                  " // 墨菲三块(均线/量仓/趋势结构)与 Carver 两块(收缩阶梯/估计可靠性)。"],
                          "_renderVerdict 留迁出说明")))
    ops.append((lqs_end + 1, "SCORE_JS", lambda i: ins(i, SCORE_JS, "loadQuantSignals 写 ① 卡计分")))
    ops.append((lqs_sym, "LQS_SYM", lambda i: rep(i, ["const sym = this._fundSym();"],
                                                  "loadQuantSignals 品种源→#f-variety")))
    ops.append((lq_rank, "LQ_RANK", lambda i: rep(i, [
        " // 【2026-10-14 迁出】截面排行已移至**总览页**的折叠模块(默认收起, 展开时才拉),",
        " //   故本页不再加载它 —— 否则会给一个隐藏表格白拉 21 品种 × 多源(首拉十余秒)。",
        " //   单品种自己的分数由 _updateDetailScore 从总览数据里直接取(零额外请求)。"],
        "loadQuant 摘掉 ranking 调用")))
    ops.append((lq_sig, "LQ_SIG", lambda i: rep(i, [
        " // 【2026-10-14 迁出】量化信号研判(库存+基差/反转/期限/利润)已迁至**基本面量化页**,",
        " //   见 loadFund 末尾的 this.loadQuantSignals() —— 本页不再加载。",
    ], "loadQuant 摘掉 signals 调用")))
    ops.append((lq_sym, "LQ_SCORE", lambda i: ins(i + 1, [
        " // 【2026-10-14】顺带刷新详情顶部的「多空计分」徽标(数据取自已加载的总览, 零额外请求)",
        " this._updateDetailScore(sym);"],
        "loadQuant 刷新详情分数")))
    ops.append((method_end(" qOpenDetail(code) {"), "QOD_SCORE", lambda i: ins(i, [
        "  // 【2026-10-14】立即写一次分数(不必等 loadQuant 的异步链走完)",
        "  this._updateDetailScore(code);"],
        "qOpenDetail 立即写分数")))
    ops.append((find(" _SEC_IDX_VER: '26',"), "IDX", lambda i: rep(i, [
        " // v27 (2026-10-14): ①「量化信号研判」卡迁出至基本面页; ② 单品种详情的",
        " // 「多品种量化截面排行」迁出至总览页折叠模块(该 tab 卡片数减少, 总览页新增折叠模块)。",
        " // 按铁律 +1 触发旧浏览器重建索引(否则残留点不开的僵尸索引项)。",
        " _SEC_IDX_VER: '27',"], "_SEC_IDX_VER 26→27")))
    ops.append((find('<div id="tab-warrant-tools">'), "VCARD",
                lambda i: ins(i, VERDICT_CARD, "多空逻辑体系研判卡 → tab-fund")))
    ops.append((find("<!-- ── ② 估值 + 驱动二维四象 ── -->"), "SCARD",
                lambda i: ins(i, SIGNAL_CARD, "量化信号卡 → tab-fund")))
    ops.append((find("<h3>① 三指标策略系统"), "SCORELINE",
                lambda i: ins(i + 1, SCORE_LINE, "① 卡加信号计分行")))
    def _replace_signal_card(i):
        cut(i, i + 4, "取走 量化信号研判卡(tab-quant)")
        ins(i, NOTE_SIGNAL, "原位留信号迁出说明")
    ops.append((s_start, "NOTE_SIGNAL", _replace_signal_card))
    ops.append((find("K 线 / 技术指标 / 量化信号 / 策略回测 / 截面排行 / 方法论研判"), "HINT",
                lambda i: rep(i, [
                    "   <span id=\"q-detail-hint\" style=\"font-size:0.75rem;color:var(--text-muted)\">"
                    "K 线 / 技术指标 / 策略回测 / 方法论研判（技术面体系） / 数据计算 · "
                    "截面排行已移至上方的折叠模块</span>"], "详情提示文案同步")))
    ops.append((qod_title, "DETAIL_SCORE", lambda i: ins(i + 1, DETAIL_SCORE, "详情顶部加计分徽标")))

    # 排序：行号降序（同号时"先插入后替换"由调用顺序决定 —— 用稳定排序保持登记顺序的反序）
    # 说明：登记顺序已按从大到小人工排列；这里只做断言校验。
    nums = [o[0] for o in ops]
    for a, b in zip(nums, nums[1:]):
        assert a >= b, "ops 未按降序排列: %d 之后是 %d" % (a, b)

    for idx, _tag, fn in ops:
        fn(idx)

    # ── 特殊：5 个扩展块（含删除，须在 EX_NOTE 之后、且按降序）──
    ex_blocks: list = []
    for anchor in (" if (ex.reversal_signals) {", " if (ex.warrant_basis) {",
                   " if (ex.cross_commodity_spread) {", " if (ex.calendar_spread) {",
                   " if (ex.basis_calculation) {"):
        i = find(anchor)
        ex_blocks = cut(i, block_end(i), "取走扩展块 " + anchor.strip()[:28]) + ex_blocks
    # 嵌入 _renderFundVotes 的占位符
    slot = find(EX_SLOT)
    rep(slot, ex_blocks, "把 5 个扩展块嵌入 _renderFundVotes")

    # ── 特殊：排行卡搬到总览（切片 + 重组）──
    r_start = find("<!-- ── ⑦ 多品种截面排行 ── -->")
    r_tbl = find('id="q-rank-table"')
    rel_tbl = r_tbl - r_start                     # ★ 先取相对下标（切走后绝对下标失效）
    assert L[r_tbl + 1].strip() == "</div>", repr(L[r_tbl + 1])
    card = cut(r_start, r_tbl + 1, "取走 截面排行卡(tab-quant)")
    assert "多品种量化截面排行" in card[2], card[2]
    control = card[3:rel_tbl]                     # 控制区（含收尾 </div>，不含 h3）
    table_line = card[rel_tbl]                    # <div ... id="q-rank-table"></div>
    assert any("q-rank-metric" in x for x in control), "控制区提取失败"
    assert any("q-rank-order" in x for x in control)
    assert any("q-rank-note" in x for x in control)
    assert "q-rank-table" in table_line, table_line
    ins(r_start, NOTE_RANK, "原位留排行迁出说明")
    ov_close = elem_close('id="q-overview-view"')   # 重新定位（前面的增删会移动它）
    ins(ov_close, RANK_HEAD + control + [table_line] + RANK_TAIL,
        "截面排行 → 总览折叠模块")

    # ── 写盘 ──
    out = "\r\n".join(L)
    TPL.write_bytes(out.encode("utf-8"))
    raw2 = TPL.read_bytes()
    assert raw2.count(b"\r\n") > 0 and (raw2.count(b"\n") - raw2.count(b"\r\n")) == 0, "EOL 被破坏"

    print()
    print("=== 改动明细（按执行顺序）===")
    for tag, det in LOG:
        print("   %-40s %s" % (tag, det))
    print()
    print("md5(后) =", hashlib.md5(raw2).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
