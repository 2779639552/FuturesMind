// 验证: 本次改动只影响 gtja —— 其他 tab 必须仍"只展开 1 块"
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const TABS = ['run', 'research', 'sentiment', 'quant', 'fund', 'warrant', 'variety'];
const b = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await b.newPage({ viewport: { width: 1600, height: 1000 } });
const errs = [];
page.on('pageerror', e => errs.push(e.message));
await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(2500);

let fail = 0;
for (const t of TABS) {
  await page.evaluate(x => App.switchTab(x), t);
  await page.waitForTimeout(2200);
  const r = await page.evaluate(tab => {
    const rows = (App._secRows || {})[tab] || [];
    const open = rows.filter(x => (x.blocks || []).some(bb => !bb.classList.contains('is-collapsed'))).length;
    return { n: rows.length, open, labels: rows.map(x => x.label) };
  }, t);
  const ok = r.open === 1;
  if (!ok) fail++;
  const tag = ok ? 'PASS' : 'FAIL';
  console.log('  ' + tag + '  ' + t.padEnd(10) + ' 模块 ' + String(r.n).padStart(2)
    + ' 个, 默认展开 ' + r.open + ' 个 (期望 1)');
}
console.log('\nJS 报错: ' + (errs.length ? errs.slice(0, 3).join(' | ') : '(无)'));
console.log('结果: ' + (TABS.length - fail) + ' 通过, ' + fail + ' 失败');
await b.close();
process.exit(fail ? 1 : 0);
