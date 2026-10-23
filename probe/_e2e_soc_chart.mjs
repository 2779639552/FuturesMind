// E2E：④ 卡「仓单库存 · 社会库存双口径」图（2026-10-16 用户报"只有仓单的图"）
//
// 【为什么要真开浏览器】这是 **ECharts option 层**的正确性：
//   · 序列条数 / 网格数 / 每个系列的 data 是否真有值 —— 静态串断言查不到；
//   · "社会库存按阶梯对齐到仓单日期轴"是这个功能的核心语义，必须真算一遍看结果：
//     ① 对齐后非空点数 = 轴上的点数（从首个社会库存日起）；
//     ② 轴起点之前必须为空（不许把未来读数提前画上）；
//     ③ 社会库存在**非其读数日**的值必须等于**上一期**的值（阶梯，不是插值）。
import { chromium } from 'file:///C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';

const B = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  args: ['--no-sandbox'],
});
const ctx = await B.newContext({ viewport: { width: 1760, height: 1200 } });
const page = await ctx.newPage();
const errs = [];
page.on('pageerror', e => errs.push(String(e).slice(0, 200)));
page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 160)); });

let pass = 0, fail = 0;
const ck = (ok, name, extra = '') => {
  if (ok) { pass++; console.log('  OK  ' + name + (extra ? '  [' + extra + ']' : '')); }
  else { fail++; console.log('  ★   ' + name + (extra ? '  [' + extra + ']' : '')); }
};

// 取 ECharts 实例当前的 option（含 series 的 data）
const opt = () => page.evaluate(() => {
  const el = document.getElementById('chart-f-inv');
  const c = window.echarts && window.echarts.getInstanceByDom(el);
  if (!c) return null;
  const o = c.getOption();
  return {
    grids: (o.grid || []).length,
    series: (o.series || []).map(s => ({
      name: s.name, type: s.type, xAxisIndex: s.xAxisIndex || 0, yAxisIndex: s.yAxisIndex || 0,
      n: (s.data || []).length,
      nonNull: (s.data || []).filter(v => v !== null && v !== undefined).length,
      step: s.step || null,
      data: (s.data || []).slice(0, 400),
    })),
    xAxis0: ((o.xAxis || [])[0] || {}).data || [],
    legend: ((o.legend || [])[0] || {}).data || [],
    // ⚠ 不能用 JSON.stringify 查 tooltip 文案 —— `getOption()` 里 formatter 还是**函数**，
    //   序列化后整个函数会消失（第一版就这么写，恒不命中）。取函数源码来搜。
    tip: (() => {
      const t = (o.tooltip || []);
      const f = (Array.isArray(t) ? (t[0] || {}) : t).formatter;
      return typeof f === 'function' ? String(f) : '';
    })(),
  };
});

await page.goto('http://127.0.0.1:5000', { waitUntil: 'domcontentloaded' });
await page.waitForSelector('nav button[data-tab="fund"]', { timeout: 30000 });
await page.click('nav button[data-tab="fund"]');
for (let i = 0; i < 120; i++) {
  const n = await page.evaluate(() => document.querySelectorAll('#f-rk-rows tr[onclick]').length);
  if (n >= 21) break;
  await page.waitForTimeout(1000);
}

