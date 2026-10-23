import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const B = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const page = await (await B.newContext({ viewport: { width: 1760, height: 1200 }, deviceScaleFactor: 2 })).newPage();
const sleep = ms => new Promise(r => setTimeout(r, ms));
await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="research"]', { timeout: 30000 });
await page.click('nav button[data-tab="research"]');
await sleep(3000);
await page.evaluate(async () => {
  const t = document.getElementById('res-daily-type');
  if (t) { t.value = '周报'; t.dispatchEvent(new Event('change')); }
  await new Promise(r => setTimeout(r, 2600));
  const d = document.getElementById('res-daily-date');
  if (d) { const o = [...d.options].find(x => x.value === '2026-10-11'); if (o) { d.value = '2026-10-11'; d.dispatchEvent(new Event('change')); } }
  await new Promise(r => setTimeout(r, 2600));
});
await sleep(2500);

// 找到第二节标题并滚到它
const ok = await page.evaluate(() => {
  const h = [...document.querySelectorAll('.report-content h2')].find(x => (x.textContent || '').includes('观点对比与冲突'));
  if (!h) return false;
  h.scrollIntoView({ block: 'start' });
  return true;
});
if (!ok) { console.log('  ★ 未找到第二节标题'); await B.close(); process.exit(1); }
await sleep(800);

// 截「第二节标题 + 前几行」那一块（用元素定位而不是整页）
const box = await page.evaluate(() => {
  const h = [...document.querySelectorAll('.report-content h2')].find(x => (x.textContent || '').includes('观点对比与冲突'));
  const tbl = [...document.querySelectorAll('.report-content table')].find(x => {
    const ths = [...x.querySelectorAll('thead th')].map(t => (t.textContent || '').trim());
    return ths.includes('分歧状态') && ths.includes('各家观点与逻辑');
  });
  const a = h.getBoundingClientRect();
  const b = tbl.getBoundingClientRect();
  // 只取前 4 行的高度，避免截出一整屏
  const rows = [...tbl.querySelectorAll('tbody tr')].slice(0, 4);
  const last = rows[rows.length - 1].getBoundingClientRect();
  return {
    x: Math.max(0, b.x - 12),
    y: Math.max(0, a.y - 8),
    width: Math.min(b.width + 24, window.innerWidth - 8),
    height: Math.min(last.bottom - a.y + 24, window.innerHeight - 20),
  };
});
console.log('  clip: ' + JSON.stringify(box));
await page.screenshot({ path: 'probe/_shot_conflict_zoom.png', clip: box });
console.log('  已保存 probe/_shot_conflict_zoom.png');
await B.close();
