// 检查国君数据页走势图: DOM 状态 + JS 报错 + 点击指标行后 #gt-chart 是否渲染
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const browser = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const page = await browser.newPage();
const jsErrors = [];
page.on('pageerror', e => jsErrors.push('PAGEERROR: ' + String(e).slice(0, 300)));
page.on('console', m => { if (m.type() === 'error') jsErrors.push('CONSOLE: ' + m.text().slice(0, 300)); });

try {
  await page.goto('http://127.0.0.1:5000/', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(3000);
  await page.click('button[data-tab="gtja"]');
  await page.waitForTimeout(4000);

  // ① 指标卡 + 走势图容器状态
  const info = await page.evaluate(() => {
    const chart = document.getElementById('gt-chart');
    const table = document.getElementById('gt-table');
    const inds = document.getElementById('gt-indicators');
    const badge = document.getElementById('gt-ind-badge');
    const r = el => {
      if (!el) return null;
      const b = el.getBoundingClientRect();
      return { w: Math.round(b.width), h: Math.round(b.height), children: el.children.length, html: el.innerHTML.slice(0, 120) };
    };
    return {
      chart: r(chart),
      table: r(table),
      inds: r(inds),
      badgeText: badge ? badge.textContent : null,
      chartParent: chart ? (chart.parentElement.className + ' / ' + (chart.parentElement.parentElement.className)) : null,
    };
  });
  console.log('① 容器状态:', JSON.stringify(info, null, 2));

  // ② 指标总览列表是否渲染出「查看」按钮
  const hasViewBtns = await page.evaluate(() => {
    const inds = document.getElementById('gt-indicators');
    return inds ? inds.innerHTML.length : 0;
  });
  console.log('② #gt-indicators innerHTML 长度:', hasViewBtns);

  // ③ 尝试点击第一行「查看」
  const clicked = await page.evaluate(() => {
    const btns = Array.from(document.querySelectorAll('#gt-indicators button'));
    if (!btns.length) return { found: 0 };
    btns[0].click();
    return { found: btns.length, label: btns[0].textContent.slice(0, 30) };
  });
  console.log('③ 点击查看:', JSON.stringify(clicked));
  await page.waitForTimeout(2500);

  // ④ 点击后走势图状态
  const after = await page.evaluate(() => {
    const c = document.getElementById('gt-chart');
    return {
      chartChildren: c ? c.children.length : -1,
      canvasCount: c ? c.querySelectorAll('canvas').length : 0,
      htmlLen: c ? c.innerHTML.length : 0,
      note: (document.getElementById('gt-chart-note') || {}).textContent || '',
    };
  });
  console.log('④ 点击后走势图:', JSON.stringify(after));

  console.log('\nJS 错误:', jsErrors.length);
  jsErrors.forEach(e => console.log('  ', e));
} catch (e) {
  console.log('异常:', String(e).slice(0, 300));
} finally {
  await browser.close();
}
