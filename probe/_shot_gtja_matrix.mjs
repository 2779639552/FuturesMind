// 取证截图: 国君数据页「覆盖矩阵」现在的落位
//  - a) 顶部: 控制条 + 紧随其下的覆盖矩阵卡(都在顶层, 矩阵已可折叠)
//  - b) 滚动 700px 后: 控制条仍吸顶, 覆盖矩阵已随页面滚走(不再遮挡下方模块)
// 用法: node probe/_shot_gtja_matrix.mjs
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = process.argv[2] || 'http://127.0.0.1:5000';
const OUT = 'D:/AgentSense/AgentSense/probe/';

const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(2200);

await page.evaluate(() => App.switchTab('gtja'));
await page.waitForTimeout(2600);

// 展开所有折叠块, 让矩阵与后续模块都可见
await page.evaluate(async () => {
  document.querySelectorAll('#tab-gtja .sec-block.is-collapsed').forEach(b => {
    b.classList.remove('is-collapsed'); b.classList.add('sec-open'); b.style.maxHeight = 'none';
  });
  window.scrollTo(0, 0);
  await new Promise(r => setTimeout(r, 300));
});
await page.screenshot({ path: OUT + '_shot_gtja_top.png' });

const top = await page.evaluate(() => {
  const pt = document.querySelector('#tab-gtja > .page-tools');
  const card = document.getElementById('gt-matrix').closest('.card');
  const blk = card.closest('.sec-block');
  return {
    ptH: Math.round(pt.getBoundingClientRect().height),
    ptCards: pt.querySelectorAll(':scope > .card').length,
    inPageTools: !!card.closest('.page-tools'),
    inBlock: !!blk,
    cardTop: Math.round(card.getBoundingClientRect().top),
    pos: getComputedStyle(card).position,
  };
});
console.log('顶部态:', JSON.stringify(top));

// 滚动后
await page.evaluate(async () => {
  window.scrollTo(0, 700);
  await new Promise(r => setTimeout(r, 400));
});
await page.screenshot({ path: OUT + '_shot_gtja_scrolled.png' });

const sc = await page.evaluate(() => {
  const pt = document.querySelector('#tab-gtja > .page-tools');
  const card = document.getElementById('gt-matrix').closest('.card');
  return {
    ptTop: Math.round(pt.getBoundingClientRect().top),
    cardTop: Math.round(card.getBoundingClientRect().top),
  };
});
console.log('滚动 700px 后:', JSON.stringify(sc));

await browser.close();
console.log('\n截图: _shot_gtja_top.png / _shot_gtja_scrolled.png');
