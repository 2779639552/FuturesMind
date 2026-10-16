import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';
const BASE = 'http://127.0.0.1:5000';
const b = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const ctx = await b.newContext({ viewport: { width: 1680, height: 1080 } });
const p = await ctx.newPage();
const errs = []; p.on('pageerror', e => errs.push(String(e).slice(0, 160)));
await p.goto(BASE, { waitUntil: 'domcontentloaded' });
await p.waitForSelector('nav button[data-tab="research"]', { timeout: 30000 });
await p.click('nav button[data-tab="research"]');
await p.waitForTimeout(4500);
const info = await p.evaluate(() => {
  const t = document.body.innerText;
  const bad = ['公众号-', '混沌天成研究', '混沌天成研究院', '恒力期货研究院', '隆众资讯-', '中信期货研究所'];
  const good = ['混沌天成', '恒力期货', '隆众资讯', '中信期货'];
  return {
    dirty: bad.filter(x => t.includes(x)),
    clean: good.filter(x => t.includes(x)),
    gen: (t.match(/生成于[^\n]{0,24}/) || ['(未找到)'])[0],
  };
});
console.log('页面残留的"脏"写法（应全为空）:', info.dirty.length ? info.dirty.join(', ') : '无 ✔');
console.log('页面出现的干净机构名:', info.clean.join(' / ') || '(无)');
console.log('页面时间戳:', info.gen);
await p.screenshot({ path: 'probe/_shot_norm_b.png' });
console.log('已截图；JS 错误数 =', errs.length);
await b.close();
