// 合并后取证截图: ③ 一张卡内 「总览表 → 走势图 → 明细数据」纵向连贯
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1250 } });
await page.goto(process.argv[2] || 'http://127.0.0.1:5000', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(2000);
await page.evaluate(() => App.switchTab('gtja'));

for (let i = 0; i < 45; i++) {
  await page.waitForTimeout(2000);
  const ok = await page.evaluate(() => {
    const el = document.getElementById('gt-indicators');
    return !!el && el.querySelectorAll('tbody tr').length > 0;
  });
  if (ok) break;
}
// 展开合并卡 + 点第一行「查看」
await page.evaluate(async () => {
  const card = document.getElementById('gt-indicators').closest('.card');
  const blk = card.closest('.sec-block');
  if (blk) { blk.classList.remove('is-collapsed'); blk.classList.add('sec-open'); blk.style.maxHeight = 'none'; }
  await new Promise(r => setTimeout(r, 200));
  const b = document.querySelector('#gt-indicators tbody tr button');
  if (b) b.click();
  await new Promise(r => setTimeout(r, 800));
  // 滚到合并卡顶部
  card.scrollIntoView({ block: 'start' });
  await new Promise(r => setTimeout(r, 400));
});
await page.screenshot({ path: 'D:/AgentSense/AgentSense/probe/_shot_gtja_merged.png' });
console.log('done');
await browser.close();
