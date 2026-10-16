// 微信读书扫码登录探针 Step 2：点开「登录」→ 抓二维码
//
// 判定标准（避免上次把书封面误判成二维码）：
//   二维码必须是**新出现的**元素，且满足下列之一：
//     · <canvas>（微信读书新版用 canvas 画 QR）
//     · <img> 且 src 含 qrcode/qr/login 关键字
//     · <img> 且 尺寸大致正方形（|w-h| < 0.2*max）
//   并且位于弹窗/遮罩容器内。

import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const OUT = 'probe/_shot_weread_qr.png';

const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const ctx = await browser.newContext({
  viewport: { width: 1280, height: 900 },
  userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36',
});
const page = await ctx.newPage();
await page.goto('https://weread.qq.com/', { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.waitForTimeout(2500);

// 找「登录」入口：优先精确文本，其次包含
const before = await page.evaluate(() => ({ imgs: document.querySelectorAll('img').length, canvas: document.querySelectorAll('canvas').length }));
console.log(`[before] img=${before.imgs} canvas=${before.canvas}`);

let clicked = null;
for (const sel of [
  'text=登录',
  'a:has-text("登录")',
  'button:has-text("登录")',
  '[class*=login]',
  '[class*=Login]',
]) {
  try {
    const el = page.locator(sel).first();
    if (await el.count() > 0 && await el.isVisible()) {
      await el.click({ timeout: 5000 });
      clicked = sel;
      break;
    }
  } catch (e) { /* 试下一个 */ }
}
console.log(`[click] ${clicked || '未找到可点击的登录入口'}`);
await page.waitForTimeout(3500);

const after = await page.evaluate(() => {
  const qs = (s) => Array.from(document.querySelectorAll(s));
  const shaped = (i) => {
    const w = i.naturalWidth, h = i.naturalHeight;
    if (w < 100 || h < 100) return false;
    return Math.abs(w - h) < 0.2 * Math.max(w, h);
  };
  const qrImgs = qs('img').filter((i) => {
    const s = (i.src || '').toLowerCase();
    return /qrcode|qr_|_qr|login.*code|code.*login/.test(s) || shaped(i);
  }).map((i) => ({ src: i.src.slice(0, 110), w: i.naturalWidth, h: i.naturalHeight,
                   rect: (r => ({ x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }))(i.getBoundingClientRect()) }));
  const canvases = qs('canvas').map((c) => ({
    w: c.width, h: c.height,
    rect: (r => ({ x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }))(c.getBoundingClientRect()),
  }));
  const dialogish = qs('[class*=modal], [class*=dialog], [class*=mask], [class*=popup], [role=dialog]')
    .slice(0, 6).map((e) => `${e.tagName}.${(e.className || '').toString().slice(0, 46)}`);
  const text = (document.body.innerText || '').replace(/\s+/g, ' ').slice(0, 320);
  return { url: location.href, imgCount: qs('img').length, qrImgs, canvases, dialogish, text,
           hasScanWord: /扫码|扫一扫|二维码|扫描/.test(document.body.innerText || '') };
});

console.log(`[after ] url=${after.url} img=${after.imgCount} canvas=${after.canvases.length}`);
console.log(`[after ] 含"扫码/二维码"字样 = ${after.hasScanWord}`);
if (after.qrImgs.length) {
  console.log('[after ] 疑似 QR <img>:');
  after.qrImgs.forEach((i) => console.log(`   ${i.w}x${i.h} @(${i.rect.x},${i.rect.y}) ${i.src}`));
}
if (after.canvases.length) {
  console.log('[after ] canvas:');
  after.canvases.forEach((c) => console.log(`   ${c.w}x${c.h} @(${c.rect.x},${c.rect.y},${c.rect.w}x${c.rect.h})`));
}
if (after.dialogish.length) console.log('[after ] 弹窗类容器:', after.dialogish);
console.log('[after ] 正文片段:', after.text.slice(0, 220));

await page.screenshot({ path: OUT, fullPage: false });
console.log(`[shot] ${OUT}`);

const cookies = await ctx.cookies();
console.log(`[cookies] ${cookies.map((c) => c.name).join(', ') || '(无)'}`);

await browser.close();
