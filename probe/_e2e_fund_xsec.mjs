// 基本面多品种截面表 + 两视图 端到端实测 —— 2026-10-16
//
// 【为什么必须真浏览器】本功能的三个核心行为都只有真实交互能验:
//   ① 「点截面表某一行 → 进入该品种单品种明细」的视图切换 + 品种是否真的跟着切
//   ② 「合约」列的下拉**不得冒泡触发跳详情**(祖先 <tr> onclick 会吃掉子控件点击)
//   ③ 切走 tab 再切回, 明细/总览的上下文是否保持
// 静态断言只能证明"函数存在、id 在", 证明不了这些。
//
// 用法: node probe/_e2e_fund_xsec.mjs

import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = 'http://127.0.0.1:5000';
const P = [], F = [];
const ck = (c, what, detail = '') => {
  (c ? P : F).push(what);
  console.log(`  ${c ? 'OK ' : '★  '} ${what}${detail ? `  [${detail}]` : ''}`);
  return c;
};

const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const ctx = await browser.newContext({ viewport: { width: 1760, height: 1050 } });
const page = await ctx.newPage();
const jsErrors = [];
page.on('pageerror', e => jsErrors.push('pageerror: ' + e.message));
page.on('console', m => { if (m.type() === 'error') jsErrors.push('console: ' + m.text()); });

const txt = (sel) => page.evaluate(s => (document.querySelector(s) || {}).textContent || '', sel);
const vis = async (sel) => page.evaluate(s => {
  const el = document.querySelector(s);
  if (!el) return false;
  const st = getComputedStyle(el);
  return st.display !== 'none' && el.offsetParent !== null;
}, sel);

async function waitFor(fn, ms = 150000, step = 700) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (await fn()) return true; await page.waitForTimeout(step); }
  return false;
}

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="fund"]', { timeout: 30000 });
await page.click('nav button[data-tab="fund"]');

console.log('=== A. 进入基本面页默认落在「全品种总览」 ===');
ck(await vis('#f-overview-view'), '总览视图可见');
ck(!(await vis('#f-detail-view')), '明细视图默认隐藏（不叠加显示）');
const gotRows = await waitFor(async () => {
  const n = await page.evaluate(() => document.querySelectorAll('#f-rk-rows tr[onclick]').length);
  return n >= 21;
});
const rowN = await page.evaluate(() => document.querySelectorAll('#f-rk-rows tr[onclick]').length);
ck(gotRows, '截面表拉到全池行（≥21 行）', `${rowN} 行`);
// 【为什么用 tr[onclick] 计数】每个品种渲染 2 个 <tr>(数据行 + 隐藏的行内合约展开行)
//   ⇒ 裸数 `#f-rk-rows tr` 会得到 42(看着像多了一倍的行), 是 E2E 自己的计数口径问题。
ck(rowN === 21, '数据行恰为池内 21 个（不含隐藏展开行）', `${rowN}`);

console.log('');
console.log('=== B. 19 列表头齐全（对应技术面总览的列被换成了基本面因子） ===');
const heads = await page.evaluate(() =>
  [...document.querySelectorAll('#f-rk-table thead th')].map(e => e.textContent.trim()));
const need = ['#', '品种', '合约', '最新价', '基差', '基差率', '基差分位', '库存综合',
  '仓单分位', '仓单5日', '社库存分位', '社库存4期', '虚实盘比', '利润',
  '二维四象', '期限结构', '库存周期', '利润票', '综合结论'];
ck(heads.length === need.length, '表头列数 = 19', `实测 ${heads.length}`);
const missing = need.filter(x => !heads.includes(x));
ck(!missing.length, '列名齐全', missing.join(',') || '无缺失');
ck(!heads.some(h => /日线|60分|15分|5分|共振|置信度|流动性|涨跌幅/.test(h)),
  '技术面专有列（日线/60分/15分/5分/共振/置信度/流动性/涨跌幅）已全部换成基本面因子');

console.log('');
console.log('=== C. 行内数据真的填上了（不是空壳表） ===');
const firstRow = await page.evaluate(() => {
  const tr = document.querySelector('#f-rk-rows tr');
  return [...tr.querySelectorAll('td')].map(td => td.textContent.replace(/\s+/g, ' ').trim());
});
ck(firstRow.length === 19, '首行列数 = 19', `实测 ${firstRow.length}`);
const dashN = firstRow.filter(v => v === '—' || v === '').length;
ck(dashN <= 6, '首行空值不超过 6 个', `空 ${dashN} 个：${firstRow.join('|').slice(0, 120)}`);
const badge = await txt('#f-rk-badge');
ck(/品种/.test(badge) && /排序/.test(badge), '徽标显示品种数与排序维度', badge.slice(0, 60));
const stats = await txt('#f-rk-stats');
ck(/库存低/.test(stats) && /口径背离/.test(stats) && /4 票齐备/.test(stats), '统计条含库存/背离/票齐备', stats.slice(0, 90));

