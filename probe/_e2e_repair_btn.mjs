// 一键修复按钮端到端闭环验证: 注入占位 → 按钮出现(计数=1) → 点击 → 后台修复 → 占位清零 → 按钮消失
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = 'http://127.0.0.1:5000';
const results = [];
function check(name, ok, detail = '') {
  results.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  | ' + detail : ''}`);
}

const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
  headless: true,
});
const ctx = await browser.newContext();
const page = await ctx.newPage();
page.on('dialog', async d => { console.log('  [dialog]', d.message().slice(0, 80)); await d.accept(); });

try {
  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(3000);

  // 切到数据仓库 tab
  await page.click('button[data-tab="warehouse"]');
  await page.waitForTimeout(2500);

  // ① 有占位时按钮应显示, 计数=1
  const btnVisible = await page.locator('#res-repair-btn').isVisible().catch(() => false);
  const count = await page.locator('#res-stuck-count').textContent().catch(() => '?');
  check('有占位时按钮可见', btnVisible, `count=${count}`);
  check('占位计数=1', count === '1', `count=${count}`);

  // ② 点击一键修复 (force: 过滤行可能被折叠壳遮挡)
  await page.locator('#res-repair-btn').scrollIntoViewIfNeeded().catch(() => {});
  await page.click('#res-repair-btn', { force: true, timeout: 10000 });
  await page.waitForTimeout(3000);
  const btnDisabled = await page.locator('#res-repair-btn').isDisabled().catch(() => null);
  check('点击后按钮进入修复中(disabled)', btnDisabled === true, `disabled=${btnDisabled}`);

  // ③ 轮询 status 直到 remaining=0 (后台线程约 60s)
  let remaining = -1;
  for (let i = 0; i < 24; i++) {
    await page.waitForTimeout(10000);
    try {
      const r = await page.evaluate(async () => {
        const res = await fetch('/api/research/reconclude-stuck/status');
        const d = await res.json();
        return d.remaining;
      });
      remaining = r;
      if (remaining === 0) break;
    } catch (e) { /* 重试 */ }
  }
  check('后台修复完成(remaining=0)', remaining === 0, `remaining=${remaining}`);

  // ④ 刷新页面 → 按钮应隐藏(占位清零)
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2500);
  await page.click('button[data-tab="warehouse"]');
  await page.waitForTimeout(2500);
  const btnHidden = await page.locator('#res-repair-btn').isVisible().catch(() => true);
  check('占位清零后按钮隐藏', btnHidden === false, `visible=${btnHidden}`);

  // ⑤ #1576 结论已恢复正式观点(非占位)
  const cm = await page.evaluate(async () => {
    const res = await fetch('/api/research/report/1576');
    const d = await res.json();
    return d.report ? (d.report.conclusion_md || '') : '';
  }).catch(() => '');
  check('#1576 观点已重新生成', cm.length > 60 && !cm.includes('观点生成失败'), `len=${cm.length}`);
} catch (e) {
  check('探针异常', false, String(e).slice(0, 200));
} finally {
  await browser.close();
}

const fails = results.filter(r => !r.ok);
console.log(`\n===== 汇总: ${results.length - fails.length}/${results.length} PASS =====`);
process.exit(fails.length ? 1 : 0);
