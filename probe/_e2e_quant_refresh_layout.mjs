// 技术面量化两项改动的浏览器 E2E（真实交互 + DOM 度量）
// A. 总览：算完后确实排了"15 分钟后的自动刷新"，进度条显示下次时刻
// B. 详情：K 线在左半边、技术指标在右半边（同 top、左右分列、各约一半宽），
//    canvas 均正常渲染（宽度 > 0），纵向空间显著压缩；窄屏自动回落上下排列
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = 'http://127.0.0.1:5000';
let pass = 0, fail = 0;
const problems = [];
const log = (ok, label, extra = '') => {
  if (ok) { pass++; console.log(`  ✔ ${label}`); }
  else { fail++; problems.push(label); console.log(`  ✘ ${label} ${extra}`); }
};

const b = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const ctx = await b.newContext({ viewport: { width: 1680, height: 1050 } });
const p = await ctx.newPage();
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 180)));
p.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 150)); });

await p.goto(BASE, { waitUntil: 'domcontentloaded' });
await p.waitForSelector('nav button[data-tab="quant"]', { timeout: 30000 });
await p.click('nav button[data-tab="quant"]');
console.log('已进入「技术面量化」（默认落在总览）\n');

// ═══ A. 总览：15 分钟自动刷新 ═══
console.log('[A] 总览：15 分钟自动刷新排期');
let A = null;
for (let i = 0; i < 90; i++) {           // 最多等 180s（首次算全池约 105s；命中缓存则秒回）
  A = await p.evaluate(() => {
    const d = App._qovData || {};
    return {
      done: !!d.done, cached: !!d.cached, rows: (d.rows || []).length,
      nextAt: App._qovNextAt || 0,
      delta: App._qovNextAt ? App._qovNextAt - Date.now() : 0,
      hasTimer: !!App._qovPollTimer,
      refreshMs: App._QOV_REFRESH_MS,
      progress: (document.getElementById('q-ov-progress') || {}).textContent || '',
      placeholder: (document.querySelector('#q-ov-rows') || {}).innerText || '',
    };
  });
  if (A.done) break;
  await p.waitForTimeout(2000);
}
log(A.done, `总览算完（${A.rows} 行${A.cached ? ' · 命中缓存' : ' · 实时'}）`);
log(A.refreshMs === 15 * 60 * 1000, '前端刷新周期常量 = 900000ms', 'got ' + A.refreshMs);
log(A.nextAt > 0, '已排下次刷新时刻（_qovNextAt 已设置）');
log(Math.abs(A.delta - 900000) < 8000,
    '下次刷新 ≈ 15 分钟后', `delta=${Math.round(A.delta / 1000)}s`);
log(A.hasTimer, '定时器存在（_qovPollTimer 非空）');
log(/每 15 分钟自动刷新（下次 \d{2}:\d{2}）/.test(A.progress),
    '进度条显示「每 15 分钟自动刷新（下次 HH:MM）」', JSON.stringify(A.progress));
log(A.progress.includes('计算完成'), '进度条处于"计算完成"态', A.progress);

// 缓存命中核实：再请求一次应立刻返回且 cached=true（后端 15 min TTL 生效）
const t0 = Date.now();
const cached = await p.evaluate(async () => {
  const r = await fetch('/api/quant/overview');
  const d = await r.json();
  return { cached: !!d.cached, done: !!d.done, rows: (d.rows || []).length };
});
const el = Date.now() - t0;
log(cached.done && cached.rows > 0, `再请求秒回（${el}ms）且 done=true`);
log(el < 3000, '★ 第二次请求走缓存（<3s，没有重算）', `${el}ms`);

// ═══ B. 详情排版 ═══
console.log('\n[B] 详情：K 线左半 + 技术指标右半');
// 真实点击第一行进详情
await p.waitForSelector('#q-ov-rows tr', { timeout: 20000 });
await p.click('#q-ov-rows tr');
// 等 K 线真正画出来（事件驱动轮询，不固定等待）
let drawn = false;
for (let i = 0; i < 40; i++) {
  drawn = await p.evaluate(() => {
    const c = document.querySelector('#chart-q-kline canvas');
    return !!(c && c.width > 0);
  });
  if (drawn) break;
  await p.waitForTimeout(1000);
}
log(drawn, 'K 线 canvas 已渲染（宽度 > 0）');
await p.waitForTimeout(1500);

