// 详细 dump: #res-repair-btn 的祖先链 + computed style
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const browser = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const page = await browser.newPage();

try {
  await page.goto('http://127.0.0.1:5000/', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(3000);
  await page.click('button[data-tab="warehouse"]');
  await page.waitForTimeout(3500);

  const info = await page.evaluate(() => {
    const b = document.getElementById('res-repair-btn');
    if (!b) return { exists: false };
    const chain = [];
    let el = b;
    for (let i = 0; i < 10 && el; i++) {
      const cs = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      chain.push({
        tag: el.tagName,
        id: el.id || '',
        cls: (el.className || '').toString().slice(0, 40),
        display: cs.display,
        visibility: cs.visibility,
        opacity: cs.opacity,
        position: cs.position,
        overflow: cs.overflow,
        rect: r.width > 0 ? `${Math.round(r.width)}x${Math.round(r.height)}@${Math.round(r.top)}` : '0',
      });
      el = el.parentElement;
    }
    // 自身全部 style
    const own = {};
    for (const k of ['display', 'visibility', 'opacity', 'pointer-events', 'z-index']) {
      own[k] = getComputedStyle(b)[k];
    }
    return { exists: true, own, chain };
  });
  console.log(JSON.stringify(info, null, 2));
} catch (e) {
  console.log('异常:', String(e).slice(0, 300));
} finally {
  await browser.close();
}
