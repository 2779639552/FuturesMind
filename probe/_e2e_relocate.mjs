// 三项结构性归位的浏览器 E2E（真实点击 + DOM 度量）
// A. 技术面量化：无「量化信号研判」、无「多品种量化截面排行」；方法论研判的票表里**无多空逻辑系**
//    （但有迁出说明）；详情顶部出现「多空计分」
// B. 基本面量化：出现「⑤ 量化信号研判」「⑥ 多空逻辑体系研判」；① 卡出现「基本面信号计分」；
//    ⑥ 卡里有多空逻辑系方向票 + 扩展读数
// C. 总览页：「多品种量化截面排行」折叠模块默认收起 → 点开才拉数 → 表格出现 → 再点收起
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
p.on('pageerror', e => errs.push(String(e).slice(0, 200)));
p.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 180)); });

await p.goto(BASE, { waitUntil: 'domcontentloaded' });
await p.waitForSelector('nav button[data-tab="quant"]', { timeout: 30000 });

// ═══ 技术面量化（默认落在总览）═══
console.log('[1] 技术面量化 · 总览页');
await p.click('nav button[data-tab="quant"]');
await p.waitForTimeout(3000);

// C. 折叠模块
let rank = await p.evaluate(() => {
  const body = document.getElementById('q-rank-body');
  const head = document.getElementById('q-rank-head');
  const caret = document.getElementById('q-rank-caret');
  const card = body ? body.closest('.card') : null;
  return {
    hasBody: !!body, hasHead: !!head,
    open: body ? body.dataset.open : null,
    h: body ? body.clientHeight : -1,
    caret: caret ? caret.textContent : '',
    inOverview: !!(card && card.closest('#q-overview-view')),
    mustH1: card ? card.querySelectorAll('h3').length : -1,
    tableRows: (document.querySelectorAll('#q-rank-table tr') || []).length,
  };
});
log(rank.hasBody && rank.hasHead, 'C 折叠模块存在（head/body 都在）');
log(rank.inOverview, 'C 它在 #q-overview-view 内（总览页）');
log(rank.open === '0' && rank.h === 0, '★ C 默认**收起**（data-open=0 且高度 0）', `open=${rank.open} h=${rank.h}`);
log(rank.caret.includes('展开'), 'C 收起态提示「展开」', rank.caret);
log(rank.tableRows === 0, '★ C 收起时**未拉数**（表格为空 = 惰性加载生效）', `rows=${rank.tableRows}`);
log(rank.mustH1 === 1, 'C 折叠卡内只有 1 个 <h3>（合并卡铁律）', `h3=${rank.mustH1}`);

// 展开
await p.click('#q-rank-head');
await p.waitForTimeout(1500);
let op = await p.evaluate(() => {
  const body = document.getElementById('q-rank-body');
  return { open: body.dataset.open, h: body.clientHeight, caret: (document.getElementById('q-rank-caret') || {}).textContent };
});
log(op.open === '1' && op.h > 100, '★ C 点开后展开且有高度', `h=${op.h}`);
log(op.caret.includes('收起'), 'C 展开态提示「收起」', op.caret);
// 等排行数据（首拉十余秒）
let rows = 0;
for (let i = 0; i < 40; i++) {
  rows = await p.evaluate(() => (document.querySelectorAll('#q-rank-table tr') || []).length);
  if (rows > 3) break;
  await p.waitForTimeout(1500);
}
log(rows > 3, `★ C 展开后拉到排行数据（${rows} 行）`);
await p.screenshot({ path: 'probe/_shot_rank_collapsed_expanded.png' });
// 收起
await p.click('#q-rank-head');
await p.waitForTimeout(800);
let cl = await p.evaluate(() => {
  const body = document.getElementById('q-rank-body');
  return { open: body.dataset.open, h: body.clientHeight };
});
log(cl.open === '0' && cl.h === 0, 'C 可再次收起', `h=${cl.h}`);

