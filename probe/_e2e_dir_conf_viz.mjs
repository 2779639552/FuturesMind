// 方向/置信度可视化 E2E（2026-10-16 用户要求）
// 验两处：
//   ① 「当日观点总表」—— 后端 markdown 渲染 ⇒ 由 _decorateViewTable 做后处理
//   ② 「逐品种观点总览」—— 前端直接生成 ⇒ chipBg/chipColor/confCell
// 判据都落到**真 DOM 的计算样式**（不是查源码字符串），这样才能证明"真的变红了"。
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const B = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const page = await (await B.newContext({ viewport: { width: 1760, height: 1200 } })).newPage();
const errs = [];
page.on('pageerror', e => errs.push(String(e.message).slice(0, 200)));
page.on('console', m => { if (m.type() === 'error') errs.push('[console] ' + m.text().slice(0, 200)); });

let pass = 0, fail = 0;
const ck = (ok, label, extra) => {
  if (ok) { pass++; console.log('  ✔ ' + label + (extra ? '  [' + extra + ']' : '')); }
  else { fail++; console.log('  ★ FAIL ' + label + (extra ? '  [' + extra + ']' : '')); }
};
const sleep = ms => new Promise(r => setTimeout(r, ms));

await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="research"]', { timeout: 30000 });
await page.click('nav button[data-tab="research"]');
await sleep(2500);

// ─────────────────────────────────────────────────────────
// A. 「当日观点总表」（研报总结第一节，markdown 渲染 + 后处理）
// ─────────────────────────────────────────────────────────
console.log('\n=== A. 当日观点总表：方向 chip + 置信度进度条 ===');

// 找到含「方向」「置信度」表头的那张表（= 观点总表）
const found = await page.evaluate(() => {
  const out = { tables: 0, vizTables: 0, dirChips: 0, confBars: 0, hasVizMark: 0, cells: 0 };
  document.querySelectorAll('.report-content table').forEach(t => {
    const ths = [...t.querySelectorAll('thead th')].map(x => (x.textContent || '').trim());
    out.tables++;
    if (ths.includes('方向') && ths.includes('置信度')) {
      out.vizTables++;
      if (t.dataset.vizDone === '1') out.hasVizMark++;
      out.dirChips += t.querySelectorAll('.dir-chip').length;
      out.confBars += t.querySelectorAll('.conf-bar').length;
      out.cells += t.querySelectorAll('tbody td').length;
    }
  });
  return out;
});
console.log('    表统计: ' + JSON.stringify(found));
ck(found.vizTables >= 1, '找到「观点总表」（表头含方向+置信度）', '表数=' + found.vizTables);
ck(found.dirChips > 0, '★ 方向列已转成分色 chip', 'chip 数=' + found.dirChips);
ck(found.confBars > 0, '★ 置信度列已转成进度条', '进度条数=' + found.confBars);
ck(found.hasVizMark === found.vizTables, '幂等标记已落（dataset.vizDone）');

// 计算样式：看多 chip 应为红、看空 chip 应为绿
const colors = await page.evaluate(() => {
  const res = { up: [], down: [], flat: [], bars: [] };
  document.querySelectorAll('.dir-chip').forEach(c => {
    const cs = getComputedStyle(c);
    const txt = c.textContent.trim();
    const rec = { txt, color: cs.color, bg: cs.backgroundColor };
    if (c.classList.contains('up')) res.up.push(rec);
    else if (c.classList.contains('down')) res.down.push(rec);
    else res.flat.push(rec);
  });
  document.querySelectorAll('.conf-bar').forEach(b => {
    const fill = b.querySelector('.fill');
    const pct = b.querySelector('.pct');
    if (fill && pct) {
      res.bars.push({
        txt: pct.textContent.trim(),
        w: fill.style.width,
        bg: getComputedStyle(fill).backgroundColor,
      });
    }
  });
  return res;
});
// 【三色判别】按 RGB 组合分类,不写死 hex（主题/亮暗会换色值）。
// ⚠ 第一版用"R 通道 > B 通道"判红 —— 结果**橙色(琥珀)的 R 也高**,中性被误判成看多 ⇒ 假 FAIL。
//   正确判据要看**绿通道**: 红 G 很低(69)、橙 G 中等(159)、绿 G 很高(209)。
const cls = rgb => {
  const m = (rgb || '').match(/(\d+),\s*(\d+),\s*(\d+)/);
  if (!m) return '?';
  const r = +m[1], g = +m[2];
  if (g < 110) return 'red';
  if (r < 130) return 'green';
  return 'amber';
};
const isRed = rgb => cls(rgb) === 'red';
const isGreen = rgb => cls(rgb) === 'green';
const isAmber = rgb => cls(rgb) === 'amber';

