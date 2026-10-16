// 诊断: 一键修复按钮的真实可点性 + reconcludeStuck 方法可用性
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const browser = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const page = await browser.newPage();
const jsErrors = [];
page.on('pageerror', e => jsErrors.push('PAGEERROR: ' + String(e).slice(0, 200)));
page.on('console', m => { if (m.type() === 'error') jsErrors.push('CONSOLE: ' + m.text().slice(0, 200)); });
page.on('dialog', async d => { console.log('[dialog]', d.message().slice(0, 100)); await d.accept(); });

try {
  await page.goto('http://127.0.0.1:5000/', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(3000);
  await page.click('button[data-tab="warehouse"]');
  await page.waitForTimeout(2500);

  // ① 按钮是否存在 + onclick 是否绑定
  const btnInfo = await page.evaluate(() => {
    const b = document.getElementById('res-repair-btn');
    if (!b) return { exists: false };
    const parent = b.parentElement;
    const box = b.getBoundingClientRect();
    // 检查是否被遮挡: 用 elementFromPoint 看点击点命中谁
    const cx = box.left + box.width / 2, cy = box.top + box.height / 2;
    const hit = document.elementFromPoint(cx, cy);
    return {
      exists: true,
      onclick: b.getAttribute('onclick'),
      display: b.style.display,
      rect: { x: box.x, y: box.y, w: box.width, h: box.height },
      hitId: hit ? (hit.id || hit.tagName) : null,
      hitIsBtn: hit === b || b.contains(hit),
      stuckCount: document.getElementById('res-stuck-count')?.textContent,
    };
  });
  console.log('① 按钮状态:', JSON.stringify(btnInfo, null, 2));

  // ② App.reconcludeStuck 是否存在
  const methodOk = await page.evaluate(() => typeof App.reconcludeStuck === 'function' && typeof App.refreshStuckCount === 'function');
  console.log('② reconcludeStuck/refreshStuckCount 方法:', methodOk);

  // ③ 直接调 reconcludeStuck(绕过点击命中问题, 验证前端→后端链路)
  const postResult = await page.evaluate(async () => {
    const r = await fetch('/api/research/reconclude-stuck', { method: 'POST' });
    return await r.json();
  });
  console.log('③ 直接 fetch POST:', JSON.stringify(postResult));

  // ④ 用 JS 派发 click 到按钮
  await page.evaluate(() => document.getElementById('res-repair-btn').click());
  await page.waitForTimeout(1500);
  const afterClick = await page.evaluate(() => ({
    disabled: document.getElementById('res-repair-btn')?.disabled,
    text: document.getElementById('res-repair-btn')?.textContent,
  }));
  console.log('④ 派发 click 后按钮:', JSON.stringify(afterClick));

  await page.waitForTimeout(3000);
  console.log('\nJS 错误数:', jsErrors.length);
  jsErrors.forEach(e => console.log('  ', e));
} catch (e) {
  console.log('异常:', String(e).slice(0, 300));
} finally {
  await browser.close();
}
