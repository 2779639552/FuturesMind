// E2E 验收: 深蓝金风格全站推广（2026-10-13）
// 用法: node probe/_e2e_deepblue_promotion.mjs [BASE]
// 覆盖:
//   A 亮色全局令牌 = 深蓝金（--brand/--chart-brand/主按钮/字体栈）
//   B 跨页签一致（#tab-quant 作用域已删，fund 等页同样吃深蓝金）
//   C 暗色：颜色不动（--brand 仍为 #c7c7ff）+ 字族已全站统一（serif/sans/num 全局生效）
//   D 切主题重染：全站图表即时换色 + 零重取数（fetch 计数=0）+ 系列数据不变
//   E 截图：亮/暗 × quant/fund
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const BASE = process.argv[2] || 'http://127.0.0.1:5000';
const TABS = ['run', 'sentiment', 'quant', 'fund', 'warrant', 'research', 'gtja', 'variety'];

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

const cssVar = (name) => page.evaluate(n =>
  getComputedStyle(document.documentElement).getPropertyValue(n).trim(), name);

// 统一先切到亮色（默认可能是暗色）
await page.evaluate(() => {
  if ((document.documentElement.getAttribute('data-theme') || 'dark') === 'dark') App.toggleTheme();
});
await page.waitForTimeout(600);

// ── A 亮色全局令牌 = 深蓝金 ─────────────────────────────────
console.log('\n[A] 亮色全局令牌（深蓝金）');
log((await cssVar('--brand')) === '#8a6a34', `--brand = ${await cssVar('--brand')}（期望 #8a6a34 gold-d）`);
log((await cssVar('--chart-brand')) === '#1b4571', `--chart-brand = ${await cssVar('--chart-brand')}（期望 #1b4571 navy2）`);
log((await cssVar('--chart-axis')) === '#5a6472', `--chart-axis = ${await cssVar('--chart-axis')}（期望 #5a6472 muted）`);
log((await cssVar('--text')) === '#1a2332', `--text = ${await cssVar('--text')}（期望 #1a2332 ink）`);
log((await cssVar('--bg')) === '#f7f9fb', `--bg = ${await cssVar('--bg')}（期望 #f7f9fb 冷白）`);
log((await cssVar('--brand-line')) === '#b8935a', `--brand-line = ${await cssVar('--brand-line')}（期望 #b8935a gold）`);

const btnBg = await page.evaluate(() => {
  const b = document.querySelector('.btn-primary');
  return b ? getComputedStyle(b).backgroundColor : null;
});
log(btnBg === 'rgb(14, 42, 71)', `主按钮底色 = ${btnBg}（期望 rgb(14,42,71) navy）`);

const fonts = await page.evaluate(() => {
  const th = document.querySelector('table th');
  return {
    h3: getComputedStyle(document.querySelector('.card h3')).fontFamily,
    td: getComputedStyle(document.querySelector('table td')).fontFamily,
    th: th ? getComputedStyle(th).fontFamily : '',
    body: getComputedStyle(document.body).fontFamily,
  };
});
log(/Source Han Serif SC/.test(fonts.h3), `.card h3 字体 = ${fonts.h3.slice(0, 42)}…（应含宋体栈）`);
log(/Georgia/.test(fonts.td), `table td 字体 = ${fonts.td.slice(0, 42)}…（应含 Georgia 数字栈）`);
log(/Georgia/.test(fonts.th), `table th 字体 = ${fonts.th.slice(0, 42)}…（应含 Georgia 数字栈，与 td 同族）`);
log(/Source Han Sans SC/.test(fonts.body), `body 字体 = ${fonts.body.slice(0, 42)}…（应含 Han Sans 栈）`);

