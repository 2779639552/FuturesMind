// 技术面量化页「总览 → 单品种详情」双视图 · 浏览器 E2E（2026-10-14）
//
// ============================== 为什么这样测 ==============================
// 运行中的 :5000 服务加载的是**旧** quant_module（新加的 /api/quant/overview 还没生效，
// 需要重启才上线）。所以：
//   · `/api/quant/overview`  → 用**真实接口产物**做桩（probe/_qov_stub.json 由
//     `quant_module._overview_one` 直接产出，不是手写字段 —— 手写字段会把
//     "后端没这个字段"这类缺陷盖掉，本项目栽过两次）。
//   · 其余接口（/api/quant/meta、kline、technical…）→ **真实放行**，
//     这样"点行进详情"这条链是真跑的，字段契约由 test_client 那层已单独验过。
//
// ============================== 断言纪律 ==============================
// · 切页签一律**真实点击** `nav button[data-tab="quant"]`；
//   `App` 是内联 script 的作用域变量、**没有挂到 window**，调 `App.switchTab` 会静默失败
//   ⇒ 页面停在默认 tab ⇒ 所有"筛选类"断言会假绿（空集合天然满足"都在范围内"）。
// · 先断言"集合非空"，再断言"筛得对"。
// · 主题在 `<html data-theme>` **属性**上，切换要点 `.theme-toggle`（App.toggleTheme 调不到）。

import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';
import fs from 'node:fs';

const STUB = JSON.parse(fs.readFileSync('probe/_qov_stub.json', 'utf8'));
const BASE = 'http://127.0.0.1:5000';
let pass = 0, fail = 0;
const log = (ok, msg) => { console.log(`${ok ? '  PASS' : '  FAIL'}  ${msg}`); ok ? pass++ : fail++; };

const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
const page = await ctx.newPage();
const jsErrors = [];
page.on('pageerror', e => jsErrors.push(String(e).slice(0, 200)));

// 只桩 overview，其余真实放行
await page.route('**/api/quant/overview*', route =>
  route.fulfill({ status: 200, contentType: 'application/json',
                  body: JSON.stringify(STUB) }));

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(2500);

// ── 1. 真实点击切到技术面量化页 ─────────────────────────────────
console.log('\n[1] 切到「技术面量化」页');
await page.click('nav button[data-tab="quant"]');
await page.waitForTimeout(3500);

const visible = sel => page.evaluate(s => {
  const el = document.querySelector(s);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  const cs = getComputedStyle(el);
  return { disp: cs.display, h: Math.round(r.height), w: Math.round(r.width) };
}, sel);

const ov = await visible('#q-overview-view');
const dv0 = await visible('#q-detail-view');
log(!!ov && ov.disp !== 'none', `总览视图可见（display=${ov && ov.disp}, 高 ${ov && ov.h}px）`);
log(!!dv0 && dv0.disp === 'none', `详情视图默认隐藏（display=${dv0 && dv0.disp}）`);

// ── 2. 表格内容 ────────────────────────────────────────────────
console.log('\n[2] 总览表格');
const tbl = await page.evaluate(() => {
  const rows = Array.from(document.querySelectorAll('#q-ov-rows tr'));
  const cells = rows.filter(tr => tr.querySelectorAll('td').length >= 10);
  const head = Array.from(document.querySelectorAll('#q-ov-table thead th')).map(t => t.textContent.trim());
  const stats = (document.getElementById('q-ov-stats') || {}).textContent || '';
  const pools = Array.from(document.querySelectorAll('#q-ov-pools button')).map(b => b.textContent.trim());
  const dirs = Array.from(document.querySelectorAll('#q-ov-dirs button')).map(b => b.textContent.trim());
  const res = Array.from(document.querySelectorAll('#q-ov-res button')).map(b => b.textContent.trim());
  const prog = (document.getElementById('q-ov-progress') || {}).textContent || '';
  return { n: cells.length, head, stats, pools, dirs, res, prog,
           first: cells[0] ? cells[0].innerText.replace(/\s+/g, ' ').trim() : '' };
});
log(tbl.n === STUB.rows.length, `表格行数 = ${tbl.n}（期望 ${STUB.rows.length}）`);
log(tbl.head.length === 14, `表头列数 = ${tbl.head.length}（期望 14）: ${tbl.head.join('/')}`);
log(tbl.head.includes('日线') && tbl.head.includes('60分') && tbl.head.includes('结论') && tbl.head.includes('置信度'),
    '表头含 多周期/结论/置信度 列');
