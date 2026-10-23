// 【用途】删「价格-库存关联分析」小模块的浏览器 E2E 验收（用户 2026-10-16 要求）。
// 【为什么必须真浏览器】静态文本断言只能证"DOM 没了"，证不了三件事：
//   ① 删掉的那个 body 取值点会不会在运行期抛错（连带把同一 try 里的 #f-research 打空）；
//   ② ① 卡其余内容（双口径图 / 四象限 / 板块相关性）有没有被一起删掉或挤出容器；
//   ③ 版本号 +1 后索引条确实重建了、条目名没变。
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const B = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await (await B.newContext({ viewport: { width: 1760, height: 1200 } })).newPage();

let pass = 0, fail = 0;
const errs = [];
page.on('pageerror', e => errs.push(String(e)));
page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
function ck(ok, msg, extra) {
  if (ok) { pass++; console.log('  ✔ ' + msg + (extra ? '  [' + extra + ']' : '')); }
  else { fail++; console.log('  ★ FAIL ' + msg + (extra ? '  [' + extra + ']' : '')); }
}
const sleep = ms => page.waitForTimeout(ms);

await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="fund"]', { timeout: 30000 });
await page.click('nav button[data-tab="fund"]');

// 等 ① 卡真正有内容（截面表 21 行 = 数据到位）
let rows = 0;
for (let i = 0; i < 150; i++) {
  rows = await page.evaluate(() => document.querySelectorAll('#f-rk-rows tr[onclick]').length);
  if (rows >= 21) break;
  await sleep(1000);
}
ck(rows >= 21, '基本面页已加载（截面表 ' + rows + ' 行）');

// ★★ 必须先进**明细视图**：#f-detail-view 默认 display:none，① 卡（含被删小节所在区块）
//   住在里面 ⇒ 不切过去它根本不加载（#f-cycle-body 恒「加载中...」、图无实例）。
//   第一版没切 ⇒ 9 条假红（看着像我把 ① 卡弄坏了，其实是我没进那个视图）——
//   与上一轮 soc_chart E2E 同一个坑，故此处显式进视图并**轮询等它真渲染**。
await page.evaluate(() => App.fOpenDetail('TA'));
let ready = false;
for (let i = 0; i < 90; i++) {
  const st = await page.evaluate(() => ({
    cycle: (document.getElementById('f-cycle-body') || {}).innerText || '',
    res: (document.getElementById('f-research') || {}).innerText || '',
    strat: (document.getElementById('f-strategy-body') || {}).innerText || '',
  }));
  if (st.cycle.indexOf('加载中') < 0 && st.cycle.length > 5
    && st.res.length > 10 && st.strat.indexOf('加载中') < 0 && st.strat.length > 5) {
    ready = true; break;
  }
  await sleep(1000);
}
ck(ready, '明细视图（TA）已渲染完毕：① 卡四象限 + ① 卡主体 + 研报卡');
await sleep(1500);

console.log('\n=== A. 该小模块确实不存在了 ===');
const gone = await page.evaluate(() => ({
  body: !!document.getElementById('f-analysis-body'),
  badge: !!document.getElementById('f-analysis-badge'),
  txt: (document.getElementById('tab-fund').innerText || '').indexOf('价格-库存关联分析') >= 0,
  // 页面上是否还残留可见的同名标题文字
  anyLabel: [...document.querySelectorAll('#tab-fund div, #tab-fund span')]
    .filter(e => (e.childNodes.length === 1 || e.childElementCount <= 1)
      && (e.textContent || '').trim().indexOf('价格-库存关联分析') === 0)
    .map(e => e.tagName + ':' + e.textContent.trim().slice(0, 20)),
}));
ck(!gone.body, '#f-analysis-body 已从 DOM 移除');
ck(!gone.badge, '#f-analysis-badge 已从 DOM 移除');
ck(!gone.txt, '页面可见文本里不再出现「价格-库存关联分析」', gone.anyLabel.join('|') || '—');

console.log('\n=== B. ★ 连带伤害检查：#f-research 必须仍在且真有内容 ===');
const res = await page.evaluate(() => {
  const el = document.getElementById('f-research');
  if (!el) return { exists: false };
  const t = (el.innerText || '').trim();
  const sect = el.querySelector('.chart-container');
  return {
    exists: true, len: t.length, loading: t.indexOf('加载中') >= 0,
    txt: t.slice(0, 60).replace(/\s+/g, ' '),
    hasChart: !!sect, inFundTab: !!el.closest('#tab-fund'),
  };
});
ck(res.exists, '#f-research 容器仍在');
ck(res.exists && res.len > 10 && !res.loading, '★ #f-research 有实质内容（未被连带打空）',
  res.exists ? (res.len + ' 字: ' + res.txt) : '—');
ck(res.exists && res.inFundTab, '#f-research 仍在 #tab-fund 容器内');

