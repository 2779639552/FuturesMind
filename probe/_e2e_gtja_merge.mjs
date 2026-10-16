// E2E: 国君页三卡合并后的交互链路
//  - ③ 一张卡内同时含 总览表 / 走势图 / 明细数据
//  - 索引从 5 项降为 3 项
//  - 点「查看」→ 图表与明细**就地**出数（同一张卡内），无需展开兄弟卡
// 用法: node probe/_e2e_gtja_merge.mjs
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = process.argv[2] || 'http://127.0.0.1:5000';
let pass = 0, fail = 0;
const log = (ok, msg) => { ok ? pass++ : fail++; console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${msg}`); };

const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1040 } });
const errs = [];
page.on('pageerror', e => errs.push(e.message));
await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(2000);
await page.evaluate(() => App.switchTab('gtja'));

// 等指标总览出数（全量数据集首次约 6~18s）
let ok = false;
for (let i = 0; i < 45; i++) {
  await page.waitForTimeout(2000);
  ok = await page.evaluate(() => {
    const el = document.getElementById('gt-indicators');
    return !!el && el.querySelectorAll('tbody tr').length > 0;
  });
  if (ok) break;
}
log(ok, '指标总览已出数');

console.log('\n[1] 结构：三块同卡 + 索引收敛');
const st = await page.evaluate(() => {
  const ind = document.getElementById('gt-indicators');
  const chart = document.getElementById('gt-chart');
  const tbl = document.getElementById('gt-table');
  const card = ind ? ind.closest('.card') : null;
  const labels = Array.from(document.querySelectorAll('#tab-gtja .sec-index-list .sec-index-item'))
    .map(b => (b.textContent || '').replace('✓', '').trim());
  return {
    sameCard: !!card && chart && tbl && chart.closest('.card') === card && tbl.closest('.card') === card,
    cardCount: document.querySelectorAll('#tab-gtja > .sec-block').length,
    cardLabels: labels,
    blocks: Array.from(document.querySelectorAll('#tab-gtja .sec-block'))
      .map(b => (b.querySelector('h3') ? b.querySelector('h3').textContent.trim().slice(0, 30) : '')),
    h3Count: card ? card.querySelectorAll('h3').length : -1,
    idxTitle: (() => { const t = card && card.querySelector('h3'); return t ? t.textContent.trim() : ''; })(),
  };
});
log(st.sameCard, `总览表/走势图/明细 同在 ① 张卡内`);
log(st.cardCount === 3, `国君页小模块数 = ${st.cardCount} (期望 3: 覆盖矩阵/四源状态/合并卡)`);
log(st.h3Count === 1, `合并卡内 h3 数 = ${st.h3Count} (期望 1 —— 多塞 h3 会令索引串名)`);
log(/指标总览/.test(st.idxTitle), `索引名 = "${st.idxTitle}"`);
console.log('     索引项: ' + st.cardLabels.join(' | '));

// 展开合并卡
await page.evaluate(async () => {
  const card = document.getElementById('gt-indicators').closest('.card');
  const blk = card.closest('.sec-block');
  if (blk) { blk.classList.remove('is-collapsed'); blk.classList.add('sec-open'); blk.style.maxHeight = 'none'; }
  await new Promise(r => setTimeout(r, 300));
});

console.log('\n[2] 点「查看」→ 就地出图出表');
const before = await page.evaluate(() => ({
  chartText: (document.getElementById('gt-chart') || {}).innerHTML || '',
  tblRows: document.querySelectorAll('#gt-table tbody tr').length,
}));
const clicked = await page.evaluate(() => {
  const btn = document.querySelector('#gt-indicators tbody tr button');
  if (!btn) return false;
  btn.click();
  return true;
});
log(clicked, '找到并点击第一行「查看」');
await page.waitForTimeout(900);

const after = await page.evaluate(() => {
  const c = document.getElementById('gt-chart');
  const canvas = c ? c.querySelector('canvas') : null;
  const tbl = document.getElementById('gt-table');
  return {
    canvasW: canvas ? Math.round(canvas.getBoundingClientRect().width) : 0,
    canvasH: canvas ? Math.round(canvas.getBoundingClientRect().height) : 0,
    chartBadge: (document.getElementById('gt-chart-badge') || {}).textContent || '',
    chartNote: (document.getElementById('gt-chart-note') || {}).innerHTML || '',
    tblRows: tbl ? tbl.querySelectorAll('tbody tr').length : 0,
    tblBadge: (document.getElementById('gt-tbl-badge') || {}).textContent || '',
    rowHighlighted: Array.from(document.querySelectorAll('#gt-indicators tbody tr'))
      .filter(tr => tr.style.background).length,
  };
});
log(after.canvasW > 100 && after.canvasH > 100,
  `走势图已渲染且尺寸正常 (${after.canvasW}×${after.canvasH}px) —— 非 0×0 空白`);
log(!!after.chartBadge, `图表徽标带指标名: "${after.chartBadge}"`);
log(/分位/.test(after.chartNote), '图表脚注含分位口径');
log(after.tblRows > 0, `明细表已出 ${after.tblRows} 行`);
log(after.tblBadge === `${after.tblRows} 行`, `明细徽标一致: "${after.tblBadge}"`);
log(after.rowHighlighted === 1, `选中行高亮 ${after.rowHighlighted} 行(便于在长表里定位)`);

// 图表与明细是否在同一屏（合并的意义所在）
const geo = await page.evaluate(() => {
  const chart = document.getElementById('gt-chart').getBoundingClientRect();
  const tbl = document.getElementById('gt-table').getBoundingClientRect();
  const ind = document.getElementById('gt-indicators').getBoundingClientRect();
  return { gap: Math.round(tbl.top - chart.bottom), order: chart.top > ind.top };
});
log(geo.order, '走势图位于总览表之下（视线延续，无需跳卡）');
log(Math.abs(geo.gap) < 200, `图表与明细相邻 (间隔 ${geo.gap}px)`);

console.log(`\nJS 报错: ${errs.length ? errs.slice(0, 3).join(' | ') : '(无)'}`);
console.log(`\n结果: ${pass} 通过, ${fail} 失败`);
await browser.close();
process.exit(fail ? 1 : 0);
