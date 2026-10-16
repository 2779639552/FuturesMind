// 总览页实拍：进度条应显示「计算完成 · 21 个品种 · 缓存 · 每 15 分钟自动刷新（下次 HH:MM）」
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';
const b = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const ctx = await b.newContext({ viewport: { width: 1680, height: 1050 } });
const p = await ctx.newPage();
await p.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded' });
await p.waitForSelector('nav button[data-tab="quant"]', { timeout: 30000 });
await p.click('nav button[data-tab="quant"]');
let txt = '';
for (let i = 0; i < 90; i++) {
  txt = await p.evaluate(() => (document.getElementById('q-ov-progress') || {}).textContent || '');
  if (txt.includes('计算完成')) break;
  await p.waitForTimeout(2000);
}
console.log('进度条文案:', txt);
await p.screenshot({ path: 'probe/_shot_quant_overview_refresh.png' });
await b.close();
