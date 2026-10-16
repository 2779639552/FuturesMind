import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1080 } });
await page.goto(process.argv[2] || 'http://127.0.0.1:5000', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(2000);
await page.evaluate(() => App.switchTab('gtja'));

for (let i = 0; i < 40; i++) {
  await page.waitForTimeout(2000);
  const ok = await page.evaluate(() => {
    const b = document.getElementById('gt-matrix');
    return b && b.querySelectorAll('tbody tr').length > 0;
  });
  if (ok) break;
}
await page.evaluate(async () => { window.scrollTo(0, 0); await new Promise(r => setTimeout(r, 300)); });
await page.screenshot({ path: 'D:/AgentSense/AgentSense/probe/_shot_gtja_matrix_data.png' });
console.log('done');
await browser.close();