const M = await p.evaluate(() => {
  const rect = el => { const r = el.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const cv = el => { const c = el.querySelector('canvas'); return c ? { w: c.width, h: c.height } : null; };
  const k = document.getElementById('chart-q-kline');
  const rsi = document.getElementById('chart-q-rsi');
  const macd = document.getElementById('chart-q-macd');
  const body = document.getElementById('q-tech-body');
  const cardOf = el => { let n = el; while (n && !n.classList.contains('card')) n = n.parentElement; return n; };
  const kc = cardOf(k), tc = cardOf(rsi);
  const wrap = kc.parentElement;
  const wrapR = wrap.getBoundingClientRect();
  const cards = Array.from(document.querySelectorAll('#q-detail-view .card'));
  const iK = cards.indexOf(kc), iT = cards.indexOf(tc);
  return {
    wrap: { x: Math.round(wrapR.x), w: Math.round(wrapR.width), display: getComputedStyle(wrap).display },
    kCard: rect(kc), tCard: rect(tc),
    kChart: rect(k), kCanvas: cv(k), rsiCanvas: cv(rsi), macdCanvas: cv(macd),
    techBody: rect(body),
    iK, iT,
    kH: parseFloat(k.style.height || '0'),
    rsiH: parseFloat(rsi.style.height || '0'), macdH: parseFloat(macd.style.height || '0'),
    techTitle: (tc.querySelector('h3') || {}).innerText || '',
    kTitle: (document.getElementById('q-kline-title') || {}).textContent || '',
    totalV: Math.round(rect(tc).y + rect(tc).h - rect(kc).y),   // K线卡顶 → 技术指标卡底
  };
});
console.log(JSON.stringify(M, null, 1));

log(M.wrap.display === 'flex', '两卡处于 flex 容器内', M.wrap.display);
log(Math.abs(M.kCard.y - M.tCard.y) <= 2, '★ 两卡**顶对齐**（左右并排，不是上下）',
    `y: K=${M.kCard.y} T=${M.tCard.y}`);
log(M.kCard.x < M.tCard.x, '★ K 线在**左侧**、技术指标在右侧',
    `x: K=${M.kCard.x} T=${M.tCard.x}`);
const half = M.wrap.w / 2;
log(Math.abs(M.kCard.w - M.tCard.w) <= 4, '两卡宽度相等', `${M.kCard.w} vs ${M.tCard.w}`);
log(M.kCard.w <= half + 4 && M.tCard.w <= half + 4,
    '★ 每卡约占容器一半宽', `卡宽 ${M.kCard.w} ≤ 半宽 ${Math.round(half)}`);
log(M.kChart.w > 0 && M.kChart.w <= half, 'K 线图（含卡内边距）宽度不超过半宽', `${M.kChart.w}`);
log(M.kCanvas && M.kCanvas.w > 0, '★ K 线 canvas 有实际宽度（未 0×0 空白）', JSON.stringify(M.kCanvas));
log(M.rsiCanvas && M.rsiCanvas.w > 0 && M.macdCanvas && M.macdCanvas.w > 0,
    'RSI / MACD canvas 均有宽度', JSON.stringify([M.rsiCanvas, M.macdCanvas]));
log(M.kH === 520, 'K 线高度仍为 520（只压宽度）', 'h=' + M.kH);
log(M.rsiH === 130 && M.macdH === 130, 'RSI/MACD 已缩到 130px', `${M.rsiH}/${M.macdH}`);
log(M.totalV < 900, `★ 纵向总高显著压缩（K线卡顶→技术指标卡底 = ${M.totalV}px，改前约 1243px）`);
log(M.techTitle.includes('技术指标'), '右侧卡标题为「技术指标」', M.techTitle.slice(0, 20));
log(M.kTitle.includes('K 线图'), '左侧卡标题为「K 线图」', M.kTitle.slice(0, 20));
await p.screenshot({ path: 'probe/_shot_quant_detail_sidebyside.png' });

// ═══ C. 窄屏回落 ═══
console.log('\n[C] 窄屏（760px）自动回落上下排列');
await p.setViewportSize({ width: 760, height: 1000 });
await p.waitForTimeout(1200);
const N = await p.evaluate(() => {
  const rect = el => { const r = el.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width) }; };
  const k = document.getElementById('chart-q-kline');
  const rsi = document.getElementById('chart-q-rsi');
  const cardOf = el => { let n = el; while (n && !n.classList.contains('card')) n = n.parentElement; return n; };
  return { k: rect(cardOf(k)), t: rect(cardOf(rsi)) };
});
log(N.t.y > N.k.y + N.k.w * 0.1, '★ 窄屏下技术指标卡落到 K 线卡**下方**（已换行）',
    `K y=${N.k.y} T y=${N.t.y}`);
log(Math.abs(N.k.x - N.t.x) <= 2, '窄屏下两卡左对齐（竖排）', `${N.k.x} vs ${N.t.x}`);
await p.screenshot({ path: 'probe/_shot_quant_detail_narrow.png' });

console.log('\n=== JS 错误 ===');
console.log(errs.length ? errs.slice(0, 6).join('\n') : '  无 ✔');
log(errs.length === 0, '0 JS 错误', errs.slice(0, 3).join(' | '));

console.log('\n' + '='.repeat(60));
console.log(`结果: ${pass} PASS / ${fail} FAIL`);
if (problems.length) { console.log('失败项:'); problems.forEach(x => console.log('   - ' + x)); }
await b.close();
process.exit(fail ? 1 : 0);
