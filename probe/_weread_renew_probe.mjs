// 抓取 weread 页面加载期间的网络请求，定位 **wr_skey 续期** 走的是哪个接口。
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const store = path.join(os.homedir(), '.tradingagents', 'weread', 'cookies.json');
const saved = JSON.parse(fs.readFileSync(store, 'utf8')).cookies || [];
const cookies = saved.map(c => ({
  name: c.name, value: String(c.value ?? ''), domain: c.domain, path: c.path || '/',
  expires: typeof c.expires === 'number' ? c.expires : -1,
}));

const B = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const ctx = await B.newContext({ viewport: { width: 1440, height: 900 } });
await ctx.addCookies(cookies);
const page = await ctx.newPage();

const hits = [];
page.on('request', req => {
  const u = req.url();
  if (!/weread\.qq\.com/.test(u)) return;
  if (!/(login|renew|refresh|sync|token)/i.test(u)) return;
  hits.push({ method: req.method(), url: u.slice(0, 180), post: (req.postData() || '').slice(0, 160) });
});
const resp = [];
page.on('response', async r => {
  const u = r.url();
  if (!/(login|renew|refresh)/i.test(u)) return;
  let body = '';
  try { body = (await r.text()).slice(0, 200); } catch { /* ignore */ }
  resp.push({ status: r.status(), url: u.slice(0, 160), body });
});

await page.goto('https://weread.qq.com/web/shelf', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(8000);

console.log('【候选请求】%d 条', hits.length);
for (const h of hits.slice(0, 20)) {
  console.log('  %-4s %s', h.method, h.url);
  if (h.post) console.log('       body: %s', h.post);
}
console.log('【续期类响应】%d 条', resp.length);
for (const r of resp.slice(0, 12)) {
  console.log('  %s %s  →  %s', r.status, r.url, r.body.replace(/\s+/g, ' ').slice(0, 120));
}

const post = (await ctx.cookies('https://weread.qq.com')).find(c => c.name === 'wr_skey');
const before = saved.find(c => c.name === 'wr_skey');
console.log('【wr_skey】%s → %s', String(before?.value), String(post?.value));

// 页面里主动调用一次 —— 确认是否有可编程续期入口
const probe = await page.evaluate(async () => {
  const out = {};
  for (const [k, u] of Object.entries({
    renewal: '/web/login/renewal',
    renewalGet: '/web/login/renewal?rt=x',
  })) {
    try {
      const r = await fetch(u, { method: 'POST', credentials: 'include' });
      const t = await r.text();
      out[k] = r.status + ' ' + t.slice(0, 100);
    } catch (e) { out[k] = String(e).slice(0, 80); }
  }
  return out;
});
console.log('【续期接口探测】', JSON.stringify(probe));

await B.close();
