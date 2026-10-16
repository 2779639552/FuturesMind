// 截图用户视角: 国君页默认态 + 点查看后 走势图区域
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const browser = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const jsErrors = [];
page.on('pageerror', e => jsErrors.push(String(e).slice(0, 200)));

try {
  await page.goto('http://127.0.0.1:5000/', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(3000);
  await page.click('button[data-tab="gtja"]');
  await page.waitForTimeout(6000);

  // 截图1: 默认态整页
  await page.screenshot({ path: 'C:/Users/lenovo/WorkBuddy/2026-09-07-16-38-57/_gtja_default.png', fullPage: false });

  // 默认态走势图区域
  const before = await page.evaluate(() => {
    const c = document.getElementById('gt-chart');
    const r = c.getBoundingClientRect();
    return { children: c.children.length, canvas: c.querySelectorAll('canvas').length,
             visible: r.height > 0 && r.width > 0, scrollY: window.scrollY,
             chartTop: r.top, chartHeight: r.height };
  });
  console.log('默认态 #gt-chart:', JSON.stringify(before));

  // 指标卡是否可见(可能被折叠)
  const cardInfo = await page.evaluate(() => {
    const h3 = Array.from(document.querySelectorAll('h3')).find(h => h.textContent.includes('指标总览'));
    if (!h3) return { found: false };
    const card = h3.closest('.sec-block') || h3.closest('.card');
    return { found: true, h3Text: h3.textContent.trim().slice(0, 30),
             cardClass: card ? card.className : null,
             cardH: card ? card.getBoundingClientRect().height : null };
  });
  console.log('指标卡折叠状态:', JSON.stringify(cardInfo));

  // 点击第一行「查看」
  const clicked = await page.evaluate(() => {
    const btn = document.querySelector('#gt-indicators button');
    if (!btn) return { found: false };
    btn.click(); return { found: true, label: btn.textContent };
  });
  console.log('点击查看:', JSON.stringify(clicked));
  await page.waitForTimeout(2500);

  // 滚动到图表
  await page.evaluate(() => document.getElementById('gt-chart').scrollIntoView({ behavior: 'instant', block: 'center' }));
  await page.waitForTimeout(800);
  await page.screenshot({ path: 'C:/Users/lenovo/WorkBuddy/2026-09-07-16-38-57/_gtja_afterclick.png' });

  const after = await page.evaluate(() => {
    const c = document.getElementById('gt-chart');
    const r = c.getBoundingClientRect();
    return { children: c.children.length, canvas: c.querySelectorAll('canvas').length,
             chartTop: r.top, chartHeight: r.height,
             note: (document.getElementById('gt-chart-note') || {}).textContent || '' };
  });
  console.log('点击后 #gt-chart:', JSON.stringify(after));

  console.log('JS 错误:', jsErrors.length, jsErrors.join(';'));
} catch (e) {
  console.log('异常:', String(e).slice(0, 300));
} finally {
  await browser.close();
}