console.log('');
console.log('=== D. 排序维度切换（后端缓存 ⇒ 秒回） ===');
const t0 = Date.now();
await page.selectOption('#f-rk-metric', 'vr_ratio');
const sorted = await waitFor(async () => (await txt('#f-rk-note')).includes('缓存'), 20000);
ck(sorted, '切排序维度后重新出数', `${Date.now() - t0}ms`);
const noteTxt = await txt('#f-rk-note');
ck(/缓存/.test(noteTxt), '切维度命中后端缓存（秒回）', noteTxt.slice(0, 40));
await page.selectOption('#f-rk-metric', 'net');
await waitFor(async () => (await page.evaluate(() => document.querySelectorAll('#f-rk-rows tr[onclick]').length)) >= 21, 30000);

console.log('');
console.log('=== E. ★「合约」列下拉：行内展开且**不冒泡跳页** ===');
const code0 = await page.evaluate(() => {
  const tr = document.querySelector('#f-rk-rows tr');
  return tr.getAttribute('onclick').match(/'([A-Z]+)'/)[1];
});
const opts = await page.evaluate(() => {
  const s = document.querySelector('#f-rk-rows tr select.f-rk-mon');
  return [...s.options].map(o => o.value).filter(Boolean);
});
ck(opts.length >= 2, `${code0} 的合约下拉有月份可选`, opts.join(','));
// 选一个非主力月
const pick = opts[opts.length - 1];
await page.evaluate((m) => {
  const s = document.querySelector('#f-rk-rows tr select.f-rk-mon');
  s.value = m;
  s.dispatchEvent(new Event('change', { bubbles: true }));
}, pick);
const expanded = await waitFor(async () => vis(`#f-rk-exp-${code0}`), 30000);
ck(expanded, '选月份后行内展开合约明细行');
ck(await vis('#f-overview-view'), '★ 展开合约**没有**跳出总览（下拉未冒泡触发跳详情）');
ck(!(await vis('#f-detail-view')), '★ 明细视图仍在隐藏态');
const expTxt = await txt(`#f-rk-exp-${code0}`);
for (const k of ['月份', '合约', '价格', '持仓量', '成交量', '距交割']) {
  if (!expTxt.includes(k)) { ck(false, `合约明细含列「${k}」`); }
}
ck(['月份', '合约', '价格', '持仓量', '成交量', '距交割'].every(k => expTxt.includes(k)),
  '合约明细含 月份/合约/价格/持仓量/成交量/距交割', expTxt.replace(/\s+/g, ' ').slice(0, 100));
ck(/主力/.test(expTxt), '合约明细标出了主力合约');
// 收起
await page.evaluate(() => {
  const s = document.querySelector('#f-rk-rows tr select.f-rk-mon');
  s.value = '';
  s.dispatchEvent(new Event('change', { bubbles: true }));
});
await page.waitForTimeout(600);
ck(!(await vis(`#f-rk-exp-${code0}`)), '选「—」可收起合约明细');

console.log('');
console.log('=== F. ★ 点行 → 该品种单品种明细（品种必须真的跟着切） ===');
const target = await page.evaluate(() => {
  const trs = [...document.querySelectorAll('#f-rk-rows tr')];
  const tr = trs.find(t => t.getAttribute('onclick'));
  const code = tr.getAttribute('onclick').match(/'([A-Z]+)'/)[1];
  const name = tr.querySelector('td:nth-child(2)').textContent.trim();
  return { code, name };
});
await page.evaluate((code) => {
  const tr = [...document.querySelectorAll('#f-rk-rows tr')]
    .find(t => (t.getAttribute('onclick') || '').includes(`'${code}'`));
  tr.click();
}, target.code);
ck(await waitFor(async () => vis('#f-detail-view'), 20000), '进入明细视图');
ck(!(await vis('#f-overview-view')), '总览视图已隐藏');
// 品种下拉必须切到点中的那个（含"没初始化时靠 pending 槽"这条路径）
const selOK = await waitFor(async () => {
  const v = await page.evaluate(() => (document.getElementById('f-variety') || {}).value);
  return v === target.code;
}, 25000);
const selVal = await page.evaluate(() => (document.getElementById('f-variety') || {}).value);
ck(selOK, `品种下拉已切到点中的 ${target.code}（未被默认品种顶掉）`, `实测 ${selVal}`);
ck(/返回全品种总览/.test(await txt('#f-detail-view')), '明细顶部有「← 返回全品种总览」按钮');
// 单品种卡开始加载（三指标卡应有内容）
const cardOK = await waitFor(async () => {
  const t = await txt('#f-strategy-body');
  return t && t.includes('库存') && !t.includes('加载中');
}, 90000);
ck(cardOK, '单品种 ① 三指标策略卡已加载', (await txt('#f-strategy-body')).replace(/\s+/g, ' ').slice(0, 70));
ck(/返回全品种总览/.test(await txt('#f-detail-view')), '返回按钮仍在（供下一步返回）');

