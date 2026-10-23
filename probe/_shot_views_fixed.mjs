import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';
const B = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const page = await (await B.newContext({ viewport: { width: 1760, height: 1100 }, deviceScaleFactor: 2 })).newPage();
const sleep = ms => new Promise(r => setTimeout(r, ms));
await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="research"]', { timeout: 30000 });
await page.click('nav button[data-tab="research"]');
await sleep(3500);
// 切到周报口径（用户报的是 10.14 周度）并选 2026-10-11
const ok = await page.evaluate(async () => {
  const t = document.getElementById('res-daily-type');
  if (t) { t.value = '周报'; t.dispatchEvent(new Event('change')); }
  await new Promise(r => setTimeout(r, 2500));
  const d = document.getElementById('res-daily-date');
  if (d) { const o = [...d.options].find(x => x.value === '2026-10-11'); if (o) { d.value = '2026-10-11'; d.dispatchEvent(new Event('change')); } }
  await new Promise(r => setTimeout(r, 2500));
  return { type: t ? t.value : null, date: d ? d.value : null };
});
console.log('  口径:', JSON.stringify(ok));
await sleep(2500);
const clip = await page.evaluate(() => {
  const t = [...document.querySelectorAll('.report-content table')].find(x => {
    const ths = [...x.querySelectorAll('thead th')].map(h => (h.textContent || '').trim());
    return ths.includes('方向') && ths.includes('置信度');
  });
  if (!t) return null;
  t.scrollIntoView({ block: 'center' });
  return null;
});
await sleep(900);
const box = await page.evaluate(() => {
  const t = [...document.querySelectorAll('.report-content table')].find(x => {
    const ths = [...x.querySelectorAll('thead th')].map(h => (h.textContent || '').trim());
    return ths.includes('方向') && ths.includes('置信度');
  });
  if (!t) return null;
  const r = t.getBoundingClientRect();
  return { x: Math.max(0, r.x - 12), y: Math.max(0, r.y - 56), width: Math.min(r.width + 24, window.innerWidth - 8), height: Math.min(r.height + 26, 720) };
});
console.log('  clip:', JSON.stringify(box));
if (box) await page.screenshot({ path: 'probe/_shot_views_fixed.png', clip: box });
// 程序化核对：无省略号 + 方向/置信度可视化仍在
const stat = await page.evaluate(() => {
  const t = [...document.querySelectorAll('.report-content table')].find(x => {
    const ths = [...x.querySelectorAll('thead th')].map(h => (h.textContent || '').trim());
    return ths.includes('方向') && ths.includes('置信度');
  });
  if (!t) return null;
  const rows = [...t.querySelectorAll('tbody tr')];
  let ell = 0, lens = [];
  rows.forEach(tr => {
    const c = tr.children[5];
    if (c) { const x = (c.textContent || '').trim(); lens.push(x.length); if (x.includes('…')) ell++; }
  });
  return { rows: rows.length, ell, avg: lens.length ? Math.round(lens.reduce((a, b) => a + b, 0) / lens.length) : 0,
           chips: t.querySelectorAll('.dir-chip').length, bars: t.querySelectorAll('.conf-bar').length };
});
console.log('  统计:', JSON.stringify(stat));
await B.close();
