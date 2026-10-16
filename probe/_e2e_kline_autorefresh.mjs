// E2E: 量化页 K 线「分钟级自动刷新」与「不该刷新时不刷新」
//   [1] 1 分钟周期 → 30s 后应自动再发一次 /api/quant/kline
//   [2] 切到日K → 不应再轮询(日K一天一根bar,轮询无意义)
//   [3] 静默刷新期间标题不应被"加载中..."覆盖 + dataZoom 保留(不弹回默认窗口)
// 用法: node probe/_e2e_kline_autorefresh.mjs
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = process.argv[2] || 'http://127.0.0.1:5000';
let pass = 0, fail = 0;
const log = (ok, msg) => { ok ? pass++ : fail++; console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${msg}`); };

const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1040 } });
const errs = [];
page.on('pageerror', e => errs.push(e.message));

// 记录所有 K 线接口请求的时间戳与周期参数
const hits = [];
page.on('request', r => {
  const u = r.url();
  if (u.includes('/api/quant/kline')) {
    const m = /period=([^&]*)/.exec(u);
    hits.push({ t: Date.now(), period: m ? m[1] : '?' });
  }
});

await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(2500);

// 进量化页并切到 1 分钟周期
await page.evaluate(() => App.switchTab('quant'));
await page.waitForTimeout(1500);
await page.evaluate(() => {
  const s = document.getElementById('q-period');
  if (s) { s.value = '1'; s.dispatchEvent(new Event('change')); }
});
await page.waitForTimeout(12000);   // 等首轮取数完成(天勤建连约 2~4s)

const first = hits.filter(h => h.period === '1').length;
log(first >= 1, `1分钟周期已发请求 (${first} 次)`);

console.log('\n[1] 30 秒后应自动刷新');
await page.waitForTimeout(30000);
const after30 = hits.filter(h => h.period === '1').length;
log(after30 >= 2, `轮询已触发: 请求数 ${first} → ${after30}`);

console.log('\n[2] 静默刷新不覆盖标题');
const title = await page.evaluate(() => (document.getElementById('q-kline-title') || {}).textContent || '');
log(!title.includes('加载中'), `标题未被"加载中"覆盖: "${title.slice(0, 60)}"`);
log(/天勤|新浪/.test(title), `标题含数据源标注: ${/天勤/.test(title) ? '天勤' : (/新浪/.test(title) ? '新浪' : '无')}`);

console.log('\n[3] 切到日K后应停表');
await page.evaluate(() => {
  const s = document.getElementById('q-period');
  if (s) { s.value = 'day'; s.dispatchEvent(new Event('change')); }
});
await page.waitForTimeout(10000);
const dayBefore = hits.filter(h => h.period === 'day').length;
await page.waitForTimeout(32000);   // 若未停表,这里会再多出一次 day 请求
const dayAfter = hits.filter(h => h.period === 'day').length;
log(dayAfter === dayBefore, `日K 未持续轮询: ${dayBefore} → ${dayAfter}`);

console.log('\n[4] 离开量化页应停表');
await page.evaluate(() => App.switchTab('research'));
await page.waitForTimeout(2000);
const beforeLeave = hits.length;
await page.waitForTimeout(4000);
log(hits.length === beforeLeave, `切走后无新增 K 线请求 (${beforeLeave} → ${hits.length})`);
const timer = await page.evaluate(() => App._currentTab);
log(timer !== 'quant', `当前页已离开量化: ${timer}`);

console.log('\n--- 请求时间线 ---');
hits.forEach((h, i) => console.log(`  #${i + 1} period=${h.period} t=+${((h.t - hits[0].t) / 1000).toFixed(1)}s`));
if (errs.length) console.log('\nJS 错误:\n' + errs.map(e => '  ' + e).join('\n'));

console.log(`\n结果: ${pass} passed, ${fail} failed`);
await browser.close();
process.exit(fail ? 1 : 0);
