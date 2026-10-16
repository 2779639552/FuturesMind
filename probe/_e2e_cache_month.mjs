// ① 技术面总览「进页面不再重新计算」 ② 基本面截面「切合约重算该行」 —— E2E（2026-10-16）
//
// 【为什么必须真浏览器】
//   ① 的核心行为是"**有旧数据时刷新期间表格不被清空**" + "切走 tab 定时器真的停了" ——
//      前者要观察 DOM 是否仍在、后者要读页面内的定时器状态, 静态断言都做不到。
//   ② 要验的是"选月份后 DOM 上那一行的数字真的变了、且分位变成 — 并带说明"。
//
// 用法: node probe/_e2e_cache_month.mjs

import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = 'http://127.0.0.1:5000';
const P = [], F = [];
const ck = (c, what, detail = '') => {
  (c ? P : F).push(what);
  console.log(`  ${c ? 'OK ' : '★  '} ${what}${detail ? `  [${detail}]` : ''}`);
  return c;
};
const txt = (page, sel) => page.evaluate(s => (document.querySelector(s) || {}).textContent || '', sel);
const vis = (page, sel) => page.evaluate(s => {
  const el = document.querySelector(s);
  return !!el && getComputedStyle(el).display !== 'none' && el.offsetParent !== null;
}, sel);
async function waitFor(fn, ms = 240000, step = 800) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (await fn()) return true; await page_wait(step); }
  return false;
}
let _page = null;
const page_wait = (ms) => _page.waitForTimeout(ms);

const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const ctx = await browser.newContext({ viewport: { width: 1760, height: 1050 } });
const page = await ctx.newPage();
_page = page;
const jsErrors = [];
page.on('pageerror', e => jsErrors.push('pageerror: ' + e.message));
page.on('console', m => { if (m.type() === 'error') jsErrors.push('console: ' + m.text()); });

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="quant"]', { timeout: 30000 });
await page.click('nav button[data-tab="quant"]');

console.log('=== A. ① 总览：等首轮算完（服务刚重启 ⇒ 首轮无旧数据，会有进度条） ===');
const done0 = await waitFor(async () => {
  const d = await page.evaluate(() => {
    const t = (document.getElementById('q-ov-progress') || {}).textContent || '';
    return { t, rows: document.querySelectorAll('#q-ov-rows tr').length };
  });
  return d.rows >= 21 && /计算完成/.test(d.t);
}, 400000);
const a0 = await page.evaluate(() => ({
  progress: (document.getElementById('q-ov-progress') || {}).textContent || '',
  rows: document.querySelectorAll('#q-ov-rows tr').length,
  nextAt: App._qovNextAt, timer: !!App._qovPollTimer,
}));
ck(done0, '首轮计算完成且表有 21 行', `${a0.rows} 行`);
ck(/每 15 分钟自动刷新（下次 \d{2}:\d{2}）/.test(a0.progress), '空闲态显示下次刷新时刻', a0.progress.slice(-30));
ck(a0.nextAt > 0 && a0.timer, '空闲态已排 15 min 定时器');

console.log('');
console.log('=== B. ★ 关键回归：点「重新计算」时必须**保留旧表**（而不是清空成进度条） ===');
await page.evaluate(() => App.loadQuantOverview(1));
await page.waitForTimeout(1200);
const during = await page.evaluate(() => ({
  rows: document.querySelectorAll('#q-ov-rows tr').length,
  progress: (document.getElementById('q-ov-progress') || {}).textContent || '',
  refreshing: !!(App._qovData || {}).refreshing,
  done: !!(App._qovData || {}).done,
  cached: !!(App._qovData || {}).cached,
  timer: !!App._qovPollTimer,
}));
ck(during.rows >= 21, '★ 重算期间表格**仍在**（旧数据未被清空）', `${during.rows} 行`);
ck(during.done === true, '★ 重算期间 done 仍为 true（前端才不会切成"计算中"）');
ck(/后台刷新中/.test(during.progress), '进度条改为「后台刷新中 n/21」并说明无需等待', during.progress.slice(0, 80));
ck(/上一轮/.test(during.progress), '进度条讲明"表格显示的是上一轮结果"');
ck(during.refreshing === true, '接口如实上报 refreshing=true');
ck(during.cached === false, '★ cached 不再无条件为 true（重算中应为 false）');