log(tbl.stats.includes('品种') && /\d/.test(tbl.stats), `统计条已渲染: ${tbl.stats.replace(/\s+/g, ' ').slice(0, 70)}`);
log(tbl.pools.length === STUB.pools.length, `板块 pill 数 = ${tbl.pools.length}（期望 ${STUB.pools.length}）: ${tbl.pools.join(',')}`);
log(tbl.dirs.length === 4, `方向 pill 数 = ${tbl.dirs.length}: ${tbl.dirs.join(',')}`);
log(tbl.res.length === 2, `共振 pill 数 = ${tbl.res.length}: ${tbl.res.join(',')}`);
log(tbl.prog.includes('完成'), `进度文案: ${tbl.prog}`);
log(tbl.first.includes(STUB.rows[0].variety), `首行含品种代码: ${tbl.first.slice(0, 60)}`);

// ── 3. 板块筛选（先证集合非空，再证筛得对）───────────────────────
console.log('\n[3] 板块筛选');
const targetPool = STUB.pools.find(p => p !== '全部品种');
const wantCount = STUB.rows.filter(r => r.sector === targetPool).length;
await page.evaluate(p => {
  const b = Array.from(document.querySelectorAll('#q-ov-pools button'))
    .find(x => x.dataset.val === p);
  if (b) b.click();
}, targetPool);
await page.waitForTimeout(600);
const afterFilter = await page.evaluate(() =>
  Array.from(document.querySelectorAll('#q-ov-rows tr'))
    .filter(tr => tr.querySelectorAll('td').length >= 10)
    .map(tr => tr.innerText.replace(/\s+/g, ' ')));
log(wantCount > 0, `「${targetPool}」在数据里有 ${wantCount} 个品种（非空前提成立）`);
log(afterFilter.length === wantCount,
    `筛选后行数 = ${afterFilter.length}（期望 ${wantCount}）`);
log(afterFilter.every(t => true), `筛选结果: ${afterFilter.map(t => t.slice(0, 14)).join(' | ')}`);
// 还原
await page.evaluate(() => {
  const b = Array.from(document.querySelectorAll('#q-ov-pools button')).find(x => x.dataset.val === '全部品种');
  if (b) b.click();
});
await page.waitForTimeout(500);

// ── 4. 搜索框 ──────────────────────────────────────────────────
console.log('\n[4] 搜索');
const code2 = STUB.rows[STUB.rows.length - 1].variety;
await page.fill('#q-ov-search', code2);
await page.waitForTimeout(600);
const searched = await page.evaluate(() =>
  Array.from(document.querySelectorAll('#q-ov-rows tr'))
    .filter(tr => tr.querySelectorAll('td').length >= 10).length);
log(searched === 1, `搜索「${code2}」命中 ${searched} 行（期望 1）`);
await page.fill('#q-ov-search', '');
await page.waitForTimeout(400);

// ── 5. 点行 → 单品种详情 ───────────────────────────────────────
console.log('\n[5] 点行进详情');
// ⚠️ 必须用**池内**品种走主路径：CU(沪铜) 不在 ACTIVE_VARIETIES 里，
//   会走「点中品种不在下拉 → 补 option」的分支（那是单独的负向用例）。
// 池内品种（ACTIVE_VARIETIES）。⚠️ RB/CU 都**不在池内**，别拿来当主用例。
const POOL_OK = ['TA', 'M', 'SC', 'RU', 'BR', 'BU'];
const pick = STUB.rows.find(r => POOL_OK.includes(r.variety))?.variety || 'RB';
await page.evaluate(code => {
  const tr = Array.from(document.querySelectorAll('#q-ov-rows tr'))
    .find(x => x.innerText.replace(/\s+/g, ' ').startsWith(code + ' '));
  if (tr) tr.click();
}, pick);
// 【不用固定等待】K 线要拉真实接口(天勤冷启动 ~5s)，固定等待会假失败。
// 改为**事件驱动轮询**：等 canvas 真正出现宽度，最多 30s。
for (let i = 0; i < 30; i++) {
  const w = await page.evaluate(() => {
    const c = document.querySelector('#chart-q-kline canvas');
    return c ? c.width : 0;
  });
  if (w > 0) break;
  await page.waitForTimeout(1000);
}
await page.waitForTimeout(600);

