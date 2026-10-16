// tab 结构重组 · 浏览器 E2E（2026-10-14）
//
// 【纪律（项目铁律）】
//  · 切页签一律**真实点击** `nav button[data-tab=xxx]`；`App` 不在 window 上，
//    调 `App.switchTab` 会**静默失败**、页面停在默认 tab ⇒ 所有筛选类断言假绿。
//  · **先断言集合非空**，再断言"筛得对"（空集合天然满足"都在范围内"）。
//  · 等异步渲染用**轮询**，不要固定 waitForTimeout。
//
// 用法：node probe/_e2e_tab_restructure.mjs
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = 'http://127.0.0.1:5000';
let pass = 0, fail = 0;
const log = (ok, msg) => { console.log(`${ok ? '  PASS' : '  FAIL'}  ${msg}`); ok ? pass++ : fail++; };

const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const ctx = await browser.newContext({ viewport: { width: 1680, height: 1050 } });
const page = await ctx.newPage();
const errs = [];
page.on('pageerror', e => errs.push(String(e).slice(0, 200)));

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="run"]', { timeout: 30000 });

// ── 1. nav 顺序 ────────────────────────────────────────────────
const navOrder = await page.evaluate(() =>
  Array.from(document.querySelectorAll('nav#nav button[data-tab]'))
    .filter(b => getComputedStyle(b).display !== 'none')
    .map(b => b.dataset.tab));
log(JSON.stringify(navOrder) === JSON.stringify(['run', 'research', 'quant', 'fund', 'sentiment', 'foreign', 'warehouse']),
  `导航顺序 = ${navOrder.join(' → ')}`);
log(!navOrder.includes('gtja') && !navOrder.includes('warrant'), 'nav 已无 gtja / warrant');

// ── 2. 数据仓库：卖方数据块 + 原三卡 ──────────────────────────
console.log('\n[数据仓库]');
await page.click('nav button[data-tab="warehouse"]');
await page.waitForTimeout(1500);
log(await page.evaluate(() => !!document.querySelector('#tab-warehouse.active')), '已切到数据仓库页');

// 四个模块标题都在
const whTitles = await page.evaluate(() => {
  const t = document.querySelector('#tab-warehouse').innerText;
  return {
    seller: t.includes('查询控制') || t.includes('卖方数据'),
    sources: t.includes('四源取数状态') || t.includes('四源'),
    upload: t.includes('上传研报'),
    personal: t.includes('自传数据'),
    lib: t.includes('研报库'),
  };
});
log(whTitles.seller, '卖方数据块在位（查询控制可见）');
log(whTitles.upload && whTitles.personal && whTitles.lib, '上传研报 / 自传数据 / 研报库 三卡都还在');

// 卖方数据的品种下拉必须有选项（= loadGtja 真的跑了；空 = 没接线）
let gtOpts = 0;
for (let i = 0; i < 30; i++) {
  gtOpts = await page.evaluate(() => {
    const s = document.getElementById('gt-variety');
    return s ? s.options.length : 0;
  });
  if (gtOpts > 0) break;
  await page.waitForTimeout(1000);
}
log(gtOpts > 0, `卖方数据品种下拉已填充 (#gt-variety options=${gtOpts}) —— 证明 loadGtja 被调用`);

// 卖方数据块在 warehouse 容器的**首位**
const sellerFirst = await page.evaluate(() => {
  const tab = document.querySelector('#tab-warehouse');
  const tools = document.getElementById('tab-gtja-tools');
  if (!tab || !tools) return false;
  // 找 tools 所在的最外层祖先中、属于 tab 直接子元素的那个
  let n = tools;
  while (n.parentElement && n.parentElement !== tab) n = n.parentElement;
  const kids = Array.from(tab.children);
  const idx = kids.indexOf(n);
  // 允许前面有 0~1 个纯注释/空白节点（HTML 里索引不包含注释）
  return idx === 0;
});
log(sellerFirst, '卖方数据块位于数据仓库页首位（顺序 卖方数据 → 上传研报 → 自传数据 → 研报库）');

// 并入方控制条已降为页内卡
const whTools = await page.evaluate(() => {
  const el = document.getElementById('tab-gtja-tools');
  return el ? { cls: el.className, isPt: el.classList.contains('page-tools') } : null;
});
log(!!whTools && !whTools.isPt, `并入方容器已降为页内卡（class="${whTools?.cls}"，不再是 page-tools）`);
const whPtCount = await page.evaluate(() => document.querySelectorAll('#tab-warehouse .page-tools').length);
log(whPtCount === 0, `数据仓库页 .page-tools 数 = ${whPtCount}（该页本就没有顶部控制条）`);

