// 最终验证: 覆盖矩阵删除 + 剩余两卡平铺
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';
const b = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const page = await b.newPage({ viewport: { width: 1600, height: 1000 } });
const errs = []; page.on('pageerror', e => errs.push(e.message));
await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(2500);
await page.evaluate(() => App.switchTab('gtja'));
await page.waitForTimeout(4000);

const r = await page.evaluate(() => {
  const tab = document.getElementById('tab-gtja');
  const out = {};
  // ① 覆盖矩阵彻底消失(容器/标题/方法/索引项)
  out.matrixGone = !document.getElementById('gt-matrix')
    && !Array.from(tab.querySelectorAll('*')).some(x => (x.textContent || '').includes('覆盖矩阵'))
    && !Array.from(tab.querySelectorAll('.sec-index-item')).some(x => (x.textContent || '').includes('覆盖矩阵'));
  // ② 索引项 = 2 (四源 + 指标总览)
  out.indexItems = Array.from(tab.querySelectorAll('.sec-index-item')).map(x => x.textContent.trim().slice(0, 30));
  // ③ 折叠块: 都平铺展开(sec-open)
  out.blocks = Array.from(tab.querySelectorAll('.sec-block')).map(b => ({
    label: (b.querySelector('h3, .sec-label')?.textContent || b.innerText || '').trim().slice(0, 30),
    open: b.classList.contains('sec-open'),
    collapsed: b.classList.contains('is-collapsed'),
  }));
  // ④ 内容就位
  out.sourcesOk = (document.getElementById('gt-sources')?.innerText || '').length > 20;
  out.indicatorsOk = (document.getElementById('gt-indicators')?.innerText || '').length > 20;
  out.idxVer = App._SEC_IDX_VER;
  return out;
});
console.log(JSON.stringify(r, null, 2));
console.log('JS 报错:', errs.length ? errs.slice(0, 3).join(' | ') : '(无)');
await b.close();
