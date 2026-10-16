/**
 * 【护栏】量化信号卡「利润校验维度」渲染 (2026-09-29)
 *
 * 验证目标: loadQuantSignals 把 signals 正确分成"计分区 / 校验区",
 *   并保证「利润」以校验维度出现、且计分口径(score/bias)不被污染。
 *
 * 手法: 不依赖登录 —— stub window.fetch 只截 /api/quant/signals, 其余放行(401)。
 *       switchTab 可能重渲染 ⇒ 直接调 App.loadQuantSignals() 后立即断言。
 *
 * 用法:
 *   NO_PROXY=127.0.0.1,localhost node tests/js/quant_signals_profit_harness.mjs
 */
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = process.argv[2] || 'http://127.0.0.1:5000';

// 两份载荷: 有利润(EB) / 无利润(SC)
const PAYLOADS = {
  EB: {
    ok: true, variety: 'EB', name: '苯乙烯', bias: '中性', score: 1,
    inv_count: 122, basis_count: 122, bars: 166,
    signals: [
      { name: '库存+基差', level: '中性', desc: '库存与基差均处中位, 无明确方向性组合信号', weight: 1 },
      { name: '反转/资金信号', level: '提示', desc: '持仓量近 5 日大增 +18%(资金进场)', weight: 1 },
      { name: '期限结构', level: '偏多', desc: '近强远弱(back) → 现货偏紧格局', weight: 1 },
      { name: '利润', level: '偏多(校验)', weight: 0,
        desc: '进口利润 现值 70 / 区间 [-773, 2545], 近5期↑, 处历史低位(分位 25%) → 亏损不可持续, 供给端存收缩预期(另有 1 个口径未列示)' },
    ],
    profit: { ok: true, scored: false, label: '进口利润', cur: 70, low: -773, high: 2545,
              pct: 0.2539, count: 2816, labels: ['加工利润', '进口利润'] },
  },
  SC: {
    ok: true, variety: 'SC', name: '原油', bias: '中性', score: 1,
    inv_count: 0, basis_count: 0, bars: 166,
    signals: [
      { name: '库存+基差', level: '降级', desc: '库存或仓单缺失, 该项不参与研判', weight: 1 },
      { name: '反转/资金信号', level: '提示', desc: '持仓量近 5 日大降 -23%(资金离场)', weight: 1 },
      { name: '期限结构', level: '偏多', desc: '近强远弱(back) → 现货偏紧格局', weight: 1 },
      { name: '利润', level: '降级', weight: 0,
        desc: '该品种无加工/进口利润数据(平台未覆盖)(该项不参与研判)' },
    ],
    profit: { ok: false, scored: false },
  },
};

