// 复现: 研报问答的品种下拉是否被静默改成池内首项
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';
const b = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const page = await b.newPage({ viewport: { width: 1600, height: 1000 } });
const errs = []; page.on('pageerror', e => errs.push(e.message));
await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(2500);
await page.evaluate(() => App.switchTab('research'));
await page.waitForTimeout(2500);

const r = await page.evaluate(() => {
  const out = {};
  for (const id of ['res-ask-variety', 'res-filter-variety', 'res-views-variety', 'event-variety', 'res-upload-variety']) {
    const el = document.getElementById(id);
    out[id] = el ? { value: el.value, opt0: el.options[0] ? el.options[0].value : null,
                     optCount: el.options.length } : '缺失';
  }
  out._poolFirst = (App._varieties && App._varieties[0]) ? App._varieties[0].code : null;
  out._defaultCode = App._defaultCode();
  out.qaState = App._tabVariety ? JSON.stringify(App._tabVariety).slice(0, 300) : null;
  return out;
});
console.log(JSON.stringify(r, null, 2));
console.log('JS 报错:', errs.length ? errs.slice(0,3).join(' | ') : '(无)');
await b.close();