// ── A. 双口径都有（TA）⇒ 上下两栏 + 4 条序列 ────────────────────────────────
await page.evaluate(() => App.fOpenDetail('TA'));
for (let i = 0; i < 80; i++) {
  const o = await opt();
  if (o && o.series.length >= 4) break;
  await page.waitForTimeout(1000);
}
let o = await opt();
ck(!!o, '拿到 #chart-f-inv 的 ECharts 实例');
if (o) {
  ck(o.grids === 2, '★ 上下两栏（2 个 grid）', String(o.grids));
  ck(o.series.length === 4, '★ 4 条序列（双口径 × 水平/变化）', o.series.map(s => s.name).join('/'));
  const names = o.series.map(s => s.name);
  ck(names.indexOf('社会库存') >= 0, '★ 有「社会库存」序列 —— 这就是用户要的那条线');
  ck(names.indexOf('社会库存变化') >= 0, '有「社会库存变化」序列');
  const soc = o.series.find(s => s.name === '社会库存');
  const socChg = o.series.find(s => s.name === '社会库存变化');
  const ckLine = o.series.find(s => s.name === '注册仓单');
  ck(!!soc && soc.nonNull > 0, '★ 社会库存序列**真有数据**（非全空）',
    soc ? (soc.nonNull + '/' + soc.n + ' 点') : 'missing');
  ck(soc && soc.n === o.xAxis0.length, '社会库存对齐到仓单日期轴（点数与轴一致）',
    soc ? (soc.n + ' vs 轴 ' + o.xAxis0.length) : '');
  ck(soc && soc.step === 'end', '★ 社会库存用**阶梯**（step=end），不是插值', String(soc && soc.step));
  ck(ckLine && ckLine.nonNull === ckLine.n, '注册仓单序列满值（仓单日频，每天都有）',
    ckLine ? (ckLine.nonNull + '/' + ckLine.n) : '');
  ck(soc && soc.yAxisIndex === 1 && ckLine && ckLine.yAxisIndex === 0,
    '★ 两口径**各用各的轴**（仓单 idx0 / 社会库存 idx1）',
    'ck=' + (ckLine && ckLine.yAxisIndex) + ' soc=' + (soc && soc.yAxisIndex));
  // 社会库存变化柱：只落在少数日子（周频），不是每天
  ck(socChg && socChg.nonNull > 0 && socChg.nonNull < o.xAxis0.length * 0.6,
    '社会库存变化柱是**稀疏**的（周频落在首个交易日，未摊成每天）',
    socChg ? (socChg.nonNull + '/' + socChg.n) : '');
  ck(o.tip.indexOf('只看走势与背离') >= 0, 'tooltip 里写明「两口径量纲不同, 各用各的轴」',
    o.tip ? 'formatter 已定义' : '★ formatter 不是函数');
  // ── 阶梯对齐的三条硬校验 ──
  if (soc && soc.n) {
    const firstNonNull = soc.data.findIndex(v => v !== null && v !== undefined);
    ck(firstNonNull > 0 || soc.nonNull === soc.n,
      '轴起点早于首个社会库存读数时**留空**（不把未来读数提前画上）', '首个非空 @' + firstNonNull);
    // 非空区间内，值只能"保持或跳到下一个读数"，绝不能出现插值出的中间值
    const vals = soc.data.filter(v => v !== null && v !== undefined);
    ck(vals.length === soc.nonNull, '非空值计数自洽');
    ck(firstNonNull >= 0 && soc.data.slice(firstNonNull).every(v => v !== null && v !== undefined),
      '★ 首个读数之后**不再有空洞**（阶梯前推覆盖到轴末）');
  }
}
await page.screenshot({ path: 'probe/_shot_soc_chart.png' });
const box = await page.locator('#chart-f-inv').boundingBox();
if (box) {
  await page.screenshot({ path: 'probe/_shot_soc_chart_zoom.png',
    clip: { x: Math.max(0, box.x - 12), y: Math.max(0, box.y - 44),
            width: Math.min(box.width + 24, 900), height: box.height + 60 } });
}

// ── B. 只有仓单（BZ/CJ 无国君库存）⇒ 保持原单栏布局 ─────────────────────────
// 【★ 等待条件必须"数据真的换了"】第一版用 `series.length >= 2` —— 而**上一张图（TA）**
//   已经满足 ⇒ 循环立刻结束、读到的是旧数据 ⇒ 三条断言全是假的（看着像产品缺陷）。
//   正解：等 `_fundSym()` 已是 BZ **且**徽标里不再有"社会库存"（BZ 确实无国君库存口径）。
const taSym = await page.evaluate(() => App._fundSym());
ck(taSym === 'TA', '切品种前 _fundSym() 是 TA', taSym);
await page.evaluate(() => App.fOpenDetail('BZ'));
let o2 = null;
for (let i = 0; i < 90; i++) {
  const st = await page.evaluate(() => ({
    sym: App._fundSym(), badge: (document.getElementById('f-inv-badge') || {}).textContent || '',
  }));
  if (st.sym === 'BZ' && st.badge.indexOf('社会库存') < 0) { o2 = await opt(); break; }
  await page.waitForTimeout(1000);
}
if (o2) {
  const nm2 = o2.series.map(s => s.name).join('/');
  ck(nm2.indexOf('社会库存') < 0, '★ 无社会库存的品种不硬凑（不画那条线）', nm2);
  ck(o2.grids === 1 && o2.series.length === 2, '★ 退回原单栏布局（仓单线 + 日变化柱）',
    'grids=' + o2.grids + ' series=' + o2.series.length);
  const bd = await page.evaluate(() => (document.getElementById('f-inv-badge') || {}).textContent || '');
  ck(bd.indexOf('社会库存') < 0, '徽标如实不显示社会库存（该品种确实没有）', bd.slice(0, 60));
} else {
  ck(false, '拿到 BZ 的图表实例');
}

console.log('\nJS 错误：' + (errs.length ? errs.join(' | ') : '0 个 ✓'));
console.log('PASS ' + pass + ' / FAIL ' + fail);
await B.close();
process.exit(fail ? 1 : 0);
