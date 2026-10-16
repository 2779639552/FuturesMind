// 命中检测: 按钮可见时, elementFromPoint 在按钮中心命中谁? 判断真实用户是否点得到
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const browser = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const page = await browser.newPage();
page.on('dialog', async d => { console.log('[dialog]', d.message().slice(0, 60)); await d.accept(); });

try {
  await page.goto('http://127.0.0.1:5000/', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(3000);
  await page.click('button[data-tab="warehouse"]');
  await page.waitForTimeout(3500);

  const info = await page.evaluate(() => {
    const b = document.getElementById('res-repair-btn');
    if (!b) return { exists: false };
    const r = b.getBoundingClientRect();
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    const hit = document.elementFromPoint(cx, cy);
    // 找所有覆盖在按钮上的元素
    const overlays = [];
    const els = document.elementsFromPoint(cx, cy);
    els.slice(0, 4).forEach(e => {
      const sr = e.getBoundingClientRect();
      overlays.push(`${e.tagName}#${e.id || ''}.${(e.className || '').toString().split(' ')[0]} z=${getComputedStyle(e).zIndex} pos=${getComputedStyle(e).position} rect=${Math.round(sr.width)}x${Math.round(sr.height)}@${Math.round(sr.top)}`);
    });
    return {
      exists: true,
      display: b.style.display,
      visible: r.width > 0 && r.height > 0,
      rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
      hitIsBtn: hit === b || b.contains(hit),
      hitTag: hit ? `${hit.tagName}#${hit.id || ''}` : null,
      overlays,
    };
  });
  console.log('按钮命中检测:', JSON.stringify(info, null, 2));

  // 真实 mouse.click(Playwright 会先做可点击性检查, 失败则说明被拦)
  try {
    await page.locator('#res-repair-btn').click({ timeout: 8000 });
    console.log('PLAYWRIGHT 常规点击: 成功(未被拦)');
  } catch (e) {
    console.log('PLAYWRIGHT 常规点击: 失败 →', String(e).split('\n')[0]);
  }
} catch (e) {
  console.log('异常:', String(e).slice(0, 300));
} finally {
  await browser.close();
}
