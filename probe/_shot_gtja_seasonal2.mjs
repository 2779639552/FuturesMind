// 截图: 季节性图在亮色主题 + 多指标下的实际观感(2026-10-19)
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = process.argv[2] || 'http://127.0.0.1:5000';
const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 2 });
await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(2500);

// 切到亮色(先读当前主题, 只在暗色时点一次)
const theme0 = await page.evaluate(() => document.documentElement.getAttribute('data-theme'));
if (theme0 === 'dark') {
  await page.click('button.theme-toggle');
  await page.waitForTimeout(800);
}
console.log('theme =', await page.evaluate(() => document.documentElement.getAttribute('data-theme')));

await page.click('button[data-tab="warehouse"]');
await page.waitForTimeout(1200);
// 多点几个指标, 覆盖多年口径(库存 5 年 / 利润 5 年 / 仓单)
const idx = await page.evaluate(() => {
  const rows = [...document.querySelectorAll('#gt-indicators tbody tr')];
  const find = t => rows.findIndex(tr => tr.textContent.includes(t));
  return { 库存: find('社会库存'), 利润: find('加工利润'), 仓单: find('注册仓单'), 基差: find('现货价') };
});
console.log('rows', JSON.stringify(idx));

async function shot(name, i) {
  if (i < 0) { console.log('skip', name); return; }
  await page.evaluate(k => App.showGtjaIndicator(k), i);
  await page.waitForTimeout(5000);  // 等多年历史回填
  const info = await page.evaluate(() => {
    const box = document.getElementById('gt-chart');
    const inst = window.echarts.getInstanceByDom(box);
    const o = inst.getOption();
    return {
      h: box.clientHeight,
      legend: (o.legend && o.legend[0] ? o.legend[0].data : []) || [],
      note: (document.getElementById('gt-chart-note') || {}).textContent.slice(0, 80),
    };
  });
  console.log(name, JSON.stringify(info));
  const box = await page.evaluate(() => {
    const r = document.getElementById('gt-chart').closest('.card').getBoundingClientRect();
    return { x: Math.max(0, r.x), y: Math.max(0, r.y), width: Math.min(1600, r.width), height: Math.min(1000, r.height) };
  });
  await page.screenshot({ path: `probe/_shot_season_${name}.png`, clip: box });
}

await shot('basis_light', idx['基差']);
await shot('inv_light', idx['库存']);
await shot('profit_light', idx['利润']);
await browser.close();
