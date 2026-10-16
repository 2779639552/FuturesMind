// E2E 验收: 「情绪得分 vs 价格走势」x 轴改为交易日（2026-10-13）
// 用法: node probe/_e2e_overlay_trading_days.mjs [BASE]
// 覆盖:
//   A 接口返回新口径（xaxis_mode=trading_days）
//   B ★ 价格 series 的 data 里 **没有 null** ⇒ 价格线渲染上不会断（用户诉求的直接证据）
//   C x 轴标签里没有周末/国庆
//   D 情绪线 / 7日均线 / 自适应因子 仍在，且情绪点数量与接口一致
//   E 双主题截图 + 无 JS 错误
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = process.argv[2] || 'http://127.0.0.1:5000';
let pass = 0, fail = 0;
const log = (ok, msg) => { ok ? pass++ : fail++; console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${msg}`); };

const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errs = [];
page.on('pageerror', e => errs.push(e.message));

// 先取接口基线，供与前端渲染结果对照
const api = await page.evaluate(async b => {
  const r = await fetch(b + '/api/overlay/RB?days=180');
  return await r.json();
}, BASE).catch(() => null);

await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(2500);

console.log('\n[A] 接口口径');
const raw = await page.evaluate(async () => {
  const r = await fetch('/api/overlay/RB?days=180');
  return await r.json();
});
const meta = raw._meta || {};
log(meta.xaxis_mode === 'trading_days', `xaxis_mode = ${meta.xaxis_mode}`);
log(meta.trading_days === raw.overlay.length,
    `trading_days = ${meta.trading_days} == overlay 点数 ${raw.overlay.length}`);
const apiNulls = raw.overlay.filter(x => x.close == null).length;
log(apiNulls === 0, `接口层缺 close 点数 = ${apiNulls}`);

// 真实点击进入情绪页（App 不在 window 上，不能调 App.switchTab）
console.log('\n[B] 前端渲染的价格序列');
await page.click('nav button[data-tab="sentiment"]');
await page.waitForTimeout(4000);

// 确保选中一个有情绪的品种
await page.evaluate(() => {
  const s = document.getElementById('overlay-sym');
  if (s && Array.from(s.options).some(o => o.value === 'RB')) { s.value = 'RB'; s.onchange && s.onchange(); }
  return true;
});
await page.waitForTimeout(4000);

const opt = await page.evaluate(() => {
  const el = document.getElementById('chart-overlay');
  if (!el) return { err: 'no container' };
  const inst = (window.echarts && echarts.getInstanceByDom(el)) || null;
  if (!inst) return { err: 'no echarts instance' };
  const o = inst.getOption();
  return {
    xLen: (o.xAxis && o.xAxis[0] && o.xAxis[0].data || []).length,
    xSample: (o.xAxis && o.xAxis[0] && o.xAxis[0].data || []).slice(0, 3),
    series: (o.series || []).map(s => ({
      name: s.name,
      len: (s.data || []).length,
      nulls: (s.data || []).filter(v => v == null).length,
    })),
    canvasW: el.querySelector('canvas') ? el.querySelector('canvas').width : 0,
  };
});
console.log('  (info)', JSON.stringify(opt));

if (opt.err) {
  log(false, `图表不可用: ${opt.err}`);
} else {
  log(opt.canvasW > 0, `canvas 已渲染（宽 ${opt.canvasW}px）`);
  log(opt.xLen > 0, `x 轴点数 = ${opt.xLen}`);

  const price = opt.series.find(s => s.name === '收盘价');
  log(!!price, '存在「收盘价」序列');
  if (price) {
    // ★ 核心：价格序列不能有 null —— 有 null 就是"断点"
    log(price.nulls === 0, `★ 收盘价序列 null 点数 = ${price.nulls}（必须为 0 = 价格线不断）`);
    log(price.len === opt.xLen, `收盘价点数 ${price.len} == x 轴点数 ${opt.xLen}`);
  }
  const sent = opt.series.find(s => s.name === '作者加权情绪');
  log(!!sent && sent.nulls > 0,
      `情绪线存在且有留白（null ${sent ? sent.nulls : '?'} 个）—— 情绪只在有数据的交易日画点，属预期`);
  log(!!opt.series.find(s => s.name === '情绪7日均线'), '存在「情绪7日均线」序列');
  log(!!opt.series.find(s => s.name === '自适应因子'), '存在「自适应因子」序列');

  // 与接口对照（防"前端画的和接口给的不是一份"）
  const apiClose = raw.overlay.map(x => x.close);
  const apiNonNull = apiClose.filter(v => v != null).length;
  log(price && price.len - price.nulls === apiNonNull,
      `前端有效价点数(${price ? price.len - price.nulls : '?'}) == 接口非空收盘价数(${apiNonNull})`);
}

console.log('\n[C] x 轴无周末/国庆');
const axis = await page.evaluate(() => {
  const el = document.getElementById('chart-overlay');
  const inst = window.echarts && echarts.getInstanceByDom(el);
  return (inst && inst.getOption().xAxis[0].data) || [];
});
const hol = ['2026-09-25', '2026-10-01', '2026-10-02', '2026-10-05', '2026-10-06', '2026-10-07'];
const bad = axis.filter(d => { const w = new Date(d + 'T00:00:00').getDay(); return w === 0 || w === 6 || hol.includes(d); });
log(bad.length === 0, `x 轴无周末/假期（命中 ${JSON.stringify(bad)}）`);

console.log('\n[D] 双主题截图');
for (const theme of ['light', 'dark']) {
  await page.evaluate(t => {
    while ((document.documentElement.getAttribute('data-theme') || 'dark') !== t) {
      document.querySelector('.theme-toggle').click();
    }
  }, theme);
  await page.waitForTimeout(1500);
  const box = await page.evaluate(() => {
    const c = document.getElementById('chart-overlay').closest('.card');
    if (!c) return null;
    const r = c.getBoundingClientRect();
    return { x: Math.max(0, r.x), y: Math.max(0, r.y),
             width: Math.min(1600, r.width), height: Math.min(1000, r.height) };
  });
  if (box && box.width > 100 && box.height > 100) {
    await page.screenshot({ path: `probe/_shot_overlay_${theme}.png`, clip: box });
  } else {
    await page.screenshot({ path: `probe/_shot_overlay_${theme}.png`, fullPage: false });
  }
  console.log(`  (shot) probe/_shot_overlay_${theme}.png`);
}

log(errs.length === 0, `页面无 JS 错误（${errs.length} 个）${errs.slice(0, 3).join(' | ')}`);

await browser.close();
console.log(`\n结果: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
