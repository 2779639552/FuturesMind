// 检查走势图 canvas 的实际渲染尺寸(是否 0×0 空白)
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const browser = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const jsErrors = [];
page.on('pageerror', e => jsErrors.push(String(e).slice(0, 200)));

try {
  await page.goto('http://127.0.0.1:5000/', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(3000);
  await page.click('button[data-tab="gtja"]');
  await page.waitForTimeout(6000);

  // 点击第一个「查看」
  await page.evaluate(() => document.querySelector('#gt-indicators button')?.click());
  await page.waitForTimeout(2500);

  const info = await page.evaluate(() => {
    const chart = document.getElementById('gt-chart');
    const cvs = chart.querySelectorAll('canvas');
    const canvasInfo = Array.from(cvs).map(c => {
      const r = c.getBoundingClientRect();
      return { w: c.width, h: c.height, cssW: r.width, cssH: r.height };
    });
    // 检查 chart 所在卡是否被折叠(sec-block)
    const card = chart.closest('.sec-block');
    return {
      canvasInfo,
      chartChildren: chart.children.length,
      cardClass: card ? card.className : null,
      cardH: card ? card.getBoundingClientRect().height : null,
      chartHTML: chart.innerHTML.slice(0, 200),
    };
  });
  console.log('走势图 canvas 尺寸:', JSON.stringify(info, null, 2));
  console.log('JS 错误:', jsErrors.length, jsErrors.join(';'));
} catch (e) {
  console.log('异常:', String(e).slice(0, 300));
} finally {
  await browser.close();
}
