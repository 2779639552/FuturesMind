// 用真实浏览器（同一套 cookie）调 /web/mp/articles，判定是「环境/签名」还是「账号级拒绝」
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const cf = path.join(os.homedir(), '.tradingagents', 'weread', 'cookies.json');
const j = JSON.parse(fs.readFileSync(cf, 'utf8'));
const cookies = (j.cookies || []).map(c => ({
  name: c.name, value: String(c.value),
  domain: c.domain || '.weread.qq.com', path: c.path || '/',
  expires: c.expires && c.expires > 0 ? c.expires : -1,
  httpOnly: !!c.httpOnly, secure: !!c.secure,
  sameSite: c.sameSite === 'lax' ? 'Lax' : c.sameSite === 'strict' ? 'Strict' : 'None',
}));

const b = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const ctx = await b.newContext({ viewport: { width: 1400, height: 900 } });
await ctx.addCookies(cookies);
const p = await ctx.newPage();
await p.goto('https://weread.qq.com/', { waitUntil: 'domcontentloaded', timeout: 45000 });
await p.waitForTimeout(3000);

const out = await p.evaluate(async () => {
  const r = await fetch('/web/mp/articles?bookId=MP_WXS_3869147758&offset=0', {
    headers: { Accept: 'application/json, text/plain, */*' }, credentials: 'include',
  });
  let d = {};
  try { d = await r.json(); } catch (e) { d = { parse: String(e) }; }
  return { status: r.status, errCode: d.errCode, errMsg: d.errMsg,
           reviews: (d.reviews || []).length, keys: Object.keys(d).slice(0, 8) };
});
console.log('browser fetch:', JSON.stringify(out));

const login = await p.evaluate(async () => {
  const r = await fetch('/web/shelf/sync', { credentials: 'include' });
  const d = await r.json().catch(() => ({}));
  return { status: r.status, books: (d.books || []).length, errCode: d.errCode };
});
console.log('browser shelf:', JSON.stringify(login));
await b.close();
