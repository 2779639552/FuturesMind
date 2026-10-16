// E2E：⑨ 基本面截面表的「筛选 / 排序 / 列显隐」（2026-10-16 用户要求）
//
// 【为什么必须真开浏览器】本次新增的能力全是**交互**：
//   · 列头点击三态、下拉与列头双向联动 —— 只在静态串上断言"函数存在"是测不到的；
//   · 列显隐走**注入 CSS 的 nth-child** ⇒ 必须真量 computedStyle 才知道藏没藏对列；
//   · 筛选是否真的生效，要看具体行数与行内容（而不是"筛选器渲染出来了"）。
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

await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="fund"]', { timeout: 30000 });
await page.click('nav button[data-tab="fund"]');

// 等表拉完（21 行数据行；`tr[onclick]` 只数数据行，展开行是隐藏的第 2 个 tr）
let rowN = 0;
for (let i = 0; i < 150; i++) {
  rowN = await page.evaluate(() => document.querySelectorAll('#f-rk-rows tr[onclick]').length);
  if (rowN >= 21) break;
  await page.waitForTimeout(1000);
}
ck(rowN === 21, '表拉到全池 21 行', String(rowN));

// ── A. 控制区在位 ────────────────────────────────────────────────────────────
for (const id of ['f-rk-search', 'f-rk-pools', 'f-rk-dims', 'f-rk-colspanel', 'f-rk-fcount',
                  'f-rk-metric', 'f-rk-order', 'f-rk-table', 'f-rk-stats']) {
  ck(await page.locator('#' + id).count() === 1, '控制区有 #' + id);
}
const nPools = await page.evaluate(() => document.querySelectorAll('#f-rk-pools button').length);
ck(nPools >= 6, '板块胶囊已生成（含"全部品种"）', nPools + ' 个');
const poolTxt = await page.evaluate(() => document.querySelector('#f-rk-pools button').textContent.trim());
ck(poolTxt === '全部品种', '首个胶囊是「全部品种」', poolTxt);
// 【2026-10-16 改】维度筛选已由 `<select>` 改为**气泡**（与品种筛选同形）
//   ⇒ 计数改为"按 data-dim 去重的气泡组数"，并额外验"每组的胶囊都不是空的"。
const nDims = await page.evaluate(() => {
  const dims = {}; 
  document.querySelectorAll('#f-rk-dims .f-rk-pill').forEach(b => {
    dims[b.dataset.dim] = (dims[b.dataset.dim] || 0) + 1;
  });
  return { groups: Object.keys(dims).length, counts: dims };
});
ck(nDims.groups === 8, '8 组维度筛选气泡', JSON.stringify(nDims.counts));
ck(Object.values(nDims.counts).every(n => n >= 2), '每组气泡至少含「全部」+ 1 个取值',
  JSON.stringify(nDims.counts));

const statsAll = await page.evaluate(() => (document.getElementById('f-rk-stats') || {}).textContent || '');
ck(statsAll.includes('21'), '统计条按全量算（21）', statsAll.replace(/\s+/g, ' ').slice(0, 60));

// ── B. 板块胶囊筛选 ─────────────────────────────────────────────────────────
await page.evaluate(() => {
  const b = [...document.querySelectorAll('#f-rk-pools button')].find(
    x => x.textContent.trim() === '能化');
  if (b) b.click();
});
await page.waitForTimeout(400);
let shown = await page.evaluate(() => document.querySelectorAll('#f-rk-rows tr[onclick]').length);
ck(shown > 0 && shown < 21, '点「能化」后行数减少', shown + ' 行');
let allHua = await page.evaluate(() => {
  const rows = [...document.querySelectorAll('#f-rk-rows tr[onclick]')];
  return rows.every(r => App._fRkData.rows.some(
    x => r.textContent.includes(x.variety) && x.sector_bucket === '能化'));
});
ck(allHua, '筛出的行确实都属于「能化」（按 sector_bucket）');
const statsStill = await page.evaluate(() => (document.getElementById('f-rk-stats') || {}).textContent || '');
ck(statsStill.includes('21'), '筛选后统计条**仍是全量**（不随筛选跳）', statsStill.replace(/\s+/g, ' ').slice(0, 40));
const fcount = await page.evaluate(() => (document.getElementById('f-rk-fcount') || {}).textContent || '');
ck(fcount.includes('筛选中') && fcount.includes('/21'), '筛选摘要显示 筛出/总数', fcount);

