// 数据仓库页实拍（四源取数状态已删, 原③升为②）
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';
const b = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
for (const theme of ['dark', 'light']) {
  const ctx = await b.newContext({ viewport: { width: 1680, height: 1050 } });
  const p = await ctx.newPage();
  await p.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded' });
  await p.evaluate(t => { document.documentElement.setAttribute('data-theme', t); }, theme);
  await p.waitForSelector('nav button[data-tab="warehouse"]', { timeout: 30000 });
  await p.click('nav button[data-tab="warehouse"]');
  await p.waitForTimeout(5000);
  await p.screenshot({ path: `probe/_shot_warehouse_trim3_${theme}.png` });
  await ctx.close();
}
console.log('已截图');
await b.close();
