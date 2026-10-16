// 最终确认：K 线标题是否如实标注数据源 + 自动刷新是否在推进（2026-10-14）
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = 'http://127.0.0.1:5000';
const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const ctx = await browser.newContext({ viewport: { width: 1680, height: 1000 } });
const page = await ctx.newPage();
let reqs = 0;
page.on('request', r => { if (r.url().includes('/api/quant/kline')) reqs++; });

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="quant"]', { timeout: 30000 });
await page.click('nav button[data-tab="quant"]');
await page.waitForTimeout(1500);

let code = null;
for (let i = 0; i < 40; i++) {
  code = await page.evaluate(() => {
    const tr = document.querySelector('#q-ov-rows tr');
    return tr ? tr.innerText.trim().split(/\s+/)[0] : null;
  });
  if (code && /^[A-Z]{1,3}$/.test(code)) break;
  await page.waitForTimeout(3000);
}
await page.evaluate(() => document.querySelector('#q-ov-rows tr').click());
await page.waitForTimeout(2000);

// 切 1 分钟
await page.evaluate(() => {
  const s = document.getElementById('q-period');
  s.value = '1';
  s.dispatchEvent(new Event('change', { bubbles: true }));
});
for (let i = 0; i < 40; i++) {
  const w = await page.evaluate(() => { const c = document.querySelector('#chart-q-kline canvas'); return c ? c.width : 0; });
  if (w > 0) break;
  await page.waitForTimeout(1000);
}
await page.waitForTimeout(2000);

const readTitle = () => page.evaluate(() => (document.getElementById('q-kline-title') || {}).textContent || '(空)');
const t1 = await readTitle();
console.log(`[1] K 线标题 = "${t1}"`);
console.log(`    （是否标注数据源: ${/天勤|新浪/.test(t1) ? '是 ✔' : '否 ✘'}）`);

const r1 = reqs;
console.log('\n[2] 等 65s，看自动刷新是否再发请求…');
await page.waitForTimeout(65000);
console.log(`    新增 kline 请求 = ${reqs - r1}（期望 ≥2，周期 30s）`);
const t2 = await readTitle();
console.log(`[3] 65s 后标题 = "${t2}"`);

await page.screenshot({ path: 'probe/_shot_kline_fixed.png' });
console.log('[4] 已截图 probe/_shot_kline_fixed.png');

await browser.close();
