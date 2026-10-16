// 三项改动的浏览器 E2E（真实点击 + DOM 度量 + 图表渲染核实）
// ① 数据仓库：无「四源取数状态」，原「③ 指标总览」已升为「②」
// ② 基本面量化：无「价格 vs 仓单库存」；「价格-库存关联分析」保留
// ③ 研报基本面指标：默认收起 → 点标题栏展开 → 七类图已渲染 → 再点收起 → 切品种后状态保持
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = 'http://127.0.0.1:5000';
let pass = 0, fail = 0;
const problems = [];
const log = (ok, label, extra = '') => {
  if (ok) { pass++; console.log(`  ✔ ${label}`); }
  else { fail++; problems.push(label); console.log(`  ✘ ${label} ${extra}`); }
};

const b = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--no-sandbox'] });
const ctx = await b.newContext({ viewport: { width: 1680, height: 1050 } });
const p = await ctx.newPage();
const errs = [];
p.on('pageerror', e => errs.push(String(e).slice(0, 180)));
p.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 150)); });

await p.goto(BASE, { waitUntil: 'domcontentloaded' });
await p.waitForSelector('nav button[data-tab="warehouse"]', { timeout: 30000 });
console.log('导航已就绪\n');

// ═══ ① 数据仓库 ═══
console.log('[1] 数据仓库：四源取数状态已删');
await p.click('nav button[data-tab="warehouse"]');
await p.waitForTimeout(3500);
let wh = await p.evaluate(() => {
  const t = document.body.innerText;
  return {
    hasSrcCard: t.includes('四源取数状态'),
    hasSrcContainer: !!document.getElementById('gt-sources'),
    hasInd: t.includes('指标总览'),
    indTitles: (t.match(/②\s*指标总览/g) || []).length,
    old3: t.includes('③ 指标总览'),
    cards: Array.from(document.querySelectorAll('#tab-warehouse .card h3')).map(h => h.innerText.trim().slice(0, 26)),
    queries: (t.match(/①\s*查询控制/g) || []).length,
  };
});
log(!wh.hasSrcCard, '页面无「四源取数状态」', JSON.stringify(wh.cards));
log(!wh.hasSrcContainer, '#gt-sources 容器不存在（DOM 核实）');
log(wh.hasInd && wh.indTitles >= 1, '「② 指标总览」在位', 'indTitles=' + wh.indTitles);
log(!wh.old3, '旧「③ 指标总览」已无');
log(wh.queries >= 1, '「① 查询控制」仍在（卖方数据块入口未被误删）');
console.log('     warehouse 卡片序列: ' + JSON.stringify(wh.cards));

// ═══ ② 基本面量化 ═══
console.log('\n[2] 基本面量化：价格 vs 仓单库存已删');
await p.click('nav button[data-tab="fund"]');
await p.waitForTimeout(8000);   // 等 dashboard 接口回填
let fd = await p.evaluate(() => {
  const t = document.body.innerText;
  return {
    hasPi: t.includes('价格 vs 仓单库存'),
    hasPiDom: !!document.getElementById('chart-f-pi'),
    hasPiBadge: !!document.getElementById('f-pi-badge'),
    hasAnalysis: t.includes('价格-库存关联分析'),
    analysisBody: (document.getElementById('f-analysis-body') || {}).innerText || '',
    hasInv: t.includes('仓单库存'),
    cycle: (document.getElementById('f-cycle-body') || {}).innerText || '',
  };
});
log(!fd.hasPi, '页面无「价格 vs 仓单库存」');
log(!fd.hasPiDom && !fd.hasPiBadge, '#chart-f-pi / #f-pi-badge 已从 DOM 移除');
log(fd.hasAnalysis, '「价格-库存关联分析」保留', fd.analysisBody.slice(0, 40));
log(fd.analysisBody.length > 10 && !fd.analysisBody.includes('加载中'),
    '关联分析有真实数据（未因删图而断链）', fd.analysisBody.slice(0, 50));