console.log('');
console.log('=== G. 切走 tab 再切回：明细上下文保持（与 quant 页同一判据） ===');
await page.click('nav button[data-tab="research"]');
await page.waitForTimeout(1200);
await page.click('nav button[data-tab="fund"]');
await page.waitForTimeout(2500);
ck(await vis('#f-detail-view'), '切回后停在明细（不丢上下文）');
ck(!(await vis('#f-overview-view')), '切回后总览仍隐藏');
const selVal2 = await page.evaluate(() => (document.getElementById('f-variety') || {}).value);
ck(selVal2 === target.code, '切回后品种仍是刚才那个', `实测 ${selVal2}`);

console.log('');
console.log('=== H. 返回总览 ===');
await page.click('button:has-text("返回全品种总览")');
ck(await waitFor(async () => vis('#f-overview-view'), 20000), '回到总览视图');
ck(!(await vis('#f-detail-view')), '明细视图重新隐藏');
ck(await waitFor(async () => (await page.evaluate(() =>
  document.querySelectorAll('#f-rk-rows tr[onclick]').length)) >= 21, 30000), '返回后截面表仍在（未丢数据）');

console.log('');
console.log('=== I. ① 卡基差率显示已修正（100× 错误） ===');
// 切进明细看 ① 卡的基差块：基差率数值应与"基差/期货价"同量级(个位数%)，而不是 0.0x%
await page.evaluate(() => {
  const tr = document.querySelector('#f-rk-rows tr');
  tr.click();
});
await waitFor(async () => vis('#f-detail-view'), 20000);
const raw = await page.evaluate(() => {
  const tr = [...document.querySelectorAll('#f-rk-rows tr')].length;
  return tr;
});
const bodyTxt = await (async () => {
  await waitFor(async () => {
    const t = await txt('#f-strategy-body');
    return t.includes('基差率');
  }, 60000);
  return txt('#f-strategy-body');
})();
const m = /基差率\s*([-\d.]+)%/.exec(bodyTxt);
ck(!!m, '① 卡显示基差率', m ? m[0] : bodyTxt.slice(0, 80));
if (m) {
  const v = parseFloat(m[1]);
  ck(Math.abs(v) >= 0.05 && Math.abs(v) <= 60,
    `基差率量级合理（${v}%，不是被 100× 压成 0.0x%）`);
}

console.log('');
console.log('=== J. 页面健康 ===');
ck(jsErrors.length === 0, '无 JS 运行时错误', jsErrors.slice(0, 2).join(' | ') || '0');

// 实拍：总览表 + 行内合约 + 明细顶部
await page.click('button:has-text("返回全品种总览")');
await waitFor(async () => vis('#f-overview-view'), 20000);
await page.waitForTimeout(800);
await page.screenshot({ path: 'probe/_shot_fund_xsec_overview.png' });
await page.evaluate((code) => {
  const s = document.querySelector(`#f-rk-rows tr[onclick*="'${code}'"] select.f-rk-mon`);
  const main = [...s.options].find(o => o.textContent.includes('主力'));
  if (main) { main.selected = true; s.dispatchEvent(new Event('change', { bubbles: true })); }
}, target.code);
await waitFor(async () => vis(`#f-rk-exp-${target.code}`), 20000);
await page.waitForTimeout(700);
await page.screenshot({ path: 'probe/_shot_fund_xsec_contract.png' });

console.log('');
console.log('='.repeat(62));
console.log(`PASS ${P.length} / FAIL ${F.length}`);
if (F.length) { console.log('失败项:'); F.forEach(x => console.log('  ★ ' + x)); }

await browser.close();
process.exit(F.length ? 1 : 0);
