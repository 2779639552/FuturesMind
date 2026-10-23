// 前端整体现状巡检截图(2026-10-19): 7 个 tab 各拍首屏
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';
const BASE = process.argv[2] || 'http://127.0.0.1:5000';
const browser = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(2500);
// 统一亮色
const t = await page.evaluate(() => document.documentElement.getAttribute('data-theme'));
if (t === 'dark') { await page.click('button.theme-toggle'); await page.waitForTimeout(700); }
const tabs = await page.$$eval('nav button[data-tab]', els => els.map(e => e.dataset.tab).filter(Boolean));
console.log('tabs', JSON.stringify(tabs));
for (const id of tabs) {
  await page.click(`nav button[data-tab="${id}"]`);
  await page.waitForTimeout(id === 'warehouse' ? 5000 : 3000);
  await page.screenshot({ path: `probe/_ui_${id}.png` });
  const h = await page.evaluate(() => document.documentElement.scrollHeight);
  console.log(id, 'scrollHeight', h);
}
await browser.close();
