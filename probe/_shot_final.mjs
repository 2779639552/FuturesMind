// 终稿取证: 展开覆盖矩阵 + 滚走的对照
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1040 } });
await page.goto(process.argv[2] || 'http://127.0.0.1:5000', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(2000);
await page.evaluate(() => App.switchTab('gtja'));

// 等矩阵出数据
for (let i = 0; i < 40; i++) {
  await page.waitForTimeout(2000);
  const ok = await page.evaluate(() => {
    const b = document.getElementById('gt-matrix');
    return b && b.querySelectorAll('tbody tr').length > 0;
  });
  if (ok) break;
}

// 展开「覆盖矩阵」那一块（模拟用户点索引标签）
await page.evaluate(async () => {
  const card = document.getElementById('gt-matrix').closest('.card');
  const blk = card.closest('.sec-block');
  if (blk) { blk.classList.remove('is-collapsed'); blk.classList.add('sec-open'); blk.style.maxHeight = 'none'; }
  window.scrollTo(0, 0);
  await new Promise(r => setTimeout(r, 400));
});
await page.screenshot({ path: 'D:/AgentSense/AgentSense/probe/_shot_gtja_matrix_open.png' });

const a = await page.evaluate(() => {
  const pt = document.querySelector('#tab-gtja > .page-tools');
  const card = document.getElementById('gt-matrix').closest('.card');
  return {
    ptH: Math.round(pt.getBoundingClientRect().height),
    ptCards: pt.querySelectorAll(':scope > .card').length,
    matrixRows: document.getElementById('gt-matrix').querySelectorAll('tbody tr').length,
    inPageTools: !!card.closest('.page-tools'),
    inBlock: !!card.closest('.sec-block'),
    cardTop: Math.round(card.getBoundingClientRect().top),
  };
});
console.log('展开态:', JSON.stringify(a));

// 滚动后: 矩阵应滚走, 控制条仍吸顶
await page.evaluate(async () => { window.scrollTo(0, 750); await new Promise(r => setTimeout(r, 400)); });
await page.screenshot({ path: 'D:/AgentSense/AgentSense/probe/_shot_gtja_matrix_scrolled.png' });
const b2 = await page.evaluate(() => {
  const pt = document.querySelector('#tab-gtja > .page-tools');
  const card = document.getElementById('gt-matrix').closest('.card');
  return { ptTop: Math.round(pt.getBoundingClientRect().top), cardTop: Math.round(card.getBoundingClientRect().top) };
});
console.log('滚动 750px 后:', JSON.stringify(b2));
await browser.close();