const ov2 = await visible('#q-overview-view');
const dv2 = await visible('#q-detail-view');
log(!!ov2 && ov2.disp === 'none', `总览已隐藏（display=${ov2 && ov2.disp}）`);
log(!!dv2 && dv2.disp !== 'none', `详情已显示（display=${dv2 && dv2.disp}, 高 ${dv2 && dv2.h}px）`);
const detail = await page.evaluate(() => ({
  title: (document.getElementById('q-detail-sym') || {}).textContent || '',
  sel: (document.getElementById('q-variety') || {}).value || '',
  opts: (document.getElementById('q-variety') || {}).options?.length || 0,
  klineCanvas: (() => { const c = document.querySelector('#chart-q-kline canvas'); return c ? c.width : 0; })(),
  cards: document.querySelectorAll('#q-detail-view .card').length,
  optVals: Array.from((document.getElementById('q-variety') || {}).options || []).map(o => o.value),
}));
log(detail.title.includes(pick), `详情标题含品种: ${detail.title.trim().slice(0, 40)}`);
// ★ 这条是"点行没生效/被默认值覆盖"的核心判据
log(detail.sel === pick, `#q-variety 值 = ${detail.sel}（期望 ${pick}）`);
log(detail.opts >= 20, `#q-variety 选项数 = ${detail.opts}（期望 ≥20）`);
log(detail.klineCanvas > 0, `K 线 canvas 已渲染（width=${detail.klineCanvas}）`);
log(detail.cards >= 7, `详情内卡片数 = ${detail.cards}（期望 ≥7，即原有模块都在）`);
const extra = detail.optVals.filter(v => !POOL_OK.includes(v));
console.log(`  info  #q-variety 选项 ${detail.optVals.length} 个；非本表品种: ${extra.join(',') || '无'}`);

// ── 6. 返回总览 ────────────────────────────────────────────────
console.log('\n[6] 返回总览');
await page.evaluate(() => {
  const b = Array.from(document.querySelectorAll('#q-detail-view button'))
    .find(x => x.textContent.includes('返回总览'));
  if (b) b.click();
});
await page.waitForTimeout(1500);
const ov3 = await visible('#q-overview-view');
log(!!ov3 && ov3.disp !== 'none', `总览恢复显示（display=${ov3 && ov3.disp}）`);
const restored = await page.evaluate(() =>
  Array.from(document.querySelectorAll('#q-ov-rows tr'))
    .filter(tr => tr.querySelectorAll('td').length >= 10).length);
log(restored === STUB.rows.length, `返回后表格行数仍为 ${restored}`);

// ── 7. 切走再切回：应维持"详情"上下文（不该被重置成总览）─────────
console.log('\n[7] 切走再切回（先进入详情，再离开再回来）');
// ⚠️ 第 6 步已经点过「返回总览」，此时停在总览 ⇒ 必须先点一行进详情，
//   否则测的是"总览→切走→切回"，那本来就应该还停在总览（第一版断言写错在这里）。
await page.evaluate(code => {
  const tr = Array.from(document.querySelectorAll('#q-ov-rows tr'))
    .find(x => x.innerText.replace(/\s+/g, ' ').startsWith(code + ' '));
  if (tr) tr.click();
}, pick);
await page.waitForTimeout(3000);
await page.click('nav button[data-tab="research"]');
await page.waitForTimeout(1200);
await page.click('nav button[data-tab="quant"]');
await page.waitForTimeout(2500);
const ctxBack = await page.evaluate(() => ({
  ov: getComputedStyle(document.getElementById('q-overview-view')).display,
  dv: getComputedStyle(document.getElementById('q-detail-view')).display,
}));
log(ctxBack.dv !== 'none', `切回后维持在该品种详情（detail=${ctxBack.dv}, overview=${ctxBack.ov}）`);

// 再回总览，准备截图
await page.evaluate(() => {
  const b = Array.from(document.querySelectorAll('#q-detail-view button'))
    .find(x => x.textContent.includes('返回总览'));
  if (b) b.click();
});
await page.waitForTimeout(1500);

// ── 8. 双主题截图 ──────────────────────────────────────────────
console.log('\n[8] 双主题截图');
for (const theme of ['light', 'dark']) {
  const cur = await page.evaluate(() => document.documentElement.getAttribute('data-theme'));
  if (cur !== theme) {
    await page.click('.theme-toggle');
    await page.waitForTimeout(1200);
  }
  await page.screenshot({ path: `probe/_shot_qov_${theme}.png`, fullPage: false });
  console.log(`  (shot) probe/_shot_qov_${theme}.png`);
}

console.log(`\n=== JS 错误 ${jsErrors.length} 条 ===`);
jsErrors.slice(0, 5).forEach(e => console.log('  ✘', e));
console.log(`\n=== 结果：PASS ${pass} / FAIL ${fail} ===`);
await browser.close();
process.exit(fail === 0 && jsErrors.length === 0 ? 0 : 1);
