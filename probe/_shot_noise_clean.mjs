// 实拍：研报页观点总览里已无「招聘」类噪声
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';
const b = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const ctx = await b.newContext({ viewport: { width: 1680, height: 1050 } });
const p = await ctx.newPage();
const errs = []; p.on('pageerror', e => errs.push(String(e).slice(0, 150)));
await p.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded' });
await p.waitForSelector('nav button[data-tab="research"]', { timeout: 30000 });
await p.click('nav button[data-tab="research"]');
await p.waitForTimeout(6000);
const info = await p.evaluate(() => {
  const t = document.body.innerText;
  return {
    hasNoise: ['招聘', '能化研究员'].filter(k => t.includes(k)),
    hasHengli: t.includes('恒力'),
    gen: (t.match(/生成于[^\n]{0,20}/) || ['(未找到)'])[0],
    chars: t.length,
  };
});
console.log('页面含噪声词:', info.hasNoise.length ? info.hasNoise.join(',') : '无 ✔');
console.log('页面含「恒力」:', info.hasHengli ? '是（正常，仍有恒力日报）' : '否');
console.log('总结时间戳:', info.gen, ' 页面文本长度:', info.chars);
await p.screenshot({ path: 'probe/_shot_research_noise_clean.png' });
console.log('JS 错误数 =', errs.length, errs.slice(0, 2));
await b.close();
