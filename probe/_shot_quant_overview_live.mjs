// 技术面量化页 · 线上真实服务截图（2026-10-14）
//
// 【与 _e2e_quant_overview.mjs 的区别】那个脚本把 /api/quant/overview **打桩**
// （因为当时运行中的服务还是旧代码）；本脚本在**服务重启后**跑，全程打**真实接口**，
// 用来产出"用户能看到的那个页面"的实拍图。
//
// 【必须轮询而不是固定等待】天勤分钟K 冷启动 + 21 品种 × 4 周期是**渐进填充**的
// （后端后台线程 + ready/total 计数），固定等待要么拍空表、要么白等 3 分钟。
//
// 用法：node probe/_shot_quant_overview_live.mjs
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = 'http://127.0.0.1:5000';
const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const ctx = await browser.newContext({ viewport: { width: 1680, height: 1050 } });
const page = await ctx.newPage();
const jsErrors = [];
page.on('pageerror', e => jsErrors.push(String(e).slice(0, 200)));

// ── 1. 进页面 → 真实点击切到「技术面量化」────────────────────────────
await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="quant"]', { timeout: 30000 });
await page.click('nav button[data-tab="quant"]');

// 默认应落在**总览**（不是详情）
await page.waitForSelector('#q-overview-view', { timeout: 15000 });
const startState = await page.evaluate(() => ({
  ovShown: getComputedStyle(document.getElementById('q-overview-view')).display !== 'none',
  detailShown: getComputedStyle(document.getElementById('q-detail-view')).display !== 'none',
}));
console.log(`[1] 进页默认视图: 总览=${startState.ovShown} 详情=${startState.detailShown}`);

// ── 2. 轮询等总览行填充（阶段A 全池可见，最多 240s）──────────────────
let rows = 0, stat = {};
for (let i = 0; i < 80; i++) {
  stat = await page.evaluate(() => ({
    rows: document.querySelectorAll('#q-ov-rows tr').length,
    prog: (document.getElementById('q-ov-progress') || {}).textContent || '',
  }));
  rows = stat.rows;
  if (i % 5 === 0) console.log(`    ...等总览填充 ${i * 3}s rows=${rows} prog=${stat.prog.trim()}`);
  if (rows >= 21) break;
  await page.waitForTimeout(3000);
}
console.log(`[2] 总览行数 = ${rows}（期望 21 = 全池）  prog="${stat.prog.trim()}"`);

// 让阶段B（多周期）再补一会儿，抓一张"信息更全"的图
await page.waitForTimeout(45000);
const after = await page.evaluate(() => ({
  rows: document.querySelectorAll('#q-ov-rows tr').length,
  prog: (document.getElementById('q-ov-progress') || {}).textContent || '',
}));
console.log(`[2b] 等待 45s 后 rows=${after.rows} prog="${after.prog.trim()}"`);

await page.screenshot({ path: 'probe/_shot_live_overview_dark.png', fullPage: false });
console.log('[3] 已截图 总览·深色');

// 表头与首行文本，便于人工核对
const head = await page.evaluate(() => {
  const th = [...document.querySelectorAll('#q-overview-view thead th')].map(x => x.innerText.trim());
  const tr = document.querySelector('#q-ov-rows tr');
  return { th, first: tr ? tr.innerText.replace(/\s+/g, ' ').slice(0, 200) : '(空)' };
});
console.log('    表头:', head.th.join(' | '));
console.log('    首行:', head.first);

// ── 3. 点第一行进详情（真实接口）───────────────────────────────────
const firstCode = await page.evaluate(() => {
  const tr = document.querySelector('#q-ov-rows tr');
  return tr ? tr.innerText.trim().split(/\s+/)[0] : null;
});
console.log(`[4] 点击首行 ${firstCode} 进详情`);
await page.evaluate(() => document.querySelector('#q-ov-rows tr').click());
await page.waitForTimeout(1500);
const detailShown = await page.evaluate(() => ({
  ov: getComputedStyle(document.getElementById('q-overview-view')).display,
  detail: getComputedStyle(document.getElementById('q-detail-view')).display,
  sym: (document.getElementById('q-variety') || {}).value,
}));
console.log(`[4b] 切后: 总览display=${detailShown.ov} 详情display=${detailShown.detail} 下拉品种=${detailShown.sym}`);

// 等 K 线 canvas 真正有宽度（天勤冷启动 ~5s）
for (let i = 0; i < 40; i++) {
  const w = await page.evaluate(() => {
    const c = document.querySelector('#chart-q-kline canvas');
    return c ? c.width : 0;
  });
  if (w > 0) { console.log(`[5] K线已渲染 canvas.width=${w}（等 ${i}s）`); break; }
  await page.waitForTimeout(1000);
}
await page.waitForTimeout(2500);
await page.screenshot({ path: 'probe/_shot_live_detail_dark.png', fullPage: false });
console.log('[6] 已截图 详情·深色');

const cards = await page.evaluate(() => document.querySelectorAll('#q-detail-view .card').length);
console.log(`[7] 详情内卡片数 = ${cards}（期望 ≥7，原有模块都在）`);

// ── 4. 返回总览 + 双主题截图 ────────────────────────────────────────
await page.click('#q-detail-view button[onclick="App.qBackOverview()"]');
await page.waitForTimeout(1200);
const back = await page.evaluate(() => ({
  ov: getComputedStyle(document.getElementById('q-overview-view')).display,
  rows: document.querySelectorAll('#q-ov-rows tr').length,
}));
console.log(`[8] 返回总览: display=${back.ov} 行数=${back.rows}`);

await page.click('.theme-toggle');
await page.waitForTimeout(900);
const theme = await page.evaluate(() => document.documentElement.getAttribute('data-theme'));
await page.screenshot({ path: 'probe/_shot_live_overview_light.png', fullPage: false });
console.log(`[9] 已截图 总览·亮色（data-theme=${theme}）`);

console.log(`\nJS 错误数 = ${jsErrors.length}`);
jsErrors.slice(0, 5).forEach(e => console.log('  ! ' + e));

await browser.close();