console.log('');
console.log('=== C. 刷新完成后回到空闲态（就地更新，全程没出现空表） ===');
const backIdle = await waitFor(async () => {
  const d = await page.evaluate(() => ({
    rows: document.querySelectorAll('#q-ov-rows tr').length,
    refreshing: !!(App._qovData || {}).refreshing,
    progress: (document.getElementById('q-ov-progress') || {}).textContent || '',
  }));
  return !d.refreshing && d.rows >= 21 && /计算完成/.test(d.progress);
}, 400000);
ck(backIdle, '刷新完成并回到「计算完成」');
const idle = await page.evaluate(() => ({
  progress: (document.getElementById('q-ov-progress') || {}).textContent || '',
  rows: document.querySelectorAll('#q-ov-rows tr').length,
  nextAt: App._qovNextAt,
}));
ck(idle.rows >= 21 && idle.nextAt > 0, '完成后表格完整且重排 15 min 定时器', idle.progress.slice(-26));

console.log('');
console.log('=== D. ★ 切走 tab 必须停掉总览定时器（否则后台每 15 min 空转重算） ===');
await page.click('nav button[data-tab="research"]');
await page.waitForTimeout(1000);
const away = await page.evaluate(() => ({
  timer: !!App._qovPollTimer, nextAt: App._qovNextAt, tab: App._currentTab,
}));
ck(!away.timer, '切走后方无总览定时器', `timer=${away.timer}`);
ck(away.nextAt === 0, '切走后"下次刷新时刻"已清空（不显示过期信息）');
// 回来
await page.click('nav button[data-tab="quant"]');
const cameBack = await waitFor(async () => {
  const d = await page.evaluate(() => ({
    rows: document.querySelectorAll('#q-ov-rows tr').length,
    progress: (document.getElementById('q-ov-progress') || {}).textContent || '',
    nextAt: App._qovNextAt,
  }));
  return d.rows >= 21 && d.nextAt > 0 && /计算完成/.test(d.progress);
}, 60000);
ck(cameBack, '★ 切回后**秒回**（命中 15 min 缓存，无重算等待）');

console.log('');
console.log('=== E. ② 基本面截面：切合约 → 该行按该月重算 ===');
await page.click('nav button[data-tab="fund"]');
await page.waitForSelector('#f-rk-rows', { timeout: 30000 });
const hasRows = await waitFor(async () =>
  (await page.evaluate(() => document.querySelectorAll('#f-rk-rows tr[onclick]').length)) >= 21, 400000);
ck(hasRows, '基本面截面表拉到 21 行');

