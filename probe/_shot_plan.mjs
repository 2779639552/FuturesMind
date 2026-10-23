import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';
const p = 'file:///D:/AgentSense/AgentSense/docs/frontend-redesign-plan-2026-10-19.html';
const browser = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox','--allow-file-access-from-files'] });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
const errs = [];
page.on('pageerror', e => errs.push(String(e)));
page.on('requestfailed', r => errs.push('FAILED ' + r.url()));
await page.goto(p, { waitUntil: 'load' });
await page.waitForTimeout(800);
console.log('height', await page.evaluate(() => document.documentElement.scrollHeight));
console.log('imgs', await page.evaluate(() => [...document.images].map(i => i.naturalWidth + 'x' + i.naturalHeight).join(',')));
for (const [name, y] of [['top', 0], ['audit', 900], ['system', 2600], ['ia', 4400], ['pages', 5600], ['road', 7600], ['effect', 8800]]) {
  await page.evaluate(v => window.scrollTo(0, v), y);
  await page.waitForTimeout(200);
  await page.screenshot({ path: `probe/_plan_${name}.png` });
}
console.log('errors', errs.slice(0,5));
await browser.close();