console.log('\n=== C. ① 卡其余内容未被误删（删的是小节，不是整块） ===');
const keep = await page.evaluate(() => {
  const g = id => document.getElementById(id);
  const vis = el => !!(el && el.offsetParent !== null);
  const tab = document.getElementById('tab-fund');
  const card = document.getElementById('f-strategy-card');
  return {
    invChart: vis(g('chart-f-inv')), cycle: (g('f-cycle-body') || {}).innerText || '',
    invBadge: (g('f-inv-badge') || {}).innerText || '',
    sectorPanel: !!g('f-sector-panel'),
    stratBody: (g('f-strategy-body') || {}).innerText || '',
    cardInTab: !!card && !!card.closest('#tab-fund'),
    // 后续卡必须还在 tab 容器内（div 配平的后验：配平错会让它们被挤到 </main> 下）
    others: ['②', '③', '④', '⑤'].map(k => {
      const h3 = [...tab.querySelectorAll('h3')].find(x => (x.textContent || '').trim().startsWith(k));
      return k + '=' + (h3 ? (!!h3.closest('#tab-fund') ? 'ok' : '★挤出去了') : '?');
    }).join(','),
  };
});
ck(keep.invChart, '左侧「仓单库存 · 社会库存双口径」图仍在且可见');
ck(keep.cycle && keep.cycle.indexOf('加载中') < 0, '「库存周期四象限」仍有内容',
  (keep.cycle || '').slice(0, 40).replace(/\s+/g, ' '));
ck(keep.sectorPanel, '「板块相关性总览」容器仍在（未误删）');
ck(keep.stratBody && keep.stratBody.indexOf('加载中') < 0, '① 卡主体（#f-strategy-body）仍有内容');
ck(keep.cardInTab, '① 卡仍在 #tab-fund 内');
ck(keep.others.indexOf('★') < 0, '后续各卡(②③④⑤)都还在 #tab-fund 内', keep.others);

console.log('\n=== D. 更新后的双口径图仍正常（本次改动的邻接模块） ===');
const soc = await page.evaluate(() => {
  const el = document.getElementById('chart-f-inv');
  if (!el || !window.echarts) return { err: 'no chart' };
  const inst = window.echarts.getInstanceByDom(el);
  if (!inst) return { err: 'no instance' };
  const o = inst.getOption();
  return { series: (o.series || []).length, grids: (o.grid || []).length };
});
ck(soc.series >= 4, '双口径图仍有 4 条序列（含社会库存阶梯）', JSON.stringify(soc));
ck(soc.grids >= 2, '双口径图仍有上下两栏 grid', JSON.stringify(soc));

console.log('\n=== E. 索引条：全局折叠已关 ⇒ 本就不建（版本号仍按铁律 +1） ===');
const idx = await page.evaluate(() => {
  const tab = document.getElementById('tab-fund');
  return {
    jsVer: (typeof App !== 'undefined' && App._SEC_IDX_VER) || null,
    flatAll: (typeof App !== 'undefined' && !!App._SEC_FLAT_TABS_ALL) || null,
    isFlat: (typeof App !== 'undefined' && typeof App._secIsFlat === 'function')
      ? App._secIsFlat('fund') : null,
    hasIndexBar: !!tab.querySelector('.sec-index'),
    dataset: tab.dataset.secIndex === undefined ? null : tab.dataset.secIndex,
    wrapped: tab.querySelectorAll('.sec-block').length,
  };
});
// 【修正第一版的误判】第一版断言"索引条必须存在"⇒ 3 条假红。真相：本项目
//   2026-10-13 起 `_SEC_FLAT_TABS_ALL: true`（用户要求取消小模块设计）⇒ 全站平铺、
//   **不建索引条也不包折叠外壳**。故此处改为验证"确实没建、且这是全局开关所致"。
ck(idx.flatAll === true && idx.isFlat === true,
  '全局折叠已关（_SEC_FLAT_TABS_ALL）⇒ 本页平铺、不参与索引', 'flatAll=' + idx.flatAll);
ck(!idx.hasIndexBar && idx.dataset === null,
  '确实没有索引条、tab 上也无版本标记（与全局关闭一致）',
  'bar=' + idx.hasIndexBar + ' dataset=' + idx.dataset);
ck(idx.wrapped === 0, '没有被折叠外壳包裹（无 .sec-block 残壳）', String(idx.wrapped));
ck(idx.jsVer === '30', '代码里的 _SEC_IDX_VER 已按铁律 +1 到 30（回退折叠模式时才会用到）',
  String(idx.jsVer));

console.log('\n=== F. 无 JS 错误 ===');
const realErrs = errs.filter(e => !/favicon|ERR_CONNECTION|net::/i.test(e));
ck(realErrs.length === 0, '无 JS 运行时错误', realErrs.slice(0, 3).join(' | ') || '0 条');

console.log('\n' + '='.repeat(58));
console.log('  PASS ' + pass + ' / FAIL ' + fail);
console.log('='.repeat(58));

await page.screenshot({ path: 'probe/_shot_rm_pi_analysis.png', fullPage: false });
await B.close();
process.exit(fail ? 1 : 0);
