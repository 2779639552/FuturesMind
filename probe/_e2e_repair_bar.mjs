// 最终验证: 常驻修复条 → 有占位显示 + 真实鼠标可点 + 后台修复 → 清零隐藏
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const browser = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const page = await browser.newPage();
const results = [];
const check = (n, ok, d = '') => { results.push({ n, ok }); console.log(`${ok ? 'PASS' : 'FAIL'}  ${n}${d ? '  | ' + d : ''}`); };
const jsErrors = [];
page.on('pageerror', e => jsErrors.push(String(e).slice(0, 150)));
page.on('dialog', async d => { console.log('  [dialog]', d.message().slice(0, 60)); await d.accept(); });

try {
  await page.goto('http://127.0.0.1:5000/', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(3000);
  await page.click('button[data-tab="warehouse"]');
  await page.waitForTimeout(3000);

  // ① 常驻条可见 + 计数=1(占位已注入)
  await page.waitForTimeout(1200);
  const bar = page.locator('#res-repair-bar');
  const barVisible = await bar.isVisible().catch(() => false);
  const cnt = await page.locator('#res-stuck-count').textContent().catch(() => '?');
  check('常驻条可见(有占位)', barVisible, `count=${cnt}`);
  check('计数=1', cnt === '1', `count=${cnt}`);

  // ② 命中检测: 按钮中心 elementFromPoint 是否命中按钮自身
  const hit = await page.evaluate(() => {
    const b = document.getElementById('res-repair-btn');
    const r = b.getBoundingClientRect();
    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return { hitIsBtn: hit === b || b.contains(hit), rect: `${Math.round(r.width)}x${Math.round(r.height)}@${Math.round(r.top)}` };
  });
  check('按钮中心可命中(真实可点)', hit.hitIsBtn, JSON.stringify(hit));

  // ③ Playwright 真实鼠标点击
  await page.locator('#res-repair-btn').click({ timeout: 10000 }).catch(e => check('真实点击', false, String(e).slice(0, 60)));
  await page.waitForTimeout(2500);

  // ④ 轮询清零
  let remaining = -1;
  for (let i = 0; i < 30; i++) {
    await page.waitForTimeout(10000);
    remaining = await page.evaluate(async () => (await (await fetch('/api/research/reconclude-stuck/status')).json()).remaining);
    if (remaining === 0) break;
  }
  check('后台修复完成(remaining=0)', remaining === 0, `remaining=${remaining}`);

  // ⑤ 刷新 → 常驻条隐藏
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3000);
  await page.click('button[data-tab="warehouse"]');
  await page.waitForTimeout(3000);
  const hidden = await page.locator('#res-repair-bar').isVisible().catch(() => true);
  check('清零后常驻条隐藏', hidden === false, `visible=${hidden}`);

  check('页面无 JS 错误', jsErrors.length === 0, jsErrors.join(';'));
} catch (e) {
  check('探针异常', false, String(e).slice(0, 200));
} finally {
  await browser.close();
}
const fails = results.filter(r => !r.ok);
console.log(`\n===== 汇总: ${results.length - fails.length}/${results.length} PASS =====`);
process.exit(fails.length ? 1 : 0);
