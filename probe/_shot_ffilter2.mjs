import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';
const B = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const ctx = await B.newContext({ viewport: { width: 1760, height: 1150 } });
const page = await ctx.newPage();
await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="fund"]', { timeout: 30000 });
await page.click('nav button[data-tab="fund"]');
for (let i = 0; i < 120; i++) {
  const n = await page.evaluate(() => document.querySelectorAll('#f-rk-rows tr[onclick]').length);
  if (n >= 21) break;
  await page.waitForTimeout(1000);
}
await page.evaluate(() => { App._fRkAllCols(); App._fRkClearFilters(); App._fRkSort = { key: 'net', order: 'desc' }; App._renderFundRanking(); });
await page.waitForTimeout(300);

// 动态挑一个**非空**的双条件组合（优先基本面特色：口径关系 / 库存档位）
const pick = await page.evaluate(() => {
  const cov = (k) => {
    const m = {};
    App._fRkData.rows.forEach(r => { const v = r[k]; if (v) m[v] = (m[v] || 0) + 1; });
    return m;
  };
  const mode = cov('inv_mode');
  const g = cov('inv_grade');
  return { mode, inv: g, n: App._fRkData.rows.length };
});
console.log('口径关系分布:', JSON.stringify(pick.mode));
console.log('库存档位分布:', JSON.stringify(pick.inv));

// ① 单维度：口径关系 = 口径背离(库存转移迹象)
await page.evaluate(() => {
  const k = Object.keys(App._fRkFilter);
  App._fRkClearFilters();
  const sel = document.querySelector('#f-rk-dims select[data-dim="inv_mode_kind"]');
  sel.value = '口径背离';
  sel.dispatchEvent(new Event('change'));
});
await page.waitForTimeout(500);
let n1 = await page.evaluate(() => document.querySelectorAll('#f-rk-rows tr[onclick]').length);
let f1 = await page.evaluate(() => (document.getElementById('f-rk-fcount') || {}).textContent || '');
console.log('筛选[口径关系=口径背离] → %d 行 | %s', n1, f1);
await page.screenshot({ path: 'probe/_shot_ffilter_filtered.png' });

// ② 双条件叠加：板块=能化 + 库存综合=低
await page.evaluate(() => {
  App._fRkClearFilters();
  const b = [...document.querySelectorAll('#f-rk-pools button')].find(x => x.textContent.trim() === '能化');
  App._fRkFilter.pool = b ? '能化' : '全部品种';
  const s1 = document.querySelector('#f-rk-dims select[data-dim="inv_grade"]');
  if (s1) { s1.value = '低'; s1.dispatchEvent(new Event('change')); }
});
await page.waitForTimeout(500);
let n2 = await page.evaluate(() => document.querySelectorAll('#f-rk-rows tr[onclick]').length);
let f2 = await page.evaluate(() => (document.getElementById('f-rk-fcount') || {}).textContent || '');
console.log('筛选[能化 + 库存低] → %d 行 | %s', n2, f2);
await page.screenshot({ path: 'probe/_shot_ffilter_two.png' });
await B.close();