// ═══ ③ 研报基本面指标折叠块 ═══
console.log('\n[3] 研报基本面指标：折叠块（默认收起）');
let rs = await p.evaluate(() => {
  const body = document.getElementById('f-research-body');
  const head = document.getElementById('f-research-head');
  const card = body ? body.closest('.card') : null;
  const charts = body ? Array.from(body.querySelectorAll('[id^="chart-f-r-"]')) : [];
  const canvases = charts.map(c => { const cv = c.querySelector('canvas'); return cv ? cv.width : 0; });
  return {
    hasHead: !!head, hasBody: !!body,
    open: body ? body.dataset.open : null,
    maxH: body ? getComputedStyle(body).maxHeight : null,
    bodyH: body ? body.clientHeight : -1,
    cards: card ? card.querySelectorAll('h3').length : -1,
    innerCards: body ? body.querySelectorAll('.card').length : -1,
    labels: body ? Array.from(body.querySelectorAll('div')).filter(d => /^研报[^ ]/.test(d.innerText.trim()) && d.innerText.includes('点')).map(d => d.innerText.trim().split('\n')[0].slice(0, 22)) : [],
    chartIds: charts.map(c => c.id),
    canvasW: canvases,
    caret: (document.getElementById('f-research-caret') || {}).textContent || '',
  };
});
log(rs.hasHead && rs.hasBody, '折叠块 head/body 已生成');
log(rs.open === '0', '**默认收起**（data-open=0）', 'open=' + rs.open);
log(parseFloat(rs.maxH) === 0 || rs.bodyH === 0, '收起态内容高度为 0', `maxH=${rs.maxH} h=${rs.bodyH}`);
log(rs.cards === 1, '合并卡内只有 1 个 <h3>（铁律）', 'h3=' + rs.cards);
log(rs.innerCards === 0, '内部不再有 7 张 .card（已整合）', 'innerCards=' + rs.innerCards);
log(rs.chartIds.length >= 1, `卡内图容器已生成（${rs.chartIds.length} 个）`, rs.chartIds.slice(0, 3).join(','));
log(rs.canvasW.every(w => w > 0) && rs.canvasW.length > 0,
    '★ 收起态下图表**已渲染**（canvas 有宽度，非 0×0 空白）', JSON.stringify(rs.canvasW.slice(0, 4)));
log(rs.caret.includes('展开'), '收起态提示为「展开」', rs.caret);
console.log('     七类口径: ' + JSON.stringify(rs.labels.slice(0, 8)));

// 展开
await p.click('#f-research-head');
await p.waitForTimeout(900);
let opened = await p.evaluate(() => {
  const body = document.getElementById('f-research-body');
  const canvases = Array.from(body.querySelectorAll('[id^="chart-f-r-"] canvas'));
  return {
    open: body.dataset.open, h: body.clientHeight,
    canvases: canvases.length,
    allW: canvases.every(cv => cv.width > 0),
    caret: (document.getElementById('f-research-caret') || {}).textContent || '',
  };
});
log(opened.open === '1', '点击后展开（data-open=1）');
log(opened.h > 300, '展开后有实际高度', 'h=' + opened.h);
log(opened.canvases >= 1 && opened.allW, `展开后 ${opened.canvases} 张图 canvas 均正常渲染`);
log(opened.caret.includes('收起'), '展开态提示为「收起」', opened.caret);
await p.screenshot({ path: 'probe/_shot_rs_expanded.png' });

// 切品种，验证折叠状态保持
let before = await p.evaluate(() => {
  const sel = document.getElementById('f-variety');
  return { val: sel ? sel.value : null, opts: sel ? sel.options.length : 0 };
});
let swung = await p.evaluate(async () => {
  const sel = document.getElementById('f-variety');
  if (!sel || sel.options.length < 2) return { ok: false };
  const other = Array.from(sel.options).map(o => o.value).find(v => v !== sel.value);
  sel.value = other;
  sel.dispatchEvent(new Event('change'));
  return { ok: true, to: other };
});
await p.waitForTimeout(7000);
let after = await p.evaluate(() => {
  const body = document.getElementById('f-research-body');
  return { open: body ? body.dataset.open : null, h: body ? body.clientHeight : -1 };
});
if (swung.ok) {
  log(after.open === '1', `切品种(${before.val}→${swung.to})后**折叠状态保持展开**`,
      `open=${after.open} h=${after.h}`);
} else {
  console.log('  info 品种下拉不足 2 项，跳过状态保持用例');
}

// 再点收起
await p.click('#f-research-head');
await p.waitForTimeout(700);
let closed = await p.evaluate(() => {
  const body = document.getElementById('f-research-body');
  return { open: body.dataset.open, maxH: getComputedStyle(body).maxHeight };
});
log(closed.open === '0', '再次点击可收起');

await p.screenshot({ path: 'probe/_shot_rs_collapsed.png', fullPage: false });

console.log('\n=== JS 错误 ===');
console.log(errs.length ? errs.slice(0, 6).join('\n') : '  无 ✔');
log(errs.length === 0, '0 JS 错误', errs.slice(0, 3).join(' | '));

console.log('\n' + '='.repeat(60));
console.log(`结果: ${pass} PASS / ${fail} FAIL`);
if (problems.length) { console.log('失败项:'); problems.forEach(x => console.log('   - ' + x)); }
await b.close();
process.exit(fail ? 1 : 0);
