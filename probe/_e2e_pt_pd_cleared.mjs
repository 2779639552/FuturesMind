// 验证: 逐品种观点总览里 SC 行不再含铂钯研报(误判已清), 且页面正常
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
  await page.click('button[data-tab="research"]');
  await page.waitForTimeout(3000);

  // ① 逐品种观点总览: 切到 SC 品种
  await page.evaluate(() => {
    const sel = document.getElementById('res-views-variety');
    if (sel) { sel.value = 'SC'; sel.dispatchEvent(new Event('change')); }
  });
  await page.waitForTimeout(2500);

  const scRows = await page.evaluate(() => {
    const tbody = document.querySelector('#res-views tbody') || document.querySelector('#res-views');
    return tbody ? tbody.textContent : '';
  });
  const hasPt = scRows.includes('铂') || scRows.includes('钯');
  check('SC 行不含铂钯研报', !hasPt, `SC 视图文本长度=${scRows.length}`);
  console.log('  SC 视图片段:', scRows.slice(0, 200).replace(/\n/g, ' '));

  // ② 检查 SC 聚合接口直接返回
  const api = await page.evaluate(async () => {
    const r = await fetch('/api/research/views?variety=SC&date=2026-10-08');
    const d = await r.json();
    return JSON.stringify(d).slice(0, 400);
  });
  console.log('② /api/research/views?variety=SC:', api);
  const apiHasPt = api.includes('铂') || api.includes('钯');
  check('SC 聚合接口无铂钯', !apiHasPt);

  check('页面无 JS 错误', jsErrors.length === 0, jsErrors.join(';'));
} catch (e) {
  check('探针异常', false, String(e).slice(0, 200));
} finally {
  await browser.close();
}
const fails = results.filter(r => !r.ok);
console.log(`\n===== 汇总: ${results.length - fails.length}/${results.length} PASS =====`);
process.exit(fails.length ? 1 : 0);
