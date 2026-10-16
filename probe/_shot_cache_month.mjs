// 交付实拍（2026-10-16）：① 总览「后台刷新中」但仍显示旧表 ② 基本面截面切到月份口径
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const b = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const ctx = await b.newContext({ viewport: { width: 1760, height: 1000 } });
const p = await ctx.newPage();
async function waitFor(fn, ms = 300000, step = 700) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (await fn()) return true; await p.waitForTimeout(step); }
  return false;
}

await p.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded' });
await p.waitForSelector('nav button[data-tab="quant"]', { timeout: 30000 });
await p.click('nav button[data-tab="quant"]');
await waitFor(async () => (await p.evaluate(() => document.querySelectorAll('#q-ov-rows tr').length)) >= 21);
await waitFor(async () => /计算完成/.test(await p.evaluate(() => (document.getElementById('q-ov-progress') || {}).textContent || '')));
// 触发后台刷新，抓「旧表仍在 + 后台刷新中」
await p.evaluate(() => App.loadQuantOverview(1));
await p.waitForTimeout(1500);
const prog = await p.evaluate(() => (document.getElementById('q-ov-progress') || {}).textContent || '');
const rowsN = await p.evaluate(() => document.querySelectorAll('#q-ov-rows tr').length);
console.log('刷新中状态:', prog.slice(0, 70), '| 表格行数', rowsN);
// 【为什么用进度条自身的 y 作基准】第一版只按表格上边 -96px 裁 ⇒ 把「后台刷新中…」
//   那行(整张图最有说服力的一句)裁掉了。改为以 #q-ov-progress 的位置往上留 60px。
const pgBox = await p.locator('#q-ov-progress').boundingBox();
const box = await p.locator('#q-ov-table').boundingBox();
await p.screenshot({
  path: 'probe/_shot_ov_refreshing.png',
  clip: { x: Math.max(0, box.x - 30), y: Math.max(0, (pgBox ? pgBox.y : box.y) - 60),
          width: Math.min(box.width + 60, 1760), height: 620 },
});

// 等刷新完，避免影响后续
await waitFor(async () => !(await p.evaluate(() => !!(App._qovData || {}).refreshing)), 300000);

// ② 基本面截面：切到某月口径
await p.click('nav button[data-tab="fund"]');
await p.waitForSelector('#f-rk-rows', { timeout: 30000 });
await waitFor(async () => (await p.evaluate(() => document.querySelectorAll('#f-rk-rows tr[onclick]').length)) >= 21);
const t = await p.evaluate(() => {
  const trs = [...document.querySelectorAll('#f-rk-rows tr[onclick]')];
  for (const tr of trs) {
    const sel = tr.querySelector('select.f-rk-mon');
    const months = [...sel.options].map(o => o.value).filter(Boolean);
    const tds = [...tr.querySelectorAll('td')];
    if (months.length >= 3 && tds[4].textContent.trim() !== '—') {
      const code = tr.getAttribute('onclick').match(/'([A-Z]+)'/)[1];
      return { code, months, main: sel.value };
    }
  }
  return null;
});
if (t) {
  const pick = t.months.find(m => m !== t.main);
  await p.evaluate(({ code, m }) => {
    const tr = [...document.querySelectorAll('#f-rk-rows tr[onclick]')]
      .find(x => (x.getAttribute('onclick') || '').includes(`'${code}'`));
    const sel = tr.querySelector('select.f-rk-mon');
    sel.value = m;
    sel.dispatchEvent(new Event('change', { bubbles: true }));
  }, { code: t.code, m: pick });
  await waitFor(async () => /已切换到/.test(await p.evaluate(() => (document.getElementById('f-rk-note') || {}).textContent || '')));
  await p.waitForTimeout(900);
  const note = await p.evaluate(() => (document.getElementById('f-rk-note') || {}).textContent || '');
  console.log(`已切到 ${t.code}/${pick}:`, note.slice(0, 80));
  const tb = await p.locator('#f-rk-table').boundingBox();
  await p.screenshot({
    path: 'probe/_shot_month_mode.png',
    clip: { x: Math.max(0, tb.x - 24), y: Math.max(0, tb.y - 40), width: Math.min(tb.width + 48, 1760), height: 430 },
  });
}
await b.close();
console.log('实拍完成');
