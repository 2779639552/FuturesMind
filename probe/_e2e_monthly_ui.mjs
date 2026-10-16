// E2E: 前端「月报」选项与月报口径显示验证(2026-10-08)
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const base = 'http://127.0.0.1:5000';
let pass = 0, fail = 0;
const check = (name, ok, detail = '') => {
  if (ok) { pass++; console.log(`  ✓ ${name}`); }
  else { fail++; console.log(`  ✗ ${name} ${detail}`); }
};

const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await browser.newPage();
const errs = [];
page.on('pageerror', e => errs.push(String(e)));

try {
  await page.goto(base + '/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(2500);

  // ① 5 处下拉含「月报」
  const info = await page.evaluate(() => {
    const ids = ['res-ask-type', 'res-daily-type', 'res-vsum-type', 'res-views-type', 'res-filter-type'];
    const r = {};
    ids.forEach(id => {
      const sel = document.getElementById(id);
      r[id] = sel ? Array.from(sel.options).map(o => o.value) : null;
    });
    r.hasMonthLabel = typeof App._monthLabel === 'function';
    return r;
  });
  const ids = ['res-ask-type', 'res-daily-type', 'res-vsum-type', 'res-views-type', 'res-filter-type'];
  ids.forEach(id => {
    const opts = info[id] || [];
    check(`下拉 ${id} 含月报`, opts.includes('月报'), JSON.stringify(opts));
  });
  check('_monthLabel 函数存在', info.hasMonthLabel === true);

  // ② 切到研报页, 选月报 → 日期下拉显示「2026年10月」
  await page.evaluate(() => { if (typeof App.switchTab === 'function') App.switchTab('research'); });
  await page.waitForTimeout(1500);
  await page.evaluate(() => {
    const sel = document.getElementById('res-daily-type');
    sel.value = '月报';
    sel.dispatchEvent(new Event('change'));
  });
  await page.waitForTimeout(2500);
  const daily = await page.evaluate(() => {
    const dateSel = document.getElementById('res-daily-date');
    return {
      opts: Array.from(dateSel.options).map(o => o.textContent),
      value: dateSel.value,
      kind: document.getElementById('res-daily-kind')?.textContent || '',
    };
  });
  check('每日总结选月报后标题字=月报', daily.kind === '月报', daily.kind);
  check('月报日期下拉含「2026年10月」', daily.opts.some(o => o.includes('2026年10月')), JSON.stringify(daily.opts));
  check('月报日期下拉默认选中 2026-10-01', daily.value === '2026-10-01', daily.value);

  // ③ 单品种总结卡也切月报
  await page.evaluate(() => {
    const sel = document.getElementById('res-vsum-type');
    sel.value = '月报';
    sel.dispatchEvent(new Event('change'));
  });
  await page.waitForTimeout(2500);
  const vsum = await page.evaluate(() => {
    const dateSel = document.getElementById('res-vsum-date');
    return Array.from(dateSel.options).map(o => o.textContent);
  });
  check('单品种总结月报日期含「2026年10月」', vsum.some(o => o.includes('2026年10月')), JSON.stringify(vsum));

  check('无 JS 错误', errs.length === 0, errs.slice(0, 3).join(' | '));
} catch (e) {
  console.log('E2E 异常:', String(e).slice(0, 400));
  fail++;
}

await browser.close();
console.log(`\n结果: ${pass} pass / ${fail} fail`);
process.exit(fail ? 1 : 0);
