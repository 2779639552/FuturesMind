# -*- coding: utf-8 -*-
"""技术面量化页改造：加「全品种总览 → 点进单品种详情」双视图（2026-10-14）。

============================== 用户原话 ==============================
「技术面量化的页面你直接用这个，得先有个所有品种的总览，然后点进去可以看单品种的详细情况。」

============================== 做法 ==============================
**不动原有 7 个模块的内部实现**，只在外面加一层视图切换：
  #tab-quant
    ├─ #q-overview-view   ← 新增：全品种总览（标题+统计+筛选 pill+表格）
    └─ #q-detail-view     ← 新增外壳，把**原有** page-tools + 7 张卡整块包进去
         ├─ 详情头（← 返回总览 + 品种名）
         ├─ #tab-quant-tools（原样）
         └─ K线/技术指标/信号/回测/截面排行/方法论/计算面板（原样）
进入 tab 默认落在总览；点表格行 → `qOpenDetail(code)` 切到详情并载入该品种；
详情顶部「← 返回总览」切回。**复用而非重写**是本节的全部要点。

============================== 三个必须处理的机制 ==============================
1. `_loadQuantMeta()` 有 `if (sel.options.length) return` 守卫，且**填充后会把下拉值
   设成"默认品种"** ⇒ 直接 `sel.value = code` 会被它覆盖。故引入 `_qovPendingSym`，
   让 meta 首次填充时**按我们的意图选**（这是"点行没反应/总是加载别的品种"的根因）。
2. 未登录/接口失败时 `#q-variety` 可能 0 个 option ⇒ 之后 `sel.value = code` 被浏览器
   **静默清空** ⇒ `_quantSym()` 空 ⇒ 各卡首行 return、页面永远"加载中"（skill 坑 1f）。
   ⇒ `qOpenDetail` 必须先确保该 option 存在。
3. 总览是**两阶段渐进**（后端日线层先出、多周期后补），接口给 `ready/total/done`
   ⇒ `done=false` 时每 3s 轮询，切进详情要**停掉轮询**（省请求），返回总览再续。

============================== 纪律 ==============================
· `web_template.html` 是 **CRLF**（15272 个 CRLF、0 裸 LF）：内部按 `\\n` 处理、写回还原 `\\r\\n`。
· 批量改动写成脚本 + **锚点先验命中数**，任一不为 1 即中止且不写文件。
· 插 `</div>` 前按 **div 深度配平**定位 tab-quant 结束边界（不能 find('</div>')）。
· 全局折叠已关（`_SEC_FLAT_TABS_ALL: true`）⇒ **无需** `_SEC_IDX_VER` +1。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "web_template.html"

TAB_OPEN = '<div id="tab-quant" class="tab">'
QUANT_LOAD = " async loadQuant(force) {"
SWITCH_LINE = " if (t === 'quant') this.loadQuant(); // 量化分析(2026-09-28 新增; 2026-09-29 并入「数据看板」全模块 → K线/关联分析/板块总览)"

# ── ① 总览视图（插在 tab-quant 开头）──────────────────────────────────────
OVERVIEW_HTML = '''<div id="tab-quant" class="tab">
 <!-- ════════════════════════════════════════════════════════════════════════
      总览视图（2026-10-14 新增）：**先看全部品种，再点进单品种**
      · 数据来自 GET /api/quant/overview（两阶段渐进：日线层先出、多周期后补）
      · 与下方 #q-detail-view 互斥显示（见 App.qOpenDetail / App.qBackOverview）
      · 全站折叠引擎已全局关闭（_SEC_FLAT_TABS_ALL）⇒ 本视图不参与小模块索引
      ════════════════════════════════════════════════════════════════════════ -->
 <div id="q-overview-view">
  <!-- 标题 + 统计 + 筛选 -->
  <div class="card" style="margin-bottom:12px">
   <div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap">
    <h3 style="margin:0">全品种技术面总览</h3>
    <span id="q-ov-updated" style="font-size:0.75rem;color:var(--text-muted)"></span>
    <span id="q-ov-progress" style="font-size:0.75rem;color:var(--text-muted);margin-left:auto"></span>
    <button class="btn btn-sm btn-outline" onclick="App.loadQuantOverview(1)">重新计算</button>
   </div>
   <div id="q-ov-stats" style="display:flex;gap:16px;flex-wrap:wrap;margin-top:10px;font-size:0.8125rem"></div>
   <div style="display:flex;gap:14px;flex-wrap:wrap;align-items:center;margin-top:12px">
    <input id="q-ov-search" type="text" placeholder="搜索代码 / 名称…" oninput="App._renderQuantOverview()"
      style="flex:0 0 170px;padding:6px 10px;font-size:0.8125rem;background:var(--bg-input);color:var(--text);border:1px solid var(--border);border-radius:8px">
    <div id="q-ov-pools" style="display:flex;gap:6px;flex-wrap:wrap"></div>
    <div id="q-ov-dirs" style="display:flex;gap:6px;flex-wrap:wrap"></div>
    <div id="q-ov-res" style="display:flex;gap:6px;flex-wrap:wrap"></div>
   </div>
  </div>
  <!-- 表格 -->
  <div class="card">
   <div style="overflow:auto;max-height:calc(100vh - 300px)">
    <table id="q-ov-table" style="width:100%;border-collapse:collapse;font-size:0.8125rem;white-space:nowrap">
     <thead>
      <tr style="position:sticky;top:0;z-index:2;background:var(--bg-card);border-bottom:1px solid var(--border)">
       <th style="text-align:left;padding:8px">品种</th>
       <th style="text-align:left;padding:8px">板块</th>
       <th style="text-align:right;padding:8px">最新价</th>
       <th style="text-align:right;padding:8px">涨跌幅</th>
       <th style="text-align:center;padding:8px">日线</th>
       <th style="text-align:center;padding:8px">60分</th>
       <th style="text-align:center;padding:8px">15分</th>
       <th style="text-align:center;padding:8px">5分</th>
       <th style="text-align:center;padding:8px">状态</th>
       <th style="text-align:center;padding:8px">共振</th>
       <th style="text-align:center;padding:8px">结论</th>
       <th style="text-align:center;padding:8px">置信度</th>
       <th style="text-align:center;padding:8px">流动性</th>
       <th style="text-align:left;padding:8px">关键信号</th>
      </tr>
     </thead>
     <tbody id="q-ov-rows"><tr><td colspan="14" style="padding:18px;color:var(--text-muted)">正在计算全品种技术面…（首次约 1~2 分钟，边算边填；之后走 5 分钟缓存秒开）</td></tr></tbody>
    </table>
   </div>
   <div style="font-size:0.75rem;color:var(--text-muted);margin-top:8px;line-height:1.7">
    点任意一行进入该品种详情 ·
    多周期为「日线 / 60分 / 15分 / 5分」的方向读数（三票制 EMA 判向，ADX&lt;20 记为「震」）·
    共振 = 与主导方向一致的周期数 ·
    置信度 = 周期共振 60 分 + 日线技术分 40 分 ·
    流动性为**池内持仓量相对分档**，仅用于同表比较，**不构成交易信号**
   </div>
  </div>
 </div>
'''

# ── ② 详情视图外壳（承接原有全部内容）────────────────────────────────────
DETAIL_OPEN = ''' <!-- ════════════════════════════════════════════════════════════════════════
      单品种详情视图（2026-10-14 新增外壳）：内含**原有**7 个模块，一字未改。
      进入方式：总览表格点行 → App.qOpenDetail(code)；返回：顶部「← 返回总览」。
      默认 display:none —— 进入 tab 时先看总览。
      ════════════════════════════════════════════════════════════════════════ -->
 <div id="q-detail-view" style="display:none">
  <div class="card" style="margin-bottom:12px;display:flex;align-items:center;gap:12px;flex-wrap:wrap">
   <button class="btn btn-sm btn-outline" onclick="App.qBackOverview()">← 返回总览</button>
   <h3 style="margin:0" id="q-detail-sym">单品种详情</h3>
   <span id="q-detail-hint" style="font-size:0.75rem;color:var(--text-muted)">K 线 / 技术指标 / 量化信号 / 策略回测 / 截面排行 / 方法论研判 / 数据计算</span>
  </div>
'''

DETAIL_CLOSE = ''' </div>
</div>'''

# ── ③ JS 方法（插在 async loadQuant 之前）───────────────────────────────
JS_METHODS = ''' // ═══════════════════════════════════════════════════════════════════════════
 // ⑭ 全品种技术面总览（2026-10-14 新增）
 // 设计目标（用户原话）：「得先有个所有品种的总览，然后点进去可以看单品种的详细情况」
 // 【与详情的关系】本视图与 #q-detail-view（内含原有 7 张卡）互斥显示：
 //   进入 tab 默认落在总览；点行 → qOpenDetail(code) 切详情并载入该品种。
 // 【渐进渲染】后端两阶段算（日线层 → 多周期），21 品种全量实测约 2 分钟。
 //   接口给 ready/total/done：done=false 时每 3s 轮询，表格边算边填；
 //   首屏 0 秒即有响应（只是还没有行），用户看到的是进度而不是白屏。
 // ═══════════════════════════════════════════════════════════════════════════
 _qovData: null,
 _qovFilter: { pool: '全部品种', dir: '全部', res: '全部' },
 _qovPollTimer: null,
 _qovPendingSym: null,   // 【变量】"点行后要选中的品种"，交给 _loadQuantMeta 首次填充时采用

 // 【功能】拉取并渲染全品种总览；未算完则自我续订轮询
 // 【输入】force=1 强制后端重算（会丢弃缓存）
 async loadQuantOverview(force) {
  const tbody = document.getElementById('q-ov-rows');
  if (!tbody) return;
  if (force) this._qovFilter = { pool: '全部品种', dir: '全部', res: '全部' };
  let d = null;
  try {
   const r = await fetch('/api/quant/overview' + (force ? '?refresh=1' : ''));
   d = await r.json();
  } catch (e) {
   tbody.innerHTML = '<tr><td colspan="14" style="padding:16px;color:var(--red)">总览加载失败：'
    + this._escHtml(String(e)) + '</td></tr>';
   return;
  }
  if (!d || !d.ok) {
   tbody.innerHTML = '<tr><td colspan="14" style="padding:16px;color:var(--red)">总览接口异常'
    + (d && d.err ? '：' + this._escHtml(d.err) : '') + '</td></tr>';
   return;
  }
  this._qovData = d;
  this._renderQuantOverview();
  if (this._qovPollTimer) { clearTimeout(this._qovPollTimer); this._qovPollTimer = null; }
  if (!d.done) {
   this._qovPollTimer = setTimeout(() => this.loadQuantOverview(), 3000);
  }
 },

 // 【功能】停掉总览轮询（切进详情时调用，避免后台空转请求）
 _stopQuantOverview() {
  if (this._qovPollTimer) { clearTimeout(this._qovPollTimer); this._qovPollTimer = null; }
 },

 // 【功能】按当前 _qovData + 筛选条件渲染整张表（可反复调用：筛选变更、轮询刷新）
 // 【为什么要能反复调】轮询每 3s 会重渲染一次；筛选 pill 只在首次构建（用 dataset.built
 //   守卫），否则每 3s 重建一次会让正在点的按钮"闪掉"。
 _renderQuantOverview() {
  const d = this._qovData;
  const tbody = document.getElementById('q-ov-rows');
  if (!d || !tbody) return;
  const all = d.rows || [];

  // 统计按**全量**算（不随筛选变化，否则数字会随筛选乱跳）
  const st = document.getElementById('q-ov-stats');
  if (st) {
   const cnt = s => all.filter(r => r.state === s).length;
   const reso = all.filter(r => (r.resonance || 0) >= 3).length;
   st.innerHTML = [
    ['品种', all.length, 'var(--text)'],
    ['偏多', cnt('偏多'), 'var(--red)'],
    ['偏空', cnt('偏空'), 'var(--green)'],
    ['震荡', cnt('震荡'), 'var(--text-secondary)'],
    ['多周期共振(≥3)', reso, 'var(--amber)'],
   ].map(([k, v, c]) =>
    '<span style="color:var(--text-secondary)">' + k
    + ' <b style="color:' + c + ';font-size:1rem">' + v + '</b></span>').join('');
  }
  const up = document.getElementById('q-ov-updated');
  if (up) {
   const dates = all.map(r => r.date).filter(Boolean).sort();
   up.textContent = dates.length ? ('数据截至 ' + dates[dates.length - 1]) : '';
  }
  const pg = document.getElementById('q-ov-progress');
  if (pg) {
   pg.textContent = d.done
    ? ('计算完成 · ' + all.length + ' 个品种 · ' + (d.cached ? '缓存' : '实时'))
    : ('计算中 ' + d.ready + '/' + d.total + ' 个品种…');
   pg.style.color = d.done ? 'var(--text-muted)' : 'var(--amber)';
  }

  this._qovBuildPills('q-ov-pools', d.pools || ['全部品种'], 'pool', v => v);
  this._qovBuildPills('q-ov-dirs', ['全部', '偏多', '偏空', '震荡'], 'dir', v => v);
  this._qovBuildPills('q-ov-res', ['全部', '共振≥3'], 'res', v => v);

  const kwEl = document.getElementById('q-ov-search');
  const kw = (kwEl ? kwEl.value : '').trim().toUpperCase();
  const f = this._qovFilter;
  const rows = all.filter(r => {
   if (f.pool !== '全部品种' && r.sector !== f.pool) return false;
   if (f.dir !== '全部' && r.state !== f.dir) return false;
   if (f.res === '共振≥3' && !((r.resonance || 0) >= 3)) return false;
   if (kw && !(((r.variety || '') + (r.name || '')).toUpperCase().includes(kw))) return false;
   return true;
  });

  tbody.innerHTML = rows.map(r => this._qovRowHtml(r)).join('')
   || '<tr><td colspan="14" style="padding:16px;color:var(--text-muted)">没有符合条件的品种</td></tr>';
 },

 // 【功能】构建一组筛选 pill（只在首次构建，后续只更新选中态 —— 防轮询把点击闪掉）
 _qovBuildPills(containerId, opts, key, labelFn) {
  const box = document.getElementById(containerId);
  if (!box) return;
  if (!box.dataset.built) {
   box.innerHTML = opts.map(o =>
    '<button class="btn btn-sm btn-outline" data-val="' + this._escHtml(o) + '">'
    + this._escHtml(labelFn(o)) + '</button>').join('');
   box.dataset.built = '1';
   box.querySelectorAll('button').forEach(b => {
    b.onclick = () => { this._qovFilter[key] = b.dataset.val; this._renderQuantOverview(); };
   });
  }
  const cur = this._qovFilter[key];
  box.querySelectorAll('button').forEach(b => {
   const on = b.dataset.val === cur;
   b.style.background = on ? 'var(--brand, #2997ff)' : 'transparent';
   b.style.color = on ? '#fff' : 'var(--text-secondary)';
   b.style.borderColor = on ? 'var(--brand, #2997ff)' : 'var(--border)';
  });
 },

 // 【功能】单行 HTML（点整行进入详情）
 _qovRowHtml(r) {
  const e = s => this._escHtml(s == null ? '' : String(s));
  const dirCell = p => {
   const v = (r.periods || {})[p];
   if (!v || !v.bars) return '<span style="color:var(--text-muted)">…</span>';
   const col = v.dir === 'up' ? 'var(--red)' : (v.dir === 'down' ? 'var(--green)' : 'var(--text-muted)');
   const tip = '趋势强度 ' + (v.strength > 0 ? '+' : '') + v.strength
    + ' · ADX ' + (v.adx == null ? '—' : Math.round(v.adx)) + ' · ' + v.bars + ' 根';
   return '<span title="' + e(tip) + '" style="color:' + col + ';font-weight:600">●' + e(v.label) + '</span>';
  };
  const chg = r.change_pct;
  const chgCol = chg == null ? 'var(--text-muted)' : (chg >= 0 ? 'var(--red)' : 'var(--green)');
  const vCol = r.verdict === '多' ? 'var(--red)' : (r.verdict === '空' ? 'var(--green)' : 'var(--text-muted)');
  const sCol = r.state === '偏多' ? 'var(--red)' : (r.state === '偏空' ? 'var(--green)' : 'var(--text-secondary)');
  const conf = r.confidence || 0;
  const liq = r.liquidity || '—';
  const td = 'padding:7px 8px;border-bottom:1px solid var(--border-light)';
  return '<tr onclick="App.qOpenDetail(\\'' + e(r.variety) + '\\')" style="cursor:pointer">'
   + '<td style="' + td + '"><b>' + e(r.variety) + '</b> <span style="color:var(--text-secondary)">' + e(r.name) + '</span></td>'
   + '<td style="' + td + ';color:var(--text-secondary)">' + e(r.sector) + '</td>'
   + '<td style="' + td + ';text-align:right">' + (r.latest == null ? '—' : e(r.latest)) + '</td>'
   + '<td style="' + td + ';text-align:right;color:' + chgCol + '">'
   + (chg == null ? '—' : ((chg > 0 ? '+' : '') + chg + '%')) + '</td>'
   + '<td style="' + td + ';text-align:center">' + dirCell('day') + '</td>'
   + '<td style="' + td + ';text-align:center">' + dirCell('60') + '</td>'
   + '<td style="' + td + ';text-align:center">' + dirCell('15') + '</td>'
   + '<td style="' + td + ';text-align:center">' + dirCell('5') + '</td>'
   + '<td style="' + td + ';text-align:center;color:' + sCol + '">' + e(r.state) + '</td>'
   + '<td style="' + td + ';text-align:center">' + (r.resonance || 0) + '/' + (r.period_total || 4) + '</td>'
   + '<td style="' + td + ';text-align:center;color:' + vCol + ';font-weight:600">' + e(r.verdict) + '</td>'
   + '<td style="' + td + ';text-align:center"><span style="display:inline-flex;align-items:center;gap:6px">'
   + '<span style="display:inline-block;width:54px;height:6px;background:var(--bg-tile-nested,#2c2c2e);'
   + 'border-radius:3px;overflow:hidden"><span style="display:block;height:100%;width:' + conf
   + '%;background:' + vCol + '"></span></span><span>' + conf + '</span></span></td>'
   + '<td style="' + td + ';text-align:center;color:var(--text-secondary)">' + e(liq) + '</td>'
   + '<td style="' + td + ';white-space:normal;max-width:330px;min-width:200px;color:var(--text-secondary)">'
   + e(r.key_signal) + '</td></tr>';
 },

 // 【功能】点总览行 → 切到单品种详情并载入该品种
 // 【关键】#q-variety 的填充有两条路（见 _loadQuantMeta 的守卫），必须分别处理：
 //   · 已初始化过 → 直接设值（并确保 option 存在，否则会被浏览器静默清空 → 各卡不渲染）
 //   · 未初始化   → 把选中意图交给 _qovPendingSym，让 meta 首次填充时按它选
 qOpenDetail(code) {
  const ov = document.getElementById('q-overview-view');
  const dv = document.getElementById('q-detail-view');
  const t = document.getElementById('q-detail-sym');
  if (t) {
   const r = ((this._qovData || {}).rows || []).find(x => x.variety === code) || {};
   t.textContent = code + (r.name ? ' · ' + r.name : '') + ' 单品种详情';
  }
  if (ov) ov.style.display = 'none';
  if (dv) dv.style.display = '';
  this._stopQuantOverview();

  const sel = document.getElementById('q-variety');
  if (sel && sel.options.length) {
   if (!Array.from(sel.options).some(o => o.value === code)) {
    const o = document.createElement('option');
    o.value = code; o.textContent = code;
    sel.appendChild(o);
   }
   sel.value = code;
  } else {
   this._qovPendingSym = code;
  }
  this.loadQuant();
  window.scrollTo({ top: 0, behavior: 'smooth' });
 },

 // 【功能】详情页「← 返回总览」
 qBackOverview() {
  const ov = document.getElementById('q-overview-view');
  const dv = document.getElementById('q-detail-view');
  if (dv) dv.style.display = 'none';
  if (ov) ov.style.display = '';
  const d = this._qovData;
  if (!d || !d.done) this.loadQuantOverview();   // 后台还在算 → 接着轮询
  window.scrollTo({ top: 0, behavior: 'smooth' });
 },

'''

# _loadQuantMeta 的默认选中：让"点行意图"优先
META_OLD = ''' // 默认选中与其它页一致的品种(优先有情绪数据的第一个, 与 _loadVarieties 同策略)
 const pref = this._varieties && this._varieties.find(v => (v.sentiment_posts || 0) > 0);
 sel.value = (pref && (d.varieties || []).some(x => x.code === pref.code))
 ? pref.code : (d.varieties?.[0]?.code || '');'''
META_NEW = ''' // 默认选中与其它页一致的品种(优先有情绪数据的第一个, 与 _loadVarieties 同策略)
 const pref = this._varieties && this._varieties.find(v => (v.sentiment_posts || 0) > 0);
 // 【2026-10-14】总览点行进来的品种优先：本函数有 `options.length` 守卫 ⇒ 只会跑一次，
 //   若仍按默认值选，用户点的那个品种会被**静默替换成别的**（点 RB 却加载了 BR）。
 const want = this._qovPendingSym;
 const valid = code => (d.varieties || []).some(x => x.code === code);
 this._qovPendingSym = null;
 sel.value = (want && valid(want)) ? want
 : ((pref && valid(pref.code)) ? pref.code : (d.varieties?.[0]?.code || ''));'''

SWITCH_NEW = '''    // 【2026-10-14 改造】技术面量化页改为「全品种总览 → 点进单品种详情」两视图：
    //   默认落在总览（loadQuantOverview）；若用户上次停在该品种详情（#q-overview-view
    //   被隐藏），则维持详情、直接载入该品种 —— 切走再切回不该丢失上下文。
 if (t === 'quant') {
 const ov = document.getElementById('q-overview-view');
 if (ov && ov.style.display === 'none') this.loadQuant();
 else this.loadQuantOverview();
 }'''


def find_tab_end(h: str, start: int) -> int:
    """按 div 深度配平找 tab-quant 的结束位置（返回 `</div>` 之后的下标）。"""
    depth = 0
    i = start
    n = len(h)
    pat = re.compile(r"<div\b|</div>")
    while i < n:
        m = pat.search(h, i)
        if not m:
            raise RuntimeError("未找到配平的结束标签")
        if m.group(0) == "<div":
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return m.end()
        i = m.end()
    raise RuntimeError("深度未归零")


def main() -> int:
    raw = TARGET.read_bytes()
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    if crlf == 0 or lf != 0:
        print(f"✘ web_template.html 应为纯 CRLF (CRLF={crlf} bareLF={lf})，中止")
        return 2
    src = raw.decode("utf-8").replace("\r\n", "\n")

    print("=" * 74)
    print("前端改造：技术面量化页「总览 → 单品种详情」双视图")
    print("=" * 74)

    print("\n[1] 锚点预检（任一不为 1 即中止，不写文件）")
    anchors = [
        ("tab-quant 开始标签", TAB_OPEN),
        ("loadQuant 定义", QUANT_LOAD),
        ("switchTab 的 quant 分支", SWITCH_LINE),
        ("_loadQuantMeta 默认选中", META_OLD),
    ]
    bad = []
    for tag, a in anchors:
        n = src.count(a)
        print(f"    {'OK' if n == 1 else '✘'} {tag}: 命中 {n} 次（期望 1）")
        if n != 1:
            bad.append(tag)
    if bad:
        print(f"\n✘ 锚点异常：{bad} —— 已中止，文件未改动")
        return 3

    # ── 应用 ────────────────────────────────────────────────────────
    # 【★ 顺序很关键】①②③ 是**结构性插入**，必须在原文件上先定位好边界、再一次性拼装；
    #   不能"插一个再找下一个"—— 因为 ② 插入的 `#q-detail-view` 是**暂时未闭合**的，
    #   一旦插进去，div 深度就永远回不到 0，`find_tab_end` 会报"未找到配平的结束标签"
    #   （第一次跑就栽在这里）。所以：**先在原文件上算出全部边界，然后一次拼好**。
    print("\n[2] 应用替换")

    start = src.index(TAB_OPEN)                    # ① 插入点
    end = find_tab_end(src, start)                 # tab-quant 的 `</div>` 之后
    close_start = end - len("</div>")              # ③ 插入点（其 `</div>` 之前）
    assert start < close_start, "边界顺序异常"

    ov = OVERVIEW_HTML.rstrip("\n")                # 含 TAB_OPEN 开头 + 总览视图
    dopen = DETAIL_OPEN.rstrip("\n")               # #q-detail-view 开标签 + 详情头（自身未闭合）
    head, tail = src[:start], src[close_start:]
    middle = src[start + len(TAB_OPEN):close_start]   # 原有 page-tools + 7 张卡（不含 TAB_OPEN）

    # ① 总览视图 → ② 详情外壳开 → 原有内容 → ③ 详情外壳闭 → 原 tab-quant 的 </div>
    src = head + ov + "\n" + dopen + "\n" + middle + " </div>\n" + tail
    print("    ✔ ① 总览视图已插入")
    print("    ✔ ② 详情外壳（开标签）已插入")
    print("    ✔ ③ 详情外壳（闭标签）已插入")

    # ④ JS 方法
    src = src.replace(QUANT_LOAD, JS_METHODS + QUANT_LOAD, 1)
    print("    ✔ ④ 总览 JS 方法已插入")

    # ⑤ _loadQuantMeta 尊重点行意图
    src = src.replace(META_OLD, META_NEW, 1)
    print("    ✔ ⑤ _loadQuantMeta 已支持「点行选中」")

    # ⑥ switchTab 分支
    src = src.replace(SWITCH_LINE, SWITCH_NEW, 1)
    print("    ✔ ⑥ switchTab 已改为进总览")

    # ── 自检 ────────────────────────────────────────────────────────
    print("\n[3] 自检")
    checks = [
        ("总览视图容器存在", 'id="q-overview-view"' in src),
        ("详情视图容器存在", 'id="q-detail-view"' in src),
        ("总览表格 tbody 存在", 'id="q-ov-rows"' in src),
        ("详情头存在", 'id="q-detail-sym"' in src),
        ("JS: loadQuantOverview", "async loadQuantOverview(force) {" in src),
        ("JS: qOpenDetail", " qOpenDetail(code) {" in src),
        ("JS: qBackOverview", " qBackOverview() {" in src),
        ("JS: 渲染函数", " _renderQuantOverview() {" in src),
        ("JS: 轮询停止", " _stopQuantOverview() {" in src),
        ("JS: pendingSym 声明", "_qovPendingSym: null," in src),
        ("switchTab 已改", "if (t === 'quant') {\n const ov = document.getElementById('q-overview-view');" in src),
        ("旧 switchTab 行已消失", SWITCH_LINE not in src),
        ("_loadQuantMeta 已改用 want", "const want = this._qovPendingSym;" in src),
        ("旧 meta 默认选中已消失", META_OLD not in src),
        ("原 7 张卡仍在（K线）", 'id="chart-q-kline"' in src),
        ("原 7 张卡仍在（计算面板）", 'id="q-cmp-corr"' in src),
        ("原 page-tools 仍在", 'id="tab-quant-tools"' in src),
        ("Ctrl+N 数组未动（quant 仍在）", "'quant'" in src),
    ]
    bad2 = [nm for nm, ok in checks if not ok]
    for nm, ok in checks:
        print(f"    {'OK' if ok else '✘'} {nm}")
    if bad2:
        print(f"\n✘ 自检未过：{bad2} —— 已中止，文件未改动")
        return 4

    # ── div 配平（结构改动必查）─────────────────────────────────────
    print("\n[4] tab-quant 结构配平")
    start = src.index(TAB_OPEN)
    end = find_tab_end(src, start)
    blk = src[start:end]
    no = len(re.findall(r"<div\b", blk))
    nc = len(re.findall(r"</div>", blk))
    print(f"    区间 {end - start} 字符 · <div>={no} </div>={nc} => {'✔ 配平' if no == nc else '✘ 不配平'}")
    if no != nc:
        print("✘ 已中止，文件未改动")
        return 5

    # ── 写回（CRLF 还原）────────────────────────────────────────────
    out = src.replace("\n", "\r\n")
    TARGET.write_bytes(out.encode("utf-8"))
    chk = TARGET.read_bytes()
    c2 = chk.count(b"\r\n")
    l2 = chk.count(b"\n") - c2
    print(f"\n[5] 已写入 {len(raw)} → {len(chk)} bytes ({len(chk) - len(raw):+d})")
    print(f"    行尾: CRLF={c2} bareLF={l2} => {'✔ 纯 CRLF' if l2 == 0 else '✘ 混入裸 LF'}")
    print("\n✔ 前端改造完成")
    return 0 if l2 == 0 else 6


if __name__ == "__main__":
    sys.exit(main())