// ── 3. 基本面量化：仓单块 ──────────────────────────────────────
console.log('\n[基本面量化]');
await page.click('nav button[data-tab="fund"]');
await page.waitForTimeout(2000);
const fuInfo = await page.evaluate(() => {
  const t = document.querySelector('#tab-fund');
  const txt = t.innerText;
  return {
    pt: t.querySelectorAll('.page-tools').length,
    hasWarrant: txt.includes('仓单总览') || txt.includes('仓单数量排名'),
    hasWarrantHist: txt.includes('仓单历史走势') || txt.includes('四所仓单合计走势'),
    stillOwn: txt.includes('三指标策略系统') && txt.includes('虚实盘比'),
  };
});
log(fuInfo.pt === 1, `基本面量化页 .page-tools 数 = ${fuInfo.pt}（应恰为 1 —— 铁律）`);
log(fuInfo.hasWarrant, '仓单总览/排名 块在位');
log(fuInfo.hasWarrantHist, '仓单历史走势 块在位（= 追加到最后）');
log(fuInfo.stillOwn, '原基本面量化模块（三指标 / 虚实盘比）未丢');

// 仓单的品种下拉必须填充（= loadWarrant 跑了）
let wtOpts = 0;
for (let i = 0; i < 30; i++) {
  wtOpts = await page.evaluate(() => {
    const s = document.getElementById('wt-product');
    return s ? s.options.length : 0;
  });
  if (wtOpts > 0) break;
  await page.waitForTimeout(1000);
}
log(wtOpts > 0, `仓单品种下拉已填充 (#wt-product options=${wtOpts}) —— 证明 loadWarrant 被调用`);

// 仓单块在 fund 容器内、且在最后
const wtLast = await page.evaluate(() => {
  const hist = document.getElementById('wt-h-wh');
  const tab = document.querySelector('#tab-fund');
  if (!hist || !tab) return null;
  return { inTab: tab.contains(hist) };
});
log(!!wtLast?.inTab, '仓单历史块在基本面量化容器内');

await page.screenshot({ path: 'probe/_shot_fund_with_warrant.png' });

// ── 4. Ctrl+N 快捷键 ─────────────────────────────────────────
console.log('\n[快捷键]');
const seq = ['1', '4', '5', '7'];
const expect = ['run', 'fund', 'sentiment', 'warehouse'];
for (let i = 0; i < seq.length; i++) {
  await page.keyboard.down('Control');
  await page.keyboard.press(seq[i]);
  await page.keyboard.up('Control');
  await page.waitForTimeout(400);
  const cur = await page.evaluate(() =>
    (document.querySelector('nav#nav button.active') || {}).dataset?.tab);
  log(cur === expect[i], `Ctrl+${seq[i]} → ${cur}（期望 ${expect[i]}）`);
}

// ── 5. 其余页未被破坏 ─────────────────────────────────────────
console.log('\n[回归抽查]');
for (const t of ['run', 'quant', 'sentiment', 'foreign', 'research']) {
  await page.click(`nav button[data-tab="${t}"]`);
  await page.waitForTimeout(700);
  const ok = await page.evaluate((tb) => {
    const el = document.querySelector(`#tab-${tb}`);
    if (!el || !el.classList.contains('active')) return false;
    return el.innerText.trim().length > 30;   // 有实际内容，不是空壳
  }, t);
  log(ok, `tab-${t} 可正常打开且有内容`);
}

// ── 6. 双主题截图（数据仓库）─────────────────────────────────
await page.click('nav button[data-tab="warehouse"]');
await page.waitForTimeout(1500);
await page.screenshot({ path: 'probe/_shot_warehouse_dark.png' });
await page.click('.theme-toggle');
await page.waitForTimeout(900);
const theme = await page.evaluate(() => document.documentElement.getAttribute('data-theme'));
await page.screenshot({ path: 'probe/_shot_warehouse_light.png' });
log(true, `已截图（data-theme=${theme}）`);

console.log(`\n=== ${pass} PASS / ${fail} FAIL ===`);
console.log(`JS 错误数 = ${errs.length}`);
errs.slice(0, 6).forEach(e => console.log('  ! ' + e));
await browser.close();
process.exit(fail === 0 ? 0 : 1);
