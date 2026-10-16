// dump 国君 tab 顶层结构
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';
const b = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const page = await b.newPage({ viewport: { width: 1600, height: 1000 } });
await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(2500);
await page.evaluate(() => App.switchTab('gtja'));
await page.waitForTimeout(3500);
const r = await page.evaluate(() => {
  const tab = document.getElementById('tab-gtja');
  const out = [];
  for (const el of tab.children) {
    out.push({
      tag: el.tagName,
      cls: (el.className || '').toString().slice(0, 60),
      id: el.id || '',
      h3: (el.querySelector(':scope > h3')?.textContent || '').trim().slice(0, 40),
      text: (el.innerText || '').slice(0, 50).replace(/\n/g, ' '),
    });
  }
  return { children: out, secIdxExists: !!tab.querySelector('.sec-index') };
});
console.log(JSON.stringify(r, null, 2));
await b.close();