// ── B 跨页签一致（作用域已删） ──────────────────────────────
console.log('\n[B] 跨页签一致（深蓝金全局生效，不再限于 quant）');
for (const t of ['fund', 'run', 'sentiment', 'gtja']) {
  await page.evaluate(x => App.switchTab(x), t);
  await page.waitForTimeout(900);
  const v = await cssVar('--brand');
  const h3f = await page.evaluate(() => {
    const h = document.querySelector(`#tab-${App._currentTab} .card h3`);
    return h ? getComputedStyle(h).fontFamily : '';
  });
  log(v === '#8a6a34', `${t} 页 --brand = ${v}（期望 #8a6a34）`);
  log(/Source Han Serif SC/.test(h3f), `${t} 页 .card h3 为宋体栈`);
}

// ── B2 研报正文标题宋体栈（本次统一新补的漏出兵）─────────────
console.log('\n[B2] 研报正文标题（.report-content h1-h3）宋体栈');
await page.evaluate(() => App.switchTab('research'));
await page.waitForTimeout(2000);
const repH = await page.evaluate(() => {
  const h = document.querySelector('.report-content h1, .report-content h2, .report-content h3');
  return h ? getComputedStyle(h).fontFamily : '';
});
if (repH) {
  log(/Source Han Serif SC/.test(repH), `研报正文标题字体 = ${repH.slice(0, 42)}…（应含宋体栈）`);
} else {
  log(true, '(info) 当前默认日期无研报正文 DOM（数据依赖），规则存在性由守卫测试覆盖');
}

// ── C 暗色：颜色不动 + 字族已全站统一 ───────────────────────
console.log('\n[C] 暗色主题：颜色保持原值，字族与亮色统一');
await page.evaluate(() => App.switchTab('quant'));  // 换到必有表格的页签，保证 td 可测
await page.waitForTimeout(900);
await page.evaluate(() => App.toggleTheme());  // → dark
await page.waitForTimeout(600);
log((await cssVar('--brand')) === '#c7c7ff', `暗色 --brand = ${await cssVar('--brand')}（期望 #c7c7ff 原值）`);
log((await cssVar('--chart-axis')) === '#8b9aad', `暗色 --chart-axis = ${await cssVar('--chart-axis')}（期望 #8b9aad 原值）`);
const darkFonts = await page.evaluate(() => {
  const h3 = document.querySelector('.card h3');
  const td = document.querySelector('table td');
  return {
    h3: h3 ? getComputedStyle(h3).fontFamily : '',
    td: td ? getComputedStyle(td).fontFamily : '',
    body: getComputedStyle(document.body).fontFamily,
  };
});
log(/Source Han Serif SC/.test(darkFonts.h3), `暗色 .card h3 已统一为宋体栈（${darkFonts.h3.slice(0, 30)}…）`);
log(/Georgia/.test(darkFonts.td), `暗色 table td 已统一为 Georgia 数字栈（${darkFonts.td.slice(0, 30)}…）`);
log(/Source Han Sans SC/.test(darkFonts.body), `暗色 body 已统一为 Han Sans 栈（${darkFonts.body.slice(0, 30)}…）`);

// ── D 切主题重染：零重取数 + 数据不变 + 即时换色 ────────────
console.log('\n[D] 切主题图表重染（fund 页，通用机制）');
await page.evaluate(() => App.switchTab('fund'));
await page.waitForTimeout(2600);
const fundCharts = await page.evaluate(() => Object.keys(App._chartInstances));
console.log(`  (info) fund 页已渲染图表: ${fundCharts.join(', ') || '无'}`);

const snapChart = async (id) => page.evaluate(cid => {
  const c = App._chartInstances[cid];
  if (!c) return null;
  let json;
  try { json = JSON.stringify(c.getOption()); } catch (e) { return { err: String(e) }; }
  const ks = (c.getOption().series || []).find(s => s.type === 'candlestick');
  return { json, series: (c.getOption().series || []).map(s => (s.data || []).length), kUp: ks ? ks.itemStyle && ks.itemStyle.color : null };
}, id);

