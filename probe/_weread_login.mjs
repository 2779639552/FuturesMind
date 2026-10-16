// 微信读书「扫码登录 → 拉公众号文章清单」一键验证脚本
//
// 为什么要这个脚本：
//   · 官方 Agent API Key（wrk-）**读不到**公众号文章清单（17 个接口都不支持，实测）
//   · 真正能列文章的接口是 `GET /web/mp/articles?bookId=MP_WXS_xxx&offset=N`，
//     但它要求 `wr_vid / wr_skey / wr_rt` 三个 cookie（网页登录态），API Key 替代不了
//   · 所以必须走一次网页扫码登录，把 cookie 存下来复用
//
// 本脚本做三件事：
//   1) 打开微信读书 → 点「登录」→ 把二维码截图出来（给人扫）
//   2) 轮询等待扫码成功 → 保存 cookie 到 ~/.tradingagents/weread/cookies.json
//   3) 立刻用该 cookie 调 `/web/mp/articles` 验证**能不能拿到文章清单**（成败判据）
//
// 用法：
//    node probe/_weread_login.mjs                 # 默认等 180 秒
//    node probe/_weread_login.mjs --wait 300      # 等 5 分钟
//    node probe/_weread_login.mjs --check-only    # 不登录，只用已存 cookie 验证

import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

const HOME = os.homedir();
const STORE = path.join(HOME, '.tradingagents', 'weread');
const COOKIE_FILE = path.join(STORE, 'cookies.json');
const QR_SHOT = 'probe/_shot_weread_qr_live.png';
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36';

// 书架里已有的目标号（来自本次实测）
const TARGETS = [
  { name: '混沌天成研究', bookId: 'MP_WXS_3869147758' },
  { name: '中信期货研究', bookId: 'MP_WXS_3008417470' },
  { name: '中信建投期货研究', bookId: 'MP_WXS_3017972175' },
  { name: '紫金天风期货研究所', bookId: 'MP_WXS_3591344548' },
];

const argv = process.argv.slice(2);
const argOf = (k, d) => {
  const i = argv.indexOf(k);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : d;
};
const WAIT_SEC = parseInt(argOf('--wait', '180'), 10);
const CHECK_ONLY = argv.includes('--check-only');

fs.mkdirSync(STORE, { recursive: true });

// ── cookie 持久化 ────────────────────────────────────────────────
function loadCookies() {
  if (!fs.existsSync(COOKIE_FILE)) return null;
  try {
    const j = JSON.parse(fs.readFileSync(COOKIE_FILE, 'utf8'));
    const have = new Set((j.cookies || []).map((c) => c.name));
    return { savedAt: j.savedAt, cookies: j.cookies || [], has: have };
  } catch {
    return null;
  }
}

// ── 用 cookie 直接打文章列表接口（不经浏览器，最贴近生产用法）──
async function fetchArticles(cookieHeader, bookId, offset = 0) {
  const url = `https://weread.qq.com/web/mp/articles?bookId=${encodeURIComponent(bookId)}&offset=${offset}`;
  const r = await fetch(url, {
    headers: {
      'User-Agent': UA,
      Accept: 'application/json, text/plain, */*',
      Referer: 'https://weread.qq.com/',
      Cookie: cookieHeader,
    },
  });
  const text = await r.text();
  let body = null;
  try { body = JSON.parse(text); } catch { /* 非 JSON */ }
  return { status: r.status, text, body };
}

// ── 主流程 ───────────────────────────────────────────────────────
const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, userAgent: UA });

const saved = loadCookies();
if (saved) {
  console.log(`[cookie] 发现已存 cookie（存于 ${saved.savedAt}）：${[...saved.has].join(', ')}`);
  await ctx.addCookies(saved.cookies);
} else {
  console.log('[cookie] 无已存 cookie');
}

