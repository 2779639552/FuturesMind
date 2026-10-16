// dump 国君 tab 完整结构（统计各类元素）
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';
const b = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const page = await b.newPage({ viewport: { width: 1600, height: 1000 } });
await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(2500);
await page.evaluate(() => App.switchTab('gtja'));
await page.waitForTimeout(4000);
const r = await page.evaluate(() => {
  const tab = document.getElementById('tab-gtja');
  const q = sel => tab.querySelectorAll(sel).length;
  const out = {
    childCount: tab.children.length,
    secBlock: q('.sec-block'),
    secIndex: q('.sec-index'),
    card: q('.card'),
    gtSources: !!document.getElementById('gt-sources'),
    gtInd: !!document.getElementById('gt-indicators'),
    gtMatrix: !!document.getElementById('gt-matrix'),
    innerHTMLHead: tab.innerHTML.slice(0, 2500),
  };
  return out;
});
console.log(JSON.stringify(r, null, 2));
await b.close();
