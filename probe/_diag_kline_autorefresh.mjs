// 排查：分钟级 K 线「自动刷新」是否还在跑（2026-10-14 用户报障）
//
// 【为什么不靠读代码下结论】`_scheduleKlineRefresh` 起表要同时满足
//   period!=='day' 且 _currentTab==='quant'；而"起表失败"在多数路径上是**静默**的
//   （第一个 if 直接 return，无任何提示）。所以必须在真浏览器里**数请求**：
//   若 40 秒内只出现 1 次 /api/quant/kline ⇒ 定时器没在跑（用户症状成立）。
//
// ⚠️ App 是内联 script 顶层的 `const` ⇒ 不在 window 上，但在**全局词法环境**里，
//   page.evaluate 的函数作用域链能解析到它（内联 onclick="App.xxx()" 同理）。
//   取值一律用 try 包住，避免"读不到状态"被当成"状态为空"。

import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = 'http://127.0.0.1:5000';
const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
const page = await ctx.newPage();

const jsErrors = [];
page.on('pageerror', e => jsErrors.push(String(e).slice(0, 220)));

// ── 数 kline 请求 ────────────────────────────────────────────────
const klineReqs = [];
page.on('request', r => {
  if (r.url().includes('/api/quant/kline')) klineReqs.push({ t: Date.now(), url: r.url() });
});
const klineResps = [];
page.on('response', async r => {
  if (!r.url().includes('/api/quant/kline')) return;
  let src = '?', last = '?', n = 0, ok = '?';
  try {
    const j = await r.json();
    ok = j.ok; src = j.source || '?';
    const ks = j.klines || j.bars || [];
    n = ks.length;
    if (ks.length) last = (ks[ks.length - 1].time || ks[ks.length - 1].date || '?');
  } catch (e) { /* 非 JSON */ }
  klineResps.push({ t: Date.now(), status: r.status(), ok, src, n, last });
});

const state = async () => page.evaluate(() => {
  const g = (fn) => { try { return fn(); } catch (e) { return 'ERR:' + String(e).slice(0, 60); } };
  return {
    hasApp: g(() => typeof App),
    curTab: g(() => App._currentTab),
    klineTimer: g(() => (App._klineTimer === undefined ? 'undefined'
      : App._klineTimer === null ? 'null(未起表)' : 'SET#' + typeof App._klineTimer)),
    period: g(() => (document.getElementById('q-period') || {}).value),
    ovDisp: g(() => getComputedStyle(document.getElementById('q-overview-view')).display),
    dvDisp: g(() => getComputedStyle(document.getElementById('q-detail-view')).display),
    canvasW: g(() => { const c = document.querySelector('#chart-q-kline canvas'); return c ? c.width : 0; }),
  };
});

const show = (tag, s) => console.log(`  ${tag}  tab=${s.curTab} klineTimer=${s.klineTimer} period=${s.period} ov=${s.ovDisp} dv=${s.dvDisp} canvasW=${s.canvasW}`);

// ── 1. 进页面 → 切到技术面量化 ──────────────────────────────────
await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="quant"]', { timeout: 30000 });
await page.click('nav button[data-tab="quant"]');
await page.waitForTimeout(1500);
console.log('[1] 刚进技术面量化（应在总览）');
show('    ', await state());

// ── 2. 等总览出第一行，点行进详情 ───────────────────────────────
let code = null;
for (let i = 0; i < 40; i++) {
  code = await page.evaluate(() => {
    const tr = document.querySelector('#q-ov-rows tr');
    return tr ? tr.innerText.trim().split(/\s+/)[0] : null;
  });
  if (code && /^[A-Z]{1,3}$/.test(code)) break;
  await page.waitForTimeout(3000);
}
console.log(`[2] 总览首行 = ${code}`);
await page.evaluate(() => document.querySelector('#q-ov-rows tr').click());
await page.waitForTimeout(2000);
console.log('[2b] 已点行进详情');
show('    ', await state());

// ── 3. 切到 1 分钟周期（自动刷新只对分钟级起表）─────────────────
await page.evaluate(() => {
  const s = document.getElementById('q-period');
  s.value = '1';
  s.dispatchEvent(new Event('change', { bubbles: true }));
});
for (let i = 0; i < 30; i++) {
  const w = await page.evaluate(() => { const c = document.querySelector('#chart-q-kline canvas'); return c ? c.width : 0; });
  if (w > 0) break;
  await page.waitForTimeout(1000);
}
await page.waitForTimeout(2500);
console.log('[3] 已切 1 分钟并等渲染');
const s3 = await state();
show('    ', s3);
console.log(`    kline 请求数 = ${klineReqs.length}`);
const r0 = klineResps[klineResps.length - 1];
if (r0) console.log(`    最近一次响应: status=${r0.status} ok=${r0.ok} source=${r0.src} bars=${r0.n} last=${r0.last}`);

// ── 4. 静候 75 秒，看是否自动再发（应 ≥2 次新请求）──────────────
const before = klineReqs.length;
console.log(`\n[4] 静候 75s（自动刷新周期 30s ⇒ 期望新增 ≥2 次请求）…`);
for (let i = 0; i < 5; i++) {
  await page.waitForTimeout(15000);
  const now = await state();
  console.log(`    +${(i + 1) * 15}s  新增请求 ${klineReqs.length - before}  klineTimer=${now.klineTimer}  canvasW=${now.canvasW}`);
}
const added = klineReqs.length - before;
console.log(`\n[结论] 75s 内新增 kline 请求 = ${added}  ⇒ ${added >= 2 ? '自动刷新在跑 OK' : '★自动刷新没跑（症状确认）'}`);
console.log('  最后 3 次响应:');
klineResps.slice(-3).forEach(r => console.log(`    status=${r.status} ok=${r.ok} src=${r.src} bars=${r.n} last=${r.last}`));

// ── 5. 顺带记录：返回总览后定时器是否被清 ────────────────────────
await page.click('#q-detail-view button[onclick="App.qBackOverview()"]');
await page.waitForTimeout(1200);
const back = await state();
console.log(`\n[5] 返回总览后: ov=${back.ovDisp} dv=${back.dvDisp} klineTimer=${back.klineTimer}`);

console.log(`\nJS 错误数 = ${jsErrors.length}`);
jsErrors.slice(0, 6).forEach(e => console.log('  ! ' + e));
await browser.close();
