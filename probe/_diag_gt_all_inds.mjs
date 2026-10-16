// 逐个指标点「查看」, 检查走势图渲染情况(找哪些指标可能显示空白)
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

  const n = await page.evaluate(() => document.querySelectorAll('#gt-indicators button').length);
  console.log('指标总数:', n);

  for (let i = 0; i < Math.min(n, 12); i++) {
    await page.evaluate(i => {
      const btns = document.querySelectorAll('#gt-indicators button');
      if (btns[i]) btns[i].click();
    }, i);
    await page.waitForTimeout(1200);
    const st = await page.evaluate(() => {
      const c = document.getElementById('gt-chart');
      const cvs = c.querySelectorAll('canvas');
      const badge = document.getElementById('gt-chart-badge');
      const note = document.getElementById('gt-chart-note');
      return {
        canvas: cvs.length,
        canvasW: cvs.length ? cvs[0].width : 0,
        badge: badge ? badge.textContent : '',
        note: note ? note.textContent.slice(0, 40) : '',
      };
    });
    const ok = st.canvas > 0 && st.canvasW > 100;
    console.log(`  [${i}] ${ok ? 'OK ' : '?? '} canvas=${st.canvas} w=${st.canvasW} | ${st.badge} | ${st.note}`);
  }
  console.log('JS 错误:', jsErrors.length, jsErrors.join(';'));
} catch (e) {
  console.log('异常:', String(e).slice(0, 300));
} finally {
  await browser.close();
}
