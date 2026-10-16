import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';
const BASE = 'http://127.0.0.1:5000';
const b = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const ctx = await b.newContext({ viewport: { width: 1680, height: 1050 } });
const p = await ctx.newPage();
await p.goto(BASE, { waitUntil: 'domcontentloaded' });
await p.waitForSelector('nav button[data-tab="fund"]', { timeout: 30000 });
await p.click('nav button[data-tab="fund"]');
// 等仓单块数据到位（不是"加载中"）
let ready = false;
for (let i = 0; i < 60; i++) {
  ready = await p.evaluate(() => {
    const map = document.getElementById('wt-c1');
    const t = (map && map.innerText) || '';
    return t.length > 0 && !t.includes('加载中');
  });
  if (ready) break;
  await p.waitForTimeout(2000);
}
console.log('仓单图表已渲染:', ready);
// 滚到仓单块
await p.evaluate(() => {
  const el = document.getElementById('tab-warrant-tools') || document.getElementById('wt-ov-badge');
  if (el) el.scrollIntoView({ block: 'start' });
});
await p.waitForTimeout(2500);
await p.screenshot({ path: 'probe/_shot_fund_warrant_scroll.png' });
// 读仓单区关键文本
const info = await p.evaluate(() => {
  const ids = ['wt-ov-badge', 'wt-kpi', 'wt-table', 'wt-rank-qty'];
  const out = {};
  ids.forEach(i => { const e = document.getElementById(i); out[i] = e ? (e.innerText || '').trim().slice(0, 60) : null; });
  return out;
});
console.log(JSON.stringify(info, null, 1));
await b.close();
