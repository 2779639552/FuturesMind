// E2E：⑨ 基本面截面表的「缓存 + 定期刷新」机制（2026-10-16 用户要求）
//
// 【为什么要单独测】本次改的是**何时请求 / 数据不在时 UI 做什么**，全是时序与状态：
//   · 进页面应**秒开**并排好"每 15 分钟"定时器；
//   · 离开这一页（切 tab / 进明细）必须**停表**（否则人不在也周期性触发，TTL 恰好到期就白算一轮）；
//   · 点「拉取/刷新」时**旧表必须留着**（stale-while-revalidate），进度条要明说"显示的是上一轮"。
//   这些静态度量抓不到，`node --check` 也抓不到。
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const B = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const ctx = await B.newContext({ viewport: { width: 1760, height: 1050 } });
const page = await ctx.newPage();
const errs = [];
page.on('pageerror', e => errs.push(String(e).slice(0, 160)));
page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 140)); });

let pass = 0, fail = 0;
const ck = (ok, name, extra = '') => {
  if (ok) { pass++; console.log('  OK  ' + name + (extra ? '  [' + extra + ']' : '')); }
  else { fail++; console.log('  ★   ' + name + (extra ? '  [' + extra + ']' : '')); }
};
const waitRows = async (n = 21, tries = 120) => {
  for (let i = 0; i < tries; i++) {
    const k = await page.evaluate(() => document.querySelectorAll('#f-rk-rows tr[onclick]').length);
    if (k >= n) return k;
    await page.waitForTimeout(1000);
  }
  return await page.evaluate(() => document.querySelectorAll('#f-rk-rows tr[onclick]').length);
};
const snap = () => page.evaluate(() => ({
  timer: !!App._frkPollTimer,
  nextAt: App._frkNextAt,
  pg: (document.getElementById('f-rk-progress') || {}).textContent || '',
  rows: document.querySelectorAll('#f-rk-rows tr[onclick]').length,
  refreshing: !!((App._fRkData || {}).refreshing),
  done: !!((App._fRkData || {}).done),
  cached: !!((App._fRkData || {}).cached),
}));

await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="fund"]', { timeout: 30000 });

// ── A. 进页面：应**秒开**（有缓存）并排好定时器 ──────────────────────────────
const t0 = Date.now();
await page.click('nav button[data-tab="fund"]');
const n0 = await waitRows();
const openMs = Date.now() - t0;
ck(n0 === 21, '进基本面页拿到全池 21 行', String(n0));
let s = await snap();
ck(s.timer, '★ 已排好轮询定时器（_frkPollTimer 非空）');
ck(s.nextAt > Date.now(), '★ 已排"下次自动刷新时刻"', new Date(s.nextAt).toTimeString().slice(0, 5));
ck(/每 15 分钟自动刷新（下次 \d{2}:\d{2}）/.test(s.pg), '进度条写明「每 15 分钟自动刷新（下次 HH:MM）」', s.pg);
ck(s.done && !s.refreshing, '当前是"完成且空闲"态（不显示进度条三态的前两态）');
console.log('  （进页面到出表耗时 ' + openMs + ' ms —— 有缓存时应是秒级）');

// ── B. 切走 tab ⇒ 必须停表 ──────────────────────────────────────────────────
await page.click('nav button[data-tab="sentiment"]');
await page.waitForTimeout(600);
s = await snap();
ck(!s.timer, '★ 切走后定时器已清（否则人不在也周期性触发 ⇒ TTL 恰好到期就白算一轮）');
ck(s.nextAt === 0, '切走后"下次刷新时刻"清零（不再显示过期信息）', String(s.nextAt));

// ── C. 切回 ⇒ 秒回 + 重排定时器 ─────────────────────────────────────────────
const t1 = Date.now();
await page.click('nav button[data-tab="fund"]');
const n1 = await waitRows();
const backMs = Date.now() - t1;
// 【★ 必须**轮询等**定时器排上】`waitRows()` 会因缓存**瞬时**返回（表一渲染就有 21 行），
//   而 `switchTab → loadFundRanking` 是异步的：fetch 还没回来时定时器自然还没排，
//   立刻读会**偶发**拿到 timer=false（实测两次跑一次红 —— 是 E2E 自己的时序问题，
//   不是产品缺陷：真实用户看到的是下一帧就排好了）。
let s2 = null;
for (let i = 0; i < 40; i++) {
  s2 = await snap();
  if (s2.timer && s2.nextAt > 0) break;
  await page.waitForTimeout(250);
}
ck(n1 === 21, '切回后表仍是 21 行（<b>秒开</b>，不再重算）', String(n1));
ck(s2.timer && s2.nextAt > 0, '★ 切回后定时器已重排', 'timer=' + s2.timer);
console.log('  （切回到出表耗时 ' + backMs + ' ms）');

// ── D. 点「拉取/刷新」⇒ 旧表必须保留 + 进度条三态之② ────────────────────────
// ⚠ 别用 `button:has-text("拉取/刷新")` —— 技术面页、基本面页、排行表里**都有**同名按钮
//   （实测匹配 3 个，Playwright 取第一个 = 技术面那个，且它不可见 ⇒ 超时崩）。
//   用精确的 onclick 选择器锁定本表的那一个。
await page.click('button[onclick="App.loadFundRanking(1)"]');
// 立刻（后端已开始后台重算）看：行数不应掉回 0
const immediate = await page.evaluate(() => ({
  rows: document.querySelectorAll('#f-rk-rows tr[onclick]').length,
  txt: (document.getElementById('f-rk-rows') || {}).textContent || '',
}));
ck(immediate.rows === 21, '★ 点刷新后**旧表仍在 21 行**（stale-while-revalidate，不是回到"加载中"）',
  immediate.rows + ' 行');
