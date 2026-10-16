// 验证: 覆盖矩阵删除 + 剩余两卡平铺 —— 针对折叠壳结构
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';
const b = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const page = await b.newPage({ viewport: { width: 1600, height: 1000 } });
const errs = []; page.on('pageerror', e => errs.push(e.message));
await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(2500);
await page.evaluate(() => App.switchTab('gtja'));
await page.waitForTimeout(3500);

const r = await page.evaluate(() => {
  const out = {};
  // 覆盖矩阵彻底消失
  out.matrixGone = !document.getElementById('gt-matrix')
    && !Array.from(document.querySelectorAll('#tab-gtja h3, #tab-gtja .sec-chip, #tab-gtja .sec-title, #tab-gtja [class*=chip]'))
      .some(x => (x.textContent || '').includes('覆盖矩阵'));
  // 折叠壳结构: sec-block 列表
  const blocks = Array.from(document.querySelectorAll('#tab-gtja .sec-block'));
  out.blocks = blocks.map(b => ({
    title: (b.querySelector('.sec-title, .sec-head, h3, [class*=title]')?.textContent || b.getAttribute('data-label') || '').trim().slice(0, 40),
    collapsed: b.classList.contains('is-collapsed'),
    innerText: (b.innerText || '').slice(0, 60).replace(/\n/g, ' '),
  }));
  // 四源/指标两卡是否平铺(都展开)
  out.sourcesBlock = blocks.find(b => (b.innerText || '').includes('四源')) 
    ? 'found' : 'not-found';
  out.indBlock = blocks.find(b => (b.innerText || '').includes('指标总览'))
    ? 'found' : 'not-found';
  out.anyCollapsed = blocks.filter(b => b.classList.contains('is-collapsed')).length;
  out.secIndexText = (document.querySelector('#tab-gtja .sec-index')?.innerText || '').replace(/\n+/g, ' | ').slice(0, 300);
  out.idxVer = App._SEC_IDX_VER;
  return out;
});
console.log(JSON.stringify(r, null, 2));
console.log('JS 报错:', errs.length ? errs.slice(0, 3).join(' | ') : '(无)');
await b.close();
