// 数据仓库 → 研报库：选「公众号（微信读书）」筛选，验证徽标与列表。
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const B = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await (await B.newContext({ viewport: { width: 1760, height: 1100 }, deviceScaleFactor: 2 })).newPage();
const errs = [];
page.on('pageerror', e => errs.push(String(e).slice(0, 160)));
page.on('console', m => { if (m.type() === 'error') errs.push(m.text().slice(0, 160)); });

await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="warehouse"]', { timeout: 30000 });
await page.click('nav button[data-tab="warehouse"]');
await page.waitForTimeout(2500);

const opts = await page.evaluate(() =>
  [...document.querySelectorAll('#res-filter-source option')].map(o => ({ v: o.value, t: o.textContent.trim() })));
console.log('  来源下拉:', JSON.stringify(opts));

const picked = await page.evaluate(async () => {
  const s = document.getElementById('res-filter-source');
  s.value = '__wechat';
  s.dispatchEvent(new Event('change'));
  await new Promise(r => setTimeout(r, 2600));
  const list = document.getElementById('res-list');
  const items = [...list.querySelectorAll('.debate-round')];
  const badge = t => (t.textContent || '').includes('公众号');
  return {
    count: items.length,
    withBadge: items.filter(badge).length,
    countText: (document.getElementById('res-count') || {}).textContent || '',
    firstThree: items.slice(0, 3).map(x => (x.textContent || '').replace(/\s+/g, ' ').slice(0, 90)),
  };
});
console.log('  筛选后:', JSON.stringify(picked, null, 0).slice(0, 600));

// 截图：先滚到第一条，再**整视口**截（clip 用 getBoundingClientRect 的坐标
//   在滚动后可能落到可视区外，导致 "Clipped area is empty"）。
await page.evaluate(() => {
  const el = document.querySelector('#res-list .debate-round');
  if (el) el.scrollIntoView({ block: 'center' });
});
await page.waitForTimeout(700);
await page.screenshot({ path: 'probe/_shot_warehouse_wechat.png' });
console.log('  截图 → probe/_shot_warehouse_wechat.png');
console.log('  JS 错误:', errs.length ? errs.slice(0, 3) : '无');
await B.close();
