// 分钟K自动刷新：**逐步插桩**，定位调用链在哪一环断掉（2026-10-14 用户报障）
//
// 【为何要插桩】上一支脚本只看到「timer=SET 但 0 请求」，而 timer 竟是在 +60s 才被设置的
//   （canvas 早在 +15s 前就渲染完了）—— 说明 loadQuantKline → _renderQuantKline →
//   _scheduleKlineRefresh 这条链**中段有断裂或异常**。光看结果无法区分
//   「render 抛异常吃掉后续」/「schedule 被守卫 return」/「fetch 卡住」这三种。
//   直接把这些方法包一层，按时间顺序记录每次调用与异常。
//
// 用法：node probe/_diag_kline_chain.mjs
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = 'http://127.0.0.1:5000';
const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
const page = await ctx.newPage();

const pageErrs = [];
page.on('pageerror', e => pageErrs.push(String(e).slice(0, 250)));

// 网络：记录 kline 请求/响应时刻
const net = [];
page.on('request', r => { if (r.url().includes('/api/quant/kline')) net.push({ t: Date.now(), ev: 'req' }); });
page.on('response', r => {
  if (r.url().includes('/api/quant/kline')) net.push({ t: Date.now(), ev: 'resp', status: r.status() });
});

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="quant"]', { timeout: 30000 });

// ── 插桩（必须在进入量化页前装好，才能抓到首次调用）────────────────
await page.evaluate(() => {
  window.__log = [];
  const T0 = Date.now();
  const rec = (ev, extra) => window.__log.push(Object.assign({ ms: Date.now() - T0, ev }, extra || {}));
  // ⚠️ App 是内联 script 顶层的 `const`（不在 window 上），但处于**全局词法环境**，
  //    page.evaluate 的函数作用域链能解析到它；拿不到时再退 window.App。
  let A = null;
  try { A = App; } catch (e) { A = window.App || null; }
  if (!A) { rec('FATAL: App 取不到'); return; }
  rec('App 取到', { keys: Object.keys(A).length });

  ['_scheduleKlineRefresh', 'loadQuantKline', '_renderQuantKline', '_clearKlineRefresh'].forEach(name => {
    const orig = A[name];
    if (typeof orig !== 'function') { rec('MISSING ' + name); return; }
    A[name] = function (...a) {
      const info = { args: JSON.stringify(a.map(x => (x === undefined ? 'undef' : x))) };
      if (name === '_scheduleKlineRefresh') {
        info.period = a[0]; info.curTab = A._currentTab; info.hidden = document.hidden;
        info.timerBefore = (A._klineTimer == null) ? 'null' : typeof A._klineTimer;
      }
      rec('→' + name, info);
      try {
        const r = orig.apply(A, a);
        if (name === '_scheduleKlineRefresh') {
          rec('  ✓' + name + '已返回', { period: a[0], timerAfter: (A._klineTimer == null) ? 'null' : 'set' });
        }
        return r;
      } catch (e) {
        rec('✘' + name + ' THREW', { err: String(e).slice(0, 160) });
        throw e;
      }
    };
  });
  rec('插桩完成');
});

await page.click('nav button[data-tab="quant"]');
await page.waitForTimeout(1500);
console.log('[1] 已进技术面量化（总览）');

// 等总览首行 → 点行进详情
let code = null;
for (let i = 0; i < 40; i++) {
  code = await page.evaluate(() => {
    const tr = document.querySelector('#q-ov-rows tr');
    return tr ? tr.innerText.trim().split(/\s+/)[0] : null;
  });
  if (code && /^[A-Z]{1,3}$/.test(code)) break;
  await page.waitForTimeout(3000);
}
console.log(`[2] 点行 ${code} 进详情`);
await page.evaluate(() => document.querySelector('#q-ov-rows tr').click());
await page.waitForTimeout(2500);

// 切 1 分钟
console.log('[3] 切到 1 分钟周期');
await page.evaluate(() => {
  const s = document.getElementById('q-period');
  s.value = '1';
  s.dispatchEvent(new Event('change', { bubbles: true }));
});
// 等渲染
for (let i = 0; i < 30; i++) {
  const w = await page.evaluate(() => { const c = document.querySelector('#chart-q-kline canvas'); return c ? c.width : 0; });
  if (w > 0) break;
  await page.waitForTimeout(1000);
}

// ── 盯 100 秒 ───────────────────────────────────────────────────
console.log('\n[4] 盯 100s（自动刷新周期 30s ⇒ 期望 ≥3 次）');
for (let i = 0; i < 10; i++) {
  await page.waitForTimeout(10000);
  const st = await page.evaluate(() => {
    try {
      return {
        timer: (App._klineTimer == null) ? 'null' : 'set',
        n: (App._log || window.__log || []).filter(x => String(x.ev).includes('scheduleKlineRefresh')).length,
        reqs: 0,
      };
    } catch (e) { return { err: String(e).slice(0, 60) }; }
  });
  console.log(`    +${(i + 1) * 10}s  timer=${st.timer}  schedule调用次数=${st.n}  kline请求=${net.filter(x => x.ev === 'req').length}`);
}

// ── 导出插桩日志 ────────────────────────────────────────────────
const log = await page.evaluate(() => window.__log || []);
console.log('\n[5] 插桩时间线（ms 为进入量化页起算）:');
log.forEach(x => {
  const extra = Object.entries(x).filter(([k]) => k !== 'ms' && k !== 'ev')
    .map(([k, v]) => `${k}=${v}`).join(' ');
  console.log(`    ${String(x.ms).padStart(6)}ms  ${x.ev}  ${extra}`);
});
console.log('\n    网络: ' + net.map(x => `${x.ev}@${x.t % 100000}`).join(' '));

console.log(`\n页面 JS 错误数 = ${pageErrs.length}`);
pageErrs.slice(0, 8).forEach(e => console.log('  ! ' + e));
await browser.close();