if (!CHECK_ONLY) {
  const page = await ctx.newPage();
  await page.goto('https://weread.qq.com/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(2500);

  // 已经登录就不必再扫
  let already = (await ctx.cookies()).some((c) => c.name === 'wr_skey');
  if (!already) {
    console.log('\n[1] 打开登录弹窗…');
    for (const sel of ['text=登录', 'a:has-text("登录")', '[class*=login]']) {
      try {
        const el = page.locator(sel).first();
        if ((await el.count()) > 0 && (await el.isVisible())) { await el.click({ timeout: 5000 }); break; }
      } catch { /* 下一个 */ }
    }
    await page.waitForTimeout(3000);

    // 把二维码单独截出来（裁剪到 QR 元素），便于用户扫
    const qrSel = [
      '.wr_login_modal_qr_wrapper_container img',
      '.wr_login_modal_qr_wrapper_old img',
      '[class*=qr] img',
    ];
    let shotBox = null;
    for (const s of qrSel) {
      try {
        const el = page.locator(s).first();
        if ((await el.count()) > 0 && (await el.isVisible())) {
          shotBox = await el.boundingBox();
          if (shotBox && shotBox.width > 100) { await el.screenshot({ path: QR_SHOT }); break; }
        }
      } catch { /* 下一个 */ }
    }
    if (!shotBox) {
      await page.screenshot({ path: QR_SHOT });
      console.log('    （未精确定位 QR 元素，已截整页）');
    }
    console.log(`    ★ 二维码已保存：${QR_SHOT}`);
    console.log(`    ★ 请用微信「扫一扫」扫描该图`);
    console.log(`    （二维码约 2~3 分钟过期，本脚本会自动刷新并重存同一文件，`);
    console.log(`      所以你随时扫都行；未登录最长等待 ${WAIT_SEC} 秒）\n`);

    // 轮询登录成功：出现 wr_skey 即视为登录。
    // ★ 每 90 秒若仍未登录，就刷新二维码（过期后扫不出来，必须换新的）。
    const t0 = Date.now();
    let ok = false;
    let lastRefresh = Date.now();
    let refreshN = 0;
    while ((Date.now() - t0) / 1000 < WAIT_SEC) {
      const cks = await ctx.cookies();
      if (cks.some((c) => c.name === 'wr_skey')) { ok = true; break; }

      if (Date.now() - lastRefresh > 90000) {
        refreshN++;
        lastRefresh = Date.now();
        try {
          // 关闭并重开弹窗 = 拿一张新二维码
          await page.keyboard.press('Escape').catch(() => {});
          await page.waitForTimeout(500);
          for (const sel of ['text=登录', 'a:has-text("登录")', '[class*=login]']) {
            try {
              const el = page.locator(sel).first();
              if ((await el.count()) > 0 && (await el.isVisible())) { await el.click({ timeout: 4000 }); break; }
            } catch { /* 下一个 */ }
          }
          await page.waitForTimeout(2500);
          for (const s of qrSel) {
            try {
              const el = page.locator(s).first();
              if ((await el.count()) > 0 && (await el.isVisible())) {
                const bb = await el.boundingBox();
                if (bb && bb.width > 100) { await el.screenshot({ path: QR_SHOT }); break; }
              }
            } catch { /* 下一个 */ }
          }
          console.log(`    ↻ 已刷新二维码（第 ${refreshN} 次），文件仍是 ${QR_SHOT}`);
        } catch (e) {
          console.log(`    ⚠ 刷新二维码失败：${String(e).slice(0, 80)}`);
        }
      }
      await page.waitForTimeout(2000);
    }
    if (!ok) {
      console.log('✘ 等待超时，未检测到登录成功。可重跑本脚本再试。');
      await browser.close();
      process.exit(1);
    }
    console.log(`✔ 登录成功（用时 ${Math.round((Date.now() - t0) / 1000)} 秒）`);
  } else {
    console.log('\n[1] 已处于登录态，跳过扫码');
  }

  const cks = await ctx.cookies();
  fs.writeFileSync(COOKIE_FILE, JSON.stringify({
    savedAt: new Date().toISOString(),
    cookies: cks,
  }, null, 1));
  console.log(`[2] cookie 已保存 → ${COOKIE_FILE}`);
  console.log(`    含: ${cks.map((c) => c.name).join(', ')}`);
}

const cks = await ctx.cookies();
const cookieHeader = cks.map((c) => `${c.name}=${c.value}`).join('; ');
const hasSkey = cks.some((c) => c.name === 'wr_skey');

console.log('\n' + '='.repeat(74));
console.log(`[3] 决定性验证：/web/mp/articles 能否列出文章清单`);
console.log(`    wr_skey 存在 = ${hasSkey}`);
console.log('='.repeat(74));

let allOk = true;
for (const t of TARGETS) {
  const { status, text, body } = await fetchArticles(cookieHeader, t.bookId, 0);
  console.log(`\n── ${t.name}  (${t.bookId})`);
  console.log(`   HTTP ${status}  bytes=${text.length}`);
  if (body && typeof body === 'object') {
    const errCode = body.errCode ?? body.errcode;
    if (errCode && errCode !== 0) {
      console.log(`   ✘ errCode=${errCode} errMsg=${body.errMsg || body.errmsg}`);
      allOk = false;
      continue;
    }
    const keys = Object.keys(body);
    console.log(`   顶层键: ${keys.join(', ')}`);
    const arr = body.articles || body.reviews || (Array.isArray(body) ? body : null);
    if (Array.isArray(arr)) {
      console.log(`   ★ 文章数 = ${arr.length}`);
      arr.slice(0, 5).forEach((a, i) => {
        const rid = a.reviewId || a.id || '';
        const token = rid.includes('_') ? rid.split('_').pop() : rid;
        const when = a.updateTime || a.publishTime || a.createTime || 0;
        const dt = when ? new Date(when * 1000).toISOString().slice(0, 10) : '(无时间)';
        console.log(`      [${i}] ${dt}  ${String(a.title || '').slice(0, 44)}`);
        console.log(`          reviewId=${String(rid).slice(0, 46)}`);
        if (token && token.length > 10) console.log(`          原文直链 https://mp.weixin.qq.com/s/${token}`);
      });
      if (arr.length && arr[0]) {
        console.log(`   ★ articles[0] 全字段: ${JSON.stringify(arr[0]).slice(0, 420)}`);
      }
    } else {
      console.log(`   ⚠ 未找到文章数组，回包: ${JSON.stringify(body).slice(0, 350)}`);
      allOk = false;
    }
  } else {
    console.log(`   ⚠ 非 JSON: ${text.slice(0, 260)}`);
    allOk = false;
  }
}

console.log('\n' + '='.repeat(74));
console.log(allOk
  ? '✔ 全部目标号均可列出文章清单 —— 微信读书路线成立'
  : '✘ 仍有号拿不到清单，需看上面的错误码');
console.log('='.repeat(74));

await browser.close();
process.exit(allOk ? 0 : 1);
