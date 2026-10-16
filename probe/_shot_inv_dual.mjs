// 库存双口径实拍 —— 2026-10-16
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const b = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const ctx = await b.newContext({ viewport: { width: 1680, height: 1200 } });
const p = await ctx.newPage();

async function gotoFund(code) {
  await p.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded' });
  await p.waitForSelector('nav button[data-tab="fund"]', { timeout: 30000 });
  await p.click('nav button[data-tab="fund"]');
  // 等品种下拉被填充
  for (let i = 0; i < 60; i++) {
    const n = await p.evaluate(() => (document.getElementById('f-variety') || {}).options?.length || 0);
    if (n > 1) break;
    await p.waitForTimeout(500);
  }
  if (code) {
    await p.evaluate(c => {
      const s = document.getElementById('f-variety');
      s.value = c;
      s.dispatchEvent(new Event('change'));
    }, code);
  }
  // 等 ①卡库存块出双行
  for (let i = 0; i < 120; i++) {
    const t = await p.evaluate(() => (document.getElementById('f-strategy-body') || {}).textContent || '');
    const c = await p.evaluate(() => (document.getElementById('f-cycle-body') || {}).textContent || '');
    if (t.includes('社会库存') && t.includes('交易所仓单') && c.includes('双口径方向交叉验证')) break;
    await p.waitForTimeout(500);
  }
}

// ① 库存块（大字综合档位 + 双行明细）
await gotoFund('CF');
const card = await p.evaluate(() => {
  const el = document.getElementById('f-strategy-body');
  return el ? el.textContent.replace(/\s+/g, ' ').trim().slice(0, 400) : '';
});
console.log('CF ①卡文本:', card);
const box = await p.locator('#f-strategy-body').boundingBox();
if (box) {
  await p.screenshot({
    path: 'probe/_shot_inv_dual_card.png',
    clip: { x: box.x - 24, y: Math.max(0, box.y - 10), width: box.width + 48, height: box.height + 30 },
  });
}

// ② 库存维度明细（仓单图 + 四象限含社会库存与交叉验证）
const dim = await p.evaluate(() => {
  const el = document.getElementById('f-cycle-body');
  return el ? el.textContent.replace(/\s+/g, ' ').trim().slice(0, 400) : '';
});
console.log('CF 四象限文本:', dim);
const box2 = await p.locator('#chart-f-inv').boundingBox();
if (box2) {
  await p.screenshot({
    path: 'probe/_shot_inv_dual_cycle.png',
    clip: { x: box2.x - 20, y: Math.max(0, box2.y - 34), width: box2.width + 470, height: box2.height + 60 },
  });
}

// ③ 口径背离品种（BU：社会库存 0.01 低 / 仓单 0.89 高 → 矛盾）
await gotoFund('BU');
const bu = await p.evaluate(() => (document.getElementById('f-strategy-body') || {}).textContent.replace(/\s+/g, ' ').trim().slice(0, 400));
console.log('BU ①卡文本:', bu);
const vd = await p.evaluate(() => (document.getElementById('f-strategy-badge') || {}).textContent || '');
console.log('BU 徽标:', vd);
const box3 = await p.locator('#f-strategy-body').boundingBox();
if (box3) {
  await p.screenshot({
    path: 'probe/_shot_inv_dual_conflict.png',
    clip: { x: box3.x - 24, y: Math.max(0, box3.y - 10), width: box3.width + 48, height: box3.height + 30 },
  });
}

await b.close();
console.log('三张实拍已保存');