const browser = await chromium.launch({
  executablePath: 'C:/Users/lenovo/AppData/Local/ms-playwright/chromium-1234/chrome-win64/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });
const logs = [];
page.on('console', m => { if (m.type() === 'error') logs.push(m.text()); });
page.on('pageerror', e => logs.push('pageerror: ' + e.message));

await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(2500);

let pass = 0, fail = 0;
const bad = [];
function chk(name, cond, extra = '') {
  if (cond) { pass++; console.log(`  ✓ ${name}`); }
  else { fail++; bad.push(name); console.log(`  ✗ ${name} ${extra}`); }
}

for (const code of ['EB', 'SC']) {
  console.log(`\n=== ${code} (${code === 'EB' ? '有利润数据' : '无利润数据'}) ===`);

  const res = await page.evaluate(async (payload) => {
    // 只截 /api/quant/signals, 其余请求原样放行(会 401, 与本测试无关)
    const orig = window.fetch;
    window.fetch = function (u, o) {
      const s = String(u);
      if (s.indexOf('/api/quant/signals') >= 0) {
        return Promise.resolve(new Response(JSON.stringify(payload),
          { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      return orig.apply(this, arguments);
    };

    // 确保目标容器存在(未登录时 tab 未加载)
    let body = document.getElementById('q-signal-body');
    if (!body) {
      body = document.createElement('div'); body.id = 'q-signal-body'; document.body.appendChild(body);
    }
    if (!document.getElementById('q-signal-badge')) {
      const b = document.createElement('span'); b.id = 'q-signal-badge'; document.body.appendChild(b);
    }
    // ⚠️ 关键: 未登录时 /api/quant/meta 返回 401 ⇒ 品种下拉是**空(0 option)**,
    //    此时 `select.value = 'EB'` 会被浏览器清空 ⇒ 品种取法返回 '' ⇒ loadQuantSignals
    //    首行 `if (!sym) return` 直接退出, 页面永远停在"加载中..."(误判为实现坏了)。
    //    必须自己造出目标 option。
    // 【2026-10-14】该函数已随卡片迁至基本面量化页 ⇒ 品种源由 _quantSym(#q-variety)
    //   改为 _fundSym(#f-variety)。本 harness 必须同步造 #f-variety, 否则测的是旧容器。
    let v = document.getElementById('f-variety');
    if (!v) { v = document.createElement('select'); v.id = 'f-variety'; document.body.appendChild(v); }
    if (!Array.from(v.options).some(o => o.value === payload.variety)) {
      v.innerHTML = '';
      const opt = document.createElement('option');
      opt.value = payload.variety; opt.textContent = payload.name || payload.variety;
      v.appendChild(opt);
    }
    v.value = payload.variety;

    await App.loadQuantSignals();
    const badge = document.getElementById('q-signal-badge');

    // 分区探查
    const rows = Array.from(body.querySelectorAll('div')).filter(
      d => d.querySelector('span') && /库存\+基差|反转\/资金信号|期限结构|利润/.test(d.textContent));
    const scoredNames = [], checkNames = [];
    rows.forEach(() => {});
    // 用"校验维度"标题块定位: 出现在它之前的属计分区(含综合研判条), 之后属校验区
    const html = body.innerHTML;
    const ci = html.indexOf('校验维度');
    const scoredPart = ci >= 0 ? html.slice(0, ci) : html;
    const checkPart = ci >= 0 ? html.slice(ci) : '';
    // 校验块只到"说明"之前(说明里也会提到"利润", 不算校验行)
    const noteIdx = checkPart.indexOf('⚠️');
    const checkOnly = noteIdx >= 0 ? checkPart.slice(0, noteIdx) : checkPart;

    return {
      html, badge: badge ? badge.textContent : '',
      scoredPart, checkPart, checkOnly,
      hasCheckHeader: ci >= 0,
      // 计分汇总条必须在校验块之前(否则会被读成校验块的一部分)
      scoreBoxBeforeCheck: scoredPart.indexOf('信号计分') >= 0,
      scoreBar: (html.match(/信号计分\s*([+\-]?\d+)/) || [])[1],
      hasProfitInScored: scoredPart.indexOf('利润') >= 0,
      hasProfitInCheck: checkOnly.indexOf('利润') >= 0,
      checkOpacity: (checkOnly.match(/opacity:\s*([\d.]+)/) || [])[1] || '',
      nScoredRows: (scoredPart.match(/padding:9px 0;border-bottom/g) || []).length,
      nCheckRows: (checkOnly.match(/padding:9px 0;border-bottom/g) || []).length,
    };
  }, PAYLOADS[code]);

  chk('渲染出「校验维度」分组标题', res.hasCheckHeader);
  chk('计分汇总条排在校验块之前', res.scoreBoxBeforeCheck);
  chk('计分区不含「利润」', !res.hasProfitInScored);
  chk('校验区含「利润」', res.hasProfitInCheck);
  chk('计分行数 = 3', res.nScoredRows === 3, `实得 ${res.nScoredRows}`);
  chk('校验行数 = 1', res.nCheckRows === 1, `实得 ${res.nCheckRows}`);
  chk('计分口径未变 (score=+1)', res.scoreBar === '+1', `实得 ${res.scoreBar}`);
  chk('计分区标注「仅计分维度」', res.scoredPart.indexOf('仅计分维度') >= 0);
  chk('badge 区分计分/校验数', /计分\+1校验|维/.test(res.badge), `badge="${res.badge}"`);
  if (code === 'EB') {
    chk('校验行用半透明色 (opacity 0.62)', res.checkOpacity === '0.62', `实得 ${res.checkOpacity}`);
    chk('利润描述含"偏多(校验)"', res.checkOnly.indexOf('偏多(校验)') >= 0);
  } else {
    chk('无数据时显示「降级」而非静默', res.checkOnly.indexOf('降级') >= 0);
    chk('无数据时告知"平台未覆盖"', res.checkOnly.indexOf('平台未覆盖') >= 0);
  }
}

console.log(`\n${'='.repeat(50)}\n${fail === 0 ? 'ALL_PASS' : 'FAILED'}(${pass})  fail=${fail}`);
if (bad.length) console.log('未通过:', bad.join(' / '));
const realErrors = logs.filter(l => !/401|Unauthorized|Failed to load resource/i.test(l));
if (realErrors.length) { console.log('\n⚠️ 非 401 的 JS 报错:'); realErrors.forEach(l => console.log('  ' + l)); }
else console.log('无 JS 报错(401 已过滤)');

await browser.close();
process.exit(fail === 0 ? 0 : 1);
