// dump 国君 tab innerHTML 尾部 + 各元素 parent 链
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';
const b = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const page = await b.newPage({ viewport: { width: 1600, height: 1000 } });
await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(2500);
await page.evaluate(() => App.switchTab('gtja'));
await page.waitForTimeout(4000);
const r = await page.evaluate(() => {
  const tab = document.getElementById('tab-gtja');
  const out = { tail: tab.innerHTML.slice(-1800) };
  for (const id of ['gt-sources', 'gt-indicators']) {
    const el = document.getElementById(id);
    const chain = [];
    let p = el;
    while (p && chain.length < 6) {
      chain.push({ tag: p.tagName, cls: (p.className || '').toString().slice(0, 50), id: p.id || '' });
      p = p.parentElement;
    }
    out[id] = { exists: !!el, chain };
  }
  // 看 dataset.secIndex
  out.datasetSecIndex = tab.dataset.secIndex || null;
  return out;
});
console.log(JSON.stringify(r, null, 2));
await b.close();
