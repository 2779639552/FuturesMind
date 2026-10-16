// 量 #tab-gtja 的直接子元素: 类名 / 高度 / 是否含覆盖矩阵 / 折叠态
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1080 } });
await page.goto(process.argv[2] || 'http://127.0.0.1:5000', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(2000);
await page.evaluate(() => App.switchTab('gtja'));
await page.waitForTimeout(6000);

const dump = await page.evaluate(() => {
  const tab = document.getElementById('tab-gtja');
  const out = [];
  for (const k of tab.children) {
    const r = k.getBoundingClientRect();
    const h3 = k.querySelector('h3');
    const hasMatrix = !!k.querySelector('#gt-matrix') || (k.id === 'gt-matrix');
    out.push({
      tag: k.tagName,
      id: k.id || '',
      cls: (k.className || '').toString(),
      h: Math.round(r.height),
      top: Math.round(r.top),
      title: h3 ? h3.textContent.trim().slice(0, 24) : '',
      hasMatrix,
      mh: k.style.maxHeight || '',
    });
  }
  const mm = document.getElementById('gt-matrix');
  const card = mm ? mm.closest('.card') : null;
  const blk = card ? card.closest('.sec-block') : null;
  return {
    children: out,
    matrixCardExists: !!card,
    matrixCardH: card ? Math.round(card.getBoundingClientRect().height) : -1,
    matrixCardTop: card ? Math.round(card.getBoundingClientRect().top) : -1,
    blockCls: blk ? blk.className : '(none)',
    blockH: blk ? Math.round(blk.getBoundingClientRect().height) : -1,
    blockMaxH: blk ? (blk.style.maxHeight || '') : '',
    rows: mm ? mm.querySelectorAll('tbody tr').length : -1,
    matrixText: mm ? mm.textContent.trim().slice(0, 60) : '(no el)',
    docScrollH: document.documentElement.scrollHeight,
  };
});
console.log(JSON.stringify(dump, null, 2));
await browser.close();
