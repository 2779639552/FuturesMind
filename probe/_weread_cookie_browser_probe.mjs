// 用**同一份 cookie** 在真实浏览器里访问 weread，判断"cookie 死了"还是"我们的请求结构不对"。
//   浏览器也登出 ⇒ cookie 真失效（登录问题）
//   浏览器正常   ⇒ requests 侧的问题（头/参数/接口变更 = 架构问题）
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const store = path.join(os.homedir(), '.tradingagents', 'weread', 'cookies.json');
const saved = JSON.parse(fs.readFileSync(store, 'utf8')).cookies || [];
const BOOK = 'MP_WXS_3869147758';

const cookies = saved.map(c => ({
  name: c.name,
  value: String(c.value ?? ''),
  domain: c.domain,
  path: c.path || '/',
  expires: typeof c.expires === 'number' ? c.expires : -1,
  httpOnly: !!c.httpOnly,
  secure: !!c.secure,
  sameSite: c.sameSite || 'Lax',
}));

const B = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const ctx = await B.newContext({
  viewport: { width: 1440, height: 900 },
  userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36',
});
await ctx.addCookies(cookies);
const page = await ctx.newPage();

// ── ① 打开书架页，看是否被要求登录
await page.goto('https://weread.qq.com/web/shelf', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(6000);
const pageInfo = await page.evaluate(() => ({
  url: location.href,
  title: document.title,
  hasLoginBtn: !!document.querySelector('.navBar_login, [class*="login"]'),
  bodySample: (document.body.innerText || '').replace(/\s+/g, ' ').slice(0, 220),
}));
console.log('【① 书架页】', JSON.stringify(pageInfo, null, 0).slice(0, 500));

// ── ② 在页面上下文里请求（浏览器自带真实头 + cookie）
const api = await page.evaluate(async (book) => {
  const out = {};
  for (const [k, u] of Object.entries({
    shelf: '/web/shelf/sync',
    cover: `/web/mp/cover?bookId=${book}`,
    articles: `/web/mp/articles?bookId=${book}&offset=0`,
  })) {
    try {
      const r = await fetch(u, { credentials: 'include', headers: { Accept: 'application/json, text/plain, */*' } });
      const t = await r.text();
      let d = null;
      try { d = JSON.parse(t); } catch { /* 非 JSON */ }
      out[k] = d ? { errCode: d.errCode, errMsg: d.errMsg, keys: Object.keys(d).slice(0, 6) } : { raw: t.slice(0, 90) };
    } catch (e) { out[k] = { err: String(e).slice(0, 90) }; }
  }
  return out;
}, BOOK);
console.log('【② 页面内 fetch】');
for (const [k, v] of Object.entries(api)) console.log('   ' + k.padEnd(9) + JSON.stringify(v));

// ── ③ cookie 是否被服务端改写过（Set-Cookie 新值）
const after = await ctx.cookies('https://weread.qq.com');
const before = new Map(saved.map(c => [c.name, String(c.value ?? '')]));
console.log('【③ cookie 变化】');
for (const c of after) {
  if (!['wr_skey', 'wr_vid', 'wr_rt', 'wr_ql'].includes(c.name)) continue;
  const old = before.get(c.name);
  const same = old === c.value;
  console.log('   ' + c.name.padEnd(9) + (same ? '未变' : '★被改写') +
    '  len ' + (old || '').length + ' → ' + c.value.length);
}

// ── ④ 导出浏览器会话后的 cookie，并用 Python 侧复核（关键：服务端是否轮换了 wr_skey）
const post = (await ctx.cookies('https://weread.qq.com')).map(c => ({
  name: c.name, value: c.value, domain: c.domain, path: c.path,
  expires: c.expires, httpOnly: c.httpOnly, secure: c.secure, sameSite: c.sameSite,
}));
fs.writeFileSync('probe/_weread_cookies_after_browser.json',
  JSON.stringify({ savedAt: new Date().toISOString(), cookies: post }, null, 2), 'utf8');

const crypto = await import('node:crypto');
const md5 = v => crypto.createHash('md5').update(String(v)).digest('hex').slice(0, 8);
for (const n of ['wr_skey', 'wr_rt', 'wr_vid']) {
  const a = (saved.find(c => c.name === n) || {}).value;
  const b = (post.find(c => c.name === n) || {}).value;
  console.log('【④ ' + n + '】旧=' + md5(a) + ' 新=' + md5(b) + (a === b ? ' 相同' : ' ★不同'));
}
console.log('【④ 已导出】probe/_weread_cookies_after_browser.json');

await B.close();
