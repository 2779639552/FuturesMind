// 库存双口径(社会库存 + 仓单) 端到端实测 —— 2026-10-16
//
// 【为什么要真浏览器】只读验收与单测都抓不到"渲染路径"上的问题:
//   `_renderFundCycle` / ①卡库存块都是**内联 JS 拼 HTML 串**, 静态断言只能证明
//   "串里有这几个词", 证明不了"页面上真看得见、颜色对、不报 JS 错误"。
//
// 【为什么还要用路由桩】当前实盘数据里**已没有仓单归零的品种**(10-16 刷新后
//   EB/BZ/CJ 都回到非零), 而"0 值警示"和"口径背离"这两条分支恰恰是最该看的
//   ⇒ 用 page.route 注入合成响应, 走**真实渲染函数**, 验产物。
//
// 用法: node probe/_e2e_inv_dual.mjs

import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = 'http://127.0.0.1:5000';
const P = [];
const F = [];
const ck = (c, what, detail = '') => {
  (c ? P : F).push(what);
  console.log(`  ${c ? 'OK ' : '★  '} ${what}${detail ? `  [${detail}]` : ''}`);
  return c;
};

const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const ctx = await browser.newContext({ viewport: { width: 1680, height: 1050 } });
const page = await ctx.newPage();

const jsErrors = [];
page.on('pageerror', e => jsErrors.push('pageerror: ' + e.message));
page.on('console', m => { if (m.type() === 'error') jsErrors.push('console: ' + m.text()); });

// 等某个选择器文本包含目标串
async function waitText(sel, needle, ms = 60000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const txt = await page.evaluate(s => (document.querySelector(s) || {}).textContent || '', sel);
    if (txt.includes(needle)) return txt;
    await page.waitForTimeout(500);
  }
  return await page.evaluate(s => (document.querySelector(s) || {}).textContent || '', sel);
}

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="fund"]', { timeout: 30000 });
await page.click('nav button[data-tab="fund"]');

console.log('=== A. 实盘：①卡库存块双口径（真实数据 + 真实交互） ===');
const cardTxt = await waitText('#f-strategy-body', '社会库存', 90000);
ck(cardTxt.includes('社会库存'), '①卡库存块出现「社会库存」行');
ck(cardTxt.includes('交易所仓单'), '①卡库存块出现「交易所仓单」行');
// 文案含"档位"二字以与四象限的"方向"验证区分(①卡比档位高低 / 四象限比方向)
ck(/库存 综合档位 (低|中|高|矛盾)\((偏多|偏空|中性)\)/.test(cardTxt),
  '①卡出现「库存 综合档位 X(方向词)」大字档位',
  (cardTxt.match(/库存 综合档位 [^·\n]{0,24}/) || ['?'])[0]);
const modeHit = /(双口径共振|口径背离|口径分歧|仅社会库存口径|仅仓单口径|无口径)/.exec(cardTxt);
ck(!!modeHit, '①卡标出了口径关系', modeHit ? modeHit[1] : '未找到');
// 双行各自带分位与日期（证明不是只印了个标签）
ck(/社会库存[^]{0,200}?分位/.test(cardTxt), '社会库存行带分位读数');
ck(/\d{4}-\d{2}-\d{2}/.test(cardTxt) || /无数据/.test(cardTxt), '双行带日期或如实标注无数据');

// 【为什么要等】徽标由 loadFundInventory 异步写入, 紧跟着读会拿到空串/旧值
//   (第一版就这么写, 红了一条 —— 不是功能问题, 是 E2E 自己的时序问题)。
let badge = '';
for (let i = 0; i < 60; i++) {
  badge = await page.evaluate(() => (document.getElementById('f-inv-badge') || {}).textContent || '');
  if (badge.includes('社会库存') || badge.includes('仓单最新')) break;
  await page.waitForTimeout(500);
}
ck(badge.includes('社会库存') && badge.includes('仓单最新'),
  '仓单徽标带双口径读数', badge.slice(0, 80));

const cycTxt = await waitText('#f-cycle-body', '社会库存', 60000);
ck(cycTxt.includes('社会库存'), '库存周期卡出现社会库存行');
ck(cycTxt.includes('近 4 期'), '社会库存行给出近 4 期方向（周频口径）');
ck(cycTxt.includes('双口径方向交叉验证'), '库存周期卡出现方向交叉验证行',
  (cycTxt.match(/双口径交叉验证: [^—\n]{0,20}/) || ['?'])[0]);
ck(cycTxt.includes('四象限') || cycTxt.includes('当前阶段') || cycTxt.includes('主动'),
  '四象限原有内容未被破坏');

console.log('');
console.log('=== B. 路由桩：仓单归零的警示标签必须真渲染出来 ===');
const WARN = '⚠ 仓单读数为 0(已连续 7 个交易日; 该品种历史 61.3% 时间为 0)。'
  + '0 可能表示无注册仓单或集中注销, 不必然等于库存极低 —— 请结合社会库存判断; '
  + '上次非零 525 @ 2026-09-29';