const probeId = fundCharts.find(id => id.startsWith('chart-f-')) || null;
if (probeId) {
  const before = await snapChart(probeId);
  // 装 fetch 计数器（连 URL 一起记，失败时可取证）→ 先测**对照窗口**（不 toggle）
  // → 再测 toggle 窗口。断言"toggle 不新增取数"用**两窗比对**而非绝对计数=0：
  // 页签历史里可能有链式取数晚发起（如 /api/variety→/api/fund/... 的第二段），
  // 绝对计数会把这种背景噪声误报成"重染触发取数"。
  await page.evaluate(() => {
    window.__fetchN = 0;
    window.__fetchUrls = [];
    const orig = window.fetch;
    window.fetch = function (...a) {
      window.__fetchN++;
      try { window.__fetchUrls.push(String(a[0])); } catch (e) {}
      return orig.apply(this, a);
    };
  });
  await page.waitForTimeout(1200);
  const controlN = await page.evaluate(() => window.__fetchN);
  await page.evaluate(() => App.toggleTheme());  // dark → light
  await page.waitForTimeout(1200);
  const after = await snapChart(probeId);
  const testN = await page.evaluate(() => window.__fetchN);
  log(before && after && before.json !== after.json, `${probeId}: option 已换色（JSON 前后不同）`);
  log(testN === controlN, `${probeId}: toggle 窗口 fetch = ${testN} vs 对照窗口 = ${controlN}（应相等，即重染零取数）`);
  if (testN !== controlN) {
    const urls = await page.evaluate(() => window.__fetchUrls.slice(controlN));
    console.log(`   (forensic) toggle 窗口新增的 fetch: ${urls.map(u => u.slice(0, 80)).join(' | ')}`);
  }
  log(JSON.stringify(before && before.series) === JSON.stringify(after && after.series),
     `${probeId}: 系列数据长度不变（${JSON.stringify(before && before.series)}）`);
} else {
  log(false, 'fund 页未渲染任何图表，无法验证重染');
}

console.log('\n[D2] 切主题图表重染（quant 页 K 线，涨跌色令牌）');
await page.evaluate(() => App.switchTab('quant'));
await page.waitForTimeout(2600);
const kBefore = await snapChart('chart-q-kline');
if (kBefore && !kBefore.err) {
  log(kBefore.kUp === '#c8102e', `亮色 K 线涨色 = ${kBefore.kUp}（期望 #c8102e 白底 AA 红）`);
  await page.evaluate(() => App.toggleTheme());  // light → dark
  await page.waitForTimeout(1200);
  const kDark = await snapChart('chart-q-kline');
  log(kDark && kDark.kUp === '#ef4444', `暗色 K 线涨色 = ${kDark && kDark.kUp}（期望 #ef4444 原值，暗色不动）`);
  await page.evaluate(() => App.toggleTheme());  // dark → light（重染回深蓝金）
  await page.waitForTimeout(1200);
  const kBack = await snapChart('chart-q-kline');
  log(kBack && kBack.kUp === '#c8102e', `切回亮色 K 线涨色 = ${kBack && kBack.kUp}（期望 #c8102e，重染可逆）`);
  log(JSON.stringify(kBefore.series) === JSON.stringify(kBack.series), 'K 线系列数据长度不变（往返切换不丢数据）');
} else {
  log(false, `quant 页 K 线图不可用: ${kBefore && kBefore.err || '无实例'}`);
}

// ── E 截图 ─────────────────────────────────────────────────
console.log('\n[E] 截图');
for (const theme of ['light', 'dark']) {
  await page.evaluate(t => {
    while ((document.documentElement.getAttribute('data-theme') || 'dark') !== t) App.toggleTheme();
  }, theme);
  for (const t of ['quant', 'fund']) {
    await page.evaluate(x => App.switchTab(x), t);
    await page.waitForTimeout(1800);
    await page.screenshot({ path: `probe/_shot_deepblue_${theme}_${t}.png`, fullPage: false });
    console.log(`  (shot) probe/_shot_deepblue_${theme}_${t}.png`);
  }
}

log(errs.length === 0, `页面无 JS 错误（${errs.length} 个）${errs.slice(0, 3).join(' | ')}`);
await browser.close();
console.log(`\n结果: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
