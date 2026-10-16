// 实拍：研报页「观点总览」是否已含今天(2026-10-14)的公众号观点（2026-10-14）
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = 'http://127.0.0.1:5000';
const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const ctx = await browser.newContext({ viewport: { width: 1680, height: 1080 } });
const page = await ctx.newPage();
const errs = [];
page.on('pageerror', e => errs.push(String(e).slice(0, 180)));

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="research"]', { timeout: 30000 });
await page.click('nav button[data-tab="research"]');
await page.waitForTimeout(4000);

// 读页面上出现的机构名 + 日期，判断今天的公众号来源是否在列
const info = await page.evaluate(() => {
  const t = document.body.innerText;
  const srcs = ['混沌天成', '恒力期货', '中信期货研究所', '隆众资讯'];
  const found = {};
  srcs.forEach(s => { found[s] = t.includes(s); });
  return {
    found,
    hasToday: t.includes('2026-10-14') || t.includes('10-14'),
    snippet: t.slice(0, 0),
  };
});
console.log('页面上是否出现今天的公众号来源:');
Object.entries(info.found).forEach(([k, v]) => console.log(`  ${v ? '✔' : '✘'} ${k}`));

await page.screenshot({ path: 'probe/_shot_research_today.png' });
console.log('已截图 probe/_shot_research_today.png');
console.log('JS 错误数 =', errs.length);
await browser.close();