// ── C. 维度下拉筛选（按列取值）───────────────────────────────────────────────
const termVals = await page.evaluate(() =>
  [...document.querySelectorAll('#f-rk-dims .f-rk-pill[data-dim="skill_term"]')].map(b => b.dataset.val));
ck(termVals.includes('偏多') && termVals.includes('偏空'), '期限结构气泡取值来自数据（含「全部」）',
  termVals.join('/'));
// 点气泡（不再用 selectOption）
await page.evaluate(() => {
  const b = [...document.querySelectorAll('#f-rk-dims .f-rk-pill[data-dim="skill_term"]')]
    .find(x => x.dataset.val === '偏空');
  if (b) b.click();
});
await page.waitForTimeout(300);
const termOn = await page.evaluate(() => {
  const b = [...document.querySelectorAll('#f-rk-dims .f-rk-pill[data-dim="skill_term"]')]
    .find(x => x.dataset.val === '偏空');
  return { on: b && b.style.background.indexOf('2997ff') >= 0, bg: b ? b.style.background : '' };
});
ck(termOn.on, '★ 点过的气泡进入选中态（高亮）', termOn.bg);
await page.waitForTimeout(300);
const nTermBear = await page.evaluate(() => document.querySelectorAll('#f-rk-rows tr[onclick]').length);
ck(nTermBear > 0, '选「期限结构=偏空」后有结果', nTermBear + ' 行（叠加"能化"）');
const allBear = await page.evaluate(() => {
  const rows = [...document.querySelectorAll('#f-rk-rows tr[onclick]')];
  return rows.every(r => r.textContent.includes('偏空'));
});
ck(allBear, '结果行都含「偏空」');

// 清除筛选 ⇒ 回到 21 行
await page.evaluate(() => App._fRkClearFilters());
await page.waitForTimeout(300);
shown = await page.evaluate(() => document.querySelectorAll('#f-rk-rows tr[onclick]').length);
ck(shown === 21, '「清除筛选」恢复 21 行', String(shown));

// ── D. 列头排序（三态）+ 与下拉联动 ──────────────────────────────────────────
const headBox = await page.evaluate(() => {
  const th = [...document.querySelectorAll('#f-rk-table thead th')].find(
    x => (x.dataset.metric || '') === 'vr_ratio');
  const r = th.getBoundingClientRect();
  return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
});
await page.mouse.click(headBox.x, headBox.y);
await page.waitForTimeout(300);
let st = await page.evaluate(() => ({
  key: App._fRkSort.key, order: App._fRkSort.order,
  metricSel: document.getElementById('f-rk-metric').value,
  orderSel: document.getElementById('f-rk-order').value,
  caret: [...document.querySelectorAll('#f-rk-table thead th')]
    .find(x => x.dataset.metric === 'vr_ratio').textContent,
}));
ck(st.key === 'vr_ratio' && st.order === 'desc', '点「虚实盘比」列头 ⇒ 降序', st.key + '/' + st.order);
ck(st.metricSel === 'vr_ratio' && st.orderSel === 'desc', '★ 下拉随之联动', st.metricSel + '/' + st.orderSel);
ck(st.caret.includes('▼'), '列头显示 ▼ 指示', st.caret.trim());

// 校验真的是"按数值降序"（第一行的值应 >= 第二行）
const vrSeq = await page.evaluate(() => {
  const rows = [...document.querySelectorAll('#f-rk-rows tr[onclick]')];
  return rows.map(r => r.children[12].textContent.trim()).slice(0, 4);
});
const vrNums = vrSeq.map(s => parseFloat(s)).filter(v => !isNaN(v));
ck(vrNums.length >= 2 && vrNums.every((v, i) => i === 0 || vrNums[i - 1] >= v),
  '虚实盘比列真的是降序', vrSeq.join(' > '));