await page.route('**/api/fund/strategy*', async route => {
  const resp = await route.fetch();
  const j = await resp.json();
  if (j && j.inv) {
    j.inv.ck = Object.assign({}, j.inv.ck, { latest: 0, warn: WARN });
    j.inv.warn = WARN;
  }
  await route.fulfill({ json: j });
});
// ★ `App` 是顶层 `const`, **不在 window 上**(项目既有事实, 此前踩过)
//   ⇒ 页面 evaluate 里可直接引用 `App`(全局词法环境), 但 `window.App` 是 undefined。
const appProbe = await page.evaluate(() => typeof App);
ck(appProbe === 'object' || appProbe === 'function', 'App 可在页面上下文访问', appProbe);
if (appProbe === 'object' || appProbe === 'function') {
  await page.evaluate(() => App.loadFundStrategy());
} else {
  // 退化路径: 切品种 → onchange="App.loadFund()" → 会重新拉 strategy
  await page.evaluate(() => {
    const sel = document.getElementById('f-variety');
    const cur = sel.value;
    const other = [...sel.options].map(o => o.value).find(v => v && v !== cur);
    if (other) { sel.value = other; sel.dispatchEvent(new Event('change')); }
  });
}
await page.waitForTimeout(1200);
const stubbed = await page.evaluate(() => (document.getElementById('f-strategy-body') || {}).textContent || '');
ck(stubbed.includes('仓单读数为 0'), '0 值警示已渲染到 ①卡');
// 【★ 别取"第一个含该文本的 div"】祖先 div 的 textContent 同样含该文本, 而颜色写在
//   **叶子** div 上 ⇒ 第一版取到的是外层容器, 看着像"颜色不对"(E2E 实测踩到)。
//   正确判据: 存在**某一个自身 style 含 amber** 且文本含该串的元素。
const amber = await page.evaluate(() => {
  const els = [...document.querySelectorAll('#f-strategy-body div')];
  const hit = els.find(el => (el.textContent || '').includes('仓单读数为 0')
    && (el.getAttribute('style') || '').includes('amber'));
  return hit ? (hit.getAttribute('style') || '') : '';
});
ck(amber.includes('amber'), '警示用琥珀色（与普通灰字区分）', amber.slice(0, 70));
ck(stubbed.includes('社会库存'), '0 值场景下社会库存行仍在（可交叉判断）');
await page.unroute('**/api/fund/strategy*');

console.log('');
console.log('=== C. 路由桩：口径背离（库存转移迹象）在四象限卡呈现 ===');
await page.route('**/api/quant/inventory*', async route => {
  const resp = await route.fetch();
  const j = await resp.json();
  j.cycle = Object.assign({}, j.cycle, {
    ok: true, phase: '主动建库', note: '价格上行伴随补库, 偏多主升段',
    inv_dir: '升', inv_chg5_pct: 3.1, price_dir: '涨', inv_pct_rank: 96,
    social: { ok: true, label: '社会库存', latest: 100, latest_date: '2026-10-07',
      pct_rank: 0.02, grade: '低', count: 300, dir: '降', chg_pct: -6.5 },
    cross: { mode: '口径背离(库存转移迹象)',
      note: '仓单升而社会库存降 → 库存可能在向交割库转移, 勿单看仓单' },
  });
  await route.fulfill({ json: j });
});
await page.evaluate(() => App.loadFundInventory());
await page.waitForTimeout(1200);
const cyc2 = await page.evaluate(() => (document.getElementById('f-cycle-body') || {}).textContent || '');
ck(cyc2.includes('口径背离'), '四象限卡渲染出「口径背离」', (cyc2.match(/双口径方向交叉验证: [^—\n]{0,22}/) || ['?'])[0]);
ck(cyc2.includes('库存转移') || cyc2.includes('向交割库转移'), '背离时给出「库存向交割库转移」的解释');
// 同上: 要找**自身**带 amber 的那个元素, 而不是外层容器
const cycAmber = await page.evaluate(() => {
  const els = [...document.querySelectorAll('#f-cycle-body div')];
  const hit = els.find(el => (el.textContent || '').includes('双口径方向交叉验证')
    && (el.getAttribute('style') || '').includes('amber'));
  return hit ? (hit.getAttribute('style') || '') : '';
});
ck(cycAmber.includes('amber'), '背离用琥珀色醒目提示', cycAmber.slice(0, 70));
ck(cyc2.includes('社会库存'), '背离场景下社会库存方向仍显示');
ck(cyc2.includes('-6.5%') || cyc2.includes('近 4 期'), '社会库存近 4 期方向已显示');
await page.unroute('**/api/quant/inventory*');

console.log('');
console.log('=== D. 实盘：换品种后双口径随之刷新（非静态写死） ===');
const found = await page.evaluate(async () => {
  const sel = document.getElementById('f-variety');
  const out = [];
  sel.value = 'CF';
  sel.dispatchEvent(new Event('change'));
  return sel.value;
});
await page.waitForTimeout(3000);
const cfTxt = await page.evaluate(() => (document.getElementById('f-strategy-body') || {}).textContent || '');
ck(found === 'CF', '品种下拉可切换到 CF', found);
ck(cfTxt.includes('社会库存') && cfTxt.includes('交易所仓单'), '换品种后双行仍在');
const cfMode = /(双口径共振|口径背离|口径分歧|仅社会库存口径|仅仓单口径|无口径)/.exec(cfTxt);
ck(!!cfMode, '换品种后口径关系已更新（不是静态串）', cfMode ? cfMode[1] : '?');

console.log('');
console.log('=== E. 页面健康 ===');
ck(jsErrors.length === 0, '无 JS 运行时错误', jsErrors.slice(0, 2).join(' | ') || '0');

await page.screenshot({ path: 'probe/_shot_inv_dual.png', fullPage: false });

console.log('');
console.log('='.repeat(62));
console.log(`PASS ${P.length} / FAIL ${F.length}`);
if (F.length) { console.log('失败项:'); F.forEach(x => console.log('  ★ ' + x)); }

await browser.close();
process.exit(F.length ? 1 : 0);