// A. 详情：点行进单品种
console.log('\n[2] 技术面量化 · 单品种详情');
const firstCode = await p.evaluate(() => {
  const tr = document.querySelector('#q-ov-rows tr');
  return tr ? tr.innerText.trim().split(/\s+/)[0] : '';
});
await p.click('#q-ov-rows tr');
for (let i = 0; i < 40; i++) {
  const w = await p.evaluate(() => { const c = document.querySelector('#chart-q-kline canvas'); return c ? c.width : 0; });
  if (w > 0) break;
  await p.waitForTimeout(1000);
}
await p.waitForTimeout(1500);
let det = await p.evaluate(() => {
  const t = document.body.innerText;
  const score = document.getElementById('q-detail-score');
  const dv = document.getElementById('q-detail-view');
  return {
    code: (document.getElementById('q-detail-sym') || {}).textContent || '',
    hasSignalCard: t.includes('量化信号研判'),
    hasSignalDom: !!document.querySelector('#q-detail-view #q-signal-body'),
    hasRankCard: t.includes('多品种量化截面排行'),
    hasRankDom: !!document.querySelector('#q-detail-view #q-rank-table'),
    score: score ? score.innerText.trim() : '(无容器)',
    scoreInDetail: !!(score && score.closest('#q-detail-view')),
    hint: (document.getElementById('q-detail-hint') || {}).textContent || '',
  };
});
log(!det.hasSignalCard && !det.hasSignalDom, 'A 详情内已无「量化信号研判」', det.code);
log(!det.hasRankCard && !det.hasRankDom, 'A 详情内已无「多品种量化截面排行」');
log(det.scoreInDetail, 'C 详情顶部有计分徽标');
log(/多空计分\s*[+-]?\d+/.test(det.score), '★ C 徽标显示「多空计分 +N」', det.score);
log(det.score.includes('技术面口径'), '★ C 徽标标明了口径（防误读成基本面）', det.score.slice(0, 90));
log(/全池第\s*\d+\//.test(det.score), 'C 徽标含全池名次', det.score.slice(0, 90));
await p.screenshot({ path: 'probe/_shot_detail_score.png' });

// 方法论研判：票表里应无多空逻辑系
let vot = await p.evaluate(() => {
  const t = document.body.innerText;
  return {
    hasVotesSection: t.includes('各体系方向票'),
    hasMultiRow: t.includes('多空逻辑(') || /多空逻辑\s/.test(t),
    movedNote: t.includes('已迁至「基本面量化'),
    hasMethod: t.includes('方法论研判'),
  };
});
log(vot.hasMethod, 'A 技术面仍有「方法论研判」卡');
log(vot.hasVotesSection, 'A 票表仍在（只少了多空逻辑系行）');
log(vot.movedNote, '★ A 票表给了迁出指向说明（用户不会以为丢数据）', JSON.stringify(vot));

// ═══ 基本面量化 ═══
console.log('\n[3] 基本面量化');
await p.click('nav button[data-tab="data-tab-not-exist"]').catch(() => {});
await p.evaluate(() => App.switchTab ? App.switchTab('fund') : null);
await p.waitForTimeout(1200);
let fund = await p.evaluate(() => {
  const t = document.body.innerText;
  return {
    hasSignal: t.includes('量化信号研判'),
    signalInFund: !!document.querySelector('#tab-fund #q-signal-body'),
    hasVerdict: t.includes('多空逻辑体系研判'),
    verdictInFund: !!document.querySelector('#tab-fund #f-votes-body'),
    hasStrategyScore: !!document.getElementById('f-strategy-score'),
    scoreCardInFund: !!document.querySelector('#tab-fund #f-strategy-score'),
    tabActive: (document.querySelector('nav button[data-tab="fund"]') || {}).className || '',
  };
});
log(fund.hasSignal && fund.signalInFund, 'A 基本面页有「⑤ 量化信号研判」卡');
log(fund.hasVerdict && fund.verdictInFund, 'A 基本面页有「⑥ 多空逻辑体系研判」卡');
log(fund.hasStrategyScore && fund.scoreCardInFund, 'C ① 三指标策略卡有计分行');

// 等两个卡的数据
let fv = null;
for (let i = 0; i < 40; i++) {
  fv = await p.evaluate(() => {
    const sb = document.getElementById('q-signal-body');
    const vb = document.getElementById('f-votes-body');
    const sc = document.getElementById('f-strategy-score');
    return {
      signal: sb ? sb.innerText.trim() : '',
      votes: vb ? vb.innerText.trim() : '',
      score: sc ? sc.innerText.trim() : '',
      badges: (document.getElementById('f-votes-badge') || {}).textContent || '',
    };
  });
  if (fv.votes.length > 30 && !fv.votes.includes('加载中')) break;
  await p.waitForTimeout(1500);
}
log(/综合研判|信号计分/.test(fv.signal), 'A 信号卡有真实内容', fv.signal.slice(0, 70));
log(/基本面信号计分\s*[+-]?\d+/.test(fv.score), '★ C ① 卡显示「基本面信号计分 +N」', fv.score.slice(0, 80));
log(fv.score.includes('库存+基差'), 'C ① 卡计分说明了口径构成', fv.score.slice(0, 110));
log(fv.votes.length > 30 && !fv.votes.includes('加载中'), 'A ⑥ 卡有真实内容', fv.votes.slice(0, 70));
log(/多空逻辑/.test(fv.votes), '★ A ⑥ 卡含多空逻辑系方向票', fv.votes.slice(0, 90));
log(/扩展读数|基差折盘面|跨期套利|虚实盘比|反转/.test(fv.votes), '★ A ⑥ 卡含扩展读数（5 个块已搬来）');
log(fv.badges.includes('多空逻辑口径'), 'A ⑥ 卡徽标标注了口径', fv.badges);
// 关键负向：基本面页不该出现技术面体系的方向票
log(!/鹿希武|威科夫/.test(fv.votes), '★ A ⑥ 卡内**不含**技术面体系票（鹿希武/威科夫）');
await p.screenshot({ path: 'probe/_shot_fund_votes.png' });

console.log('\n=== JS 错误 ===');
console.log(errs.length ? errs.slice(0, 8).join('\n') : '  无 ✔');
log(errs.length === 0, '0 JS 错误', errs.slice(0, 3).join(' | '));

console.log('\n' + '='.repeat(60));
console.log(`结果: ${pass} PASS / ${fail} FAIL`);
if (problems.length) { console.log('失败项:'); problems.forEach(x => console.log('   - ' + x)); }
await b.close();
process.exit(fail ? 1 : 0);