// 挑一个既有合约下拉、又有基差的品种
const target = await page.evaluate(() => {
  const trs = [...document.querySelectorAll('#f-rk-rows tr[onclick]')];
  for (const tr of trs) {
    const sel = tr.querySelector('select.f-rk-mon');
    const tds = [...tr.querySelectorAll('td')];
    const code = tr.getAttribute('onclick').match(/'([A-Z]+)'/)[1];
    const months = [...sel.options].map(o => o.value).filter(Boolean);
    if (months.length >= 3 && tds[4].textContent.trim() !== '—') {
      return { code, months, basis: tds[4].textContent.replace(/\s+/g, ' ').trim(),
               pct: tds[6].textContent.trim(), vr: tds[12].textContent.trim(),
               vrmonth: sel.value, term: tds[15].textContent.trim() };
    }
  }
  return null;
});
ck(!!target, '找到可测品种（有合约下拉 + 有基差）', target ? `${target.code} 主力=${target.vrmonth}` : '无');
if (target) {
  const pick = target.months.find(m => m !== target.vrmonth);
  // 选月份
  await page.evaluate(({ code, m }) => {
    const tr = [...document.querySelectorAll('#f-rk-rows tr[onclick]')]
      .find(t => (t.getAttribute('onclick') || '').includes(`'${code}'`));
    const sel = tr.querySelector('select.f-rk-mon');
    sel.value = m;
    sel.dispatchEvent(new Event('change', { bubbles: true }));
  }, { code: target.code, m: pick });
  const recomputed = await waitFor(async () => {
    const t = await txt(page, '#f-rk-note');
    return /已切换到/.test(t);
  }, 120000);
  ck(recomputed, `选 ${pick} 后触发了该行重算`, (await txt(page, '#f-rk-note')).slice(0, 70));

  const after = await page.evaluate((code) => {
    const tr = [...document.querySelectorAll('#f-rk-rows tr[onclick]')]
      .find(t => (t.getAttribute('onclick') || '').includes(`'${code}'`));
    const tds = [...tr.querySelectorAll('td')];
    const sel = tr.querySelector('select.f-rk-mon');
    const pctCell = tds[6].querySelector('span');
    return {
      basis: tds[4].textContent.replace(/\s+/g, ' ').trim(),
      pct: tds[6].textContent.trim(),
      pctTitle: pctCell ? (pctCell.getAttribute('title') || '') : '',
      vr: tds[12].textContent.trim(),
      term: tds[15].textContent.replace(/\s+/g, ' ').trim(),
      style: tr.getAttribute('style') || '',
      selVal: sel.value,
    };
  }, target.code);

  ck(after.selVal === pick, '下拉停在所选月份', after.selVal);
  ck(after.style.includes('inset 3px 0 0'), '★ 该行有"月份口径"视觉标记（看得出它已换口径）', after.style.slice(0, 50));
  ck(after.basis !== target.basis, '★ 基差已按该月重算', `${target.basis} → ${after.basis}`);
  ck(after.vr !== target.vr, '★ 虚实盘比已按该月持仓重算', `${target.vr} → ${after.vr}`);
  ck(after.pct === '—', '★ 基差分位如实显示「—」（该月无历史序列）', after.pct);
  ck(/历史序列/.test(after.pctTitle), '分位的「—」带说明（悬停可见原因）', after.pctTitle.slice(0, 46));
  ck(new RegExp(`自 ${pick} 看:`).test(after.term) || /自 \d{4} 看:/.test(after.term),
    '★ 「自该月看」的期限结构读数已出现（原票另起读数）', after.term.slice(0, 60));
  ck(/默认\(主力\)/.test(await page.evaluate(() =>
    [...document.querySelectorAll('#f-rk-rows tr[onclick] select.f-rk-mon option')][0].textContent)),
    '空选项已改名为「默认(主力)」');
  ck(await vis(page, `#f-rk-exp-${target.code}`), '行内合约明细仍展开（重算后未被清掉）');

  // 恢复默认
  await page.evaluate(({ code }) => {
    const tr = [...document.querySelectorAll('#f-rk-rows tr[onclick]')]
      .find(t => (t.getAttribute('onclick') || '').includes(`'${code}'`));
    const sel = tr.querySelector('select.f-rk-mon');
    sel.value = '';
    sel.dispatchEvent(new Event('change', { bubbles: true }));
  }, { code: target.code });
  await page.waitForTimeout(900);
  const restored = await page.evaluate((code) => {
    const tr = [...document.querySelectorAll('#f-rk-rows tr[onclick]')]
      .find(t => (t.getAttribute('onclick') || '').includes(`'${code}'`));
    const tds = [...tr.querySelectorAll('td')];
    return { basis: tds[4].textContent.replace(/\s+/g, ' ').trim(), pct: tds[6].textContent.trim(),
             style: tr.getAttribute('style') || '', term: tds[15].textContent.replace(/\s+/g, ' ').trim() };
  }, target.code);
  const noteNow = await txt(page, '#f-rk-note');
  ck(restored.basis === target.basis, '★ 选「默认(主力)」后基差**还原**成默认口径', `${restored.basis}`);
  ck(!restored.style.includes('inset 3px 0 0'), '还原后月份口径标记消失');
  ck(!/自 \d{4} 看:/.test(restored.term), '还原后「自该月看」读数消失', restored.term.slice(0, 40));
  ck(/恢复默认/.test(noteNow), '提示文案讲明已恢复默认', noteNow.slice(0, 50));
}

console.log('');
console.log('=== F. 页面健康 ===');
ck(jsErrors.length === 0, '无 JS 运行时错误', jsErrors.slice(0, 2).join(' | ') || '0');

await page.screenshot({ path: 'probe/_shot_cache_month.png' });
console.log('');
console.log('='.repeat(62));
console.log(`PASS ${P.length} / FAIL ${F.length}`);
if (F.length) { console.log('失败项:'); F.forEach(x => console.log('  ★ ' + x)); }

await browser.close();
process.exit(F.length ? 1 : 0);
