// 交付截图：⑨ 基本面截面表的「气泡筛选 + 定期刷新」
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const B = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const ctx = await B.newContext({ viewport: { width: 1760, height: 1200 } });
const page = await ctx.newPage();
await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="fund"]', { timeout: 30000 });
await page.click('nav button[data-tab="fund"]');
for (let i = 0; i < 150; i++) {
  const n = await page.evaluate(() => document.querySelectorAll('#f-rk-rows tr[onclick]').length);
  if (n >= 21) break;
  await page.waitForTimeout(1000);
}
await page.evaluate(() => { App._fRkClearFilters(); App._fRkSort = { key: 'net', order: 'desc' }; App._renderFundRanking(); });
await page.waitForTimeout(600);

// ① 全景：气泡筛选区 + 进度条（"每 15 分钟自动刷新（下次 HH:MM）"）+ 全表
const pg = await page.evaluate(() => (document.getElementById('f-rk-progress') || {}).textContent || '');
console.log('进度条:', pg);
await page.screenshot({ path: 'probe/_shot_frk_pills.png' });

// ② 用气泡筛一遍（库存综合=低）—— 展示气泡选中态 + 筛选生效
await page.evaluate(() => {
  const b = [...document.querySelectorAll('#f-rk-dims .f-rk-pill[data-dim="inv_grade"]')]
    .find(x => x.dataset.val === '低');
  if (b) b.click();
});
await page.waitForTimeout(500);
const n2 = await page.evaluate(() => document.querySelectorAll('#f-rk-rows tr[onclick]').length);
const fc = await page.evaluate(() => (document.getElementById('f-rk-fcount') || {}).textContent || '');
console.log('筛「库存综合=低」→ %d 行 | %s', n2, fc);
const box = await page.locator('#f-rk-dims').boundingBox();
await page.screenshot({
  path: 'probe/_shot_frk_pills_filtered.png',
  clip: { x: 0, y: Math.max(0, box.y - 120), width: 1760, height: Math.min(700, 1200 - box.y + 120) },
});

// ③ 点「拉取/刷新」后立刻抓"后台刷新中"进度条 + 旧表仍在
await page.evaluate(() => App._fRkClearFilters());
await page.waitForTimeout(400);
await page.click('button[onclick="App.loadFundRanking(1)"]');
let hit = null;
for (let i = 0; i < 60; i++) {
  const s = await page.evaluate(() => ({
    refreshing: !!((App._fRkData || {}).refreshing),
    rows: document.querySelectorAll('#f-rk-rows tr[onclick]').length,
    pg: (document.getElementById('f-rk-progress') || {}).textContent || '',
  }));
  if (s.refreshing) { hit = s; break; }
  await page.waitForTimeout(400);
}
console.log('刷新中:', hit ? (hit.pg.slice(0, 70) + ' | 表 ' + hit.rows + ' 行') : '（未抓到）');
await page.screenshot({ path: 'probe/_shot_frk_refreshing.png' });
console.log('已保存 3 张');
await B.close();