ck(colors.up.length > 0, '有「看多」chip', 'n=' + colors.up.length);
ck(colors.down.length > 0, '有「看空」chip', 'n=' + colors.down.length);
ck(colors.up.every(c => isRed(c.color)), '★★ 看多 = 红（中国市场惯例）',
  colors.up.map(c => c.txt + ':' + c.color).slice(0, 3).join(' | '));
ck(colors.down.every(c => isGreen(c.color)), '★★ 看空 = 绿',
  colors.down.map(c => c.txt + ':' + c.color).slice(0, 3).join(' | '));
ck(colors.flat.every(c => isAmber(c.color)), '中性 = 琥珀（既非红也非绿）',
  colors.flat.map(c => c.txt + ':' + c.color).slice(0, 2).join(' | '));

// 进度条：宽度字符串要等于自身百分比；颜色按强度（高=红）
ck(colors.bars.length > 0, '有置信度进度条', 'n=' + colors.bars.length);
const widthOk = colors.bars.every(b => {
  const n = parseInt(String(b.txt).replace('%', ''), 10);
  return b.w === n + '%';
});
ck(widthOk, '★ 进度条宽度 = 百分比数值', colors.bars.slice(0, 4).map(b => b.txt + '→' + b.w).join(' '));
const high = colors.bars.filter(b => parseInt(b.txt, 10) >= 70);
const low = colors.bars.filter(b => parseInt(b.txt, 10) < 40);
if (high.length) ck(high.every(b => isRed(b.bg)), '★ 高置信度 = 红（强度色）',
  high.map(b => b.txt + ':' + b.bg).slice(0, 3).join(' | '));
if (low.length) ck(low.every(b => isGreen(b.bg)), '★ 低置信度 = 绿（强度色）',
  low.map(b => b.txt + ':' + b.bg).slice(0, 3).join(' | '));
console.log('    样本: ' + colors.bars.slice(0, 5).map(b => b.txt + '/' + b.w + '/' + b.bg).join('  '));

// ─────────────────────────────────────────────────────────
// B. 逐品种观点总览
// ─────────────────────────────────────────────────────────
console.log('\n=== B. 逐品种观点总览：方向 chip + 置信度进度条 ===');
await page.evaluate(() => {
  const sel = document.getElementById('res-views-variety');
  if (sel) {
    const first = [...sel.options].find(o => o.value);
    if (first) sel.value = first.value;
  }
});
await page.evaluate(() => App.loadResearchViews && App.loadResearchViews());
let bstat = null;
for (let i = 0; i < 60; i++) {
  bstat = await page.evaluate(() => {
    const el = document.getElementById('res-views');
    if (!el) return null;
    const chips = [...el.querySelectorAll('.rv-chip')];
    const bars = [...el.querySelectorAll('#res-views .conf-bar, #res-views [style*="width:56px"]')];
    return {
      rows: el.querySelectorAll('tbody tr').length,
      chips: chips.length,
      chipSample: chips.slice(0, 4).map(c => ({
        txt: c.textContent.trim(), color: getComputedStyle(c).color,
      })),
      bars: bars.length,
    };
  });
  if (bstat && bstat.rows > 0) break;
  await sleep(500);
}
console.log('    总览: ' + JSON.stringify(bstat));
ck(bstat && bstat.rows > 0, '逐品种总览已加载', '行数=' + (bstat ? bstat.rows : '-'));