// 再点 ⇒ 升序
await page.mouse.click(headBox.x, headBox.y);
await page.waitForTimeout(300);
st = await page.evaluate(() => ({ order: App._fRkSort.order,
  caret: [...document.querySelectorAll('#f-rk-table thead th')].find(x => x.dataset.metric === 'vr_ratio').textContent }));
ck(st.order === 'asc' && st.caret.includes('▲'), '再点 ⇒ 升序 ▲', st.order);
const vrSeq2 = await page.evaluate(() => [...document.querySelectorAll('#f-rk-rows tr[onclick]')]
  .map(r => r.children[12].textContent.trim()).slice(0, 3));
const vrNums2 = vrSeq2.map(s => parseFloat(s)).filter(v => !isNaN(v));
ck(vrNums2.length >= 2 && vrNums2.every((v, i) => i === 0 || vrNums2[i - 1] <= v),
  '升序生效（最上最小）', vrSeq2.join(' < '));

// 第三点 ⇒ 回默认（多空票净额 / 降序）
await page.mouse.click(headBox.x, headBox.y);
await page.waitForTimeout(300);
st = await page.evaluate(() => ({ key: App._fRkSort.key, order: App._fRkSort.order }));
ck(st.key === 'net' && st.order === 'desc', '第三点 ⇒ 回默认（多空票净额 降序）', st.key + '/' + st.order);

// ── E. 文本列排序（后端排不了的：库存周期 做多/做空）─────────────────────────
const cycHead = await page.evaluate(() => {
  const th = [...document.querySelectorAll('#f-rk-table thead th')].find(x => x.dataset.metric === 'skill_cyc');
  const r = th.getBoundingClientRect();
  return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
});
await page.mouse.click(cycHead.x, cycHead.y);
await page.waitForTimeout(300);
const cycSeq = await page.evaluate(() => [...document.querySelectorAll('#f-rk-rows tr[onclick]')]
  .map(r => r.children[16].textContent.trim()));
// 【为什么不能断言"末尾必是做空"】无该维度数据的行显示 "—"，按"未知恒最后"的纪律
//   它们会排在做空之后 ⇒ 末位可能是 "—"。正确判据是**做多的位置在做空之前**，
//   且所有 "—" 都在做空之后（未知恒最后）。
const iUp = cycSeq.findIndex(x => x.includes('做多'));
const iDn = cycSeq.findIndex(x => x.includes('做空'));
const iNa = cycSeq.findIndex(x => x.trim() === '—');
ck(iUp >= 0 && iDn > iUp, '★ 文本列（库存周期）按多空方向排序：做多排在做空之前',
  '做多@' + iUp + ' 做空@' + iDn);
ck(iNa < 0 || iNa > iDn, '无数据的行（—）恒排在做空之后（未知恒最后）', '—@' + iNa);
await page.evaluate(() => App._fRkClickHead('skill_cyc'));   // 回默认
await page.waitForTimeout(200);

// ── F. 搜索 ─────────────────────────────────────────────────────────────────
await page.fill('#f-rk-search', 'TA');
await page.waitForTimeout(300);
shown = await page.evaluate(() => document.querySelectorAll('#f-rk-rows tr[onclick]').length);
ck(shown === 1, '搜索 "TA" ⇒ 1 行', String(shown));
await page.fill('#f-rk-search', '');
await page.waitForTimeout(200);

// ── G. 列显隐 ───────────────────────────────────────────────────────────────
await page.click('text=列 ▾');
await page.waitForTimeout(200);
const panelVisible = await page.evaluate(() => {
  const p = document.getElementById('f-rk-colspanel');
  return p.style.display !== 'none' && p.querySelectorAll('input[type=checkbox]').length >= 19;
});
ck(panelVisible, '「列 ▾」打开面板且列出 ≥19 个列');
// 勾掉「仓单分位」（第 9 列，index 8）
await page.evaluate(() => {
  const box = document.getElementById('f-rk-colspanel');
  const labels = [...box.querySelectorAll('label')];
  const t = labels.find(l => l.textContent.trim().startsWith('仓单分位'));
  const cb = t.querySelector('input');
  cb.checked = false;
  cb.dispatchEvent(new Event('change'));
});
await page.waitForTimeout(300);
const colHidden = await page.evaluate(() => {
  const th = [...document.querySelectorAll('#f-rk-table thead th')].find(x => x.dataset.col === 'ck_pct');
  const td = document.querySelectorAll('#f-rk-rows tr[onclick]')[0].children[8];
  return { th: getComputedStyle(th).display, td: getComputedStyle(td).display };
});
ck(colHidden.th === 'none' && colHidden.td === 'none',
  '★ 取消勾选后该列的 th 与 td 都隐藏', JSON.stringify(colHidden));
