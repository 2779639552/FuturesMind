// 微信读书扫码登录可行性探针（只读：只截图二维码，不点任何按钮、不提交）
//
// 目的：确认能否用现有 Playwright + 本机 Chrome 走完「打开登录页 → 拿到二维码」
// 这一步。若能，则自建采集器就不需要用户装任何东西（qrcode-terminal 之类），
// 直接截图给他扫即可。
//
// ⚠️ 本探针只做：导航 + 截图 + 报告 DOM 里有没有 qrcode 元素。
//    绝不输入凭据、绝不点击确认。

import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';
import fs from 'node:fs';

const OUT = 'probe/_shot_weread_login.png';
const CANDIDATES = [
  'https://weread.qq.com/login',
  'https://weread.qq.com/web/login',
  'https://weread.qq.com/',
];

const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const ctx = await browser.newContext({
  viewport: { width: 1280, height: 900 },
  userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36',
});
const page = await ctx.newPage();

const cookiesBefore = await ctx.cookies();

for (const url of CANDIDATES) {
  try {
    const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(2500);
    const info = await page.evaluate(() => {
      const qs = (s) => Array.from(document.querySelectorAll(s));
      const imgs = qs('img').map((i) => ({
        src: (i.src || '').slice(0, 90),
        w: i.naturalWidth,
        h: i.naturalHeight,
        cls: (i.className || '').toString().slice(0, 50),
      }));
      const canvas = qs('canvas').length;
      const text = (document.body.innerText || '').replace(/\s+/g, ' ').slice(0, 260);
      const qrLike = qs('[class*=qr], [class*=Qr], [class*=QR], [id*=qr], [class*=code], [class*=login]')
        .slice(0, 8)
        .map((e) => `${e.tagName}.${(e.className || '').toString().slice(0, 40)}`);
      return {
        title: document.title,
        url: location.href,
        imgCount: imgs.length,
        bigImgs: imgs.filter((i) => i.w >= 100 || i.h >= 100),
        canvasCount: canvas,
        qrLike,
        text,
        hasLoginWord: /登录|扫码|二维码/.test(document.body.innerText || ''),
      };
    });
    console.log(`\n=== ${url}`);
    console.log(`  HTTP ${resp ? resp.status() : '?'}  title="${info.title}"`);
    console.log(`  final url = ${info.url}`);
    console.log(`  img=${info.imgCount} canvas=${info.canvasCount} 含"登录/扫码/二维码"=${info.hasLoginWord}`);
    if (info.bigImgs.length) {
      console.log('  大图:');
      info.bigImgs.slice(0, 5).forEach((i) => console.log(`     ${i.w}x${i.h} ${i.src}`));
    }
    if (info.qrLike.length) console.log('  qr 类元素:', info.qrLike);
    console.log('  正文片段:', info.text.slice(0, 160));

    await page.screenshot({ path: OUT, fullPage: false });
    const hasQr = info.bigImgs.some((i) => i.w >= 120 && i.h >= 120) || info.canvasCount > 0;
    console.log(`  (shot) ${OUT}  判定可见二维码=${hasQr}`);
    if (hasQr) break;
  } catch (e) {
    console.log(`\n=== ${url}\n  ERR ${String(e).slice(0, 120)}`);
  }
}

const cookiesAfter = await ctx.cookies();
console.log(`\n[cookies] 登录前 ${cookiesBefore.length} 个 → 现在 ${cookiesAfter.length} 个`);
console.log('  名字:', cookiesAfter.map((c) => c.name).join(', ') || '(无)');
fs.writeFileSync('probe/_tmp_weread_login_cookies.json', JSON.stringify(cookiesAfter, null, 1));
console.log('  (dump) probe/_tmp_weread_login_cookies.json');

await browser.close();
