// 验证: 进入国君页后走势图自动渲染第一个指标(不再空白)
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const browser = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const results = [];
const check = (n, ok, d = '') => { results.push({ n, ok }); console.log(`${ok ? 'PASS' : 'FAIL'}  ${n}${d ? '  | ' + d : ''}`); };
const jsErrors = [];
page.on('pageerror', e => jsErrors.push(String(e).slice(0, 150)));

try {
  await page.goto('http://127.0.0.1:5000/', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(3000);
  await page.click('button[data-tab="gtja"]');
  await page.waitForTimeout(8000);  // 等 overview 加载 + 自动渲染

  const info = await page.evaluate(() => {
    const c = document.getElementById('gt-chart');
    const cvs = c.querySelectorAll('canvas');
    const badge = document.getElementById('gt-chart-badge');
    const note = document.getElementById('gt-chart-note');
    const card = c.closest('.sec-block');
    return {
      canvasCount: cvs.length,
      canvasW: cvs.length ? cvs[0].width : 0,
      canvasH: cvs.length ? cvs[0].height : 0,
      badge: badge ? badge.textContent : '',
      note: note ? note.textContent.slice(0, 50) : '',
      cardClass: card ? card.className : null,
      indicators: document.querySelectorAll('#gt-indicators button').length,
    };
  });
  console.log('自动渲染状态:', JSON.stringify(info, null, 2));
  check('进入页面自动渲染走势图', info.canvasCount >= 1 && info.canvasW > 100,
    `canvas=${info.canvasCount} w=${info.canvasW}`);
  check('badge 显示第一个指标', info.badge.length > 0, `badge=${info.badge}`);
  check('note 有数据', info.note.includes('点'), `note=${info.note}`);
  check('指标表正常', info.indicators > 0, `indicators=${info.indicators}`);
  check('页面无 JS 错误', jsErrors.length === 0, jsErrors.join(';'));
} catch (e) {
  check('探针异常', false, String(e).slice(0, 200));
} finally {
  await browser.close();
}
const fails = results.filter(r => !r.ok);
console.log(`\n===== 汇总: ${results.length - fails.length}/${results.length} PASS =====`);
process.exit(fails.length ? 1 : 0);