// 【★ 为什么要遍历】被选中的第一个品种当天可能只有"中性"观点 ⇒ 那样"看多=红/看空=绿"
//   在 B 表其实**没被验到**（只验了 A 表）。遍历品种直到找到含看多/看空的样本,
//   找不到就如实报"未取到样本",不假装通过。
const allChips = await page.evaluate(() => {
  const el = document.getElementById('res-views');
  return el ? [...el.querySelectorAll('.rv-chip')].map(c => ({
    txt: c.textContent.trim(), color: getComputedStyle(c).color,
  })) : [];
});
let bUps = allChips.filter(c => c.txt.includes('多'));
let bDowns = allChips.filter(c => c.txt.includes('空'));
if (!bUps.length || !bDowns.length) {
  const codes = await page.evaluate(() => {
    const sel = document.getElementById('res-views-variety');
    return sel ? [...sel.options].map(o => o.value).filter(Boolean) : [];
  });
  for (const code of codes.slice(0, 10)) {
    await page.evaluate(c => {
      const sel = document.getElementById('res-views-variety');
      if (sel) { sel.value = c; }
      return App.loadResearchViews && App.loadResearchViews();
    }, code);
    for (let i = 0; i < 25; i++) {
      const got = await page.evaluate(() => {
        const el = document.getElementById('res-views');
        return el && el.innerText && !el.innerText.includes('加载中')
          ? [...el.querySelectorAll('.rv-chip')].map(c => ({ txt: c.textContent.trim(), color: getComputedStyle(c).color }))
          : null;
      });
      if (got) {
        const u = got.filter(c => c.txt.includes('多'));
        const d = got.filter(c => c.txt.includes('空'));
        if (u.length) bUps = bUps.concat(u);
        if (d.length) bDowns = bDowns.concat(d);
        break;
      }
      await sleep(400);
    }
    if (bUps.length && bDowns.length) { console.log('    找到样本品种: ' + code); break; }
  }
}
if (bUps.length) ck(bUps.every(c => isRed(c.color)), '★★ 总览：看多 = 红',
  bUps.map(c => c.txt + ':' + c.color).slice(0, 3).join(' | '));
else ck(false, '★★ 总览：看多 = 红（未取到看多样本）');
if (bDowns.length) ck(bDowns.every(c => isGreen(c.color)), '★★ 总览：看空 = 绿',
  bDowns.map(c => c.txt + ':' + c.color).slice(0, 3).join(' | '));
else ck(false, '★★ 总览：看空 = 绿（未取到看空样本）');

// ─────────────────────────────────────────────────────────
// C. 幂等 + 不误伤其它表
// ─────────────────────────────────────────────────────────
console.log('\n=== C. 幂等与边界 ===');
const twice = await page.evaluate(() => {
  const before = document.querySelectorAll('.report-content .dir-chip').length;
  const el = document.getElementById('res-daily-content') || document.querySelector('.report-content');
  if (window.App && App._decorateViewTable) App._decorateViewTable(el);
  if (window.App && App._decorateViewTable) App._decorateViewTable(el);
  const after = document.querySelectorAll('.report-content .dir-chip').length;
  return { before, after };
});
ck(twice.before === twice.after, '★ 重复调用不叠加（幂等）',
  twice.before + ' → ' + twice.after);

const untouched = await page.evaluate(() => {
  // 二~四节的表格（无「方向」+「置信度」表头）不应被写入 vizDone
  let other = 0, marked = 0;
  document.querySelectorAll('.report-content table').forEach(t => {
    const ths = [...t.querySelectorAll('thead th')].map(x => (x.textContent || '').trim());
    if (!(ths.includes('方向') && ths.includes('置信度'))) {
      other++;
      if (t.dataset.vizDone === '1') marked++;
    }
  });
  return { other, marked };
});
ck(untouched.marked === 0, '不误伤其它表格（只处理含方向+置信度的表）',
  '其它表 ' + untouched.other + ' 张，被误标 ' + untouched.marked);

console.log('\n=== D. JS 错误 ===');
ck(errs.length === 0, '无 JS 运行时错误', errs.slice(0, 2).join(' | ') || '0 条');

console.log('\n' + '='.repeat(58));
console.log('  PASS ' + pass + ' / FAIL ' + fail);
console.log('='.repeat(58));

await page.screenshot({ path: 'probe/_shot_dirconf_full.png', fullPage: false });
await B.close();
process.exit(fail ? 1 : 0);
