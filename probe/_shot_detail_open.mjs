import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';
const BASE = process.argv[2] || 'http://127.0.0.1:5000';
const browser = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1600, height: 1200 }, deviceScaleFactor: 1.4 });
await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(2200);
await page.click('button[data-tab="warehouse"]');
await page.waitForTimeout(3800);
// 隐藏吸顶 header, 避免整元素截图时压住卡片
await page.addStyleTag({ content: 'header, nav, .header{visibility:hidden!important}' });
const card = page.locator('#gt-detail-expander').locator('xpath=ancestor::div[contains(@class,"card")][1]');
await card.screenshot({ path: 'probe/_shot_detail_collapsed_card.png' });
await page.evaluate(() => { const b = document.querySelectorAll('#gt-indicators button'); if (b[2]) b[2].click(); });
await page.waitForTimeout(1400);
await card.screenshot({ path: 'probe/_shot_detail_open_card.png' });
console.log('shots ok');
await browser.close();
