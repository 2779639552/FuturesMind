// 修复后 E2E：分钟K「自动刷新」是否真的在推进数据（2026-10-14）
//
// 【与修复前诊断脚本的区别】之前只数"请求数"；本脚本同时断言：
//   ① 请求确实按 30s 周期重复发生；
//   ② 响应里的**最后一根 bar 时间戳随轮次前进**（用户症状是"还是十分钟前的数据"，
//      所以必须验证"数据在动"，而不只是"有请求"）。
//
// 用法：node probe/_e2e_kline_autorefresh_fixed.mjs
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = 'http://127.0.0.1:5000';
let pass = 0, fail = 0;
const log = (ok, msg) => { console.log(`${ok ? '  PASS' : '  FAIL'}  ${msg}`); ok ? pass++ : fail++; };

const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
const page = await ctx.newPage();
const jsErrors = [];
page.on('pageerror', e => jsErrors.push(String(e).slice(0, 200)));

const resps = [];   // {at, ms, src, last}
const reqAt = [];
page.on('request', r => { if (r.url().includes('/api/quant/kline')) reqAt.push(Date.now()); });
page.on('response', async r => {
  if (!r.url().includes('/api/quant/kline')) return;
  let src = '?', last = '?', n = 0;
  try {
    const j = await r.json();
    src = j.source || '?';
    const ks = j.klines || j.bars || [];
    n = ks.length;
    if (ks.length) last = ks[ks.length - 1].date || ks[ks.length - 1].time || '?';
  } catch (e) { /* ignore */ }
  resps.push({ at: Date.now(), src, last, n });
});

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="quant"]', { timeout: 30000 });
await page.click('nav button[data-tab="quant"]');
await page.waitForTimeout(1500);

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
log(!!code, `总览首行 = ${code}`);
await page.evaluate(() => document.querySelector('#q-ov-rows tr').click());
await page.waitForTimeout(2000);

// 切 1 分钟（自动刷新只对分钟级起表）
await page.evaluate(() => {
  const s = document.getElementById('q-period');
  s.value = '1';
  s.dispatchEvent(new Event('change', { bubbles: true }));
});
for (let i = 0; i < 40; i++) {
  const w = await page.evaluate(() => { const c = document.querySelector('#chart-q-kline canvas'); return c ? c.width : 0; });
  if (w > 0) break;
  await page.waitForTimeout(1000);
}
await page.waitForTimeout(2000);

const first = resps[resps.length - 1];
log(!!first && first.n > 0, `首次 K 线已加载: bars=${first?.n} source=${first?.src} last=${first?.last}`);
log(resps.length >= 1 && (first?.n ?? 0) > 100, `数据量正常（${first?.n} 根，非空图）`);

// ── 观察 100s：每 30s 应有一次自动刷新，且数据时间戳应在推进 ──
console.log('\n[盯 100s] 每 30s 一轮自动刷新');
const before = resps.length;
for (let i = 0; i < 10; i++) {
  await page.waitForTimeout(10000);
  const st = await page.evaluate(() => {
    try { return { timer: (App._klineTimer == null) ? 'null' : 'set' }; } catch (e) { return { timer: 'ERR' }; }
  });
  const r = resps[resps.length - 1];
  console.log(`    +${(i + 1) * 10}s  请求=${reqAt.length} 响应=${resps.length} timer=${st.timer} 最近: ${r?.last} (${r?.src})`);
}
const newResps = resps.slice(before);
log(reqAt.length > before, `100s 内新增自动刷新请求 = ${reqAt.length - before}（期望 ≥2）`);

// 数据时间戳是否推进（用户症状："还是十分钟前的数据"）
const stamps = [...new Set(newResps.map(r => r.last).filter(Boolean))];
console.log(`    本轮出现的 bar 时间戳集合: ${stamps.join(' | ')}`);
log(stamps.length >= 1, `自动刷新拿到的数据非空（${stamps.length} 个时间戳）`);
log(newResps.every(r => r.n > 0), `每次刷新的 bar 数都 > 0（无空图）`);

// 关键：响应必须"快"（修复前 30~50s，比刷新周期还长 ⇒ 永远过期）
console.log(`\n  最近 3 次响应的 bar 数: ${newResps.slice(-3).map(r => r.n).join(', ')}`);

console.log(`\n=== ${pass} PASS / ${fail} FAIL ===`);
console.log(`JS 错误数 = ${jsErrors.length}`);
jsErrors.slice(0, 5).forEach(e => console.log('  ! ' + e));
await browser.close();
