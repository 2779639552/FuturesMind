// E2E 验收: 情绪数据页精简（2026-10-13）
// 用法: node probe/_e2e_sentiment_trim.mjs [BASE]
// 覆盖:
//   A 子导航只剩 2 个按钮（综合分析 / 事件）
//   B 5 个已删模块在 DOM / 小模块索引里都不再出现
//   C 两个保留子模块能正常切换并渲染
//   D 「更新日志」卡片已消失，但 #update-log 隐藏载体仍在（runDataUpdate 依赖它）
//   E 「一键更新」控件完好（⚠️ 绝不点击，会真启动采集）
//   F 双主题截图 + 无 JS 错误
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = process.argv[2] || 'http://127.0.0.1:5000';
let pass = 0, fail = 0;
const log = (ok, msg) => { ok ? pass++ : fail++; console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${msg}`); };

const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errs = [];
page.on('pageerror', e => errs.push(e.message));
await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(2500);

// 真实点击进入情绪页（App 不在 window 上，不能直接调 switchTab）
console.log('\n[A] 子导航按钮');
await page.click('nav button[data-tab="sentiment"]');
await page.waitForTimeout(3000);

const navBtns = await page.evaluate(() =>
  Array.from(document.querySelectorAll('#analysis-subnav button')).map(b => (b.textContent || '').trim()));
console.log(`  (info) 子导航 = ${JSON.stringify(navBtns)}`);
log(navBtns.length === 2, `子导航按钮数 = ${navBtns.length}（应为 2）`);
log(navBtns.includes('综合分析') && navBtns.includes('事件'), '两个按钮 = 综合分析 / 事件');

console.log('\n[B] 已删模块不再出现');
const removed = ['作者', '框架说明', 'Agent验证', 'Agent回测', '图谱'];
const domHas = await page.evaluate(list => list.filter(n =>
  !!Array.from(document.querySelectorAll('#analysis-subnav button')).find(b => (b.textContent || '').includes(n))), removed);
log(domHas.length === 0, `子导航里无已删项（命中 ${JSON.stringify(domHas)}）`);

const indexLabels = await page.evaluate(() =>
  Array.from(document.querySelectorAll('#tab-sentiment .sec-index-item')).map(x => (x.textContent || '').trim()));
console.log(`  (info) 小模块索引项 = ${JSON.stringify(indexLabels)}`);
const badIdx = indexLabels.filter(t => removed.some(n => t.includes(n)) && t.includes('分析工具'));
log(badIdx.length === 0, `小模块索引里无「分析工具 · 已删项」（命中 ${JSON.stringify(badIdx)}）`);

const leftoverIds = await page.evaluate(() =>
  ['asub-authors', 'asub-framework', 'asub-agentval', 'asub-agentbt', 'asub-graph',
   'authors-table', 'chart-graph', 'chart-sankey'].filter(id => !!document.getElementById(id)));
log(leftoverIds.length === 0, `已删容器 id 全部不存在（残留 ${JSON.stringify(leftoverIds)}）`);

console.log('\n[C] 保留子模块可切换');
const subVisible = (id) => page.evaluate(i => {
  const el = document.getElementById(i);
  return !!el && getComputedStyle(el).display !== 'none';
}, id);
log(await subVisible('asub-comprehensive'), '默认落在「综合分析」(asub-comprehensive 可见)');

// 点「事件」
await page.evaluate(() => { document.querySelector('#analysis-subnav button[data-asub="events"]').click(); return true; });
await page.waitForTimeout(1800);
log(await subVisible('asub-events'), '点「事件」后 asub-events 可见');
log(!(await subVisible('asub-comprehensive')), '此时「综合分析」已隐藏（互斥正确）');

// 点回「综合分析」
await page.evaluate(() => { document.querySelector('#analysis-subnav button[data-asub="comprehensive"]').click(); return true; });
await page.waitForTimeout(2500);
log(await subVisible('asub-comprehensive'), '点回「综合分析」恢复可见');
const compContent = await page.evaluate(() => {
  const el = document.getElementById('asub-comprehensive');
  return el ? (el.innerText || '').length : 0;
});
log(compContent > 50, `「综合分析」有渲染内容（文本 ${compContent} 字）`);

console.log('\n[D] 更新日志已隐藏、载体仍在');
const logCard = await page.evaluate(() => {
  const el = document.getElementById('update-log');
  if (!el) return { exists: false };
  const card = el.closest('.card');
  return {
    exists: true,
    visible: getComputedStyle(el).display !== 'none',
    inCard: !!card,
    cardHasH3: card ? !!card.querySelector('h3') : false,
    h3Text: card && card.querySelector('h3') ? card.querySelector('h3').textContent.trim() : '',
  };
});
console.log(`  (info) #update-log = ${JSON.stringify(logCard)}`);
log(logCard.exists, '#update-log 载体仍存在（SSE 写入依赖它，不可删）');
log(logCard.visible === false, '更新日志容器不可见');
log(!logCard.cardHasH3, `没有「更新日志」标题卡（h3=${JSON.stringify(logCard.h3Text)}）`);

const anyUpdateLogTitle = await page.evaluate(() =>
  Array.from(document.querySelectorAll('#tab-sentiment h3')).map(h => h.textContent.trim()).filter(t => t.includes('更新日志')));
log(anyUpdateLogTitle.length === 0, `页面上无「更新日志」标题（命中 ${JSON.stringify(anyUpdateLogTitle)}）`);

console.log('\n[E] 一键更新控件完好（不点击）');
const upd = await page.evaluate(() => {
  const b = document.getElementById('btn-update');
  if (!b) return { exists: false };
  return { exists: true, disabled: b.disabled, text: b.textContent.trim(),
           perkw: !!document.getElementById('update-perkw'),
           plat: document.querySelectorAll('#tab-sentiment input[type=checkbox]').length };
});
console.log(`  (info) 更新控件 = ${JSON.stringify(upd)}`);
log(upd.exists && upd.disabled === false, '「一键更新」按钮存在且可用（未被误禁用）');
log(upd.perkw, '配置输入框仍在');

console.log('\n[F] 双主题截图 + JS 错误');
for (const theme of ['light', 'dark']) {
  await page.evaluate(t => {
    while ((document.documentElement.getAttribute('data-theme') || 'dark') !== t) {
      document.querySelector('.theme-toggle').click();
    }
  }, theme);
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `probe/_shot_sentiment_trim_${theme}.png`, fullPage: false });
  console.log(`  (shot) probe/_shot_sentiment_trim_${theme}.png`);
}
log(errs.length === 0, `页面无 JS 错误（${errs.length} 个）${errs.slice(0, 3).join(' | ')}`);

await browser.close();
console.log(`\n结果: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
