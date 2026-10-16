// 总览表颜色语义核对（项目铁律：**多=红 / 空=绿 / 涨=红 / 跌=绿**，中国习惯）
//
// 【为什么必须单独测】颜色只在 canvas/DOM 的 computed style 里，截图肉眼容易看错
//   （尤其深色主题下红绿饱和度接近）。本项目有"符号/颜色语义"铁律，且历史上踩过
//   "接口全绿、语义全反"的坑，所以要拿真实 computed 值比对，不能目测。

import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';
import fs from 'node:fs';

const STUB = JSON.parse(fs.readFileSync('probe/_qov_stub.json', 'utf8'));
let pass = 0, fail = 0;
const log = (ok, msg) => { console.log(`${ok ? '  PASS' : '  FAIL'}  ${msg}`); ok ? pass++ : fail++; };

const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await (await browser.newContext({ viewport: { width: 1600, height: 1000 } })).newPage();
await page.route('**/api/quant/overview*', r =>
  r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(STUB) }));
await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(2500);
await page.click('nav button[data-tab="quant"]');
await page.waitForTimeout(3000);

for (const theme of ['light', 'dark']) {
  const cur = await page.evaluate(() => document.documentElement.getAttribute('data-theme'));
  if (cur !== theme) { await page.click('.theme-toggle'); await page.waitForTimeout(900); }

  // 取「结论」列（第 11 个 td，0-based 10）与「涨跌幅」列（第 4 个，0-based 3）的实际颜色
  const rows = await page.evaluate(() => Array.from(document.querySelectorAll('#q-ov-rows tr'))
    .filter(tr => tr.querySelectorAll('td').length >= 10)
    .map(tr => {
      const td = tr.querySelectorAll('td');
      return {
        code: td[0].innerText.trim().split(/\s+/)[0],
        chgText: td[3].innerText.trim(),
        chgColor: getComputedStyle(td[3]).color,
        verdict: td[10].innerText.trim(),
        verdictColor: getComputedStyle(td[10]).color,
      };
    }));
  console.log(`\n[${theme}] 共 ${rows.length} 行`);
  rows.forEach(r => console.log(`  ${r.code}  涨跌 ${r.chgText} ${r.chgColor}  |  结论 ${r.verdict} ${r.verdictColor}`));

  // 红色系 / 绿色系的判据：R 明显大于 G ⇒ 红；G 明显大于 R ⇒ 绿
  const isRed = c => { const m = c.match(/(\d+),\s*(\d+),\s*(\d+)/); return m && +m[1] > +m[2] + 40; };
  const isGreen = c => { const m = c.match(/(\d+),\s*(\d+),\s*(\d+)/); return m && +m[2] > +m[1] + 40; };

  const wrongChg = rows.filter(r => {
    const v = parseFloat(r.chgText);
    if (!isFinite(v)) return false;
    return v > 0 ? !isRed(r.chgColor) : (v < 0 ? !isGreen(r.chgColor) : false);
  });
  const wrongVerdict = rows.filter(r =>
    (r.verdict === '多' && !isRed(r.verdictColor)) || (r.verdict === '空' && !isGreen(r.verdictColor)));

  log(wrongChg.length === 0, `涨跌幅配色（涨红跌绿）: ${wrongChg.length ? '✘ ' + wrongChg.map(r => r.code + '=' + r.chgColor).join(',') : '全部正确'}`);
  log(wrongVerdict.length === 0, `结论配色（多红空绿）: ${wrongVerdict.length ? '✘ ' + wrongVerdict.map(r => r.code + '/' + r.verdict + '=' + r.verdictColor).join(',') : '全部正确'}`);
  log(rows.length > 0, '行集合非空（否则上面的"全部正确"是假绿）');
}

console.log(`\n=== 结果：PASS ${pass} / FAIL ${fail} ===`);
await browser.close();
process.exit(fail === 0 ? 0 : 1);