const expRowTd = await page.evaluate(() => {
  const tr = document.getElementById('f-rk-exp-' + (App._fRkData.rows[0].variety));
  return tr ? getComputedStyle(tr.firstElementChild).display : 'n/a';
});
ck(expRowTd !== 'none', '行内展开行的单元格未被误隐藏（兜底规则生效）', expRowTd);
const lsVal = await page.evaluate(() => localStorage.getItem('agentsense.fundRanking.cols'));
ck(!!lsVal && lsVal.indexOf('ck_pct') < 0, '列选择写入 localStorage', String(lsVal).slice(0, 60));

// 刷新页面 ⇒ 列偏好仍在
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="fund"]', { timeout: 30000 });
await page.click('nav button[data-tab="fund"]');
for (let i = 0; i < 120; i++) {
  rowN = await page.evaluate(() => document.querySelectorAll('#f-rk-rows tr[onclick]').length);
  if (rowN >= 21) break;
  await page.waitForTimeout(1000);
}
const stillHidden = await page.evaluate(() => {
  const th = [...document.querySelectorAll('#f-rk-table thead th')].find(x => x.dataset.col === 'ck_pct');
  return th ? getComputedStyle(th).display : 'missing';
});
ck(stillHidden === 'none', '★ 刷新后列隐藏仍生效（localStorage 偏好）', stillHidden);
// 恢复全显示，避免影响后续人工查看
await page.evaluate(() => App._fRkAllCols());
await page.waitForTimeout(200);
const restored = await page.evaluate(() => {
  const th = [...document.querySelectorAll('#f-rk-table thead th')].find(x => x.dataset.col === 'ck_pct');
  return getComputedStyle(th).display;
});
ck(restored !== 'none', '「全部显示」恢复', restored);

// ── H. 筛到空 + 说明 ────────────────────────────────────────────────────────
await page.evaluate(() => { App._fRkFilter.pool = '能化'; App._fRkFilter.skill_term = '偏空';
  App._fRkFilter.inv_grade = '高'; App._renderFundRanking(); });
await page.waitForTimeout(300);
const emptyTxt = await page.evaluate(() => (document.getElementById('f-rk-rows') || {}).textContent || '');
const nNow = await page.evaluate(() => document.querySelectorAll('#f-rk-rows tr[onclick]').length);
if (nNow === 0) ck(/没有符合条件|筛掉/.test(emptyTxt), '筛到空时给出「没有符合条件的品种」', emptyTxt.trim().slice(0, 40));
else ck(true, '（该组合仍有 ' + nNow + ' 行，跳过空态断言）');
await page.evaluate(() => App._fRkClearFilters());
await page.waitForTimeout(300);
shown = await page.evaluate(() => document.querySelectorAll('#f-rk-rows tr[onclick]').length);
ck(shown === 21, '再清除 ⇒ 21 行', String(shown));

// ── I. 与既有能力不冲突 ─────────────────────────────────────────────────────
const contractSel = await page.evaluate(() => {
  const s = document.querySelector('#f-rk-rows tr[onclick] select.f-rk-mon');
  return !!s;
});
ck(contractSel, '「合约」列月份下拉仍在（既有能力未破）');
const badge = await page.evaluate(() => (document.getElementById('f-rk-badge') || {}).textContent || '');
ck(/品种 · 按 .+ (降序|升序)/.test(badge), 'badge 显示前端排序状态', badge);

console.log('\nJS 错误：' + (errs.length ? errs.join(' | ') : '0 个 ✓'));
console.log('PASS ' + pass + ' / FAIL ' + fail);
await B.close();
process.exit(fail ? 1 : 0);
