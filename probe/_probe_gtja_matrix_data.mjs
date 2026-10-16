// 实测覆盖矩阵「能真的填上数据」(不是停在"加载中…")
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = process.argv[2] || 'http://127.0.0.1:5000';
const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errs = [];
page.on('pageerror', e => errs.push(e.message));
await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(2000);
await page.evaluate(() => App.switchTab('gtja'));

// 轮询等待矩阵出内容(最多 90s)
let st = null;
for (let i = 0; i < 45; i++) {
  await page.waitForTimeout(2000);
  st = await page.evaluate(() => {
    const box = document.getElementById('gt-matrix');
    const badge = document.getElementById('gt-matrix-badge');
    return {
      loading: /加载中/.test(box ? box.textContent : ''),
      rows: box ? box.querySelectorAll('tbody tr').length : -1,
      bad: box ? box.querySelectorAll('span[style*="red"]').length : -1,
      badge: badge ? badge.textContent.trim() : '(no badge)',
      head: box ? (box.querySelector('thead') ? box.querySelector('thead').textContent.trim() : '') : '',
    };
  });
  if (!st.loading && (st.rows > 0 || st.bad > 0)) break;
}
console.log('矩阵状态:', JSON.stringify(st, null, 2));
console.log('JS 报错:', errs.length ? errs.slice(0, 3).join(' | ') : '(无)');

// 再确认它仍不在 .page-tools 内、且可折叠
const where = await page.evaluate(() => {
  const card = document.getElementById('gt-matrix').closest('.card');
  return {
    inPageTools: !!card.closest('.page-tools'),
    inBlock: !!card.closest('.sec-block'),
    ptCards: document.querySelectorAll('#tab-gtja > .page-tools > .card').length,
  };
});
console.log('落位:', JSON.stringify(where));
await browser.close();
console.log('\n结论:', st && st.rows > 0
  ? `矩阵已出数据 ${st.rows} 行, badge="${st.badge}" —— 移动 DOM 未影响渲染`
  : '⚠️ 矩阵未出数据, 需排查');