ck(!/正在计算全池基本面指标/.test(immediate.txt), '未被"正在计算…"占位行替换');
// 等出现 refreshing（后台重算中）
let sawRefresh = false, pgTxt = '', rowsDuring = 0;
for (let i = 0; i < 40; i++) {
  const c = await page.evaluate(() => ({
    refreshing: !!((App._fRkData || {}).refreshing),
    rows: document.querySelectorAll('#f-rk-rows tr[onclick]').length,
    pg: (document.getElementById('f-rk-progress') || {}).textContent || '',
  }));
  if (c.refreshing) { sawRefresh = true; pgTxt = c.pg; rowsDuring = c.rows; break; }
  await page.waitForTimeout(500);
}
ck(sawRefresh, '★ 抓到"后台刷新中"状态（refreshing=true）');
if (sawRefresh) {
  ck(/后台刷新中 \d+\/21 个品种/.test(pgTxt), '进度条显示"后台刷新中 n/21 个品种"', pgTxt.slice(0, 60));
  ck(/表格显示上一轮结果/.test(pgTxt), '★ 明说"表格显示上一轮结果, 无需等待"', pgTxt.slice(0, 80));
  ck(!/每 15 分钟自动刷新（下次/.test(pgTxt), '★ 刷新中**不显示**"下次刷新时刻"（那是过期信息）');
  ck(rowsDuring === 21, '刷新期间表仍是 21 行（旧结果继续显示）', String(rowsDuring));
}
// 等刷新结束 ⇒ 回到第三态
let ended = false;
for (let i = 0; i < 90; i++) {
  const c = await page.evaluate(() => ({
    refreshing: !!((App._fRkData || {}).refreshing),
    done: !!((App._fRkData || {}).done),
    pg: (document.getElementById('f-rk-progress') || {}).textContent || '',
  }));
  if (c.done && !c.refreshing) { ended = true; pgTxt = c.pg; break; }
  await page.waitForTimeout(1000);
}
ck(ended, '刷新结束回到"完成且空闲"态');
if (ended) ck(/计算完成 · 21 个品种/.test(pgTxt) && /每 15 分钟自动刷新（下次/.test(pgTxt),
  '完成后进度条回到「计算完成 · 缓存 · 每 15 分钟自动刷新（下次 HH:MM）」', pgTxt.slice(0, 70));

// ── E. 进单品种明细 ⇒ 停表；返回总览 ⇒ 重启 ─────────────────────────────────
await page.evaluate(() => App.fOpenDetail('TA'));
await page.waitForTimeout(800);
s = await snap();
ck(!s.timer && s.nextAt === 0, '★ 进明细后总览定时器已停（总览不可见，轮询无意义）',
  'timer=' + s.timer);
await page.evaluate(() => App.fBackOverview());
await page.waitForTimeout(2500);
s = await snap();
ck(s.timer && s.nextAt > 0, '★ 返回总览后定时器已重启', 'timer=' + s.timer);
ck(s.rows === 21, '返回总览后表仍在', String(s.rows));

// ── F. 筛选气泡与定期刷新共存（重建不冲掉选中态）────────────────────────────
await page.evaluate(() => {
  const b = [...document.querySelectorAll('#f-rk-dims .f-rk-pill[data-dim="inv_grade"]')]
    .find(x => x.dataset.val === '低');
  if (b) b.click();
});
await page.waitForTimeout(400);
const before = await page.evaluate(() => ({
  filter: App._fRkFilter.inv_grade,
  rows: document.querySelectorAll('#f-rk-rows tr[onclick]').length,
  on: [...document.querySelectorAll('#f-rk-dims .f-rk-pill[data-dim="inv_grade"]')]
    .filter(b => b.style.background.indexOf('2997ff') >= 0).map(b => b.dataset.val),
}));
ck(before.rows < 21, '选「库存综合=低」后行数减少', String(before.rows));
await page.evaluate(() => App._renderFundRanking());
await page.waitForTimeout(300);
const after = await page.evaluate(() => ({
  on: [...document.querySelectorAll('#f-rk-dims .f-rk-pill[data-dim="inv_grade"]')]
    .filter(b => b.style.background.indexOf('2997ff') >= 0).map(b => b.dataset.val),
  rows: document.querySelectorAll('#f-rk-rows tr[onclick]').length,
}));
ck(JSON.stringify(after.on) === JSON.stringify(before.on),
  '★ 重渲染后气泡选中态保持（守卫生效，不会"点完就闪掉"）', JSON.stringify(after.on));
ck(after.rows === before.rows, '重渲染后筛选结果一致', String(after.rows));

console.log('\nJS 错误：' + (errs.length ? errs.join(' | ') : '0 个 ✓'));
console.log('PASS ' + pass + ' / FAIL ' + fail);
await B.close();
process.exit(fail ? 1 : 0);
